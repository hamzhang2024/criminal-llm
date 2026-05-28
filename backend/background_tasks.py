"""
后台任务管理模块

PDF 转 MD 等长时间运行的操作在后台线程中执行，不阻塞 FastAPI 主事件循环。
任务状态持久化到文件，重启后端后可恢复进度。
"""
import json
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

from config import DATA_DIR

TASKS_FILE = DATA_DIR / "criminal-llm-tasks.json"

# 线程池：单线程串行执行转换任务（MinerU 有并发限制）
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="convert")

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
            }
    TASKS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def init_tasks():
    """初始化：从文件恢复任务状态"""
    persisted = _load_tasks()
    for task_id, state in persisted.items():
        if state.get("status") in ("running", "pending"):
            # 之前运行中的任务标记为中断
            state["status"] = "interrupted"
            state["updated_at"] = datetime.now().isoformat()
        _task_states[task_id] = state
    print(f"[后台任务] 已恢复 {len(_task_states)} 个任务状态")


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
            }
        _task_states[task_id].update(kwargs)
        _task_states[task_id]["updated_at"] = datetime.now().isoformat()
    _save_tasks()


def start_convert_task(case_id: str):
    """启动后台转换任务"""
    _update_task(
        case_id,
        status="pending",
        message="排队中...",
        results=[],
    )

    def _run():
        from power_manager import PowerInhibitor

        with PowerInhibitor(f"PDF 转换: {case_id}"):
            try:
                # 导入需要的模块（延迟导入，避免循环依赖）
                import sys
                backend_dir = Path(__file__).parent
                if str(backend_dir) not in sys.path:
                    sys.path.insert(0, str(backend_dir))

                from case_manager import find_case_path
                from pdf_to_md import get_evidence_text
                import fitz

                case_path = find_case_path(case_id)
                if not case_path:
                    _update_task(case_id, status="failed", message="案件不存在")
                    return

                processed_dir = case_path / "processed"
                md_dir = case_path / "md"
                md_dir.mkdir(parents=True, exist_ok=True)

                if not processed_dir.exists():
                    _update_task(case_id, status="failed", message="案件中无已处理的 PDF 文件")
                    return

                pdf_files = [f for f in sorted(processed_dir.iterdir()) if f.is_file() and f.suffix.lower() == ".pdf"]
                if not pdf_files:
                    _update_task(case_id, status="failed", message="processed/ 目录中无 PDF 文件")
                    return

                _update_task(case_id, status="running", total=len(pdf_files), message=f"共 {len(pdf_files)} 个文件")

                results = []
                converted = 0

                for idx, pdf_file in enumerate(pdf_files):
                    md_file = md_dir / f"{pdf_file.stem}.md"
                    if md_file.exists() and md_file.stat().st_size > 200:
                        _update_task(
                            case_id,
                            current=idx + 1,
                            current_file=pdf_file.name,
                            message=f"跳过已有: {pdf_file.name}",
                            results=results,
                        )
                        results.append({
                            "file": pdf_file.name,
                            "success": True,
                            "md_name": md_file.name,
                            "md_size": md_file.stat().st_size,
                            "skipped": True,
                        })
                        converted += 1
                        continue

                    _update_task(
                        case_id,
                        current=idx + 1,
                        current_file=pdf_file.name,
                        message=f"转换中: {pdf_file.name} ({idx + 1}/{len(pdf_files)})",
                        results=results,
                    )

                    try:
                        # 子步骤进度回调
                        def _sub_progress(stage: str, detail: str,
                                          _case=case_id, _idx=idx, _total=len(pdf_files), _name=pdf_file.name):
                            msg_map = {
                                "submitting": "正在提交转换",
                                "uploading": "正在发送文件",
                                "processing": detail,
                                "downloading": "正在生成文本",
                                "parsing": "正在整理输出",
                            }
                            msg = msg_map.get(stage, detail)
                            _update_task(_case, current=_idx + 1, current_file=_name,
                                         message=f"{msg}（{_idx + 1}/{_total}）")

                        text, images_dir = get_evidence_text(
                            str(pdf_file), True, str(md_dir),
                            progress_cb=_sub_progress,
                        )
                        if text is None:
                            results.append({
                                "file": pdf_file.name,
                                "success": False,
                                "error": "MinerU 转换失败（可能超时或配额不足）",
                            })
                            continue

                        if not md_file.exists():
                            md_file.write_text(text, encoding="utf-8")

                        md_size = md_file.stat().st_size if md_file.exists() else 0
                        is_blank = md_size < 50 or not text.strip()

                        results.append({
                            "file": pdf_file.name,
                            "success": not is_blank,
                            "md_name": md_file.name if md_file.exists() else None,
                            "md_size": md_size,
                            "error": "PDF 内容为空" if is_blank else None,
                        })
                        if not is_blank:
                            converted += 1

                    except Exception as e:
                        results.append({
                            "file": pdf_file.name,
                            "success": False,
                            "error": str(e)[:200],
                        })

                # 失败文件自动重试一轮（MinerU 偶发失败）
                failed_files = [r for r in results if not r.get("success") and not r.get("skipped")]
                if failed_files:
                    _update_task(case_id, message=f"对 {len(failed_files)} 个失败文件重试中...")
                    print(f"[后台任务] {case_id}: {len(failed_files)} 个文件失败，等待 20s 后重试...")
                    time.sleep(20)
                    for fail_result in failed_files:
                        fail_name = fail_result.get("file", "")
                        fail_pdf = processed_dir / fail_name
                        if not fail_pdf.exists():
                            continue
                        _update_task(case_id, message=f"重试: {fail_name}")
                        try:
                            text, images_dir = get_evidence_text(
                                str(fail_pdf), True, str(md_dir),
                                progress_cb=lambda stage, detail: _update_task(
                                    case_id, message=f"重试中: {fail_name} - {detail}"
                                ),
                            )
                            if text:
                                md_file = md_dir / f"{fail_pdf.stem}.md"
                                if not md_file.exists():
                                    md_file.write_text(text, encoding="utf-8")
                                md_size = md_file.stat().st_size if md_file.exists() else 0
                                # 更新 results 中对应的记录
                                for r in results:
                                    if r.get("file") == fail_name:
                                        r["success"] = True
                                        r["md_name"] = md_file.name
                                        r["md_size"] = md_size
                                        r.pop("error", None)
                                        r["retried"] = True
                                        break
                                converted += 1
                                print(f"[后台任务] 重试成功: {fail_name}")
                            else:
                                print(f"[后台任务] 重试仍失败: {fail_name}")
                        except Exception as e:
                            print(f"[后台任务] 重试异常: {fail_name}, {e}")

                # 安全清理
                pdf_stems = {f.stem for f in pdf_files}
                for item in list(md_dir.iterdir()):
                    if item.is_file() and item.suffix == ".md":
                        if item.stem not in pdf_stems:
                            item.unlink()
                    elif item.is_dir() and item.name.endswith("_images"):
                        stem_without_images = item.name.replace("_images", "")
                        if stem_without_images not in pdf_stems:
                            shutil.rmtree(item, ignore_errors=True)

                _update_task(
                    case_id,
                    status="completed",
                    message=f"完成: {converted}/{len(pdf_files)} 个文件转换成功",
                    results=results,
                )
                print(f"[后台任务] {case_id}: 转换完成 {converted}/{len(pdf_files)}")

            except Exception as e:
                _update_task(case_id, status="failed", message=str(e)[:500])
                print(f"[后台任务] {case_id}: 任务异常 {e}")

    _executor.submit(_run)
    return {"success": True, "task_id": _make_task_id(case_id), "status": "started", "message": "转换任务已启动"}


def cancel_task(case_id: str) -> bool:
    """取消任务（仅标记，线程池不支持强制中断）"""
    task_id = _make_task_id(case_id)
    with _lock:
        if task_id in _task_states and _task_states[task_id]["status"] == "running":
            _task_states[task_id]["status"] = "cancelled"
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
    """触发后台转换任务"""
    # 检查是否已有运行中的任务
    status = get_task_status(case_id)
    if status and status.get("status") == "running":
        return {"status": "running", "message": "转换任务正在运行中"}

    result = start_convert_task(case_id)
    return result


@router.post("/{case_id}/cancel")
async def cancel(case_id: str):
    """取消转换任务"""
    success = cancel_task(case_id)
    return {"success": success}
