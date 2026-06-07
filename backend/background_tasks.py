"""
后台任务管理模块

PDF 转 MD 等长时间运行的操作在后台线程中执行，不阻塞 FastAPI 主事件循环。
任务状态持久化到文件，重启后端后可恢复进度。

优化版本：
- 使用 asyncio 异步并发处理
- 支持批量提交多个 PDF 到 MinerU
- 并发轮询任务状态，减少等待时间
"""
import json
import shutil
import threading
import time
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

from config import DATA_DIR

# 配置日志
logger = logging.getLogger(__name__)

TASKS_FILE = DATA_DIR / "criminal-llm-tasks.json"

# 线程池：支持并发转换（异步模式可并行处理多个文件）
_executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="convert")

# 内存中的任务状态
_task_states: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


def _load_tasks() -> Dict[str, Any]:
    """从文件加载任务状态"""
    if TASKS_FILE.exists():
        try:
            return json.loads(TASKS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_tasks():
    """保存任务状态到文件"""
    TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        data = {}
        for task_id, state in _task_states.items():
            data[task_id] = {
                "case_id": state["case_id"],
                "status": state["status"],
                "total": state["total"],
                "current": state["current"],
                "current_file": state.get("current_file", ""),
                "message": state.get("message", ""),
                "started_at": state.get("started_at", ""),
                "updated_at": state.get("updated_at", ""),
                "results": state.get("results", []),
                "error_details": state.get("error_details", []),
                "stopped_by_user": state.get("stopped_by_user", False),
                "recoverable": state.get("recoverable", True),
            }
    TASKS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def init_tasks():
    """初始化：从文件恢复任务状态"""
    persisted = _load_tasks()
    with _lock:
        for task_id, state in persisted.items():
            if state.get("status") in ("running", "pending"):
                # 之前运行中的任务标记为中断
                state["status"] = "interrupted"
                state["updated_at"] = datetime.now().isoformat()
            _task_states[task_id] = state
    logger.info(f"[后台任务] 已恢复 {len(_task_states)} 个任务状态")


def _make_task_id(case_id: str) -> str:
    return f"convert_{case_id}"


def get_task_status(case_id: str) -> Optional[Dict[str, Any]]:
    """获取任务状态"""
    task_id = _make_task_id(case_id)
    with _lock:
        return _task_states.get(task_id)


def _update_task(case_id: str, **kwargs):
    """更新任务状态"""
    task_id = _make_task_id(case_id)
    with _lock:
        if task_id not in _task_states:
            _task_states[task_id] = {
                "case_id": case_id,
                "status": "pending",
                "total": 0,
                "current": 0,
                "current_file": "",
                "message": "",
                "started_at": datetime.now().isoformat(),
                "results": [],
                "error_details": [],       # 结构化错误列表
                "stopped_by_user": False,  # 是否用户主动停止
                "recoverable": True,       # 是否可恢复
            }
        _task_states[task_id].update(kwargs)
        _task_states[task_id]["updated_at"] = datetime.now().isoformat()
    _save_tasks()


def start_convert_task(case_id: str, max_concurrent: int = 3):
    """启动后台转换任务（异步并发版本）

    Args:
        case_id: 案件 ID
        max_concurrent: 最大并发数（默认 3，过高可能触发 API 限流）
    """
    _update_task(
        case_id,
        status="pending",
        total=0,
        current=0,
        message="排队中...",
        results=[],
    )

    def _run():
        from power_manager import PowerInhibitor

        with PowerInhibitor(f"PDF 转换: {case_id}"):
            try:
                # 导入需要的模块
                import sys
                backend_dir = Path(__file__).parent
                if str(backend_dir) not in sys.path:
                    sys.path.insert(0, str(backend_dir))

                from case_manager import find_case_path
                from config_manager import get_config_value

                # 获取配置的 PDF 引擎
                pdf_engine = get_config_value("pdf_engine") or "mineru"

                case_path = find_case_path(case_id)
                if not case_path:
                    _update_task(case_id, status="failed", message="案件不存在")
                    return

                processed_dir = case_path / "processed"
                original_dir = case_path / "original"
                md_dir = case_path / "md"
                md_dir.mkdir(parents=True, exist_ok=True)

                # 优先从 processed/ 读取，不存在则从 original/ 读取
                pdf_files = []
                if processed_dir.exists():
                    pdf_files = [f for f in sorted(processed_dir.iterdir()) if f.is_file() and f.suffix.lower() == ".pdf"]
                    logger.info(f"[后台任务] {case_id}: processed/ 目录存在，找到 {len(pdf_files)} 个 PDF 文件")
                    for pdf in pdf_files:
                        logger.info(f"  - {pdf.name}")

                # 如果 processed/ 没有文件，尝试从 original/ 读取
                if not pdf_files and original_dir.exists():
                    pdf_files = [f for f in sorted(original_dir.iterdir()) if f.is_file() and f.suffix.lower() == ".pdf"]
                    logger.info(f"[后台任务] {case_id}: processed/ 为空，从 original/ 读取，找到 {len(pdf_files)} 个 PDF 文件")

                if not pdf_files:
                    logger.info(f"[后台任务] {case_id}: 案件中无 PDF 文件")
                    _update_task(case_id, status="failed", message="案件中无 PDF 文件（请先上传 PDF）")
                    return

                # 过滤掉已有 MD 文件的 PDF（跳过已转换的）
                pending_files = []
                skipped_files = []
                logger.info(f"[后台任务] {case_id}: 检查 MD 文件是否已存在...")
                logger.info(f"[后台任务] {case_id}: md_dir = {md_dir}, exists = {md_dir.exists()}")
                if md_dir.exists():
                    existing_md_files = [f.name for f in md_dir.glob("*.md")]
                    logger.info(f"[后台任务] {case_id}: md/ 目录中有 {len(existing_md_files)} 个 MD 文件: {existing_md_files}")

                for pdf in pdf_files:
                    md_path = md_dir / f"{pdf.stem}.md"
                    logger.info(f"[后台任务] {case_id}: 检查 PDF '{pdf.name}' -> MD '{md_path.name}', exists={md_path.exists()}, size={md_path.stat().st_size if md_path.exists() else 0}")
                    if md_path.exists() and md_path.stat().st_size > 100:  # 至少 100 字节才算有效
                        skipped_files.append(pdf.name)
                        logger.info(f"  -> 跳过（已有 MD）")
                    else:
                        pending_files.append(pdf)
                        logger.info(f"  -> 待转换")

                # 全部已转换完成
                if not pending_files:
                    logger.info(f"[后台任务] {case_id}: 所有 {len(pdf_files)} 个 PDF 都已有对应 MD，无需转换")
                    _update_task(
                        case_id,
                        status="completed",
                        total=len(pdf_files),
                        message=f"全部 {len(pdf_files)} 个文件已转换完成，无需重复处理",
                        results=[{"file": f, "success": True, "skipped": True} for f in skipped_files],
                    )
                    return

                # 部分已转换，仅处理待转换文件
                if skipped_files:
                    logger.info(f"[后台任务] {case_id}: 跳过 {len(skipped_files)} 个已转换文件")

                _update_task(
                    case_id,
                    status="running",
                    total=len(pending_files),
                    message=f"共 {len(pdf_files)} 个文件，跳过 {len(skipped_files)} 个已转换，处理 {len(pending_files)} 个待转换文件",
                )

                results = []

                # 根据配置选择转换引擎
                if pdf_engine == "paddleocr":
                    from paddleocr_async import AsyncPaddleOCRConverter, BatchProgress, ConvertResult
                    try:
                        converter = AsyncPaddleOCRConverter()
                    except ValueError as e:
                        _update_task(case_id, status="failed", message=str(e))
                        return
                else:
                    from mineru_async import AsyncMinerUConverter, BatchProgress, ConvertResult
                    try:
                        converter = AsyncMinerUConverter()
                    except ValueError as e:
                        _update_task(case_id, status="failed", message=str(e))
                        return

                # 进度回调
                def batch_progress_cb(progress: BatchProgress):
                    _update_task(
                        case_id,
                        current=progress.completed,
                        current_file=", ".join(progress.current_files[-3:]) if progress.current_files else "",
                        message=f"已完成 {progress.completed}/{progress.total}（失败 {progress.failed}）",
                        results=results,
                    )

                # 执行异步批量转换（仅处理待转换文件）
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    convert_results: List[ConvertResult] = loop.run_until_complete(
                        converter.convert_batch(
                            pending_files,
                            md_dir,
                            max_concurrent=max_concurrent,
                            timeout=3600,
                            progress_cb=batch_progress_cb,
                        )
                    )
                finally:
                    loop.close()

                # 收集结果（先添加跳过的文件）
                for f in skipped_files:
                    results.append({"file": f, "success": True, "skipped": True})

                converted = 0
                for i, result in enumerate(convert_results):
                    results.append({
                        "file": result.file_name,
                        "success": result.success,
                        "md_name": f"{Path(result.file_name).stem}.md" if result.success else None,
                        "md_size": len(result.text) if result.text else 0,
                        "error": result.error,
                    })
                    if result.success:
                        converted += 1

                # 安全清理（使用全部 PDF 文件列表）
                pdf_stems = {f.stem for f in pdf_files}
                for item in list(md_dir.iterdir()):
                    if item.is_file() and item.suffix == ".md":
                        if item.stem not in pdf_stems:
                            item.unlink()
                    elif item.is_dir() and item.name.endswith("_images"):
                        stem_without_images = item.name.replace("_images", "")
                        if stem_without_images not in pdf_stems:
                            shutil.rmtree(item, ignore_errors=True)

                # 构建完成消息
                if skipped_files:
                    msg = f"完成: 新转换 {converted}/{len(pending_files)}，跳过 {skipped_files} 个已转换文件"
                else:
                    msg = f"完成: {converted}/{len(pdf_files)} 个文件转换成功"

                # 如果没有跳过文件且转换全部失败，标记为失败
                all_failed = converted == 0 and not skipped_files and len(pdf_files) > 0
                if all_failed:
                    # 收集错误信息
                    error_msgs = []
                    for r in results:
                        if r.get("error"):
                            error_msgs.append(f'{r["file"]}: {r["error"]}')
                    error_detail = "; ".join(error_msgs[:5])
                    _update_task(
                        case_id,
                        status="failed",
                        total=len(pdf_files),
                        message=f"所有 {len(pdf_files)} 个文件转换失败: {error_detail}",
                        results=results,
                        error_details=[{
                            "reason": "all_conversions_failed",
                            "message": f"全部 {len(pdf_files)} 个文件转换失败",
                            "recoverable": True,
                        }],
                    )
                    logger.error(f"[后台任务] {case_id}: 所有 {len(pdf_files)} 个文件转换失败")
                else:
                    # 至少部分成功
                    if converted < len(pending_files):
                        msg = f"部分成功: 成功 {converted}/{len(pending_files)}，失败 {len(pending_files) - converted}"
                        if skipped_files:
                            msg += f"，跳过 {len(skipped_files)} 个已转换文件"

                    _update_task(
                        case_id,
                        status="completed",
                        total=len(pdf_files),
                        message=msg,
                        results=results,
                    )
                    logger.info(f"[后台任务] {case_id}: {msg}")

            except Exception as e:
                logger.exception(f"[后台任务] {case_id}: 任务异常")
                _update_task(case_id, status="failed", message=str(e)[:500], error_details=[{
                    "reason": "generic",
                    "message": str(e)[:500],
                    "recoverable": True,
                }], recoverable=True)

    _executor.submit(_run)
    return {"success": True, "task_id": _make_task_id(case_id), "status": "started", "message": "转换任务已启动（异步并发模式）"}


def cancel_task(case_id: str) -> bool:
    """取消任务（仅标记，线程池不支持强制中断）"""
    task_id = _make_task_id(case_id)
    with _lock:
        if task_id in _task_states and _task_states[task_id]["status"] == "running":
            _task_states[task_id]["status"] = "cancelled"
            _task_states[task_id]["stopped_by_user"] = True
            _task_states[task_id]["recoverable"] = True
            _save_tasks()
            return True
    return False


def list_all_tasks() -> Dict[str, Any]:
    """列出所有任务"""
    with _lock:
        return dict(_task_states)


# ═══════════════════════════════════════════════════════════
# API 路由
# ═══════════════════════════════════════════════════════════
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/tasks", tags=["后台任务"])


@router.get("/{case_id}/convert-status")
async def get_convert_status(case_id: str):
    """获取转换任务进度"""
    status = get_task_status(case_id)
    if not status:
        return {"status": "idle", "message": "无任务"}
    return status


@router.post("/{case_id}/convert-all-to-md")
async def trigger_convert(case_id: str):
    """触发后台转换任务（异步并发模式，固定 3 并发）

    已转换的文件会被自动跳过，仅处理缺失 MD 的 PDF。
    """
    # 检查是否已有运行中的任务
    status = get_task_status(case_id)
    if status and status.get("status") == "running":
        return {"status": "running", "message": "转换任务正在运行中"}

    result = start_convert_task(case_id, max_concurrent=3)
    return result


@router.post("/{case_id}/cancel")
async def cancel(case_id: str):
    """取消转换任务"""
    success = cancel_task(case_id)
    return {"success": success}
