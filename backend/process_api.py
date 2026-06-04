"""
PDF 处理 API - 去水印 + OCR 三明治结构

功能：
1. 扫描本地目录，获取 PDF 文件列表
2. 处理 PDF：去水印 / OCR 三明治 / 两者组合
3. 输出保存在同目录
"""
from pathlib import Path
import os
import subprocess
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import traceback

from fastapi import APIRouter, HTTPException, Body
from typing import List, Dict, Any, Optional
from pydantic import BaseModel

router = APIRouter(prefix="/api/process", tags=["PDF处理"])


@router.get("/browse-directory")
async def browse_directory():
    """
    打开系统原生目录选择对话框

    Returns:
        path: 用户选择的目录路径
    """
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)

        path = filedialog.askdirectory(title="选择案卷目录")
        root.destroy()

        if not path:
            return {"path": None, "message": "用户取消选择"}

        # 验证路径存在
        if not Path(path).exists():
            return {"path": None, "message": "选择的目录不存在"}

        return {"path": path, "message": "已选择目录"}

    except Exception as e:
        return {"path": None, "message": f"无法打开目录选择对话框: {str(e)}"}

# 线程池用于后台处理
executor = ThreadPoolExecutor(max_workers=4)

# 任务状态存储
tasks: Dict[str, Dict[str, Any]] = {}


# ═══════════════════════════════════════════════════════════
# PDF 处理工具函数
# ═══════════════════════════════════════════════════════════


def enhance_pdf_resolution(pdf_path: str, output_path: str, dpi: int = 300) -> Dict[str, Any]:
    """
    使用 Ghostscript 提高 PDF 图片精度（重采样）

    Args:
        pdf_path: 输入 PDF
        output_path: 输出 PDF
        dpi: 目标 DPI（默认 300）

    Returns:
        {"success": bool, "error": str, "output": str}
    """
    import tempfile

    try:
        # 如果输入输出路径相同，先写到临时文件
        same_file = os.path.abspath(pdf_path) == os.path.abspath(output_path)
        if same_file:
            tmp_fd, temp_path = tempfile.mkstemp(suffix='.pdf', dir=os.path.dirname(pdf_path))
            os.close(tmp_fd)
            gs_output = temp_path
        else:
            gs_output = output_path

        cmd = [
            "gs",
            "-dNOPAUSE",
            "-dBATCH",
            "-sDEVICE=pdfwrite",
            f"-dPDFSETTINGS=/prepress",
            f"-dDownsampleColorImages=false",
            f"-dDownsampleGrayImages=false",
            f"-dDownsampleMonoImages=false",
            f"-dColorImageResolution={dpi}",
            f"-dGrayImageResolution={dpi}",
            f"-dMonoImageResolution={dpi}",
            f"-sOutputFile={gs_output}",
            pdf_path,
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            encoding='utf-8',
            errors='replace'
        )

        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "Ghostscript 处理失败"
            if same_file and os.path.exists(temp_path):
                os.unlink(temp_path)
            return {"success": False, "error": error_msg[:500]}

        if not os.path.exists(gs_output):
            if same_file and os.path.exists(temp_path):
                os.unlink(temp_path)
            return {"success": False, "error": "输出文件未生成"}

        # 如果是同路径，替换原文件
        if same_file:
            os.replace(gs_output, output_path)

        original_size = Path(pdf_path).stat().st_size
        output_size = Path(output_path).stat().st_size

        return {
            "success": True,
            "output": output_path,
            "original_size_human": f"{original_size / (1024*1024):.1f} MB" if original_size > 1024*1024 else f"{original_size / 1024:.0f} KB",
            "output_size_human": f"{output_size / (1024*1024):.1f} MB" if output_size > 1024*1024 else f"{output_size / 1024:.0f} KB"
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "PDF 优化超时（>5分钟）"}
    except Exception as e:
        return {"success": False, "error": str(e)}



class ScanRequest(BaseModel):
    directory: str


class ProcessRequest(BaseModel):
    files: List[str]
    password: Optional[str] = None


class FileInfo(BaseModel):
    path: str
    name: str
    size: int
    size_human: str
    has_text: bool  # 是否已有文字层


def get_file_info(path: Path) -> FileInfo:
    """获取文件信息"""
    size = path.stat().st_size
    # 检查是否有文字层（简单检测：尝试提取文字）
    has_text = False
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(str(path))
        for page in doc:
            text = page.get_text()
            if len(text.strip()) > 50:  # 有足够文字
                has_text = True
                break
        doc.close()
    except Exception:
        pass

    return FileInfo(
        path=str(path),
        name=path.name,
        size=size,
        size_human=_format_size_human(size),
        has_text=has_text
    )


