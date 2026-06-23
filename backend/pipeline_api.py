"""
案卷分析 5 步流水线 API（重构版）

步骤：
1. 合并笔录（按人名+类型）
2. 逐次详细总结
3. 内部矛盾分析
4. 案件 Wiki 构建（LLM Wiki 模式）
5. 辩护意见生成
"""
import json
import shutil
import time
from pathlib import Path

from analysis_pipeline import AnalysisPipeline, _contains_indictment_title
from case_manager import find_case_path
from fastapi import APIRouter, Body, File, HTTPException, UploadFile
from utils.path_validator import sanitize_filename

router = APIRouter(prefix="/api/pipeline", tags=["案卷分析流水线"])

# 实时进度状态（内存存储，按 case_id 分组）
PIPELINE_PROGRESS: dict = {}

# 起诉意见书匹配模式
_OPINION_PATTERNS = ["起诉意见书", "呈请起诉", "起诉报告"]


def _set_progress(case_id: str, step: int, message: str, current: int = 0, total: int = 0):
    """更新指定案件的流水线进度"""
    PIPELINE_PROGRESS[case_id] = {
        "step": step,
        "message": message,
        "current": current,
        "total": total,
        "started_at": PIPELINE_PROGRESS.get(case_id, {}).get("started_at", time.time()),
        "elapsed_seconds": round(time.time() - PIPELINE_PROGRESS.get(case_id, {}).get("started_at", time.time())),
    }


def _clear_progress(case_id: str):
    """清除指定案件的流水线进度"""
    PIPELINE_PROGRESS.pop(case_id, None)


@router.get("/{case_id}/indictment-candidates")
async def get_indictment_candidates(case_id: str):
    """扫描案件 MD 文件，找出所有起诉书和起诉意见书候选。

    返回每份候选文件的文件名、文书类型（起诉书/起诉意见书）和前 500 字预览。
    """
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


@router.post("/{case_id}/step/{step_num}")
async def run_pipeline_step(
    case_id: str,
    step_num: float,
    defendant: str = Body(..., embed=True),
    crime_type: str | None = Body(default=None, embed=True),
    indictment_file: str | None = Body(default=None, embed=True),
):
    """
    执行分析流水线的指定步骤

    步骤说明：
    1. 合并笔录（按人名+类型合并，分隔单次笔录）
    2. 逐次详细总结（每次笔录单独 LLM 总结）
    3. 内部矛盾分析（多次笔录者对比差异）
    4. 案件 Wiki 构建（LLM Wiki 模式，串行）
    4.5. 控辩对抗模拟（红蓝辩论 + 交叉询问预演）
    5. 辩护意见生成
    """
    valid_steps = {1, 2, 3, 4, 4.5, 5}
    if step_num not in valid_steps:
        raise HTTPException(status_code=400, detail="无效步骤编号，请输入 1-5 或 4.5")

    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    pipeline = AnalysisPipeline(case_id, case_path, indictment_file=indictment_file)

    # 初始化进度
    _set_progress(case_id, int(step_num), "准备开始...", 0, 0)

    try:
        step_methods = {
            1: lambda: pipeline.step1_merge_statements(defendant, crime_type),
            2: lambda: pipeline.step2_detailed_summaries(
                defendant, crime_type,
                progress_cb=lambda current, total, msg: _set_progress(case_id, int(step_num), msg, current, total),
            ),
            3: lambda: pipeline.step3_internal_contradiction(
                defendant, crime_type,
                progress_cb=lambda current, total, msg: _set_progress(case_id, int(step_num), msg, current, total),
            ),
            4: lambda: pipeline.step4_build_case_wiki(
                defendant, crime_type,
                progress_cb=lambda current, total, msg: _set_progress(case_id, int(step_num), msg, current, total),
            ),
            4.5: lambda: pipeline.step45_debate_simulation(
                defendant, crime_type,
                progress_cb=lambda current, total, msg: _set_progress(case_id, 4, msg, current, total),
            ),
            5: lambda: pipeline.step5_defense_opinion(
                defendant, crime_type,
                progress_cb=lambda current, total, msg: _set_progress(case_id, 5, msg, current, total),
            ),
        }
        result = await step_methods[step_num]()
        return {"success": True, "step": step_num, "data": result}

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"步骤 {step_num} 执行失败: {str(e)}")
    finally:
        _clear_progress(case_id)


