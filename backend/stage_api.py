"""
5 阶段案卷分析 API

新分析引擎入口，替代旧的 pipeline_api.py 流水线
阶段 1：读起诉书 — 提取指控要素
阶段 2：人物关系图
阶段 3：事件时间线 + 事件拆解
阶段 4：法律法规梳理
阶段 5：证据分析 + 矛盾分析 + 口供对比 + 三阶层辩护
"""
import asyncio
import json
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, HTTPException

from analysis_engine import AnalysisEngine
from analysis_pipeline import _contains_indictment_title
from case_manager import find_case_path

router = APIRouter(prefix="/api/stage-analysis", tags=["5阶段案卷分析"])

# 实时进度状态
STAGE_PROGRESS: dict = {}

# 起诉意见书匹配模式
_OPINION_PATTERNS = ["起诉意见书", "呈请起诉", "起诉报告"]


async def _run_sub_stage(engine, sub_stage_type: str, defendant: str, crime_type: Optional[str]):
    """
    运行阶段 5 的子阶段（51/52/53）
    需要阶段 1-4 的结果已存在
    """
    # 读取阶段 1-4 的 Markdown
    stage1_md = _read_stage_md(engine.analysis_dir, 1)
    stage2_md = _read_stage_md(engine.analysis_dir, 2)
    stage3_md = _read_stage_md(engine.analysis_dir, 3)
    stage4_md = _read_stage_md(engine.analysis_dir, 4)

    texts = engine._load_evidence_texts()

    from llm_client import get_llm_client
    client = get_llm_client()

    if sub_stage_type == "evidence_analysis":
        parts = []
        for ev in texts:
            result = await client.chat([
                {"role": "system", "content": "你是刑事辩护律师，正在逐份审查证据，评估证据的合法性、真实性、关联性。"},
                {"role": "user", "content": f"""## 证据文件：{ev["filename"]}（{ev["type"]}）

{ev["text"][:50000]}

请从以下三个方面对每份证据进行审查：

### 一、合法性审查（重点）
- 取证程序是否合法？（讯问/询问时间、地点、人员数量、签名等）
- 是否存在非法证据排除情形？（刑讯逼供、威胁、引诱、欺骗等）
- 程序文书是否齐全？（立案、拘留、逮捕、取保等手续是否完备）
- 对应刑诉法条文：指出具体违反的刑诉法条文编号

### 二、真实性审查
- 证据内容是否真实可靠？是否存在矛盾或不合理之处？
- 同一人多次笔录是否一致？有无翻供或重大变化？

### 三、关联性审查
- 证据与待证事实的关联程度？
- 对指控的支持程度如何？

### 四、综合评价
- 有利/不利方面
- 薄弱环节
- 辩护切入点"""},
            ])
            parts.append(f"### {ev['filename']}（{ev['type']}）\n{result}")
        engine._save_stage(51, {"name": "证据分析"}, "\n\n".join(parts))
        return {"success": True}

    elif sub_stage_type == "contradiction_analysis":
        all_text = "\n\n".join([f"### {t['filename']}\n{t['text'][:30000]}" for t in texts])
        result = await client.chat([
            {"role": "system", "content": "你是刑事辩护律师，正在识别证据间的矛盾和口供变化。"},
            {"role": "user", "content": f"""## 辩护对象：{defendant}\n\n## 案卷材料\n{all_text[:150000]}\n\n请分析：1. 同一人多次笔录的变化 2. 不同证据对同一事实的矛盾 3. 证据链条薄弱环节"""},
        ])
        engine._save_stage(52, {"name": "矛盾分析"}, result)
        return {"success": True}

    elif sub_stage_type == "three_tier_defense":
        # 需要 5A 和 5B 的结果
        stage51 = _read_stage_md(engine.analysis_dir, 51)
        stage52 = _read_stage_md(engine.analysis_dir, 52)

        from legal_knowledge import THEORY_THREE_TIERS, CONSTITUTIVE_ELEMENT_ANALYSIS
        try:
            from legal_knowledge import get_dynamic_legal_knowledge
            crime_specific = get_dynamic_legal_knowledge(crime_type) if crime_type else ""
        except Exception:
            crime_specific = ""

        result = await client.chat([
            {"role": "system", "content": "你是资深刑事辩护律师，正在撰写三阶层综合辩护分析报告。"},
            {"role": "user", "content": f"""## 辩护对象：{defendant}\n## 阶段1：指控要素\n{stage1_md[:3000]}\n## 阶段3：事件拆解\n{stage3_md[:3000]}\n## 阶段4：法律法规\n{stage4_md[:5000]}\n## 5A：证据分析\n{stage51[:5000]}\n## 5B：矛盾分析\n{stage52[:5000]}\n## 三阶层体系\n{THEORY_THREE_TIERS[:2000]}\n\n请完成三阶层综合辩护分析：1. 辩护概要 2. 构成要件符合性 3. 违法性 4. 有责性 5. 综合辩护意见"""},
        ])
        engine._save_stage(53, {"name": "三阶层辩护"}, result)
        return {"success": True}

    raise ValueError(f"未知子阶段类型: {sub_stage_type}")


