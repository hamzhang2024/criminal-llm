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
import logging
import time
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Body, HTTPException, Query

from analysis_engine import AnalysisEngine
from analysis_pipeline import AnalysisPipeline, _contains_indictment_title
from case_manager import find_case_path

logger = logging.getLogger(__name__)

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



def _resolve_stage_path(case_path: Path, stage_num: int, charge: Optional[str] = None) -> Path:
    """解析阶段 Markdown 文件的存储路径（共享层 vs 罪名层）

    - 共享层(stage_1/2/3/51/52): analysis/stage_N/output.md
    - 罪名层(stage_4/5/6): analysis/{charge}/stage_N/output.md
    - stage_6 固定映射到 04.5-控辩对抗/对抗分析.md
    - stage_5 完整报告映射到 full_defense_report.md
    - 安全: 拒绝目录穿越字符
    """
    if charge and any(c in str(charge) for c in ('..', '/', chr(92), chr(0))):
        raise HTTPException(status_code=400, detail="无效的罪名名称")

    # 确定基础目录和文件名
    if stage_num == 6:
        filename = "对抗分析.md"
        subdir = "04.5-控辩对抗"
    elif stage_num == 5 and charge:  # 罪名层的 stage_5 完整报告
        return case_path / "analysis" / charge / "full_defense_report.md"
    else:
        filename = "output.md"
        subdir = f"stage_{stage_num}"

    # 共享层走旧路径，罪名层走 analysis/{charge}/
    if charge and stage_num not in (1, 2, 3, 51, 52):
        return case_path / "analysis" / charge / subdir / filename
    return case_path / "analysis" / subdir / filename


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
    """扫描证据目录，找出所有起诉书和起诉意见书候选。"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    candidates = []

    # 扫描 evidence/ 目录（证据提取后的文件）
    evidence_dir = case_path / "evidence"
    if evidence_dir.exists():
        for f in sorted(evidence_dir.iterdir(), key=lambda x: x.name):
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
                    "source": "evidence",
                    "preview": text[:500],
                })

    return {"case_id": case_id, "candidates": candidates}


# 分析任务状态追踪
ANALYSIS_TASKS: dict = {}


async def _execute_all_stages(case_id: str, defendant: str, charges: list = None, indictment_file: Optional[str] = None):
    """后台执行全部 5 阶段分析（支持多罪名）

    共享层(stage_1/2/3/51/52)只跑一次 → analysis/_shared/
    罪名层(stage_4/5)每个罪名独立跑 → analysis/{charge}/stage_N/
    """
    try:
        case_path = find_case_path(case_id)
        if not case_path:
            ANALYSIS_TASKS[case_id] = {"status": "error", "error": "案件不存在"}
            return

        evidence_dir = case_path / "evidence"
        index_file = evidence_dir / "index.json"
        if not index_file.exists():
            ANALYSIS_TASKS[case_id] = {"status": "error", "error": "未提取证据，无法进行分析。请先完成证据提取。"}
            return

        if not charges:
            charges = []

        engine = AnalysisEngine(case_id, case_path, indictment_file=indictment_file)
        _set_progress(case_id, 0, "开始分析...")

        # ── 共享层(stage_1/2/3): 案件事实,多罪名共用 ──
        ANALYSIS_TASKS[case_id] = {"status": "running", "current_stage": 1}
        _set_progress(case_id, 1, "正在分析起诉书，提取指控要素...")
        r1 = await engine.stage_1_read_indictment(defendant, charges[0] if charges else None)

        ANALYSIS_TASKS[case_id] = {"status": "running", "current_stage": 2}
        _set_progress(case_id, 2, "正在分析人物关系...")
        r2 = await engine.stage_2_character_relations(defendant, charges[0] if charges else None)

        ANALYSIS_TASKS[case_id] = {"status": "running", "current_stage": 3}
        _set_progress(case_id, 3, "正在分析事件时间线和证据归组...")
        r3 = await engine.stage_3_event_timeline(defendant, charges[0] if charges else None)

        # ── 罪名层(stage_4/5): 每个罪名独立 ──
        charge_count = len(charges) if charges else 1
        for idx, charge in enumerate(charges or [None]):
            charge_name = charge or "默认"
            ANALYSIS_TASKS[case_id] = {"status": "running", "current_stage": 4}
            _set_progress(case_id, 4, f"正在梳理{charge_name}相关法律法规 ({idx+1}/{charge_count})...")
            await engine.stage_4_legal_regulations(defendant, charge)

            ANALYSIS_TASKS[case_id] = {"status": "running", "current_stage": 5}
            _set_progress(case_id, 5, f"正在生成{charge_name}辩护分析报告 ({idx+1}/{charge_count})...")
            await engine.stage_5_full_defense(defendant, charge)

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
    charges: List[str] = Body(default=[], embed=True),
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
    # 兼容旧调用: crime_type 非空时包装为 charges
    effective_charges = charges or ([crime_type] if crime_type else [])

    # 保存 charges 到 case.json（如果提供了 charges）
    if effective_charges:
        meta_file = case_path / "case.json"
        if meta_file.exists():
            try:
                with open(meta_file, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                meta["charges"] = effective_charges
                with open(meta_file, 'w', encoding='utf-8') as f:
                    json.dump(meta, f, ensure_ascii=False, indent=2)
            except Exception:
                pass  # 保存失败不阻塞分析

    asyncio.create_task(_execute_all_stages(case_id, defendant, effective_charges, indictment_file=indictment_file))

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
    if not (1 <= stage_num <= 6 or stage_num in (51, 52, 53)):
        raise HTTPException(status_code=400, detail="无效阶段编号，请输入 1-6 或 51-53")

    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    # 阶段 6 需要 AnalysisPipeline
    if stage_num == 6:
        pipeline = AnalysisPipeline(case_id, case_path, indictment_file=indictment_file)

        ANALYSIS_TASKS[case_id] = {"status": "running", "current_stage": stage_num}
        _set_progress(case_id, stage_num, "正在执行阶段 6：控辩对抗模拟...")
        try:
            result = await pipeline.step45_debate_simulation(defendant, crime_type)
            _clear_progress(case_id)
            ANALYSIS_TASKS[case_id] = {"status": "completed", "stages": 1}
            return {"success": True, "stage": stage_num, "data": result}
        except ValueError as e:
            _clear_progress(case_id)
            ANALYSIS_TASKS.pop(case_id, None)
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            _clear_progress(case_id)
            ANALYSIS_TASKS.pop(case_id, None)
            raise HTTPException(status_code=500, detail=f"阶段 {stage_num} 执行失败: {str(e)}")

    engine = AnalysisEngine(case_id, case_path, indictment_file=indictment_file)

    stage_methods = {
        1: lambda: engine.stage_1_read_indictment(defendant, crime_type),
        2: lambda: engine.stage_2_character_relations(defendant, crime_type),
        3: lambda: engine.stage_3_event_timeline(defendant, crime_type),
        4: lambda: engine.stage_4_legal_regulations(defendant, crime_type),
        5: lambda: engine.stage_5_full_defense(defendant, crime_type),
        6: lambda: AnalysisPipeline(case_id, case_path, indictment_file=indictment_file).step45_debate_simulation(defendant, crime_type),
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
        6: "控辩对抗",
        51: "证据分析",
        52: "矛盾分析",
        53: "三阶层辩护",
    }

    ANALYSIS_TASKS[case_id] = {"status": "running", "current_stage": stage_num}
    _set_progress(case_id, stage_num, f"正在执行阶段 {stage_num}：{stage_names[stage_num]}...")

    try:
        result = await stage_methods[stage_num]()
        _clear_progress(case_id)
        ANALYSIS_TASKS[case_id] = {"status": "completed", "stages": 1}
        return {"success": True, "stage": stage_num, "data": result}
    except ValueError as e:
        _clear_progress(case_id)
        ANALYSIS_TASKS.pop(case_id, None)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _clear_progress(case_id)
        ANALYSIS_TASKS.pop(case_id, None)
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
        6: "控辩对抗",
        51: "证据分析",
        52: "矛盾分析",
        53: "三阶层辩护",
    }

    for stage in [1, 2, 3, 4, 5, 6, 51, 52, 53]:
        # 阶段 6 路径特殊处理
        if stage == 6:
            result_file = analysis_dir / "04.5-控辩对抗" / "对抗分析.md"
        elif stage in (51, 52, 53):
            result_file = analysis_dir / f"stage_{stage}" / "output.json"
        else:
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
    if not (1 <= stage_num <= 6 or stage_num in (51, 52, 53)):
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
async def get_stage_markdown(case_id: str, stage_num: int, charge: Optional[str] = None):
    """获取指定阶段的 Markdown 输出（支持 1-6、6 控辩对抗、51/52/53 子阶段）

    多罪名支持:
    - charge 为 None 或 "" → 读旧路径 analysis/stage_N/output.md（兼容旧数据）
    - charge 指定 → 读 analysis/{charge}/stage_N/output.md（罪名层）
    - 共享层(stage_1/2/3/51/52)不受 charge 影响，读 _shared 路径
    """
    valid_stages = set(range(1, 7)) | {51, 52, 53}
    if stage_num not in valid_stages:
        raise HTTPException(status_code=400, detail="无效阶段编号")

    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    # 共享层(stage_1/2/3/51/52): 读 analysis/_shared/
    # 罪名层(stage_4/5/6): 读 analysis/{charge}/
    md_file = _resolve_stage_path(case_path, stage_num, charge)

    if not md_file.exists():
        raise HTTPException(status_code=404, detail=f"阶段 {stage_num} 的 Markdown 不存在")

    content = md_file.read_text(encoding="utf-8")
    return {"case_id": case_id, "stage": stage_num, "content": content, "charge": charge}


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


@router.put("/{case_id}/stage/{stage_num}/markdown")
async def save_stage_markdown(
    case_id: str,
    stage_num: int,
    content: str = Body(..., embed=True),
    charge: Optional[str] = None,
):
    """保存指定阶段的 Markdown 内容到磁盘（多罪名：charge 参数指定罪名目录）"""
    valid_stages = set(range(1, 7)) | {51, 52, 53}
    if stage_num not in valid_stages:
        raise HTTPException(status_code=400, detail="无效阶段编号")

    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    md_file = _resolve_stage_path(case_path, stage_num, charge)
    md_file.parent.mkdir(parents=True, exist_ok=True)

    md_file.parent.mkdir(parents=True, exist_ok=True)
    md_file.write_text(content, encoding="utf-8")
    return {"success": True, "case_id": case_id, "stage": stage_num}


@router.put("/{case_id}/full-report")
async def save_full_report(
    case_id: str,
    content: str = Body(..., embed=True),
):
    """保存完整综合辩护报告到磁盘"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    report_file = case_path / "analysis" / "full_defense_report.md"
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(content, encoding="utf-8")
    return {"success": True}