@router.get("/{case_id}/progress")
async def get_pipeline_progress(case_id: str):
    """获取正在执行的流水线进度"""
    progress = PIPELINE_PROGRESS.get(case_id)
    if not progress:
        return {"case_id": case_id, "running": False}
    return {"case_id": case_id, "running": True, **progress}


@router.get("/{case_id}/status")
async def get_pipeline_status(case_id: str):
    """获取流水线各步骤完成状态"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    analysis_dir = case_path / "analysis"
    status = {}
    for step in range(1, 6):
        result_file = analysis_dir / f"step_{step}_result.json"
        status[f"step_{step}"] = {
            "completed": result_file.exists(),
        }
        if result_file.exists():
            try:
                with open(result_file, encoding="utf-8") as f:
                    step_data = json.load(f)
                if step == 1:
                    status[f"step_{step}"]["summary"] = f"{step_data.get('total_persons', 0)} 人，{step_data.get('total_sessions', 0)} 次笔录"
                elif step == 2:
                    status[f"step_{step}"]["summary"] = f"{step_data.get('total_persons', 0)} 人总结"
                elif step == 3:
                    status[f"step_{step}"]["summary"] = f"{step_data.get('total_analyzed', 0)} 人矛盾分析"
                elif step == 4:
                    sub_steps = step_data.get("sub_steps", [])
                    done = len([s for s in sub_steps if s.get("status") == "done"])
                    status[f"step_{step}"]["summary"] = f"Wiki 构建 {done}/{len(sub_steps)} 子步骤"
                elif step == 5:
                    status[f"step_{step}"]["summary"] = "辩护意见已生成"
            except Exception:
                pass

    # 步骤 4.5 单独处理
    result_45 = analysis_dir / "step_4.5_result.json"
    status["step_4.5"] = {"completed": result_45.exists()}
    if result_45.exists():
        status["step_4.5"]["summary"] = "控辩对抗已生成"

    return {"case_id": case_id, "status": status}


@router.get("/{case_id}/analysis-state")
async def get_analysis_state(case_id: str):
    """获取分析状态（各步骤/子步骤完成度，用于断点恢复 UI）"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    pipeline = AnalysisPipeline(case_id, case_path)
    return {
        "case_id": case_id,
        "state": pipeline._get_resume_summary(),
        "next_step": pipeline._get_next_unfinished_step(),
    }