def _read_stage_md(analysis_dir, stage: int) -> str:
    """读取指定阶段的 Markdown 输出"""
    from pathlib import Path
    stage_file = analysis_dir / f"stage_{stage}" / "output.md"
    if stage_file.exists():
        return stage_file.read_text(encoding="utf-8")
    return ""


def _set_progress(case_id: str, stage: int, message: str):
    STAGE_PROGRESS[case_id] = {
        "stage": stage,
        "message": message,
        "status": "running",
        "updated_at": time.time(),
    }


def _clear_progress(case_id: str):
    STAGE_PROGRESS.pop(case_id, None)


@router.get("/{case_id}/indictment-candidates")
async def get_indictment_candidates(case_id: str):
    """扫描案件 MD 文件，找出所有起诉书和起诉意见书候选。"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    md_dir = case_path / "md"
    if not md_dir.exists():
        return {"case_id": case_id, "candidates": []}

    candidates = []
    for f in sorted(md_dir.iterdir(), key=lambda x: x.name):
        if f.suffix.lower() != ".md":
            continue
        text = f.read_text(encoding="utf-8")
        head = text[:3000]

        doc_type = None
        if _contains_indictment_title(head):
            doc_type = "起诉书"
        elif any(p in head for p in _OPINION_PATTERNS):
            doc_type = "起诉意见书"

        if doc_type:
            candidates.append({
                "filename": f.name,
                "doc_type": doc_type,
                "preview": text[:500],
            })

    return {"case_id": case_id, "candidates": candidates}


# 分析任务状态追踪
ANALYSIS_TASKS: dict = {}


async def _execute_all_stages(case_id: str, defendant: str, crime_type: Optional[str], indictment_file: Optional[str] = None):
    """后台执行全部 5 阶段分析"""
    try:
        case_path = find_case_path(case_id)
        if not case_path:
            ANALYSIS_TASKS[case_id] = {"status": "error", "error": "案件不存在"}
            return

        md_dir = case_path / "md"
        if not md_dir.exists() or not any(md_dir.glob("*.md")):
            ANALYSIS_TASKS[case_id] = {"status": "error", "error": "案件中无 MD 文件"}
            return

        evidence_dir = case_path / "evidence"
        index_file = evidence_dir / "index.json"
        if not index_file.exists():
            ANALYSIS_TASKS[case_id] = {"status": "error", "error": "未提取证据，无法进行分析。请先完成证据提取。"}
            return

        engine = AnalysisEngine(case_id, case_path, indictment_file=indictment_file)
        _set_progress(case_id, 0, "开始 5 阶段分析...")

        # 阶段 1
        _set_progress(case_id, 1, "正在分析起诉书，提取指控要素...")
        r1 = await engine.stage_1_read_indictment(defendant, crime_type)

        # 阶段 2
        _set_progress(case_id, 2, "正在分析人物关系...")
        r2 = await engine.stage_2_character_relations(defendant, crime_type)

        # 阶段 3
        _set_progress(case_id, 3, "正在分析事件时间线和证据归组...")
        r3 = await engine.stage_3_event_timeline(defendant, crime_type)

        # 阶段 4
        _set_progress(case_id, 4, f"正在梳理{crime_type or '涉案罪名'}相关法律法规...")
        r4 = await engine.stage_4_legal_regulations(defendant, crime_type)

        # 阶段 5
        _set_progress(case_id, 5, "正在生成综合辩护分析报告...")
        r5 = await engine.stage_5_full_defense(defendant, crime_type)

        _clear_progress(case_id)
        ANALYSIS_TASKS[case_id] = {"status": "completed", "stages": 5}

    except ValueError as e:
        _clear_progress(case_id)
        ANALYSIS_TASKS[case_id] = {"status": "error", "error": str(e)}
    except Exception as e:
        _clear_progress(case_id)
        ANALYSIS_TASKS[case_id] = {"status": "error", "error": f"分析执行失败: {str(e)}"}


@router.post("/{case_id}/run-all")
async def run_all_stages(
    case_id: str,
    defendant: str = Body(..., embed=True),
    crime_type: Optional[str] = Body(default=None, embed=True),
    indictment_file: Optional[str] = Body(default=None, embed=True),
):
    """
    异步执行全部 5 阶段分析
    触发后立即返回，前端通过 /status 接口轮询完成状态
    """
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    md_dir = case_path / "md"
    if not md_dir.exists() or not any(md_dir.glob("*.md")):
        raise HTTPException(status_code=400, detail="案件中无 MD 文件，请先完成 PDF 转 MD")

    evidence_dir = case_path / "evidence"
    index_file = evidence_dir / "index.json"
    if not index_file.exists():
        raise HTTPException(status_code=400, detail="未提取证据，无法进行分析。请先完成证据提取。")

    # 如果已有任务在运行中，不允许重复触发
    existing = ANALYSIS_TASKS.get(case_id)
    if existing and existing.get("status") == "running":
        raise HTTPException(status_code=409, detail="分析任务已在运行中，请稍候...")

    ANALYSIS_TASKS[case_id] = {"status": "running", "started_at": time.time()}

    # 后台执行，不阻塞
    import asyncio
    asyncio.create_task(_execute_all_stages(case_id, defendant, crime_type, indictment_file=indictment_file))

    return {
        "success": True,
        "case_id": case_id,
        "message": "分析任务已触发，请轮询 /status 接口查看进度",
    }


@router.post("/{case_id}/run-stage/{stage_num}")
async def run_single_stage(
    case_id: str,
    stage_num: int,
    defendant: str = Body(..., embed=True),
    crime_type: Optional[str] = Body(default=None, embed=True),
    indictment_file: Optional[str] = Body(default=None, embed=True),
):
    """
    单独执行某个阶段（支持 51/52/53 子阶段）
    """
    if not (1 <= stage_num <= 5 or stage_num in (51, 52, 53)):
        raise HTTPException(status_code=400, detail="无效阶段编号，请输入 1-5 或 51-53")

    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    engine = AnalysisEngine(case_id, case_path, indictment_file=indictment_file)

    stage_methods = {
        1: lambda: engine.stage_1_read_indictment(defendant, crime_type),
        2: lambda: engine.stage_2_character_relations(defendant, crime_type),
        3: lambda: engine.stage_3_event_timeline(defendant, crime_type),
        4: lambda: engine.stage_4_legal_regulations(defendant, crime_type),
        5: lambda: engine.stage_5_full_defense(defendant, crime_type),
        51: lambda: _run_sub_stage(engine, "evidence_analysis", defendant, crime_type),
        52: lambda: _run_sub_stage(engine, "contradiction_analysis", defendant, crime_type),
        53: lambda: _run_sub_stage(engine, "three_tier_defense", defendant, crime_type),
    }

    stage_names = {
        1: "指控要素",
        2: "人物关系",
        3: "事件拆解",
        4: "法律法规",
        5: "综合辩护分析",
        51: "证据分析",
        52: "矛盾分析",
        53: "三阶层辩护",
    }

    _set_progress(case_id, stage_num, f"正在执行阶段 {stage_num}：{stage_names[stage_num]}...")

    try:
        result = await stage_methods[stage_num]()
        _clear_progress(case_id)
        return {"success": True, "stage": stage_num, "data": result}
    except ValueError as e:
        _clear_progress(case_id)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _clear_progress(case_id)
        raise HTTPException(status_code=500, detail=f"阶段 {stage_num} 执行失败: {str(e)}")


@router.get("/{case_id}/progress")
async def get_progress(case_id: str):
    """获取分析进度"""
    progress = STAGE_PROGRESS.get(case_id)
    if not progress:
        return {"case_id": case_id, "running": False}
    return {"case_id": case_id, "running": True, **progress}


@router.get("/{case_id}/status")
async def get_status(case_id: str):
    """获取各阶段完成状态（含 51/52/53 子阶段）+ 任务运行状态"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    analysis_dir = case_path / "analysis"
    status = {}
    stage_names = {
        1: "指控要素",
        2: "人物关系",
        3: "事件拆解",
        4: "法律法规",
        5: "综合辩护分析",
        51: "证据分析",
        52: "矛盾分析",
        53: "三阶层辩护",
    }

    for stage in [1, 2, 3, 4, 5, 51, 52, 53]:
        result_file = analysis_dir / f"stage_{stage}" / "output.json"
        status[f"stage_{stage}"] = {
            "name": stage_names[stage],
            "completed": result_file.exists(),
        }

    # 合并任务运行状态
    task = ANALYSIS_TASKS.get(case_id)
    return {"case_id": case_id, "status": status, "task": task}


