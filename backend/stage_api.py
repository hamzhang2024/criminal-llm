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
from typing import Optional

from analysis_engine import AnalysisEngine
from analysis_pipeline import AnalysisPipeline, _contains_indictment_title
from case_manager import find_case_path
from fastapi import APIRouter, Body, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stage-analysis", tags=["5阶段案卷分析"])

# 实时进度状态
STAGE_PROGRESS: dict = {}

# 证据审查/质证/阅卷笔录的后台任务状态（case_id -> task dict）
# 避免长耗时 LLM 操作阻塞前端，切页面后可轮询恢复
REVIEW_TASKS: dict = {}

# 起诉意见书匹配模式
_OPINION_PATTERNS = ["起诉意见书", "呈请起诉", "起诉报告"]


async def _run_sub_stage(engine, sub_stage_type: str, defendant: str, crime_type: Optional[str]):
    """
    运行阶段 5 的子阶段（51/52/53）
    需要阶段 1-4 的结果已存在
    """
    # 读取阶段 1-4 的 Markdown
    stage1_md = _read_stage_md(engine.analysis_dir, 1)
    stage3_md = _read_stage_md(engine.analysis_dir, 3)
    stage4_md = _read_stage_md(engine.analysis_dir, 4)

    texts = engine._load_evidence_texts()

    from llm_client import get_llm_client
    client = get_llm_client()

    if sub_stage_type == "evidence_analysis":
        parts = []
        for ev in texts:
            result = await client.chat([
                {"role": "system", "content": "你是刑事案卷智能分析系统，请逐份审查证据，评估证据的合法性、真实性、关联性。"},
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
            {"role": "system", "content": "你是刑事案卷智能分析系统，请客观识别证据间的矛盾和口供变化。"},
            {"role": "user", "content": f"""## 辩护对象：{defendant}\n\n## 案卷材料\n{all_text[:150000]}\n\n请分析：1. 同一人多次笔录的变化 2. 不同证据对同一事实的矛盾 3. 证据链条薄弱环节"""},
        ])
        engine._save_stage(52, {"name": "矛盾分析"}, result)
        return {"success": True}

    elif sub_stage_type == "three_tier_defense":
        # 需要 5A 和 5B 的结果
        stage51 = _read_stage_md(engine.analysis_dir, 51)
        stage52 = _read_stage_md(engine.analysis_dir, 52)

        from legal_knowledge import THEORY_THREE_TIERS
        try:
            from legal_knowledge import get_dynamic_legal_knowledge
            # 保留联网搜索副作用，返回值当前未使用（疑似遗留代码）
            if crime_type:
                get_dynamic_legal_knowledge(crime_type)
        except Exception:
            pass

        result = await client.chat([
            {"role": "system", "content": "你是刑事案卷智能分析系统，请客观撰写三阶层综合辩护分析报告。"},
            {"role": "user", "content": f"""## 辩护对象：{defendant}\n## 阶段1：指控要素\n{stage1_md[:3000]}\n## 阶段3：事件拆解\n{stage3_md[:3000]}\n## 阶段4：法律法规\n{stage4_md[:5000]}\n## 5A：证据分析\n{stage51[:5000]}\n## 5B：矛盾分析\n{stage52[:5000]}\n## 三阶层体系\n{THEORY_THREE_TIERS[:2000]}\n\n请完成三阶层综合辩护分析：1. 辩护概要 2. 构成要件符合性 3. 违法性 4. 有责性 5. 综合辩护意见"""},
        ])
        engine._save_stage(53, {"name": "三阶层辩护"}, result)
        return {"success": True}

    raise ValueError(f"未知子阶段类型: {sub_stage_type}")


def _read_stage_md(analysis_dir, stage: int) -> str:
    """读取指定阶段的 Markdown 输出"""
    stage_file = analysis_dir / f"stage_{stage}" / "output.md"
    if stage_file.exists():
        return stage_file.read_text(encoding="utf-8")
    return ""