def _format_size_human(size: int) -> str:
    """格式化文件大小"""
    if size > 1024 * 1024 * 1024:
        return f"{size / (1024*1024*1024):.2f} GB"
    elif size > 1024 * 1024:
        return f"{size / (1024*1024):.2f} MB"
    elif size > 1024:
        return f"{size / 1024:.2f} KB"
    return f"{size} bytes"


@router.post("/scan")
async def scan_directory(request: ScanRequest):
    """
    扫描目录，获取 PDF 文件列表
    
    Args:
        directory: 目录路径
        
    Returns:
        files: PDF 文件列表
        total_size: 总大小
    """
    dir_path = Path(request.directory)
    
    if not dir_path.exists():
        raise HTTPException(status_code=404, detail=f"目录不存在: {request.directory}")
    
    if not dir_path.is_dir():
        raise HTTPException(status_code=400, detail=f"不是目录: {request.directory}")
    
    # 扫描 PDF 文件（递归）
    pdf_files = list(dir_path.rglob("*.pdf"))
    
    # 获取文件信息
    files_info = []
    total_size = 0
    
    for pdf in pdf_files:
        try:
            info = get_file_info(pdf)
            files_info.append(info.model_dump())
            total_size += info.size
        except Exception as e:
            print(f"[WARN] 无法读取文件 {pdf}: {e}")
    
    # 格式化总大小
    if total_size > 1024 * 1024 * 1024:
        total_size_human = f"{total_size / (1024*1024*1024):.2f} GB"
    elif total_size > 1024 * 1024:
        total_size_human = f"{total_size / (1024*1024):.2f} MB"
    elif total_size > 1024:
        total_size_human = f"{total_size / 1024:.2f} KB"
    else:
        total_size_human = f"{total_size} bytes"
    
    return {
        "directory": request.directory,
        "files": files_info,
        "total_files": len(files_info),
        "total_size": total_size,
        "total_size_human": total_size_human
    }