@router.get("/{case_id}/stage/{stage_num}/result")
async def get_stage_result(case_id: str, stage_num: int):
    """获取指定阶段的分析结果（支持 51/52/53 子阶段）"""
    if not (1 <= stage_num <= 5 or stage_num in (51, 52, 53)):
        raise HTTPException(status_code=400, detail="无效阶段编号")

    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    result_file = case_path / "analysis" / f"stage_{stage_num}" / "output.json"
    if not result_file.exists():
        raise HTTPException(status_code=404, detail=f"阶段 {stage_num} 的结果不存在")

    with open(result_file, "r", encoding="utf-8") as f:
        return json.load(f)


@router.get("/{case_id}/stage/{stage_num}/markdown")
async def get_stage_markdown(case_id: str, stage_num: int):
    """获取指定阶段的 Markdown 输出（支持 51/52/53 子阶段）"""
    if not (1 <= stage_num <= 5 or stage_num in (51, 52, 53)):
        raise HTTPException(status_code=400, detail="无效阶段编号")

    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    md_file = case_path / "analysis" / f"stage_{stage_num}" / "output.md"
    if not md_file.exists():
        raise HTTPException(status_code=404, detail=f"阶段 {stage_num} 的 Markdown 不存在")

    content = md_file.read_text(encoding="utf-8")
    return {"case_id": case_id, "stage": stage_num, "content": content}


@router.get("/{case_id}/full-report")
async def get_full_report(case_id: str):
    """获取完整综合辩护报告"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    report_file = case_path / "analysis" / "full_defense_report.md"
    if not report_file.exists():
        raise HTTPException(status_code=404, detail="完整报告尚未生成")

    content = report_file.read_text(encoding="utf-8")
    return {"case_id": case_id, "content": content}