def _set_progress(case_id: str, stage: int, message: str, current: int = 0, total: int = 0, substage: str = ""):
    """设置阶段分析进度

    Args:
        case_id: 案件 ID
        stage: 阶段号（1-6）
        message: 进度消息
        current: 当前进度（如子阶段序号）
        total: 总进度（如子阶段总数）
        substage: 子阶段名称（如 "5a 案件概述"）
    """
    STAGE_PROGRESS[case_id] = {
        "stage": stage,
        "message": message,
        "status": "running",
        "current": current,
        "total": total,
        "substage": substage,
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


async def _execute_all_stages(case_id: str, defendant: str, crime_type: Optional[str], indictment_file: Optional[str] = None):
    """后台执行全部 5 阶段分析"""
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

        engine = AnalysisEngine(case_id, case_path, indictment_file=indictment_file)
        _set_progress(case_id, 0, "开始 5 阶段分析...", current=0, total=5)

        # 阶段 1
        ANALYSIS_TASKS[case_id] = {"status": "running", "current_stage": 1}
        _set_progress(case_id, 1, "正在分析起诉书，提取指控要素...", current=1, total=5, substage="指控要素")
        await engine.stage_1_read_indictment(defendant, crime_type)

        # 阶段 2
        ANALYSIS_TASKS[case_id] = {"status": "running", "current_stage": 2}
        _set_progress(case_id, 2, "正在分析人物关系...", current=2, total=5, substage="人物关系")
        await engine.stage_2_character_relations(defendant, crime_type)

        # 阶段 3
        ANALYSIS_TASKS[case_id] = {"status": "running", "current_stage": 3}
        _set_progress(case_id, 3, "正在分析事件时间线和证据归组...", current=3, total=5, substage="事件拆解")
        await engine.stage_3_event_timeline(defendant, crime_type)

        # 阶段 4
        ANALYSIS_TASKS[case_id] = {"status": "running", "current_stage": 4}
        _set_progress(case_id, 4, f"正在梳理{crime_type or '涉案罪名'}相关法律法规...", current=4, total=5, substage="法律法规")
        await engine.stage_4_legal_regulations(defendant, crime_type)

        # 阶段 5
        ANALYSIS_TASKS[case_id] = {"status": "running", "current_stage": 5}
        _set_progress(case_id, 5, "正在生成综合辩护分析报告...", current=5, total=5, substage="综合辩护")
        await engine.stage_5_full_defense(defendant, crime_type)

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
async def get_stage_markdown(case_id: str, stage_num: int):
    """获取指定阶段的 Markdown 输出（支持 1-6、6 控辩对抗、51/52/53 子阶段）"""
    valid_stages = set(range(1, 7)) | {51, 52, 53}
    if stage_num not in valid_stages:
        raise HTTPException(status_code=400, detail="无效阶段编号")

    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    # 阶段 6 的路径特殊处理
    if stage_num == 6:
        md_file = case_path / "analysis" / "04.5-控辩对抗" / "对抗分析.md"
    else:
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


@router.put("/{case_id}/stage/{stage_num}/markdown")
async def save_stage_markdown(
    case_id: str,
    stage_num: int,
    content: str = Body(..., embed=True),
):
    """保存指定阶段的 Markdown 内容到磁盘"""
    valid_stages = set(range(1, 7)) | {51, 52, 53}
    if stage_num not in valid_stages:
        raise HTTPException(status_code=400, detail="无效阶段编号")

    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    if stage_num == 6:
        md_file = case_path / "analysis" / "04.5-控辩对抗" / "对抗分析.md"
    elif stage_num in (51, 52, 53):
        md_file = case_path / "analysis" / f"stage_{stage_num}" / "output.md"
    elif stage_num == 5:
        md_file = case_path / "analysis" / "full_defense_report.md"
    else:
        md_file = case_path / "analysis" / f"stage_{stage_num}" / "output.md"

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

    改为异步任务模式：立即返回任务状态，后台执行 LLM 审查。
    前端通过 GET /review-evidence-status 轮询进度，GET /evidence-review 获取最终结果。
    避免长耗时操作阻塞前端，切页面后可恢复。

    审查内容包括：
    - 合法性审查：取证主体资格、取证程序、证据形式、非法证据排除
    - 真实性审查：来源可靠性、内容客观性、保管链条、同一性确认
    - 关联性审查：与待证事实的关系、证明价值、证据间印证
    """
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    # 若已有任务在运行，直接返回当前状态
    existing = REVIEW_TASKS.get(case_id)
    if existing and isinstance(existing, dict) and existing.get("status") == "running":
        return {"case_id": case_id, "status": "running", "message": "审查任务已在运行中", "total_evidence": existing.get("total_evidence", 0), "processed": existing.get("processed", 0)}

    import asyncio
    import time

    # 初始化任务状态
    REVIEW_TASKS[case_id] = {
        "status": "running",
        "started_at": time.time(),
        "total_evidence": 0,
        "processed": 0,
        "current_evidence": "",
        "error": None,
    }

    async def _run_review_bg():
        """后台执行证据审查"""
        try:
            engine = AnalysisEngine(case_id, case_path)
            # 预估总数（用于进度展示）
            texts = engine._load_evidence_texts()
            evidence_texts = [t for t in texts if not t.get("is_indictment")]
            task = REVIEW_TASKS.get(case_id)
            if isinstance(task, dict):
                task["total_evidence"] = len(evidence_texts)

            # 检查是否有证据分组：有则走组合质证，无则走逐份质证
            import json as _json
            index_file = case_path / "evidence" / "index.json"
            has_groups = False
            if index_file.exists():
                try:
                    idx = _json.loads(index_file.read_text(encoding="utf-8"))
                    has_groups = bool(idx.get("evidence_groups"))
                except Exception:
                    pass

            if has_groups:
                logger.info("[证据审查] 检测到证据分组，启用组合质证模式")
                result = await engine.generate_grouped_cross_examination_opinion()
            else:
                result = await engine.review_evidence_triple_property()

            task = REVIEW_TASKS.get(case_id)
            if isinstance(task, dict):
                if result.get("error"):
                    task["status"] = "error"
                    task["error"] = result["error"]
                else:
                    task["status"] = "completed"
                    task["processed"] = result.get("total_evidence", 0)
        except Exception as e:
            logger.exception(f"[证据审查] 后台任务失败: {e}")
            task = REVIEW_TASKS.get(case_id)
            if isinstance(task, dict):
                task["status"] = "error"
                task["error"] = str(e)[:500]

    asyncio.create_task(_run_review_bg())

    return {"case_id": case_id, "status": "running", "message": "审查任务已启动，请轮询状态"}


@router.get("/{case_id}/review-evidence-status")
async def get_review_evidence_status(case_id: str):
    """获取证据审查任务状态（供前端轮询）"""
    task = REVIEW_TASKS.get(case_id)
    if not task:
        # 检查结果文件是否已存在（历史已完成任务）
        case_path = find_case_path(case_id)
        if case_path and (case_path / "evidence" / "evidence_review.json").exists():
            return {"case_id": case_id, "status": "completed", "total_evidence": 0, "processed": 0}
        return {"case_id": case_id, "status": "idle"}
    if isinstance(task, dict):
        import time
        elapsed = time.time() - task.get("started_at", time.time()) if task.get("started_at") else 0
        return {
            "case_id": case_id,
            "status": task.get("status", "idle"),
            "total_evidence": task.get("total_evidence", 0),
            "processed": task.get("processed", 0),
            "current_evidence": task.get("current_evidence", ""),
            "elapsed_seconds": round(elapsed),
            "error": task.get("error"),
        }
    return {"case_id": case_id, "status": "idle"}


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
    """生成阅卷笔录（异步任务模式）

    阅卷笔录是律师阅卷工作的核心文档，包含：
    - 案件基本信息
    - 证据目录
    - 证据三性审查摘要
    - 指控要素
    - 事实认定
    - 法律分析
    - 辩护要点

    立即返回任务状态，后台执行。前端轮询 /review-evidence-status（task_type=review_notes）。
    """
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    existing = REVIEW_TASKS.get(case_id)
    if existing and isinstance(existing, dict) and existing.get("status") == "running":
        return {"case_id": case_id, "status": "running", "task_type": "review_notes", "message": "任务已在运行中"}

    import asyncio
    import time

    REVIEW_TASKS[case_id] = {
        "status": "running",
        "task_type": "review_notes",
        "started_at": time.time(),
        "error": None,
    }

    async def _run_bg():
        try:
            engine = AnalysisEngine(case_id, case_path)
            result = await engine.generate_review_notes()
            task = REVIEW_TASKS.get(case_id)
            if isinstance(task, dict):
                if result.get("error"):
                    task["status"] = "error"
                    task["error"] = result["error"]
                else:
                    task["status"] = "completed"
        except Exception as e:
            logger.exception(f"[阅卷笔录] 后台任务失败: {e}")
            task = REVIEW_TASKS.get(case_id)
            if isinstance(task, dict):
                task["status"] = "error"
                task["error"] = str(e)[:500]

    asyncio.create_task(_run_bg())
    return {"case_id": case_id, "status": "running", "task_type": "review_notes", "message": "阅卷笔录生成已启动"}


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
    """生成或获取质证意见文档（异步任务模式）

    注意：此功能已合并到证据审查中。如果已进行证据审查，直接返回结果；
    如果未审查，会自动执行质证意见生成（包含三性审查）。

    立即返回任务状态，后台执行。前端轮询 /review-evidence-status（task_type=cross_examination）。
    """
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    existing = REVIEW_TASKS.get(case_id)
    if existing and isinstance(existing, dict) and existing.get("status") == "running":
        return {"case_id": case_id, "status": "running", "task_type": "cross_examination", "message": "任务已在运行中"}

    import asyncio
    import time

    REVIEW_TASKS[case_id] = {
        "status": "running",
        "task_type": "cross_examination",
        "started_at": time.time(),
        "error": None,
    }

    async def _run_bg():
        try:
            engine = AnalysisEngine(case_id, case_path)
            result = await engine.generate_cross_examination()
            task = REVIEW_TASKS.get(case_id)
            if isinstance(task, dict):
                if result.get("error"):
                    task["status"] = "error"
                    task["error"] = result["error"]
                else:
                    task["status"] = "completed"
        except Exception as e:
            logger.exception(f"[质证意见] 后台任务失败: {e}")
            task = REVIEW_TASKS.get(case_id)
            if isinstance(task, dict):
                task["status"] = "error"
                task["error"] = str(e)[:500]

    asyncio.create_task(_run_bg())
    return {"case_id": case_id, "status": "running", "task_type": "cross_examination", "message": "质证意见生成已启动"}


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
async def get_evidence_chain(case_id: str):
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
        result = generate_evidence_chain(case_path)
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


# 解析函数从 stage_api_parsers 导入（向后兼容 re-export）
from stage_api_parsers import (  # noqa: F401
    _parse_person_relation,
    _parse_event_timeline,
)


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
        result = await _search_similar_cases_llm(stage1_content, case_path)
        return result
    except Exception as e:
        logger.error(f"[类案检索] {case_id}: 搜索失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _search_similar_cases_llm(stage1_content: str, case_path: Path = None) -> dict:
    """使用 元典 API 或 LLM 搜索类案"""
    import re

    import httpx
    from config_manager import get_config_value
    from llm_client import get_llm_client

    client = get_llm_client()
    
    # 如果传入了 case_path，尝试读取保存的结果
    if case_path:
        saved_file = case_path / "analysis" / "similar_cases.json"
        if saved_file.exists():
            try:
                import json as json_load
                with open(saved_file, 'r', encoding='utf-8') as f:
                    saved_data = json_load.load(f)
                logger.info(f"[类案检索] 加载已保存的结果，共 {len(saved_data.get('similar_cases', []))} 个案例")
                return saved_data
            except Exception as load_err:
                logger.warning(f"[类案检索] 加载保存结果失败: {load_err}")

    # 从 stage_1 提取罪名
    crime_type = ""
    # 尝试匹配 Markdown 格式：- **罪名**：诈骗罪
    crime_match = re.search(r'\*\*罪名\*\*[：:]\s*([^\n]+)', stage1_content)
    if crime_match:
        crime_type = crime_match.group(1).strip()
    else:
        # 尝试匹配普通格式：罪名：诈骗罪
        crime_match2 = re.search(r'罪名[：:]\s*([^\n]+)', stage1_content)
        if crime_match2:
            crime_type = crime_match2.group(1).strip()
        else:
            # 尝试从标题提取
            crime_match3 = re.search(r'涉嫌(.{2,10}罪)', stage1_content)
            if crime_match3:
                crime_type = crime_match3.group(1).strip()

    if not crime_type:
        return {
            "crime_type": "",
            "key_facts": [],
            "similar_cases": [],
            "error": "未能识别罪名"
        }

    # 提取关键事实
    key_facts = []
    # 尝试匹配"核心事实"部分
    core_facts_match = re.search(r'\*\*核心事实\*\*[：:]\s*(.+?)(?=\n-|\n\n|\n##|$)', stage1_content, re.DOTALL)
    if core_facts_match:
        core_facts = core_facts_match.group(1).strip()
        # 提取关键句子（按句号分割，取前3个）
        sentences = re.split(r'[。！]', core_facts)
        key_facts = [s.strip() + "。" for s in sentences[:3] if s.strip()]
    else:
        # 回退：从"指控事实"或"犯罪事实"部分提取
        facts_section = re.search(r'(?:指控事实|犯罪事实)[：:]\s*(.+?)(?:\n\n|\n##|$)', stage1_content, re.DOTALL)
        if facts_section:
            facts_text = facts_section.group(1).strip()
            bullet_points = re.findall(r'[•\-\*]\s*([^\n]+)', facts_text)
            key_facts = [p.strip() for p in bullet_points[:3] if len(p.strip()) > 10]

    if not key_facts:
        # 回退：取 stage_1 前500字
        key_facts = [stage1_content[:500].replace("\n", " ").strip()]

    # ========== 优先使用元典 API ==========
    yuandian_token = get_config_value("yuandian_token", "")
    if yuandian_token:
        try:
            import sys
            if sys.platform == "darwin" and getattr(sys, "frozen", False):
                ssl_verify = "/etc/ssl/cert.pem"
            else:
                ssl_verify = True

            all_cases = []
            seen_titles = set()

            async with httpx.AsyncClient(timeout=45, verify=ssl_verify) as http_client:
                # 直接搜索罪名，获取更多案例
                search_payload = {
                    "ay": [crime_type],
                    "top_k": 30,
                    "wszl": ["判决书"],
                }

                resp = await http_client.post(
                    "https://open.chineselaw.com/open/rh_ptal_search",
                    headers={
                        "X-API-Key": yuandian_token,
                        "Accept": "application/json",
                        "Content-Type": "application/json; charset=utf-8",
                    },
                    json=search_payload,
                )

                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("status") == "success" and data.get("data", {}).get("lst"):
                        for case in data["data"]["lst"]:
                            title = case.get("title", "")
                            if title and title not in seen_titles:
                                seen_titles.add(title)

                                # 获取更丰富的内容
                                content = case.get("content", "")
                                if not content:
                                    content = case.get("fxgc", "")

                                # 判断法院级别
                                court = case.get("jbdw", "")
                                priority = "普通案例"
                                if "最高" in court:
                                    priority = "最高人民法院"
                                elif "高级" in court:
                                    priority = "高级人民法院"
                                elif "中级" in court:
                                    priority = "中级人民法院"

                                all_cases.append({
                                    "title": title,
                                    "court": court,
                                    "priority": priority,
                                    "crime_type": case.get("ay", [crime_type])[0] if case.get("ay") else crime_type,
                                    "amount": "",
                                    "result": "",
                                    "key_point": content[:500] if content else "",  # 扩展内容
                                    "link": case.get("url", "") if case.get("url", "").startswith("http") else ("https://wenshu.court.gov.cn" + case.get("url", "") if case.get("url") else ""),
                                })

            # 使用 LLM 对案例进行重要性和说理补充
            if all_cases:
                try:
                    logger.info(f"[类案检索] 开始 LLM 增强，共 {len(all_cases)} 个案例")
                    
                    # 构建案例内容列表给 LLM 分析
                    case_contents = "\n\n".join([
                        f"案例{i+1}：{c['title']}\n法院：{c['court']}\n内容：{c.get('key_point', '')[:500]}"
                        for i, c in enumerate(all_cases[:10])  # 最多10个案例，每个截取500字
                    ])
                    
                    enhance_prompt = f"""你是资深刑事法官，请根据以下{crime_type}案例的判决书内容，为每个案例提取：
1. 事实摘要：谁、做了什么、涉案金额、判决结果
2. 裁判要旨：法院定罪和量刑的核心理由
3. 重要性：法院级别和案例影响力

案例内容：
{case_contents}

返回JSON数组（每项对应一个案例，保持顺序）：
[{{"title":"案件名","fact_summary":"事实摘要","key_point":"裁判要旨","priority_note":"重要性"}}]"""
                    
                    response = await client.chat(
                        messages=[
                            {"role": "system", "content": "你是资深刑事法官。只返回JSON数组。"},
                            {"role": "user", "content": enhance_prompt},
                        ]
                    )
                    
                    logger.info(f"[类案检索] LLM响应: {response[:200]}")
                    
                    import json as json_lib
                    import re as re_mod
                    try:
                        json_match = re_mod.search(r'\[.*\]', response, re_mod.DOTALL)
                        if json_match:
                            enhanced = json_lib.loads(json_match.group(0))
                            if isinstance(enhanced, list):
                                logger.info(f"[类案检索] LLM返回{len(enhanced)}条增强数据")
                                logger.info(f"[类案检索] 匹配示例: all_cases[0]={all_cases[0].get('title','')}[:30], enhanced[0]={enhanced[0].get('title','')}[:30] if enhanced else 'no enhanced'")
                                matched = 0
                                for e in enhanced:
                                    for c in all_cases:
                                        # 更宽松的匹配：忽略"一审刑事判决书"后缀
                                        title1 = e.get("title", "").replace("一审刑事判决书", "").replace("二审", "").strip()
                                        title2 = c.get("title", "").replace("一审刑事判决书", "").replace("二审", "").strip()
                                        if title1 and title2 and (title1 in title2 or title2 in title1):
                                            if e.get("key_point"):
                                                c["key_point"] = e["key_point"]
                                            if e.get("fact_summary"):
                                                c["fact_summary"] = e["fact_summary"]
                                            if e.get("priority_note"):
                                                c["priority_note"] = e["priority_note"]
                                            matched += 1
                                            break
                                logger.info(f"[类案检索] 成功匹配 {matched} 个案例")
                    except Exception as perr:
                        logger.warning(f"[类案检索] JSON解析失败: {perr}")
                except Exception as llm_err:
                    logger.warning(f"[类案检索] LLM增强失败: {llm_err}")
                
                # 按优先级排序
                priority_order = {"指导性案例": 0, "公报案例": 1, "典型案例": 2, "普通案例": 3}
                all_cases.sort(key=lambda x: priority_order.get(x.get("priority_note", ""), 4))

                logger.info(f"[类案检索] 元典 API 成功返回 {len(all_cases)} 个案例")
                
                # 保存到文件
                try:
                    save_path = case_path / "analysis" / "similar_cases.json"
                    save_path.parent.mkdir(parents=True, exist_ok=True)
                    import json as json_save
                    with open(save_path, 'w', encoding='utf-8') as f:
                        json_save.dump({
                            "crime_type": crime_type,
                            "key_facts": key_facts,
                            "similar_cases": all_cases[:30]
                        }, f, ensure_ascii=False, indent=2)
                    logger.info(f"[类案检索] 已保存到 {save_path}")
                except Exception as save_err:
                    logger.warning(f"[类案检索] 保存失败: {save_err}")
                
                return {
                    "crime_type": crime_type,
                    "key_facts": key_facts,
                    "similar_cases": all_cases[:30],
                }
        except Exception as e:
            logger.warning(f"[类案检索] 元典 API 调用失败，回退到 LLM: {e}")
    else:
        logger.info("[类案检索] 未配置元典 Token，回退到 LLM")

    # ========== 回退：使用 LLM（严格禁止编造案例）==========
    search_prompt = f"""请检索与以下罪名和事实相似的已判决案例。

**罪名**：{crime_type}

**关键事实**：
{chr(10).join(f"- {f}" for f in key_facts)}

**严格要求**：
1. **严禁编造案例！** 只能返回你确信真实存在的案例
2. **严禁编造案号！** 如不确定案号，不要填写虚构案号
3. 可以引用最高人民法院发布的指导性案例（引用编号+标题即可，如"指导案例14号：董某某故意伤害案"）
4. 可以引用公报案例（注明来源：《最高人民法院公报》年份+期号）
5. 可以概括裁判规则（如"司法实践中，被害人明显过错可减轻被告人责任"），但不要附编造的具体案件
6. 如不确定具体案例，宁可返回空列表，也不要编造
7. 返回的案例数量不超过 10 个，重质不重量

**输出格式**（JSON）：
```json
[
  {{
    "title": "案件标题（如不确定可写概括性标题）",
    "court": "审理法院",
    "crime_type": "认定罪名",
    "amount": "涉案金额（如有）",
    "result": "判决结果（刑期/罚金）",
    "key_point": "裁判要旨（100字以内）",
    "link": "来源链接（如不确定则为空字符串）",
    "verified": true
  }}
]
```

**注意**：如无法确认案例的真实性，请在 verified 字段填 false，并在 key_point 中注明"需人工核实"。
"""

    try:
        # 先尝试联网搜索，如果失败则使用本地知识
        try:
            response = await client.chat(
                messages=[
                    {"role": "system", "content": "你是法律检索系统，擅长检索和分析类案。严禁编造案例和案号，只返回确信真实存在的案例。请返回严格的 JSON 格式。"},
                    {"role": "user", "content": search_prompt},
                ]
            )
        except Exception as search_err:
            logger.warning(f"[类案检索] 搜索失败，回退本地模式: {search_err}")
            # 回退：使用本地知识
            response = await client.chat(
                messages=[
                    {"role": "system", "content": "你是法律检索系统，精通最高人民法院指导性案例、最高人民检察院指导性案例、公报案例等。严禁编造案例和案号，只返回确信真实存在的权威案例。请返回严格的 JSON 格式。"},
                    {"role": "user", "content": search_prompt + "\n\n请优先提供最高人民法院指导性案例、最高人民检察院指导性案例、公报案例等权威案例。如果没有这类案例，请提供其他参考案例，但需注明来源。"},
                ]
            )

        # 解析 JSON - chat() 直接返回字符串
        content = response  # 不再是 dict，直接是字符串
        # 提取 JSON 数组 - 支持多种格式
        import json
        try:
            # 尝试直接解析整个响应
            similar_cases = json.loads(content)
        except json.JSONDecodeError:
            # 尝试提取 JSON 数组
            json_match = re.search(r'\[\s*[\[{].*}[\]]\s*\]', content, re.DOTALL)
            if json_match:
                try:
                    similar_cases = json.loads(json_match.group(0))
                except json.JSONDecodeError:
                    similar_cases = []
            else:
                similar_cases = []

        # 确保是列表
        if not isinstance(similar_cases, list):
            similar_cases = []

        return {
            "crime_type": crime_type,
            "key_facts": key_facts,
            "similar_cases": similar_cases[:30],
        }

    except Exception as e:
        logger.error(f"[类案检索] LLM 调用失败: {e}")
        return {
            "crime_type": crime_type,
            "key_facts": key_facts,
            "similar_cases": [],
            "error": str(e),
        }