# ========== 证据质证意见 API ==========

@router.post("/{case_id}/review-evidence")
async def review_evidence(case_id: str):
    """对全部证据进行质证意见生成（合并三性审查）

    审查内容包括：
    - 合法性审查：取证主体资格、取证程序、证据形式、非法证据排除
    - 真实性审查：来源可靠性、内容客观性、保管链条、同一性确认
    - 关联性审查：与待证事实的关系、证明价值、证据间印证

    审查结果包含：
    - 审查结论（采信/不采信/存疑）
    - 法律依据（具体法条引用）
    - 质证意见（可当庭陈述的质证理由）
    - 质证策略（申请/请求/主张）

    输出保存到：
    - evidence/evidence_review.json（结构化数据）
    - analysis/cross_examination.md（质证意见文档）
    """
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    engine = AnalysisEngine(case_id, case_path)

    try:
        result = await engine.review_evidence_triple_property()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{case_id}/evidence-review")
async def get_evidence_review(case_id: str):
    """获取证据质证审查结果

    返回结构化的三性审查数据，包含每份证据的：
    - 合法性、真实性、关联性评分和发现
    - 具体法律依据引用
    - 质证意见和质证策略
    """
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    review_file = case_path / "evidence" / "evidence_review.json"
    if not review_file.exists():
        return {"case_id": case_id, "total_evidence": 0, "reviews": [], "error": "证据审查结果不存在，请先运行审查"}

    return json.loads(review_file.read_text(encoding="utf-8"))