def process_single_file(
    input_path: str,
    password: Optional[str] = None,
    watermark_text: Optional[str] = None
) -> Dict[str, Any]:
    """
    处理单个 PDF 文件 - 去水印

    优化：先快速检测是否需要处理。
    没有密码、没有水印 → 直接复制，跳过去水印流程。
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from watermark_remover import remove_watermark as wm_remove

    input_file = Path(input_path)

    print(f"\n{'='*60}")
    print(f"处理文件: {input_path}")
    print(f"密码: '{password}'")
    print(f"水印文字: '{watermark_text}'")
    print(f"{'='*60}")

    if not input_file.exists():
        return {"success": False, "error": f"文件不存在: {input_path}", "file": input_path}

    # ── 快速检测：是否需要处理 ──
    import fitz
    from watermark_remover import _open_pdf_with_repair, find_watermark_xobj, detect_rotation_watermark, auto_detect_repeating_text
    try:
        doc = _open_pdf_with_repair(str(input_file), password)
        if doc is None:
            # 无法打开，走常规流程
            raise RuntimeError("无法打开 PDF，走常规去水印流程")
        needs_password = doc.needs_pass
        doc.close()

        if needs_password and not (password and password.strip()):
            # 需要密码但没提供，交给去水印模块处理（它会报错）
            pass
        elif not needs_password and not watermark_text:
            # 不需要密码、没有指定水印文字 → 进一步检测是否有水印
            doc = _open_pdf_with_repair(str(input_file), password)
            if doc is None:
                raise RuntimeError("无法打开 PDF，走常规去水印流程")
            xref, _ = find_watermark_xobj(doc)
            has_rotation = detect_rotation_watermark(doc)
            detected_text = auto_detect_repeating_text(doc)
            doc.close()

            if not xref and not has_rotation and not detected_text:
                # 确认无水印 → 直接复制，跳过去水印
                output_file = input_file.parent / f"{input_file.stem}_去水印.pdf"
                import shutil
                shutil.copy2(str(input_file), str(output_file))
                out_size = output_file.stat().st_size
                print(f"[快速检测] 无密码、无水印，直接复制 → {output_file.name}")
                return {
                    "success": True,
                    "file": input_path,
                    "output": str(output_file),
                    "output_size": out_size,
                    "output_size_human": f"{out_size / 1024:.2f} KB" if out_size > 1024 else f"{out_size} bytes",
                    "quick_copy": True,
                }
    except Exception as e:
        print(f"[快速检测] 检测失败，走常规去水印流程: {e}")
        # 检测失败，走正常流程

    # 输出文件名
    output_file = input_file.parent / f"{input_file.stem}_去水印.pdf"

    try:
        result = wm_remove(
            str(input_file),
            str(output_file),
            watermark_text=watermark_text,
            password=password
        )
        if not result["success"]:
            return {
                "success": False,
                "error": result.get("error", "去水印失败"),
                "file": input_path
            }

        # 检查输出文件
        if output_file.exists():
            output_size = output_file.stat().st_size
            return {
                "success": True,
                "file": input_path,
                "output": str(output_file),
                "output_size": output_size,
                "output_size_human": f"{output_size / 1024:.2f} KB" if output_size > 1024 else f"{output_size} bytes"
            }
        else:
            return {
                "success": False,
                "error": "输出文件未生成",
                "file": input_path
            }

    except Exception as e:
        from pipeline_errors import PDFProcessingError
        if isinstance(e, PDFProcessingError):
            return {
                "success": False,
                "error": str(e),
                "file": input_path,
                "error_type": e.__class__.__name__,
                "reason": e.reason,
                "recoverable": e.recoverable,
            }
        return {
            "success": False,
            "error": str(e),
            "file": input_path
        }


@router.post("/start")
async def start_process(request: ProcessRequest):
    """
    开始处理 PDF 文件（去水印）

    Args:
        files: 文件路径列表
        password: PDF 密码（可选）

    Returns:
        task_id: 任务 ID
    """
    import uuid
    task_id = str(uuid.uuid4())[:8]

    # 初始化任务状态
    tasks[task_id] = {
        "status": "running",
        "total": len(request.files),
        "completed": 0,
        "success": 0,
        "failed": 0,
        "results": [],
        "started_at": datetime.now().isoformat()
    }

    # 在后台线程中处理
    async def process_files():
        try:
            for file_path in request.files:
                result = process_single_file(
                    file_path,
                    password=request.password
                )

                tasks[task_id]["results"].append(result)
                tasks[task_id]["completed"] += 1

                if result["success"]:
                    tasks[task_id]["success"] += 1
                else:
                    tasks[task_id]["failed"] += 1

                # 更新状态
                if tasks[task_id]["completed"] >= tasks[task_id]["total"]:
                    tasks[task_id]["status"] = "completed"
                    tasks[task_id]["completed_at"] = datetime.now().isoformat()
        except Exception as e:
            # 记录异常，标记任务为失败
            import traceback
            print(f"[ERROR] 任务 {task_id} 异常: {e}\n{traceback.format_exc()}")
            tasks[task_id]["status"] = "failed"
            tasks[task_id]["error"] = str(e)[:500]
            tasks[task_id]["completed_at"] = datetime.now().isoformat()

    # 启动后台任务
    asyncio.create_task(process_files())
    
    return {
        "task_id": task_id,
        "total_files": len(request.files),
        "options": {
            "remove_watermark": remove_watermark,
            "ocr": ocr
        },
        "message": f"已开始处理 {len(request.files)} 个文件"
    }


@router.get("/status/{task_id}")
async def get_status(task_id: str):
    """
    获取处理任务状态
    
    Args:
        task_id: 任务 ID
        
    Returns:
        status: 任务状态
        progress: 进度信息
        results: 处理结果（已完成的部分）
    """
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    task = tasks[task_id]
    
    return {
        "task_id": task_id,
        "status": task["status"],
        "total": task["total"],
        "completed": task["completed"],
        "success": task["success"],
        "failed": task["failed"],
        "progress": f"{task['completed']}/{task['total']}",
        "progress_percent": round(task["completed"] / task["total"] * 100, 1) if task["total"] > 0 else 0,
        "results": task["results"],
        "started_at": task.get("started_at"),
        "completed_at": task.get("completed_at")
    }


@router.delete("/task/{task_id}")
async def delete_task(task_id: str):
    """
    删除任务记录
    
    Args:
        task_id: 任务 ID
    """
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    del tasks[task_id]
    
    return {"success": True, "message": "任务已删除"}


@router.get("/tasks")
async def list_tasks():
    """
    列出所有任务
    """
    return {
        "tasks": [
            {
                "task_id": tid,
                "status": t["status"],
                "total": t["total"],
                "completed": t["completed"],
                "started_at": t.get("started_at")
            }
            for tid, t in tasks.items()
        ]
    }