@router.post("/{case_id}/resume")
async def resume_pipeline(
    case_id: str,
    defendant: str = Body(..., embed=True),
    crime_type: str | None = Body(default=None, embed=True),
    indictment_file: str | None = Body(default=None, embed=True),
):
    """从断点恢复，自动找到下一个未完成的步骤继续执行"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    pipeline = AnalysisPipeline(case_id, case_path, indictment_file=indictment_file)

    next_step = pipeline._get_next_unfinished_step()
    if next_step is None:
        return {"success": True, "message": "所有步骤已完成", "all_done": True}

    # 初始化进度
    _set_progress(case_id, next_step, "从断点恢复...", 0, 0)

    try:
        pipeline._mark_step_running(next_step)

        step_methods = {
            1: lambda: pipeline.step1_merge_statements(defendant, crime_type),
            2: lambda: pipeline.step2_detailed_summaries(
                defendant, crime_type,
                progress_cb=lambda current, total, msg: _set_progress(case_id, int(next_step), msg, current, total),
            ),
            3: lambda: pipeline.step3_internal_contradiction(
                defendant, crime_type,
                progress_cb=lambda current, total, msg: _set_progress(case_id, int(next_step), msg, current, total),
            ),
            4: lambda: pipeline.step4_build_case_wiki(
                defendant, crime_type,
                progress_cb=lambda current, total, msg: _set_progress(case_id, int(next_step), msg, current, total),
            ),
            4.5: lambda: pipeline.step45_debate_simulation(
                defendant, crime_type,
                progress_cb=lambda current, total, msg: _set_progress(case_id, 4, msg, current, total),
            ),
            5: lambda: pipeline.step5_defense_opinion(
                defendant, crime_type,
                progress_cb=lambda current, total, msg: _set_progress(case_id, 5, msg, current, total),
            ),
        }
        result = await step_methods[next_step]()
        pipeline._mark_step_done(next_step)

        return {
            "success": True,
            "step": next_step,
            "data": result,
            "next_step": pipeline._get_next_unfinished_step(),
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"步骤 {next_step} 执行失败: {str(e)}")
    finally:
        _clear_progress(case_id)


@router.get("/{case_id}/step/{step_num}/result")
async def get_step_result(case_id: str, step_num: int):
    """获取指定步骤的分析结果"""
    if not (1 <= step_num <= 5):
        raise HTTPException(status_code=400, detail="无效步骤编号")

    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    result_file = case_path / "analysis" / f"step_{step_num}_result.json"
    if not result_file.exists():
        raise HTTPException(status_code=404, detail=f"步骤 {step_num} 的结果不存在")

    with open(result_file, encoding="utf-8") as f:
        return json.load(f)


# ========== Wiki 相关 API ==========

@router.get("/{case_id}/wiki/index")
async def get_wiki_index(case_id: str):
    """获取 Wiki 目录索引"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    wiki_dir = case_path / "analysis" / "indictment_wiki"
    if not wiki_dir.exists():
        return {"case_id": case_id, "pages": [], "message": "Wiki 尚未构建，请先执行步骤 4"}

    pages = []
    for item in sorted(wiki_dir.rglob("*")):
        if item.is_file() and item.suffix == ".md":
            rel = str(item.relative_to(wiki_dir))
            pages.append({
                "path": rel,
                "filename": item.name,
            })

    return {"case_id": case_id, "pages": pages}


@router.get("/{case_id}/wiki/pages/{path:path}")
async def get_wiki_page(case_id: str, path: str):
    """读取 Wiki 页面内容"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    wiki_file = case_path / "analysis" / "indictment_wiki" / path
    if not wiki_file.exists() or not wiki_file.is_file():
        raise HTTPException(status_code=404, detail=f"Wiki 页面不存在: {path}")

    content = wiki_file.read_text(encoding="utf-8")
    return {"case_id": case_id, "path": path, "content": content}


@router.get("/{case_id}/md-files/{filename}")
async def get_md_file(case_id: str, filename: str):
    """读取 md/ 目录下的文件内容（自动重写图片路径为 API URL）"""
    import re
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    md_file = case_path / "md" / filename
    if not md_file.exists() or not md_file.is_file():
        raise HTTPException(status_code=404, detail=f"MD 文件不存在: {filename}")

    content = md_file.read_text(encoding="utf-8")
    # 重写图片路径为 API URL（支持新格式 ./stem_images/ 和旧格式 images/）
    stem = md_file.stem
    content = re.sub(
        r'!\[([^\]]*)\]\(\.\/' + re.escape(stem) + r'_images\/([^)]+)\)',
        r'![\1](http://localhost:8080/api/cases/' + case_id + r'/md-images/' + re.escape(stem) + r'_images/\2)',
        content,
    )
    # 兼容旧格式：images/xxx.jpg → md-images/stem_images/xxx.jpg
    content = re.sub(
        r'!\[([^\]]*)\]\(images\/([^)]+)\)',
        r'![\1](http://localhost:8080/api/cases/' + case_id + r'/md-images/' + re.escape(stem) + r'_images/\2)',
        content,
    )
    return {"case_id": case_id, "filename": filename, "content": content}


@router.get("/{case_id}/pdf-text/{filename}")
async def get_pdf_text(case_id: str, filename: str):
    """从 splits/ 目录下的 PDF 文件提取文本（直接读取，不用 md 缓存）"""
    import fitz
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    pdf_file = case_path / "splits" / filename
    if not pdf_file.exists() or not pdf_file.is_file():
        raise HTTPException(status_code=404, detail=f"PDF 文件不存在: {filename}")

    try:
        doc = fitz.open(str(pdf_file))
        text_parts = []
        for page in doc:
            text_parts.append(page.get_text())
        doc.close()
        content = "\n".join(text_parts)
        return {"case_id": case_id, "filename": filename, "content": content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF 文本提取失败: {str(e)}")


@router.post("/{case_id}/wiki/upload-reference")
async def upload_wiki_reference(case_id: str, file: UploadFile = File(...)):
    """上传参考材料到 user_reference/ 目录"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    ref_dir = case_path / "analysis" / "user_reference"
    ref_dir.mkdir(parents=True, exist_ok=True)

    target = ref_dir / sanitize_filename(Path(file.filename).name)
    with open(target, "wb") as f:
        content = await file.read()
        f.write(content)

    return {"success": True, "filename": file.filename, "path": str(target)}