# ========== 阅卷笔录 API ==========

@router.post("/{case_id}/review-notes")
async def generate_review_notes(case_id: str):
    """生成阅卷笔录

    阅卷笔录是律师阅卷工作的核心文档，包含：
    - 案件基本信息
    - 证据目录
    - 证据三性审查摘要
    - 指控要素
    - 事实认定
    - 法律分析
    - 辩护要点
    """
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    engine = AnalysisEngine(case_id, case_path)

    try:
        result = await engine.generate_review_notes()
        return result
    except Exception as e:
        logger.error(f"[阅卷笔录] {case_id}: 生成失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{case_id}/review-notes")
async def get_review_notes(case_id: str):
    """获取阅卷笔录"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    notes_file = case_path / "analysis" / "review_notes.md"
    if not notes_file.exists():
        return {"case_id": case_id, "content": "", "error": "阅卷笔录尚未生成，请先点击生成"}

    content = notes_file.read_text(encoding="utf-8")
    return {"case_id": case_id, "content": content}


# ========== 质证意见文档 API ==========

@router.post("/{case_id}/cross-examination")
async def generate_cross_examination(case_id: str):
    """生成或获取质证意见文档

    注意：此功能已合并到证据审查中。如果已进行证据审查，直接返回结果；
    如果未审查，会自动执行质证意见生成（包含三性审查）。

    质证意见格式：
    - 审查概览
    - 问题证据清单
    - 详细质证意见（每份证据的三性审查+质证策略）
    """
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    engine = AnalysisEngine(case_id, case_path)

    try:
        result = await engine.generate_cross_examination()
        return result
    except Exception as e:
        logger.error(f"[质证意见] {case_id}: 生成失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{case_id}/cross-examination")
async def get_cross_examination(case_id: str):
    """获取质证意见 Markdown 文档

    返回可直接用于庭审的质证意见文档，包含：
    - 审查概览
    - 问题证据清单
    - 每份证据的详细质证意见
    """
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    notes_file = case_path / "analysis" / "cross_examination.md"
    if not notes_file.exists():
        return {"case_id": case_id, "content": "", "error": "质证意见尚未生成，请先进行证据审查"}

    content = notes_file.read_text(encoding="utf-8")
    return {"case_id": case_id, "content": content}


# ========== 证据链可视化 ==========

@router.get("/{case_id}/evidence-chain")
async def get_evidence_chain(
    case_id: str,
    charge: Optional[str] = Query(default=None, description="按罪名过滤证据链（空=全部）"),
):
    """获取证据链可视化数据

    返回证据节点和关系边，用于前端 SVG 可视化渲染：
    - nodes: 证据节点列表
    - edges: 关系边（印证/矛盾/补充）
    - groups: 证据类型分组
    """
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    from analysis_engine import generate_evidence_chain

    try:
        result = generate_evidence_chain(case_path, charge=charge if charge else None)
        return result
    except Exception as e:
        logger.error(f"[证据链] {case_id}: 分析失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ========== 人物关系图 & 事件时间线（SVG 可视化）==========

@router.get("/{case_id}/person-relation")
async def get_person_relation(case_id: str):
    """获取人物关系图数据（SVG 可视化用）

    从 stage_2/output.md 解析人物节点和关系边
    """
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    analysis_dir = case_path / "analysis"
    stage2_file = analysis_dir / "stage_2" / "output.md"

    if not stage2_file.exists():
        return {"nodes": [], "edges": [], "error": "人物关系分析尚未完成"}

    try:
        content = stage2_file.read_text(encoding="utf-8")
        result = _parse_person_relation(content)
        return result
    except Exception as e:
        logger.error(f"[人物关系] {case_id}: 解析失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{case_id}/event-timeline")
async def get_event_timeline(case_id: str):
    """获取事件时间线数据（SVG 可视化用）

    从 stage_3/output.md 解析事件节点
    """
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    analysis_dir = case_path / "analysis"
    stage3_file = analysis_dir / "stage_3" / "output.md"

    if not stage3_file.exists():
        return {"events": [], "error": "事件时间线分析尚未完成"}

    try:
        content = stage3_file.read_text(encoding="utf-8")
        result = _parse_event_timeline(content)
        return result
    except Exception as e:
        logger.error(f"[事件时间线] {case_id}: 解析失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _parse_person_relation(content: str) -> dict:
    """从 stage_2 Markdown 解析人物关系数据"""
    import re

    nodes = []
    edges = []

    # 先解析表格获取角色信息（表格中有明确的角色列）
    name_role_map = {}  # 姓名 -> 角色
    role_map = {
        "被告人": "defendant",
        "嫌疑人": "defendant",
        "主犯": "defendant",
        "从犯": "co defendant",
        "同案犯": "co defendant",
        "共犯": "co defendant",
        "证人": "witness",
        "关联人": "witness",
        "关系人": "witness",
        "被害人": "victim",
        "受害人": "victim",
        "介绍人": "witness",
        "保证人": "witness",
    }

    lines = content.split('\n')
    in_table = False
    for line in lines:
        # 匹配表格分隔行：| --- | 或 | :--- | 或 |------|------|
        if re.match(r'^\|[\s:]*-+[\s:|-]*\|', line):
            in_table = True
            continue
        if not in_table or not line.strip().startswith('|'):
            continue

        cols = [c.strip() for c in line.split('|') if c.strip()]
        if len(cols) < 2:
            continue

        name = cols[0].replace('*', '').strip()
        role_str = cols[1] if len(cols) > 1 else ""

        if name == "姓名" or "涉案人员" in name:
            continue

        # 确定角色
        node_role = "other"
        for key, val in role_map.items():
            if key in role_str:
                node_role = val
                break

        if name and len(name) <= 10:
            name_role_map[name] = node_role

    # 方法1：解析 mermaid graph 代码块（获取关系边）
    mermaid_match = re.search(r'```mermaid\s*graph\s*\w+\s*(.*?)```', content, re.DOTALL)
    if mermaid_match:
        mermaid_code = mermaid_match.group(1)

        # 解析节点定义：支持单字母ID(A[姓名])和多字符拼音ID(FengYefei[姓名])
        node_pattern = r'(\w+)\[([^\]]+)\]'
        node_matches = re.findall(node_pattern, mermaid_code)
        node_id_map = {}  # ID -> 姓名

        for node_id, node_name in node_matches:
            # 每个 ID 只取第一次出现的定义，避免 subgraph 中重复定义覆盖
            if node_id not in node_id_map:
                node_id_map[node_id] = node_name
            # 从表格角色映射中获取角色，优先级最高
            role = "other"

            # 精确匹配
            if node_name in name_role_map:
                role = name_role_map[node_name]
            else:
                # 前缀匹配：戴子佳(佳诚数码) -> 戴子佳
                base_name = node_name.split('(')[0].strip() if '(' in node_name else node_name
                if base_name in name_role_map:
                    role = name_role_map[base_name]

            # 如果表格中没有，尝试从名字推断
            if role == "other":
                if "被告" in node_name or "嫌疑人" in node_name or "犯罪" in node_name:
                    role = "defendant"
                elif "(" in node_name and ")" in node_name:
                    desc = node_name[node_name.find("("):node_name.find(")")+1]
                    if any(k in desc for k in ["被告", "嫌疑人", "主犯"]):
                        role = "defendant"
                    elif any(k in desc for k in ["从犯", "同案", "共犯"]):
                        role = "co defendant"
                    elif any(k in desc for k in ["证", "关联"]):
                        role = "witness"
                    elif any(k in desc for k in ["被害", "受害人"]):
                        role = "victim"

            nodes.append({
                "id": node_name,
                "name": node_name,
                "role": role,
                "description": "",
            })

        # 解析边：A -- "关系" --> B（支持多字符ID）
        edge_pattern = r'(\w+)\s*--\s*"([^"]+)"\s*-->\s*(\w+)'
        edge_matches = re.findall(edge_pattern, mermaid_code)

        for src_id, label, tgt_id in edge_matches:
            src_name = node_id_map.get(src_id, src_id)
            tgt_name = node_id_map.get(tgt_id, tgt_id)

            # 确定边类型
            edge_type = "other"
            if "雇佣" in label or "债务" in label:
                edge_type = "cooperation"
            elif "介绍" in label:
                edge_type = "introduction"
            elif "参赌" in label or "招募" in label:
                edge_type = "participation"

            edges.append({
                "source": src_name,
                "target": tgt_name,
                "type": edge_type,
                "label": label,
            })

    # 如果 mermaid 解析失败，尝试解析表格
    if not nodes:
        # 解析人物表格 - 匹配每行的所有列
        # 格式：| 姓名 | 角色 | 与xxx的关系 | 涉案程度 | 证据来源 | 备注 |
        lines = content.split('\n')
        in_table = False

        role_map = {
            "被告人": "defendant",
            "主犯": "defendant",
            "从犯": "co defendant",
            "同案犯": "co defendant",
            "证人": "witness",
            "关联人": "witness",
        }

        for line in lines:
            # 匹配表格分隔行
            if re.match(r'^\|[\s:]*---[\s:]*\|', line):
                in_table = True
                continue
            if not in_table or not line.strip().startswith('|'):
                continue

            # 解析表格行
            cols = [c.strip() for c in line.split('|') if c.strip()]
            if len(cols) < 2:
                continue

            name = cols[0].replace('*', '').strip()
            role_str = cols[1] if len(cols) > 1 else ""
            relation = cols[2] if len(cols) > 2 else ""

            # 跳过表头
            if name == "姓名" or "涉案人员" in name:
                continue

            # 确定角色
            node_role = "other"
            for key, val in role_map.items():
                if key in role_str:
                    node_role = val
                    break

            if name and len(name) <= 10:
                nodes.append({
                    "id": name,
                    "name": name,
                    "role": node_role,
                    "description": relation or role_str,
                })

        # 根据角色生成边
        defendant = next((n for n in nodes if n["role"] == "defendant"), None)
        if defendant:
            for node in nodes:
                if node["id"] != defendant["id"]:
                    if node["role"] == "co defendant":
                        edges.append({
                            "source": defendant["id"],
                            "target": node["id"],
                            "type": "cooperation",
                            "label": "共犯",
                        })
                    elif node["role"] == "witness":
                        edges.append({
                            "source": defendant["id"],
                            "target": node["id"],
                            "type": "other",
                            "label": "关联",
                        })

    return {"nodes": nodes, "edges": edges}


def _parse_event_timeline(content: str) -> dict:
    """从 stage_3 Markdown 解析事件时间线数据

    从 mermaid timeline 代码块提取所有细粒度事件（10个），
    并尝试从"事件拆解与证据归组"部分匹配详细描述。
    """
    import re

    events = []

    def normalize_event_date(raw: str) -> str:
        if not raw:
            return '1900-01-01'

        # 去掉时间部分（如 "15点00分"、"22点00分"）
        raw = re.sub(r'\s*\d+点\d+分.*$', '', raw)
        raw = re.sub(r'\s*\d+:\d+.*$', '', raw)

        # 取时间范围的第一个日期
        raw = re.split(r'至|\s*-\s+', raw)[0].strip()

        if "年中" in raw:
            raw = raw.replace("年中", "06")
        elif "年底" in raw:
            raw = raw.replace("年底", "12")

        raw = re.sub(r'起$', '', raw)
        raw = raw.replace("年", "-").replace("月", "-").replace("日", "").replace(".", "-").strip("-")
        raw = re.sub(r'-+', '-', raw)
        parts = raw.strip("-").split("-")

        if len(parts) == 1:
            return f"{parts[0]}-01-01"
        elif len(parts) == 2:
            return f"{parts[0]}-{parts[1].zfill(2)}-01"
        else:
            return f"{parts[0]}-{parts[1].zfill(2)}-{parts[2].zfill(2)}"

    def infer_event_type(text: str) -> str:
        if any(kw in text for kw in ["诈骗", "骗取", "投资", "借款", "转账", "倒卖", "吸金", "犯罪", "名借"]):
            return "crime"
        elif any(kw in text for kw in ["拘留", "逮捕", "取保", "立案", "移送", "起诉", "抓获"]):
            return "procedure"
        elif any(kw in text for kw in ["证据", "笔录", "鉴定", "辨认"]):
            return "evidence"
        elif any(kw in text for kw in ["辩护", "律师", "申诉"]):
            return "defense"
        return "other"

    # 从 mermaid timeline 代码块提取细粒度事件
    block_match = re.search(r'```(?:mermaid\s+)?timeline(.*?)```', content, re.DOTALL)
    if block_match:
        block = block_match.group(1)
        timeline_pattern = r'(\d{4}[年0-9\-./][^:\n]{0,25}?)\s*[:：]\s*([^\n]+)'
        matches = re.findall(timeline_pattern, block)

        for date_raw, desc in matches[:50]:
            desc = desc.strip()
            if not desc or len(desc) < 3:
                continue

            date_str = normalize_event_date(date_raw)

            # 提取相关证据
            evidence_refs = re.findall(r'证据\d+', desc)

            # 截取标题
            title = desc.split("，")[0] if "，" in desc else desc
            if len(title) > 30:
                title = title[:30] + "..."

            events.append({
                "id": f"event_{len(events)}",
                "date": date_str,
                "title": title,
                "description": desc,
                "type": infer_event_type(desc),
                "evidenceRefs": evidence_refs[:5],
            })

    return {"events": events}


# ========== 类案检索 ==========

@router.post("/{case_id}/similar-cases")
async def search_similar_cases(case_id: str):
    """类案检索

    从起诉书/起诉意见书中提取罪名和关键事实，
    使用 LLM 联网搜索类似案例，返回结构化结果。

    Returns:
        {
            "crime_type": "诈骗罪",
            "key_facts": ["...", "..."],
            "similar_cases": [
                {"title": "...", "court": "...", "result": "...", "link": "..."}
            ]
        }
    """
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    # 读取 stage_1 结果（指控要素）
    stage1_file = case_path / "analysis" / "stage_1" / "output.md"
    if not stage1_file.exists():
        return {
            "crime_type": "",
            "key_facts": [],
            "similar_cases": [],
            "error": "请先完成阶段1分析（指控要素提取）"
        }

    stage1_content = stage1_file.read_text(encoding="utf-8")

    try:
        result = await _search_similar_cases_llm(stage1_content)
        return result
    except Exception as e:
        logger.error(f"[类案检索] {case_id}: 搜索失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _search_similar_cases_llm(stage1_content: str) -> dict:
    """使用 LLM 联网搜索类案"""
    import re
    from llm_client import get_llm_client

    client = get_llm_client()

    # 从 stage_1 提取罪名
    crime_type = ""
    crime_match = re.search(r'罪名[：:]\s*([^\n]+)', stage1_content)
    if crime_match:
        crime_type = crime_match.group(1).strip()
    else:
        # 尝试从标题提取
        crime_match2 = re.search(r'涉嫌(.{2,10}罪)', stage1_content)
        if crime_match2:
            crime_type = crime_match2.group(1).strip()

    if not crime_type:
        return {
            "crime_type": "",
            "key_facts": [],
            "similar_cases": [],
            "error": "未能识别罪名"
        }

    # 提取关键事实（简化版：取前3个要点）
    key_facts = []
    facts_section = re.search(r'犯罪事实[：:]\s*(.+?)(?:\n\n|\n##|$)', stage1_content, re.DOTALL)
    if facts_section:
        facts_text = facts_section.group(1).strip()
        # 提取前3个要点
        bullet_points = re.findall(r'[•\-\*]\s*([^\n]+)', facts_text)
        key_facts = [p.strip() for p in bullet_points[:3] if len(p.strip()) > 10]

    if not key_facts:
        # 回退：取 stage_1 前500字
        key_facts = [stage1_content[:500].replace("\n", " ").strip()]

    # 构建搜索提示
    search_prompt = f"""请搜索与以下罪名和事实相似的已判决案例：