@router.delete("/{case_id}/wiki/clear")
async def clear_wiki(case_id: str):
    """清空 Wiki 目录（用于重新开始步骤 4）"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    wiki_dir = case_path / "analysis" / "indictment_wiki"
    if wiki_dir.exists():
        shutil.rmtree(wiki_dir)

    # 也清除步骤 4 的结果
    step4_result = case_path / "analysis" / "step_4_result.json"
    if step4_result.exists():
        step4_result.unlink()

    return {"success": True, "message": "Wiki 已清空"}


# ========== 辩护意见子阶段 API ==========

@router.get("/{case_id}/defense-stages")
async def get_defense_stages(case_id: str):
    """返回辩护意见各子阶段完成状态"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    defense_dir = case_path / "analysis" / "05-辩护意见"
    stages = {
        "01-案件概述": "pending",
        "02-证据评估": "pending",
        "03-矛盾利用": "pending",
        "04-三阶层辩护": "pending",
        "05-量刑情节": "pending",
        "06-结论建议": "pending",
    }

    if defense_dir.exists():
        for filename, status_key in stages.items():
            if (defense_dir / f"{filename}.md").exists():
                stages[filename] = "done"

    # 检查完整报告
    analysis_dir = case_path / "analysis"
    full_report_exists = len(list(analysis_dir.glob("辩护分析报告_*.md"))) > 0

    return {
        "case_id": case_id,
        "stages": stages,
        "full_report": full_report_exists,
        "defense_dir": str(defense_dir) if defense_dir.exists() else None,
    }


@router.get("/{case_id}/defense-stage/{stage_name}")
async def get_defense_stage(case_id: str, stage_name: str):
    """返回指定辩护子阶段的内容"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    defense_dir = case_path / "analysis" / "05-辩护意见"

    # 自动补全 .md 扩展名
    if not stage_name.endswith('.md'):
        stage_name = stage_name + '.md'

    stage_file = defense_dir / stage_name
    if not stage_file.exists():
        raise HTTPException(status_code=404, detail=f"辩护阶段文件不存在: {stage_name}")

    return {
        "case_id": case_id,
        "stage": stage_name,
        "content": stage_file.read_text(encoding="utf-8"),
    }


# ========== 证据浏览 API ==========

@router.get("/{case_id}/evidence/summaries")
async def get_evidence_summaries(case_id: str):
    """列出 analysis/summaries/ 下的所有总结文件"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    summaries_dir = case_path / "analysis" / "summaries"
    if not summaries_dir.exists():
        return {"case_id": case_id, "categories": []}

    categories = []
    for cat_dir in sorted(summaries_dir.iterdir()):
        if not cat_dir.is_dir():
            continue
        files = []
        for f in sorted(cat_dir.iterdir()):
            if f.suffix == ".md":
                # 从文件名提取 displayName: "顾君燕_共11次_总结.md" -> "顾君燕（共11次）"
                name = f.stem  # 顾君燕_共11次_总结
                display = name.replace("_总结", "").replace("_共", "（共").replace("次", "次）")
                files.append({
                    "name": f.name,
                    "displayName": display,
                })
        categories.append({
            "name": cat_dir.name,
            "files": files,
        })

    return {"case_id": case_id, "categories": categories}