**罪名**：{crime_type}

**关键事实**：
{chr(10).join(f"- {f}" for f in key_facts)}

请搜索中国裁判文书网、最高法院指导性案例等公开来源，找出3-5个相似的已判决案例。

**输出格式**（JSON）：
```json
[
  {
    "title": "案件标题（如：张三诈骗案）",
    "court": "审理法院",
    "crime_type": "认定罪名",
    "amount": "涉案金额（如有）",
    "result": "判决结果（刑期/罚金）",
    "key_point": "裁判要旨（100字以内）",
    "link": "来源链接（如有）"
  }
]
```

**注意**：
1. 优先选择最高法院指导性案例、典型案例
2. 关注涉案金额相近、情节相似的案例
3. 如无法联网搜索，请基于训练数据提供参考案例，并注明"基于训练数据"
"""

    try:
        # 使用 enable_search 启用联网搜索
        response = client.chat(
            messages=[
                {"role": "system", "content": "你是法律检索专家，擅长搜索和分析类案。请返回严格的 JSON 格式。"},
                {"role": "user", "content": search_prompt},
            ],
            extra_body={"enable_search": True},
        )

        # 解析 JSON
        content = response.get("content", "")
        # 提取 JSON 数组
        json_match = re.search(r'\[\s*\{.*\}\s*\]', content, re.DOTALL)
        if json_match:
            import json
            similar_cases = json.loads(json_match.group(0))
        else:
            similar_cases = []

        return {
            "crime_type": crime_type,
            "key_facts": key_facts,
            "similar_cases": similar_cases[:5],
        }

    except Exception as e:
        logger.error(f"[类案检索] LLM 调用失败: {e}")
        return {
            "crime_type": crime_type,
            "key_facts": key_facts,
            "similar_cases": [],
            "error": str(e),
        }