@router.get("/{case_id}/evidence/other")
async def get_evidence_other(case_id: str):
    """列出 splits/ 下剔除笔录的 PDF + processed/ 下的 PDF"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    files = []

    # splits/ 下剔除询问笔录/讯问笔录
    splits_dir = case_path / "splits"
    if splits_dir.exists():
        for f in sorted(splits_dir.iterdir()):
            if f.suffix == ".pdf" and "询问笔录" not in f.name and "讯问笔录" not in f.name:
                files.append({"name": f.name, "dir": "splits"})

    # processed/ 下的 PDF
    processed_dir = case_path / "processed"
    if processed_dir.exists():
        for f in sorted(processed_dir.iterdir()):
            if f.suffix == ".pdf":
                files.append({"name": f.name, "dir": "processed"})

    return {"case_id": case_id, "files": files}


@router.get("/{case_id}/evidence/summary/{category}/{filename}")
async def get_evidence_summary(case_id: str, category: str, filename: str):
    """读取指定总结文件的 Markdown 内容"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    summary_file = case_path / "analysis" / "summaries" / category / filename
    if not summary_file.exists() or not summary_file.is_file():
        raise HTTPException(status_code=404, detail=f"总结文件不存在: {category}/{filename}")

    content = summary_file.read_text(encoding="utf-8")
    return {"case_id": case_id, "filename": filename, "content": content}


@router.get("/{case_id}/evidence/files")
async def get_evidence_files(case_id: str):
    """列出 processed/ 和 md/ 下的所有文件，供对话选择"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    files = []

    # processed/ 下所有 PDF
    processed_dir = case_path / "processed"
    if processed_dir.exists():
        for f in sorted(processed_dir.iterdir()):
            if f.suffix.lower() == ".pdf":
                files.append({"name": f.name, "dir": "processed"})

    # md/ 下所有 MD 文件
    md_dir = case_path / "md"
    if md_dir.exists():
        for f in sorted(md_dir.iterdir()):
            if f.suffix == ".md":
                files.append({"name": f.name, "dir": "md"})

    return {"case_id": case_id, "files": files}


# ========== 矛盾分析 API ==========

@router.get("/{case_id}/evidence/contradictions")
async def get_contradiction_files(case_id: str):
    """列出 analysis/contradictions/ 下的所有矛盾分析 MD 文件"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    contradictions_dir = case_path / "analysis" / "contradictions"
    if not contradictions_dir.exists():
        return {"case_id": case_id, "files": []}

    files = []
    for f in sorted(contradictions_dir.iterdir()):
        if f.is_file() and f.suffix == ".md":
            name = f.stem  # "顾君燕_共11次_矛盾分析"
            files.append({
                "filename": f.name,
                "displayName": name.replace("_矛盾分析", ""),
            })

    return {"case_id": case_id, "files": files}


@router.get("/{case_id}/evidence/contradiction/{filename}")
async def get_contradiction_content(case_id: str, filename: str):
    """读取指定矛盾分析文件的 Markdown 内容"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    contradiction_file = case_path / "analysis" / "contradictions" / filename
    if not contradiction_file.exists() or not contradiction_file.is_file():
        raise HTTPException(status_code=404, detail=f"矛盾分析文件不存在: {filename}")

    content = contradiction_file.read_text(encoding="utf-8")
    return {"case_id": case_id, "filename": filename, "content": content}
