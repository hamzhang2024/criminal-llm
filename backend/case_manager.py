"""
案件管理 API

功能：
1. 扫描所有案件文件夹
2. 识别合法案件（有 case.json）
3. 识别待导入文件夹（无 case.json 但有 PDF）
4. 导入文件夹为合法案件
"""
from pathlib import Path
import re
import logging
from datetime import datetime
import shutil
from typing import List, Dict, Optional
from pydantic import BaseModel

logger = logging.getLogger(__name__)
from fastapi import APIRouter, UploadFile, File, HTTPException, Request
from fastapi.responses import JSONResponse
import json
import uuid
import os
import glob
import time
import asyncio

router = APIRouter(prefix="/api/cases", tags=["案件管理"])

# 证据提取状态追踪（并发数从 config_manager 读取）
# 结构: { case_id: { "status": "running", "total_files": N, "processed_files": N, "current_file": "xxx.md", "started_at": time.time() } }
# 当提取失败时，额外记录 "error_detail" 字段，方便前端展示具体原因
EXTRACT_TASKS: dict = {}

from config import MAX_FILE_SIZE, DATA_DIR, UPLOAD_DIR as CONFIG_UPLOAD_DIR

# 案件存储根目录
CASES_DIR = DATA_DIR / "cases"
CASES_DIR.mkdir(parents=True, exist_ok=True)

# 回收站目录
TRASH_DIR = CASES_DIR / ".trash"
TRASH_DIR.mkdir(parents=True, exist_ok=True)

# 保留天数
TRASH_RETAIN_DAYS = 5


def natural_sort_key(path):
    """自然排序：将文件名中的数字按数值大小比较（如 第1卷 < 第2卷 < 第10卷）"""
    import re
    name = path.name if hasattr(path, 'name') else str(path)
    parts = re.split(r'(\d+)', name)
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def cleanup_trash():
    """清理回收站中超过保留天数的案件"""
    import time
    now = time.time()
    retain_seconds = TRASH_RETAIN_DAYS * 86400
    cleaned = []
    
    if not TRASH_DIR.exists():
        return cleaned
    
    for item in TRASH_DIR.iterdir():
        if item.is_dir():
            deleted_at = item.stat().st_mtime
            if now - deleted_at > retain_seconds:
                shutil.rmtree(item, ignore_errors=True)
                cleaned.append(item.name)
    
    return cleaned


class CaseInfo(BaseModel):
    id: str
    name: str
    defendant: str
    created_at: str
    status: str
    file_count: int = 0
    case_dir: str  # 案件文件夹路径


class CreateCaseRequest(BaseModel):
    name: str
    defendant: str
    owner: Optional[str] = None  # 创建者邮箱


class PendingFolder(BaseModel):
    path: str
    name: str
    pdf_count: int
    size_mb: float


def scan_cases(owner: Optional[str] = None) -> List[CaseInfo]:
    """扫描所有合法案件（有 case.json），可按 owner 过滤"""
    cases = []

    if not CASES_DIR.exists():
        return cases

    for case_dir in sorted(CASES_DIR.iterdir()):
        if case_dir.is_dir():
            for sub in case_dir.iterdir():
                if sub.is_dir():
                    metadata_file = sub / "case.json"
                    if metadata_file.exists():
                        try:
                            with open(metadata_file, 'r', encoding='utf-8') as f:
                                metadata = json.load(f)

                            # 按 owner 过滤
                            case_owner = metadata.get("owner")
                            if owner and case_owner and case_owner != owner:
                                continue

                            # 计算各阶段文件数量
                            file_count = sum(1 for _ in sub.rglob("*.pdf"))
                            file_count += sum(1 for _ in sub.rglob("*.md"))

                            metadata['file_count'] = file_count

                            # 检查案件状态
                            original_count = len(list((sub / "original").glob("*.pdf")))
                            processed_count = len(list((sub / "processed").glob("*.pdf")))
                            md_count = len(list((sub / "md").glob("*.md")))

                            if md_count > 0:
                                metadata['status'] = 'md_ready'
                            elif processed_count > 0:
                                metadata['status'] = 'processed'
                            elif original_count > 0:
                                metadata['status'] = 'uploaded'
                            else:
                                metadata['status'] = 'new'

                            # 同步状态回写 case.json
                            with open(metadata_file, 'w', encoding='utf-8') as f:
                                json.dump(metadata, f, ensure_ascii=False, indent=2)

                            cases.append(CaseInfo(**metadata))
                        except Exception as e:
                            print(f"读取案件失败：{sub}: {e}")

    return sorted(cases, key=lambda x: x.created_at, reverse=True)


def scan_pending_folders() -> List[PendingFolder]:
    """扫描待导入文件夹（无 case.json 但有 PDF）"""
    pending = []

    if not CASES_DIR.exists():
        return pending

    for case_dir in sorted(CASES_DIR.iterdir()):
        if case_dir.is_dir() and not case_dir.name.startswith('.'):  # 排除 .trash 等隐藏目录
            for sub in case_dir.iterdir():
                if sub.is_dir():
                    metadata_file = sub / "case.json"
                    if not metadata_file.exists():
                        # 检查是否有 PDF 文件
                        pdf_files = list(sub.glob("*.pdf"))
                        pdf_files.extend(sub.rglob("*.pdf"))
                        pdf_files = list(set(pdf_files))  # 去重
                        
                        if pdf_files:
                            total_size = sum(f.stat().st_size for f in pdf_files)
                            pending.append(PendingFolder(
                                path=str(sub),
                                name=sub.name,
                                pdf_count=len(pdf_files),
                                size_mb=round(total_size / (1024 * 1024), 1)
                            ))
    
    return pending


def find_case_path(case_id: str) -> Optional[Path]:
    """查找案件目录（扫描 CASES_DIR 下所有子目录匹配 case_id）"""
    if not CASES_DIR.exists():
        return None
    for case_dir in CASES_DIR.iterdir():
        if case_dir.is_dir() and case_dir.name == case_id:
            for sub in case_dir.iterdir():
                if sub.is_dir() and (sub / "case.json").exists():
                    return sub
    return None


def extract_header_footer_text(pdf_path: str, top_pct: float = 0.15, bottom_pct: float = 0.15) -> Dict[int, str]:
    """提取 PDF 每页的页头和页尾文本（跳过页面中间部分），用于快速 LLM 拆分分析。

    用 fitz 直接提取文字层中的页头页尾区域文本。

    Args:
        pdf_path: PDF 文件路径
        top_pct: 页头占页面高度的比例（默认 15%）
        bottom_pct: 页尾占页面高度的比例（默认 15%）

    Returns:
        {页码: 文本} 映射，页码从 1 开始
    """
    import fitz

    page_texts = {}
    doc = fitz.open(pdf_path)
    num_pages = len(doc)

    for i in range(num_pages):
        page = doc[i]
        rect = page.rect
        height = rect.height
        header_rect = fitz.Rect(0, 0, rect.width, height * top_pct)
        footer_rect = fitz.Rect(0, height * (1 - bottom_pct), rect.width, height)

        header_text = page.get_text("text", clip=header_rect).strip()
        footer_text = page.get_text("text", clip=footer_rect).strip()
        text_parts = []
        if header_text:
            text_parts.append(header_text)
        if footer_text and footer_text != header_text:
            text_parts.append(footer_text)
        text = "\n".join(text_parts)
        if text:
            page_texts[i + 1] = text

    doc.close()
    return page_texts


def extract_full_page_text(pdf_path: str) -> Dict[int, str]:
    """提取 PDF 每页的完整文本（用于文书类型识别）"""
    import fitz

    page_texts = {}
    doc = fitz.open(pdf_path)
    num_pages = len(doc)

    for i in range(num_pages):
        page = doc[i]
        text = page.get_text("text").strip()
        if text:
            page_texts[i + 1] = text

    doc.close()
    return page_texts


def get_case_dir(case_id: str, name: str) -> Path:
    """获取案件文件夹路径"""
    safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_')).strip()
    date_str = datetime.now().strftime("%Y%m%d")
    folder_name = f"案件_{safe_name}_{date_str}"
    return CASES_DIR / case_id / folder_name


@router.get("/list")
async def list_cases(owner: Optional[str] = None):
    """列出所有合法案件，可按 owner 过滤"""
    return scan_cases(owner)


@router.get("/pending")
async def list_pending_folders():
    """列出待导入文件夹"""
    return scan_pending_folders()


@router.post("/create")
async def create_case(request: CreateCaseRequest) -> CaseInfo:
    """创建新案件"""
    case_id = f"case_{uuid.uuid4().hex[:8]}"

    # 创建案件文件夹结构
    case_path = get_case_dir(case_id, request.name)
    case_path.mkdir(parents=True, exist_ok=True)

    # 创建子文件夹
    for subdir in ['original', 'processed', 'md', 'analysis']:
        (case_path / subdir).mkdir(exist_ok=True)

    # 创建案件元数据
    metadata = {
        "id": case_id,
        "name": request.name,
        "defendant": request.defendant,
        "created_at": datetime.now().strftime("%Y-%m-%d"),
        "status": "new",
        "case_dir": str(case_path),
    }
    if request.owner:
        metadata["owner"] = request.owner

    # 保存元数据
    metadata_file = case_path / "case.json"
    with open(metadata_file, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    return CaseInfo(**metadata)


@router.post("/import")
async def import_folder(folder_path: str, name: str, defendant: str) -> CaseInfo:
    """导入文件夹为合法案件"""
    import urllib.parse
    folder_path = urllib.parse.unquote(folder_path)
    folder = Path(folder_path)
    
    if not folder.exists():
        return {"error": "文件夹不存在"}
    
    if (folder / "case.json").exists():
        return {"error": "已经是合法案件"}
    
    # 生成案件 ID
    case_id = f"case_{uuid.uuid4().hex[:8]}"
    
    # 移动文件夹到案件目录
    case_path = CASES_DIR / case_id / folder.name
    if case_path.exists():
        shutil.rmtree(case_path)
    
    # 复制文件夹
    shutil.copytree(folder, case_path)
    
    # 创建 case.json
    metadata = {
        "id": case_id,
        "name": name,
        "defendant": defendant,
        "created_at": datetime.now().strftime("%Y-%m-%d"),
        "status": "uploaded",
        "case_dir": str(case_path),
    }

    metadata_file = case_path / "case.json"
    with open(metadata_file, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    
    # 创建其他子文件夹（如果不存在）
    for subdir in ['original', 'processed', 'md', 'analysis']:
        (case_path / subdir).mkdir(exist_ok=True)
    
    # 如果有 PDF 文件，移动到 original/
    original_dir = case_path / "original"
    for pdf in case_path.rglob("*.pdf"):
        if pdf.parent.name != "original":
            shutil.move(str(pdf), str(original_dir / pdf.name))
    
    # 删除原始文件夹
    if folder.exists():
        shutil.rmtree(folder)
    
    return CaseInfo(**metadata)


@router.post("/{case_id}/upload")
async def upload_files(case_id: str, files: list[UploadFile] = File(...)):
    """上传文件到案件 - 保持原始文件名"""
    print(f"[upload] case_id={case_id}, file_count={len(files)}, file_names={[f.filename for f in files]}")
    case_path = find_case_path(case_id)
    if not case_path:
        print(f"[upload] case not found: {case_id}")
        return {"success": False, "error": "案件不存在"}

    original_dir = case_path / "original"
    original_dir.mkdir(exist_ok=True)

    uploaded_files = []
    for file in files:
        # 检查文件大小
        file_content = await file.read()
        if len(file_content) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"文件 {file.filename} 过大，最大支持 {MAX_FILE_SIZE // (1024*1024)}MB"
            )

        # 保持原始文件名
        original_name = file.filename or "unknown.pdf"
        file_path = original_dir / original_name

        # 如果文件已存在，添加后缀避免覆盖
        if file_path.exists():
            base = Path(original_name).stem
            ext = Path(original_name).suffix
            counter = 1
            while file_path.exists():
                file_path = original_dir / f"{base}_{counter}{ext}"
                counter += 1

        with open(file_path, "wb") as f:
            f.write(file_content)
        
        uploaded_files.append({
            "id": f"file_{file_path.stem}",
            "name": file_path.name,
            "size": file_path.stat().st_size,
            "status": "pending",
            "path": str(file_path)
        })
    
    # 更新案件状态
    metadata_file = case_path / "case.json"
    with open(metadata_file, 'r', encoding='utf-8') as f:
        metadata = json.load(f)
    metadata["status"] = "uploaded"
    metadata["file_count"] = len(list(original_dir.glob("*.pdf")))
    with open(metadata_file, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    
    print(f"[upload] success: {[f['name'] for f in uploaded_files]}")
    return {"success": True, "files": uploaded_files}


@router.delete("/{case_id}/file/{file_name}")
async def delete_file(case_id: str, file_name: str):
    """删除案件中的文件（同时删除 processed/ 和 md/ 中的对应文件）"""
    import urllib.parse
    file_name = urllib.parse.unquote(file_name)

    case_path = find_case_path(case_id)
    if not case_path:
        return {"success": False, "error": "案件不存在"}

    original_dir = case_path / "original"
    file_path = original_dir / file_name
    if not file_path.exists():
        return {"success": False, "error": f"文件不存在：{file_name}"}

    # 删除 original 文件
    file_path.unlink()
    print(f"[delete] removed original: {file_path}")

    # 同步删除 processed/ 中的对应文件
    stem = file_path.stem
    processed_dir = case_path / "processed"
    if processed_dir.exists():
        # 可能有 _去水印 后缀的变体
        for p in processed_dir.iterdir():
            if p.suffix == ".pdf" and p.stem == stem:
                p.unlink()
                print(f"[delete] removed processed: {p}")

    # 同步删除 md/ 中的对应文件
    md_dir = case_path / "md"
    if md_dir.exists():
        for p in md_dir.iterdir():
            if p.suffix == ".md" and p.stem == stem:
                p.unlink()
                print(f"[delete] removed md: {p}")

    return {"success": True, "message": f"已删除 {file_name}"}


@router.get("/{case_id}/files")
async def list_case_files(case_id: str):
    """列出案件的所有文件"""
    case_path = find_case_path(case_id)
    if not case_path:
        return []
    
    files = []
    
    # 扫描 original/ 目录
    original_dir = case_path / "original"
    if original_dir.exists():
        for pdf in sorted(original_dir.glob("*.pdf"), key=natural_sort_key):
            stat = pdf.stat()
            files.append({
                "id": f"file_{pdf.stem}",
                "name": pdf.name,
                "size": stat.st_size,
                "status": "pending",
                "path": str(pdf),
                "created_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            })
    
    return files


@router.get("/{case_id}/step-files/{step}")
async def get_step_files(case_id: str, step: int):
    """获取某一步骤的输入文件

    step: 0=原始文件, 1=PDF处理后, 2=转MD(读取processed/), 3=MD文件(分析)
    兼容旧映射: step=2 旧拆分, step=3 旧转MD, step=4 旧分析
    """
    case_path = find_case_path(case_id)
    if not case_path:
        return []

    # 新简化流程映射
    if step == 0:
        input_dir = case_path / "original"
    elif step == 1:
        input_dir = case_path / "original"
    elif step == 2:
        # 转MD：读取 md/ 目录下已转换的 MD 文件
        input_dir = case_path / "md"
    elif step == 3:
        # 分析：同样读取 md/ 目录
        input_dir = case_path / "md"
    else:
        return []

    if not input_dir.exists():
        return []

    # 扫描文件（按步骤类型过滤）
    files = []
    # 步骤0-1 只加载 PDF，步骤2-3 只加载 MD
    allowed_suffixes = {".pdf"} if step <= 1 else {".md"}

    # 对于步骤1，检查 processed/ 中是否已有对应文件（标记为 done）
    processed_dir = case_path / "processed"

    def _is_freshly_processed(src: Path, dst: Path) -> bool:
        """检查目标文件是否确实是在源文件之后生成的（防止误判）"""
        if not dst.exists():
            return False
        src_stat = src.stat()
        dst_stat = dst.stat()
        # 处理后文件的修改时间必须晚于或等于源文件的修改时间。
        # 不再强制要求大小差异——去水印/去密码可能只改变很小一部分内容，
        # 特别是大文件差异可能不到 1%。
        # 只要求：时间戳合理 + 文件不完全相同（inode 不同）。
        if dst_stat.st_mtime < src_stat.st_mtime:
            return False
        # 如果大小完全相同，检查是否是硬链接或同一文件
        if dst_stat.st_size == src_stat.st_size and dst_stat.st_ino == src_stat.st_ino:
            return False  # 同一 inode，不是处理后生成的文件
        return True

    for f in sorted(input_dir.iterdir(), key=natural_sort_key):
        if f.is_file() and f.suffix.lower() in allowed_suffixes:
            stat = f.stat()
            # 步骤1：检查 processed/ 中是否已有同名文件（含 _去水印 后缀变体）
            # 步骤2/3：MD 文件已存在说明已转换完成，直接标记 done
            status = "pending"
            if step == 1 and processed_dir.exists():
                processed_file = processed_dir / f.name
                if _is_freshly_processed(f, processed_file):
                    status = "done"
                else:
                    # 也检查带 _去水印 后缀的文件
                    stem_no_ext = f.stem
                    for pf in processed_dir.iterdir():
                        if pf.is_file() and pf.stem.startswith(stem_no_ext) and _is_freshly_processed(f, pf):
                            status = "done"
                            break
            elif step >= 2:
                # MD 文件已存在于 md/ 目录，说明转换已完成
                status = "done"

            file_info = {
                "id": f"file_{f.stem}",
                "name": f.name,
                "size": stat.st_size,
                "status": status,
                "path": str(f),
                "source": input_dir.name,
            }

            files.append(file_info)

    return files


class BatchProcessRequest(BaseModel):
    step: int
    file_names: list[str]
    password: Optional[str] = None
    dpi: Optional[int] = None
    remove_watermark: Optional[bool] = None
    enhance_resolution: Optional[bool] = None
    delete_original: Optional[bool] = None


@router.post("/{case_id}/batch-process")
async def batch_process(case_id: str, request: BatchProcessRequest):
    """批量处理文件

    step=1: 统一PDF处理（去水印/精度提升，由复选框控制）
    """
    import asyncio
    loop = asyncio.get_event_loop()

    # 处理完成后直接返回结果，前端用 PdfViewer 直接预览 PDF
    result = await loop.run_in_executor(
        None,
        _do_batch_process,
        case_id,
        request.step,
        request.file_names,
        request.password,
        request.dpi,
        request.remove_watermark,
        request.enhance_resolution,
        request.delete_original
    )

    return result


def _do_batch_process(case_id: str, step: int, file_names: list, password: str = None, dpi: int = None, remove_watermark: bool = None, enhance_resolution: bool = None, delete_original: bool = None):
    """实际处理逻辑（在线程池中执行）

    step=1: 统一PDF处理（去水印/精度提升）
    """
    case_path = find_case_path(case_id)
    if not case_path:
        return {"success": False, "error": "案件不存在"}

    results = []

    if step == 1:
        # 统一PDF处理：去水印 → 精度提升（按顺序执行）
        input_dir = case_path / "original"
        output_dir = case_path / "processed"
        output_dir.mkdir(exist_ok=True)

        # 确定哪些选项被启用
        do_watermark = remove_watermark if remove_watermark is not None else True
        do_enhance = enhance_resolution if enhance_resolution is not None else False

        if do_watermark:
            import sys
            sys.path.insert(0, str(Path(__file__).parent))
            from process_api import process_single_file

        if do_enhance:
            import sys
            sys.path.insert(0, str(Path(__file__).parent))
            from process_api import enhance_pdf_resolution

        target_dpi = dpi or 300

        for file_name in file_names:
            input_file = input_dir / file_name
            if not input_file.exists():
                results.append({"file": file_name, "success": False, "error": "文件不存在"})
                continue

            try:
                # 去水印处理
                if do_watermark:
                    try:
                        result = process_single_file(
                            str(input_file),
                            password=password
                        )
                        if result["success"] and result.get("output"):
                            final_output = Path(result["output"])
                            if final_output.exists():
                                # 移动到 output_dir，保持输出文件名（带 _去水印 后缀）
                                output_name = final_output.name
                                target = output_dir / output_name
                                shutil.move(str(final_output), str(target))
                                current_path = target
                            else:
                                results.append({"file": file_name, "success": False, "error": "输出文件未生成"})
                                continue
                        else:
                            results.append({"file": file_name, "success": False, "error": result.get("error", "处理失败")})
                            continue
                    except Exception as e:
                        results.append({"file": file_name, "success": False, "error": f"处理失败: {str(e)[:100]}"})
                        continue
                else:
                    current_path = input_file

                # 精度提升（在最终文件上操作）
                if do_enhance:
                    try:
                        enhance_result = enhance_pdf_resolution(str(current_path), str(current_path), target_dpi)
                        if not enhance_result["success"]:
                            results.append({"file": file_name, "success": False, "error": enhance_result.get("error", "精度提升失败")})
                            continue
                    except Exception as e:
                        results.append({"file": file_name, "success": False, "error": f"精度提升失败: {str(e)[:100]}"})
                        continue

                # 页头页尾文本提取（供步骤2拆分使用）
                try:
                    ocr_texts = extract_header_footer_text(str(current_path))
                    text_cache = case_path / ".cache" / "ocr"
                    text_cache.mkdir(parents=True, exist_ok=True)
                    text_file = text_cache / f"{Path(file_name).stem}.json"
                    with open(text_file, 'w', encoding='utf-8') as f:
                        json.dump(ocr_texts, f, ensure_ascii=False)
                except Exception:
                    pass  # 页头页尾提取失败不影响主流程

                results.append({
                    "file": file_name,
                    "success": True,
                    "output": str(current_path),
                    "operations": {
                        "watermark": do_watermark,
                        "enhance": do_enhance,
                        "dpi": target_dpi if do_enhance else None
                    }
                })

            except Exception as e:
                results.append({"file": file_name, "success": False, "error": str(e)})

        # 如果启用了删除原始文件选项，删除处理成功的原始文件
        do_delete = delete_original if delete_original is not None else False
        if do_delete:
            for r in results:
                if r["success"]:
                    original_file = input_dir / r["file"]
                    if original_file.exists():
                        original_file.unlink()
                        print(f"[DELETE] 已删除原始文件: {r['file']}")

    return {"results": results}


@router.post("/{case_id}/cleanup-processed")
async def cleanup_processed(case_id: str):
    """清理已处理的文件（取消处理后调用），删除 processed/ 目录中的文件"""
    case_path = find_case_path(case_id)
    if not case_path:
        return {"success": False, "error": "案件不存在"}

    processed_dir = case_path / "processed"
    cleaned = 0

    if processed_dir.exists():
        for f in processed_dir.iterdir():
            if f.is_file() and f.suffix.lower() == '.pdf':
                f.unlink()
                cleaned += 1
        # 如果目录空了，删除目录
        if not any(processed_dir.iterdir()):
            processed_dir.rmdir()

    # 同时清理 OCR 文本缓存
    cache_dir = case_path / ".cache" / "ocr"
    if cache_dir.exists():
        for f in cache_dir.iterdir():
            if f.is_file():
                f.unlink()

    return {"success": True, "cleaned": cleaned, "message": f"已清理 {cleaned} 个已处理文件"}


def _get_source_from_evidence_file(ev_path: Path) -> str:
    """从证据文件中读取来源文件名"""
    try:
        text = ev_path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("| **来源文件** |"):
                return line.split("|")[2].strip()
    except Exception:
        pass
    return ""


async def _process_indictment_single(md_file: Path, md_text: str, evidence_dir: Path, next_id: int) -> Path:
    """将起诉书/起诉意见书作为一份独立证据提取，真实记录指控的全部事实。

    Returns: 生成的证据文件路径
    """
    from llm_client import get_llm_client
    client = get_llm_client()

    # 确定文书类型
    doc_type = "起诉意见书" if "意见" in md_file.name else "起诉书"

    result = await client.chat([
        {"role": "system", "content": f"""你是刑事案卷审查专家，正在审查一份{doc_type}。

**核心原则：真实、完整、逐笔记录该文书指控的全部犯罪事实。**

提取要求：
1. **总体指控**：指控的罪名、涉案总金额、涉案人员总数、整体犯罪模式
2. **逐笔犯罪事实**：文书中列出的每一笔犯罪事实必须逐一列出，不得遗漏。每笔包括：
   - 时间：具体作案时间
   - 地点：作案地点
   - 涉案人员及角色：谁主谋、谁参与
   - 行为方式：具体手段、方法
   - 金额/结果：涉案金额、受害人、造成后果
   - 简要案情：该笔事实的完整描述，保留原文细节
3. **涉案人员**：全部涉案人员姓名、身份证号、住址、角色分工
4. **关键关联信息**：电话号码、微信号、银行账号、车牌号、地址信息等

不要概括、不要简化、不要对比其他证据，只做真实记录。"""},
        {"role": "user", "content": f"""## {doc_type}：{md_file.name}

{md_text[:100000]}

---

请按以下格式输出完整分析：

### 总体指控
- **指控罪名**：[罪名]
- **涉案总金额**：[如有]
- **涉案人员总数**：[人数]
- **整体犯罪模式**：[概括性描述]

### 逐笔犯罪事实
（逐笔列出，每笔用`---SEPARATOR---`分隔）

#### 第N笔事实
- **时间**：[具体时间或时间段]
- **地点**：[作案地点]
- **涉案人员及角色**：[人员及分工]
- **行为方式**：[具体手段]
- **金额/结果**：[涉案金额、受害人、后果]
- **简要案情**：[该笔事实的完整描述，保留原文细节]

### 涉案人员汇总
（表格形式：姓名 | 身份证号 | 角色 | 备注）

### 关键关联信息
（电话号码、微信号、银行账号、车牌号、地址信息等，每项格式：`[类型] 内容 — 涉及人员/说明`）"""},
    ])

    # 保存为一份独立证据文件，使用传入的 next_id 编号
    safe_name = _sanitize_filename(f"{doc_type} — {md_file.stem}")
    ev_md_file = evidence_dir / f"{next_id:03d}_{safe_name}.md"

    content = f"""# {doc_type} — {md_file.stem}

| 项目 | 内容 |
|------|------|
| **证据类型** | {doc_type} |
| **来源文件** | {md_file.name} |

## 详细提取

{result}
"""
    ev_md_file.write_text(content, encoding="utf-8")
    logger.info(f"[证据提取] 已保存{doc_type}完整记录: {ev_md_file.name}")
    return ev_md_file


# ── 证据提取系统提示词（提取为模块常量） ──
_EVIDENCE_SYSTEM_PROMPT = """你是刑事案卷审查专家，正在逐份审查案卷材料。"""

# 固定的提取规则（作为 assistant 消息，构成稳定缓存前缀）
_EVIDENCE_EXTRACTION_RULES = """
**第一步：识别文书边界**

一个 MD 文件可能包含多份独立文书（如卷内目录、起诉意见书、移送告知书、讯问笔录等）。
你必须先识别每份文书的标题和起止位置，然后对每份文书分别提取。

**第二步：逐份文书提取**

对于每份文书，按以下规则提取：

### 起诉意见书/起诉书

**必须逐笔提取全部犯罪事实，不得遗漏，不得简化。**

每笔犯罪事实必须包含以下六项，缺一不可：
1. **时间**：具体作案时间（精确到年月日，如有）
2. **地点**：具体作案地址（必须包含街道/小区/门牌号等详细地址信息，不得只写"某地"）
3. **涉案人员及角色**：全部参与人员姓名及各自分工（谁主谋、谁出资、谁管理、谁记账等）
4. **行为方式**：具体犯罪手段、方法、流程
5. **金额/结果**：涉案金额（精确到元）、违法所得、受害人、造成后果
6. **简要案情**：该笔事实的完整描述，保留原文关键细节和数字

**禁止**：不要写"具体作案地点不详"、"未提及"等概括性表述。原文有地址就必须提取出来。

### 关键规则：讯问/询问笔录逐份提取

**每次讯问笔录 = 一份独立证据，不得合并。**

一个 MD 文件中可能包含同一人的多次讯问笔录（如第一次、第二次、第三次），或不同人的讯问笔录。**必须逐份提取，每份作为独立证据。**

### 讯问/询问笔录类提取要求

每份笔录必须包含以下信息：
- **讯问/询问时间**：精确到年月日时分
- **讯问/询问地点**：具体地址（如"江阴市公安局XX派出所XX讯问室"）
- **讯问/询问人**：姓名及职务
- **被讯问/被询问人**：姓名、身份证号、角色
- **笔录全文要点**：保留关键问答原文摘录（问答形式），特别是：
  - 关于案发时间、地点、参与人员的问答
  - 关于犯罪经过、分工、获利的问答
  - 关于主观明知、犯罪目的的问答
  - 关于认罪态度的问答
  - 前后供述有变化的问答
- **涉及案件事实的详细内容**：时间、地点、人物、事件经过，必须完整记录，不得概括简化

**书证/金融类**必须保留具体金额、时间、账号等数据
**鉴定意见**必须保留鉴定方法、检材来源、鉴定结论
**辨认笔录**必须保留辨认对象、辨认结果、辨认过程
**程序性文书**概括核心内容，标注法律程序阶段和文书名称

### 关键关联信息提取
务必提取以下类型的关联信息（用于后续证据交叉印证）：
- **电话号码**：所有人物的手机号、联系电话
- **微信号**：所有微信号（含微信昵称+微信号）
- **银行账号**：银行卡号、支付宝账号
- **车牌号**：涉案车辆牌照
- **身份证号**：涉案人员身份证
- **地址信息**：住址、经营地址、赌场地等具体地址
- **网络账号**：QQ号、抖音号、快手号等其他网络身份
- **其他标识**：绰号、代号、暗语中的人物代号

每项关联信息格式：`[类型] 内容 — 涉及人员/说明`（如：`手机号 13800138000 — 项少甫使用`）

---

请对文件中的每份证据，按以下格式输出：

### 证据N：[证据名称]

- **证据类型**：[物证/书证/证人证言/被害人陈述/犯罪嫌疑人供述和辩解/鉴定意见/勘验检查辨认笔录/视听资料、电子数据/程序性文书]
- **来源文件**：[与案卷文件名一致]
- **页码范围**：[如原文有标注]
- **涉案人员**：[列出涉及的人员姓名及角色，区分主从犯/证人/被害人等]
- **关键事实**：[按时间顺序列出关键事实，每条带"时间+主体+行为+结果"，保留具体金额、时间、地点等数据，不少于5条]
- **详细摘要**：[尽可能详细的摘要。对讯问/询问笔录，用问答形式保留关键原文摘录（不少于5个问答对）；对书证，列明具体数据；对文书，概括核心内容]
- **原文摘录**：[关键问答或关键原文的直接引用，不少于3-5段，标注页码或原文位置]
- **矛盾提示**：[供述前后是否一致？有无自相矛盾之处？]
- **关联信息**：[列出所有关键关联信息，见上方"关键关联信息提取"要求。如无则填"无"]

**注意（起诉意见书/起诉书/多次供述专用）：**
- 必须逐笔提取全部犯罪事实，每笔必须包含：**时间、地点（详细到门牌号/街道）、涉案人员及角色、行为方式、金额/结果、简要案情**
- **地址必须从原文提取出来，不得写"不详"或"未提及"**
- **多笔犯罪必须逐笔提取，不得合并概括。但多笔犯罪事实仍属于同一份证据记录，在"详细摘要"中逐笔列出即可，不需要拆分为多条证据**

**注意（讯问/询问笔录专用）：**
- **每次讯问/询问 = 一份独立证据，不得合并**
- 每份笔录必须包含：**讯问时间、讯问地点、讯问人、被讯问人、关键问答、涉及案件事实的详细内容（时间+地点+人物+事件）**

注意：
- 如果文件包含多份独立文书（如起诉意见书+告知书），分别提取为多份证据
- **保持原文的关键细节，不要过度概括**
- 页码引用必须准确
- 金额、时间、人名等数据必须精确，不要用"约"、"左右"等模糊词
- **关联信息是重点**：手机号、微信号、银行账号、车牌号等是证据互相关联印证的关键线索，务必逐一提取"""


async def _extract_single_file(
    md_file: Path,
    md_text: str,
    temp_dir: Path,
) -> tuple:
    """
    提取单个 MD 文件的证据（不含信号量和重试控制，由调用方管理）。

    返回：(md_filename, evidence_list)
    evidence_list 中每项包含证据数据，文件保存在 temp_dir 中。
    """
    from llm_client import get_llm_client, LLMRetryExhaustedError
    client = get_llm_client()

    # 无重试的直接调用，超时由调用方控制
    timeout_seconds = 600  # 10 分钟

    # 截断超长文本（LLM 上下文限制）
    max_chars = 100000
    if len(md_text) > max_chars:
        logger.info(f"[证据提取] {md_file.name}: 文件过长（{len(md_text)} 字符），截断至 {max_chars} 字符，后续内容将被忽略")
        md_text = md_text[:max_chars]

    result = await asyncio.wait_for(
        client.chat([
            # system 消息：角色定义 + 完整提取规则（合并为一条，避免 assistant role 兼容性问题）
            {"role": "system", "content": _EVIDENCE_SYSTEM_PROMPT + "\n\n" + _EVIDENCE_EXTRACTION_RULES},
            # user 消息：文件名 + 文件内容（变化的部分，放在最后）
            {"role": "user", "content": f"## 案卷文件：{md_file.name}\n\n{md_text}"},
        ]),
        timeout=timeout_seconds,
    )

    evidence_blocks = _parse_evidence_blocks(result, md_file.name)
    # 调试：将 LLM 原始响应保存到 debug 文件，方便排查解析失败
    debug_file = temp_dir / f"_debug_{md_file.stem}.txt"
    try:
        debug_file.write_text(f"=== LLM 返回 ({len(result)} 字符) ===\n\n{result}\n\n=== 解析结果 ({len(evidence_blocks)} 份证据) ===\n", encoding="utf-8")
    except Exception:
        pass
    logger.info(f"[证据提取] {md_file.name}: LLM 返回 {len(result)} 字符，解析为 {len(evidence_blocks)} 份证据")

    # 保存到临时目录，用临时编号（最终编号由合并阶段分配）
    evidence_list = []
    for i, ev_block in enumerate(evidence_blocks):
        ev_name = ev_block["name"]
        safe_name = _sanitize_filename(ev_name)
        temp_name = f"evid_{i:03d}_{safe_name}.md"
        ev_path = temp_dir / temp_name

        ev_content = f"""# {ev_name}

| 项目 | 内容 |
|------|------|
| **证据类型** | {ev_block['type']} |
| **来源文件** | {ev_block['source']} |
| **页码范围** | {ev_block.get('page_range', '未标注')} |
| **涉案人员** | {ev_block.get('persons', '未识别')} |

## 关联信息

{ev_block.get('related_entities', '无')}

## 关键事实

{ev_block.get('key_facts', '无')}

## 详细摘要

{ev_block['summary']}

## 原文摘录

{ev_block.get('original_quotes', '无')}

## 矛盾提示

{ev_block.get('contradiction_hints', '无')}

---

## LLM 原始输出

{ev_block['raw_text']}"""
        ev_path.write_text(ev_content, encoding="utf-8")

        evidence_list.append({
            "name": ev_name,
            "type": ev_block["type"],
            "source": md_file.name,
            "page_range": ev_block.get("page_range", ""),
            "persons": ev_block.get("persons", ""),
            "related_entities": ev_block.get("related_entities", ""),
            "summary_preview": ev_block["summary"][:200],
            "has_quotes": bool(ev_block.get("original_quotes", "").strip()),
            "md_file": ev_path.name,
            "_temp_dir": str(temp_dir),
        })

    logger.info(f"[证据提取] {md_file.name} → {len(evidence_list)} 份证据")
    return (md_file.name, evidence_list)


async def _extract_single_file_with_tracking(
    md_file: Path,
    md_text: str,
    temp_dir: Path,
    semaphore: asyncio.Semaphore,
) -> tuple:
    """
    包装 _extract_single_file，管理信号量和重试。
    重试等待期间释放信号量，让其他文件获得并发机会。
    """
    max_retries = 2
    last_error = None

    for attempt in range(1, max_retries + 1):
        # 获取信号量，执行提取
        async with semaphore:
            try:
                result = await _extract_single_file(md_file, md_text, temp_dir)
                return result
            except asyncio.TimeoutError:
                last_error = f"LLM 调用超时（600s）"
                logger.info(f"[证据提取] {md_file.name}: 第 {attempt} 次尝试超时")
            except Exception as e:
                last_error = str(e)
                error_msg = str(e).lower()
                if any(kw in error_msg for kw in ['429', 'rate limit', 'too many', 'quota']):
                    logger.info(f"[证据提取] {md_file.name}: 触发限流，退避后重试")
                    if attempt < max_retries:
                        # 信号量已释放（with 块退出），其他文件可继续
                        await asyncio.sleep(30 * attempt)
                        continue
                raise

        # 重试等待在信号量之外（其他文件可在此期间获得并发机会）
        if attempt < max_retries:
            logger.info(f"[证据提取] {md_file.name}: {attempt * 10}s 退避等待中...")
            await asyncio.sleep(10 * attempt)

    raise RuntimeError(f"[证据提取] {md_file.name}: 重试 {max_retries} 次均失败，最后错误: {last_error}")


@router.post("/{case_id}/extract-evidence")
async def extract_evidence(case_id: str):
    """
    从 md/ 目录下所有 MD 文件中提取证据清单和详细总结

    流程：
    1. 遍历 md/ 下每个 MD 文件
    2. 用 LLM 识别文件包含的独立证据，生成详细总结
    3. 保存至 evidence/ 目录：每份证据一个 .md 文件 + index.json 清单

    非起诉书文件采用并发提取（3个并行），按卷号排序后分配连续证据编号。
    """
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    md_dir = case_path / "md"
    if not md_dir.exists() or not any(md_dir.glob("*.md")):
        raise HTTPException(status_code=400, detail="案件中无 MD 文件，请先完成 PDF 转 MD")

    evidence_dir = case_path / "evidence"

    # 检查是否已有提取在运行中
    if EXTRACT_TASKS.get(case_id) == "running":
        raise HTTPException(status_code=409, detail="证据提取已在运行中")

    # 统计文件总数（用于进度显示）
    total_md_count = len(list(md_dir.glob("*.md")))

    EXTRACT_TASKS[case_id] = {
        "status": "running",
        "total_files": total_md_count,
        "processed_files": 0,
        "current_file": "",
        "started_at": time.time(),
    }

    # 在后台启动提取任务，不阻塞 HTTP 响应
    asyncio.create_task(
        _run_extract_background(case_id, case_path, md_dir, evidence_dir)
    )

    return {
        "success": True,
        "case_id": case_id,
        "total_evidence": 0,
        "evidence": [],
    }


async def _run_extract_background(case_id: str, case_path, md_dir, evidence_dir):
    """后台运行证据提取，不阻塞 HTTP 响应"""
    try:
        logger.info("[证据提取] 后台任务启动: %s", case_id)
        await _do_extract_evidence(case_id, case_path, md_dir, evidence_dir)
        logger.info("[证据提取] 后台任务完成: %s", case_id)
    except Exception as e:
        logger.exception("[证据提取] 后台任务失败: %s", e)
        import traceback
        traceback.print_exc()
        # 记录错误详情到 EXTRACT_TASKS，让前端轮询能拿到
        task = EXTRACT_TASKS.get(case_id)
        if isinstance(task, dict):
            task["status"] = "error"
            task["error_detail"] = str(e)
        EXTRACT_TASKS.pop(case_id, None)


async def _do_extract_evidence(
    case_id: str,
    case_path: Path,
    md_dir: Path,
    evidence_dir: Path,
):
    """证据提取核心逻辑（从 extract_evidence 拆分，便于异常清理）"""
    # 诊断：打印 LLM 配置和 MD 文件信息
    from config_manager import load_config
    cfg = load_config()
    logger.info(f"[证据提取] LLM 配置: baseUrl={cfg.get('llm_base_url', '')}, model={cfg.get('llm_model', '')}, apiKey={'已配置' if cfg.get('llm_api_key') else '未配置'}")
    logger.info(f"[证据提取] MD 目录: {md_dir}")
    if md_dir.exists():
        md_files = list(md_dir.glob("*.md"))
        logger.info(f"[证据提取] 找到 {len(md_files)} 个 MD 文件: {[f.name for f in md_files]}")
    else:
        logger.info(f"[证据提取] MD 目录不存在！")

    # 读取已提取的证据索引（断点续传：跳过已提取的 MD 文件）
    index_file = evidence_dir / "index.json"
    processed_sources = set()
    existing_evidence = []

    if index_file.exists():
        try:
            old_index = json.loads(index_file.read_text(encoding="utf-8"))
            existing_evidence = old_index.get("evidence", [])
            processed_sources = {ev["source"] for ev in existing_evidence}
            logger.info(f"[证据提取] 断点续传：已有 {len(existing_evidence)} 份证据，跳过已处理的 MD 文件")
        except Exception:
            pass

    evidence_dir.mkdir(parents=True, exist_ok=True)

    # 清理上次中断遗留的临时文件
    old_temp = evidence_dir / "_temp_extract"
    if old_temp.exists():
        shutil.rmtree(old_temp)
        logger.info(f"[证据提取] 清理上次中断的临时目录")

    # 使用电源管理器防止休眠
    from power_manager import PowerInhibitor

    with PowerInhibitor(f"证据提取: {case_id}"):
        # 检查是否被取消
        if EXTRACT_TASKS.get(case_id) == "cancelled":
            logger.info(f"[证据提取] 任务已被取消")
            EXTRACT_TASKS.pop(case_id, None)
            return {"success": False, "error": "用户已停止提取", "case_id": case_id}

        # 排序辅助函数
        def _is_indictment(name: str) -> bool:
            return ("起诉书" in name and "意见" not in name) or "起诉意见书" in name

        def _parse_volume_sort_key(name: str) -> tuple:
            import re
            cn2arabic = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
                         '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
            m = re.search(r'第([一二三四五六七八九十0-9]+)卷', name)
            volume = 9999
            sub = 9999
            if m:
                vol_str = m.group(1)
                if vol_str.isdigit():
                    volume = int(vol_str)
                elif vol_str in cn2arabic:
                    volume = cn2arabic[vol_str]
            m2 = re.search(r'_(\d{2})_', name)
            if m2:
                sub = int(m2.group(1))
            return (volume, sub)

        def _sort_md_files(files: list) -> list:
            return sorted(files, key=lambda f: _parse_volume_sort_key(f.name))

        all_md_files = _sort_md_files(list(md_dir.glob("*.md")))
        indictment_files = [f for f in all_md_files if _is_indictment(f.name)]
        other_files = [f for f in all_md_files if not _is_indictment(f.name)]

        # ── 第1步：普通文件并发提取（先处理，让用户快速看到进度）──
        pending_files = [f for f in other_files if f.name not in processed_sources]
        all_evidence = list(existing_evidence)
        next_id = len(all_evidence) + 1

        if pending_files:
            # 信号量控制并发
            from config_manager import load_config
            config = load_config()
            initial_concurrency = config.get("evidence_concurrency", 3)
            # 自动修正：并发数不超过待处理文件数
            initial_concurrency = min(initial_concurrency, len(pending_files))
            semaphore = asyncio.Semaphore(initial_concurrency)
            logger.info(f"[证据提取] 并发提取 {len(pending_files)} 个文件，并发={initial_concurrency}")

            temp_dir = evidence_dir / "_temp_extract"
            temp_dir.mkdir(exist_ok=True)

            # 断点续传：检查已完成的文件（.done 标记），跳过
            completed_markers = {
                f.stem for f in temp_dir.glob("*.done")
            }
            files_to_extract = [
                f for f in pending_files
                if f.stem not in completed_markers
            ]
            if completed_markers:
                logger.info(f"[证据提取] 断点续传：跳过已完成的 {len(completed_markers)} 个文件")

            async def extract_and_save_temp(md_file: Path) -> tuple:
                """并发提取单个文件，证据保存到独立子目录，完成后写 .done 标记"""
                nonlocal last_progress_time
                try:
                    md_text = md_file.read_text(encoding="utf-8")
                    if not md_text.strip():
                        # 空文件也写标记，避免重复检查
                        (temp_dir / f"{md_file.stem}.done").write_text("", encoding="utf-8")
                        # 更新进度计数，避免前端进度条永远差一格
                        task = EXTRACT_TASKS.get(case_id)
                        if task:
                            task["processed_files"] = task.get("processed_files", 0) + 1
                        last_progress_time = time.time()
                        return (md_file.name, [])

                    # 每个文件用独立子目录，避免文件名冲突
                    file_temp_dir = temp_dir / md_file.stem
                    file_temp_dir.mkdir(exist_ok=True)

                    # 更新进度
                    req_start = time.time()
                    task = EXTRACT_TASKS.get(case_id)
                    if task:
                        task["current_file"] = md_file.name
                        task["llm_waiting"] = True

                    # 心跳：LLM 等待期间每 30 秒更新进度，避免卡死检测误杀
                    heartbeat_cancelled = False

                    async def heartbeat():
                        nonlocal heartbeat_cancelled
                        while not heartbeat_cancelled:
                            await asyncio.sleep(30)
                            if not heartbeat_cancelled:
                                t = EXTRACT_TASKS.get(case_id)
                                if t:
                                    t["llm_waiting"] = True
                                # 更新 last_progress_time（外部变量）
                                nonlocal last_progress_time
                                last_progress_time = time.time()

                    heartbeat_task = asyncio.create_task(heartbeat())

                    logger.info(f"[证据提取] 处理: {md_file.name}")
                    source_name, evidence_list = await _extract_single_file_with_tracking(
                        md_file, md_text, file_temp_dir, semaphore
                    )

                    # 停止心跳
                    heartbeat_cancelled = True
                    heartbeat_task.cancel()
                    try:
                        await heartbeat_task
                    except (asyncio.CancelledError, Exception):
                        pass

                    # LLM 调用成功，立即更新心跳（细粒度心跳）
                    last_progress_time = time.time()

                    if task:
                        task["llm_waiting"] = False
                        task["llm_latency"] = round((time.time() - req_start) * 1000)

                    # 写完成标记（断点续传用）
                    (temp_dir / f"{md_file.stem}.done").write_text("", encoding="utf-8")

                    if task:
                        task["processed_files"] = task.get("processed_files", 0) + 1

                    # 更新进度时间，让卡死检测器知道有进展
                    last_progress_time = time.time()

                    logger.info(f"[证据提取] {md_file.name}: 提取 {len(evidence_list)} 条证据，耗时 {(time.time() - req_start):.1f}s")
                    return (source_name, evidence_list)
                except asyncio.CancelledError:
                    logger.info(f"[证据提取] {md_file.name}: 提取被取消")
                    raise

            # 取消监视器：定期检查 EXTRACT_TASKS，检测到取消时取消所有任务
            gather_task = None

            async def cancel_watcher():
                nonlocal gather_task
                while True:
                    await asyncio.sleep(1)
                    if gather_task and gather_task.done():
                        return  # gather 已完成，自动退出
                    if EXTRACT_TASKS.get(case_id) == "cancelled":
                        logger.info(f"[证据提取] 检测到取消信号，停止并发提取")
                        if gather_task and not gather_task.done():
                            gather_task.cancel()
                        return

            # 创建取消监视器
            watcher = asyncio.create_task(cancel_watcher())

            # 卡死检测监视器：如果超过 N 秒没有任何文件完成，发出警告
            last_progress_time = time.time()
            stall_threshold = 600  # 10 分钟无进展视为可能卡死（大文件 LLM 可能需要 5-8 分钟）

            async def stall_detector():
                """检测长时间无进展，自动取消任务"""
                nonlocal last_progress_time
                while True:
                    await asyncio.sleep(10)
                    if gather_task and gather_task.done():
                        return  # gather 已完成，自动退出
                    elapsed = time.time() - last_progress_time
                    if elapsed > stall_threshold:
                        logger.info(f"[证据提取] 检测到卡死：{elapsed:.0f}s 无进展（阈值 {stall_threshold}s），自动取消提取")
                        if gather_task and not gather_task.done():
                            gather_task.cancel()
                        return

            stall_task = asyncio.create_task(stall_detector())

            try:
                # 创建提取任务（只提取未完成的文件）
                coros = [
                    extract_and_save_temp(f)
                    for f in files_to_extract
                ]

                # 用 asyncio.gather 并发执行
                gather_task = asyncio.gather(*coros, return_exceptions=True)
                gather_results = await gather_task

            except asyncio.CancelledError:
                logger.info(f"[证据提取] 并发提取被取消")
                EXTRACT_TASKS.pop(case_id, None)
                return {"success": False, "error": "用户已停止提取", "case_id": case_id}
            finally:
                # 取消监视器
                watcher.cancel()
                stall_task.cancel()
                try:
                    await watcher
                except (asyncio.CancelledError, Exception):
                    pass
                try:
                    await stall_task
                except (asyncio.CancelledError, Exception):
                    pass

            # 合并结果：已完成的文件 + 新提取的文件
            # 按 pending_files 原始顺序，保证证据编号跟随卷号顺序
            extracted = {}
            success_count = 0
            fail_count = 0
            zero_count = 0
            for i, result in enumerate(gather_results):
                if i < len(files_to_extract):
                    f = files_to_extract[i]
                    extracted[f.name] = result
                    if isinstance(result, Exception):
                        fail_count += 1
                        logger.info(f"[证据提取] {f.name}: 提取失败 — {result}")
                    elif result is None:
                        fail_count += 1
                        logger.info(f"[证据提取] {f.name}: 提取返回 None")
                    else:
                        source_name, ev_list = result
                        if ev_list:
                            success_count += 1
                        else:
                            zero_count += 1
                            logger.info(f"[证据提取] {f.name}: LLM 调用成功但返回 0 份证据（LLM 响应可能未按要求格式输出）")

            logger.info(f"[证据提取] 提取汇总：成功 {success_count} 个文件，失败 {fail_count} 个文件，0 份证据 {zero_count} 个文件")

            # ── 按原始文件顺序分配编号（保持卷号顺序）──
            for md_file in pending_files:
                # 已完成的文件：从子目录读取证据
                if md_file.stem in completed_markers:
                    file_temp_dir = temp_dir / md_file.stem
                    ev_files = sorted(file_temp_dir.glob("evid_*.md"))
                    ev_list = []
                    for ef in ev_files:
                        ev_text = ef.read_text(encoding="utf-8")
                        blocks = _parse_evidence_blocks(ev_text, md_file.name)
                        ev_list.extend([{
                            "name": b["name"],
                            "type": b["type"],
                            "source": md_file.name,
                            "page_range": b.get("page_range", ""),
                            "persons": b.get("persons", ""),
                            "related_entities": b.get("related_entities", ""),
                            "summary_preview": b["summary"][:200],
                            "has_quotes": bool(b.get("original_quotes", "").strip()),
                            "md_file": ef.name,
                            "_temp_dir": str(file_temp_dir),
                        } for b in blocks])
                    if not ev_list:
                        continue
                else:
                    # 新提取的文件：从 extracted 结果读取
                    result = extracted.get(md_file.name)
                    if isinstance(result, Exception):
                        logger.info(f"[证据提取] {md_file.name}: 提取异常: {result}")
                        continue
                    if result is None:
                        continue
                    _, ev_list = result
                    if not ev_list:
                        continue

                for ev_data in ev_list:
                    new_name = f"{next_id:03d}_{_sanitize_filename(ev_data['name'])}.md"
                    final_path = evidence_dir / new_name

                    temp_path = Path(ev_data["_temp_dir"]) / ev_data["md_file"]
                    if temp_path.exists():
                        shutil.move(str(temp_path), str(final_path))

                    all_evidence.append({
                        "id": next_id,
                        "name": ev_data["name"],
                        "type": ev_data["type"],
                        "source": ev_data["source"],
                        "page_range": ev_data.get("page_range", ""),
                        "persons": ev_data.get("persons", ""),
                        "related_entities": ev_data.get("related_entities", ""),
                        "summary_preview": ev_data["summary_preview"],
                        "has_quotes": ev_data["has_quotes"],
                        "md_file": new_name,
                    })
                    next_id += 1
        else:
            logger.info("[证据提取] 所有文件已提取，跳过并发处理")

        # ── 第2步：起诉书/起诉意见书处理（内容优先判断：单独 / 混合 / 公安文书）──
        indictment_files.sort(key=lambda f: (0 if "起诉意见书" not in f.name else 1))

        # 内容判断辅助函数：读取文件内容，判断文书类型和处理方式
        def _classify_indictment_doc(md_file: Path) -> dict:
            """
            读取文件内容，判断文书类型。

            返回：
              {"type": "procuratorate_standalone", "doc_name": "起诉书"}  → 直接复制
              {"type": "procuratorate_mixed", "doc_name": "起诉书"}      → LLM 提取
              {"type": "police", "doc_name": "起诉意见书"}               → LLM 提取
            """
            text = md_file.read_text(encoding="utf-8")
            head = text[:5000]  # 文书编号通常在文件最开头

            # 公安文书编号特征：任意长度的地名前缀 + "公" + 业务类型 + "字"
            has_police_number = bool(re.search(r'.+公(刑|治|行|刑立|刑强|刑诉)\w*字', head[:2000]))
            # 检察院文书编号特征：任意长度的地名前缀 + "检" + 业务类型 + "字"
            has_procuratorate_number = bool(re.search(r'.+检(刑诉|公诉|刑执)\w*字', head[:2000]))

            # 文书抬头判断（抬头通常在文件名附近）
            has_police_title = bool(re.search(r'起诉意见书', head[:1000]))
            has_procuratorate_title = bool(re.search(r'起\s*诉\s*书', head[:300]))

            # 公安文书：有公安编号 或 抬头为"起诉意见书"
            is_police_doc = has_police_number or has_police_title
            # 检察院文书：有检察院编号 或 抬头为"起诉书"
            is_procuratorate_doc = has_procuratorate_number or has_procuratorate_title

            # 判断逻辑
            if is_procuratorate_doc and not is_police_doc:
                return {"type": "procuratorate_standalone", "doc_name": "起诉书"}
            if is_procuratorate_doc and is_police_doc:
                return {"type": "procuratorate_mixed", "doc_name": "起诉书（混合文件）"}
            # 兜底：两者都无，按文件名判断
            if "起诉书" in md_file.name and "意见" not in md_file.name:
                return {"type": "procuratorate_standalone", "doc_name": "起诉书"}
            return {"type": "police", "doc_name": "起诉意见书"}

        for md_file in indictment_files:
            if EXTRACT_TASKS.get(case_id) == "cancelled":
                logger.info(f"[证据提取] 任务已被取消（处理 {md_file.name} 前）")
                EXTRACT_TASKS.pop(case_id, None)
                return {"success": False, "error": "用户已停止提取", "case_id": case_id}

            if md_file.name in processed_sources:
                logger.info(f"[证据提取] 跳过已处理: {md_file.name}")
                continue

            # 读内容判断类型
            classification = _classify_indictment_doc(md_file)
            is_standalone = classification["type"] == "procuratorate_standalone"

            if is_standalone:
                # 检察院起诉书单独存在 → 直接复制
                dest_name = f"{next_id:03d}_{md_file.name}"
                dest_path = evidence_dir / dest_name
                shutil.copy2(str(md_file), str(dest_path))
                logger.info(f"[证据提取] {md_file.name} → {dest_name}（{classification['doc_name']}，直接复制）")

                all_evidence.append({
                    "name": md_file.stem,
                    "type": classification["doc_name"],
                    "source": md_file.name,
                    "page_range": "",
                    "persons": "",
                    "related_entities": "",
                    "summary_preview": f"{md_file.name}（待案卷分析时详细提取）",
                    "has_quotes": True,
                    "md_file": dest_name,
                })
                next_id += 1
            else:
                # 起诉意见书 / 混合文件 → LLM 提取
                md_text = md_file.read_text(encoding="utf-8")
                logger.info(f"[证据提取] {md_file.name} → LLM 提取（{classification['doc_name']}）")
                ev_path = await _process_indictment_single(md_file, md_text, evidence_dir, next_id)

                ev_text = ev_path.read_text(encoding="utf-8")
                all_evidence.append({
                    "name": ev_path.stem.split("_", 1)[1] if "_" in ev_path.stem else ev_path.stem,
                    "type": doc_type if doc_type != "混合文件" else "起诉意见书",
                    "source": md_file.name,
                    "page_range": "",
                    "persons": "",
                    "related_entities": "",
                    "summary_preview": ev_text[:200],
                    "has_quotes": True,
                    "md_file": ev_path.name,
                })
                next_id += 1

            # 更新进度
            task = EXTRACT_TASKS.get(case_id)
            if task:
                task["current_file"] = md_file.name
                task["processed_files"] = task.get("processed_files", 0) + 1

        # ── 最终保存 ──
        index_data = {
            "case_id": case_id,
            "total_evidence": len(all_evidence),
            "evidence": all_evidence,
            "generated_at": datetime.now().isoformat(),
        }
        # 如果提取结果为 0，记录可能的原因供前端展示
        if len(all_evidence) == 0:
            index_data["error_hint"] = "LLM 提取全部失败（详见后端日志），可能原因：API Key 无效、Base URL 不可达、模型名称错误、或所有 MD 文件解析失败"
        index_file.write_text(json.dumps(index_data, ensure_ascii=False, indent=2), encoding="utf-8")

        # 清理临时文件（无论走哪个分支都清理）
        old_temp = evidence_dir / "_temp_extract"
        if old_temp.exists():
            shutil.rmtree(old_temp)
            logger.info(f"[证据提取] 临时目录已清理")

        logger.info(f"[证据提取] 完成，共 {len(all_evidence)} 份证据")

    EXTRACT_TASKS.pop(case_id, None)

    return {
        "success": True,
        "case_id": case_id,
        "total_evidence": len(all_evidence),
        "evidence": all_evidence,
    }


@router.get("/{case_id}/md-files")
async def list_md_files(case_id: str):
    """列出 md/ 目录下所有文件"""
    case_path = find_case_path(case_id)
    if not case_path:
        return {"files": []}

    md_dir = case_path / "md"
    if not md_dir.exists():
        return {"files": []}

    def _infer_type(name: str) -> str:
        if "讯问" in name: return "讯问笔录"
        if "询问" in name: return "询问笔录"
        if "辨认" in name: return "辨认笔录"
        if "鉴定" in name: return "鉴定意见"
        if "勘验" in name: return "勘验笔录"
        if "微信" in name or "支付" in name or "流水" in name: return "电子数据/书证"
        if "照片" in name: return "物证/照片"
        return "其他证据"

    files = []
    for f in sorted(md_dir.glob("*.md")):
        if f.stem.startswith("_temp"):
            continue
        files.append({
            "name": f.name,
            "type": _infer_type(f.name),
        })
    return {"files": files}


@router.get("/{case_id}/evidence-index")
async def get_evidence_index(case_id: str):
    """获取证据清单索引"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    evidence_dir = case_path / "evidence"
    index_file = evidence_dir / "index.json"
    if not index_file.exists():
        return {"total_evidence": 0, "evidence": []}

    return json.loads(index_file.read_text(encoding="utf-8"))


@router.get("/{case_id}/extract-status")
async def get_extract_status(case_id: str):
    """获取证据提取状态（含进度信息）"""
    task = EXTRACT_TASKS.get(case_id)
    if task == "cancelled":
        return {"case_id": case_id, "status": "cancelled"}
    if task and isinstance(task, dict):
        elapsed = time.time() - task.get("started_at", time.time())
        result = {
            "case_id": case_id,
            "status": task.get("status", "running"),
            "total_files": task.get("total_files", 0),
            "processed_files": task.get("processed_files", 0),
            "current_file": task.get("current_file", ""),
            "elapsed_seconds": round(elapsed),
            "llm_waiting": task.get("llm_waiting", False),
            "llm_latency_ms": task.get("llm_latency", 0),
        }
        # 传递错误详情给前端
        if task.get("error_detail"):
            result["error_detail"] = task["error_detail"]
        return result
    return {"case_id": case_id, "status": "idle"}


@router.get("/{case_id}/evidence-summary/{filename}")
async def get_evidence_summary(case_id: str, filename: str):
    """获取指定证据的详细总结内容"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    evidence_dir = case_path / "evidence"
    ev_file = evidence_dir / filename

    # fallback：带前缀的文件名找不到时，去掉前缀再试
    if not ev_file.exists():
        # 如 "001_证据1：xxx.md" → "证据1：xxx.md"
        m = re.match(r"^\d+_(.+)$", filename)
        if m:
            ev_file = evidence_dir / m.group(1)

    if not ev_file.exists():
        raise HTTPException(status_code=404, detail=f"证据文件不存在：{filename}")

    return {"content": ev_file.read_text(encoding="utf-8")}

@router.post("/{case_id}/stop-extract")
async def stop_extract(case_id: str):
    """停止正在运行的提取任务，保留已提取的证据"""
    # 标记为取消状态，让正在运行的提取任务退出
    EXTRACT_TASKS[case_id] = "cancelled"
    logger.info(f"[停止提取] 已取消 {case_id} 的提取任务，保留已提取的证据")
    return {"success": True, "message": "提取任务已取消"}


@router.post("/{case_id}/clear-evidence")
async def clear_evidence(case_id: str):
    """清除证据目录和卡死的提取任务状态，允许重新提取"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    # 标记为取消状态，让正在运行的提取任务退出
    EXTRACT_TASKS[case_id] = "cancelled"
    logger.info(f"[清除证据] 已取消 {case_id} 的提取任务")

    evidence_dir = case_path / "evidence"
    if not evidence_dir.exists():
        return {"success": True, "message": "证据目录不存在，无需清除"}

    # 删除整个目录再重建（比逐个 unlink 更可靠，能正确处理子目录）
    shutil.rmtree(evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    return {"success": True, "message": "已清除证据目录，可重新提取"}


@router.delete("/{case_id}/md-file/{md_file_name}")
async def delete_md_file(case_id: str, md_file_name: str):
    """删除单个 MD 文件（删除后可从 PDF 重新转换）"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    md_dir = case_path / "md"
    md_file = md_dir / md_file_name

    if not md_file.exists():
        raise HTTPException(status_code=404, detail=f"MD 文件不存在：{md_file_name}")

    md_file.unlink()

    # 同时删除关联的图片目录（如果有）
    images_dir = md_dir / f"{md_file.stem}_images"
    if images_dir.exists():
        shutil.rmtree(images_dir)

    # 返回对应的 PDF 文件名（用于重新转换）
    pdf_name = md_file.stem + ".pdf"
    return {
        "success": True,
        "message": f"已删除 {md_file_name}，可从 PDF 重新转换",
        "pdf_name": pdf_name,
    }


@router.delete("/{case_id}/pdf-file/{pdf_file_name}")
async def delete_pdf_file(case_id: str, pdf_file_name: str):
    """删除 PDF 文件（同时删除对应的 MD 文件）"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    # 查找并删除 PDF（processed/ 或 original/）
    pdf_path = None
    for d in [case_path / "processed", case_path / "original"]:
        p = d / pdf_file_name
        if p.exists():
            pdf_path = p
            break

    if not pdf_path:
        raise HTTPException(status_code=404, detail=f"PDF 文件不存在：{pdf_file_name}")

    pdf_path.unlink()

    # 同时删除对应的 MD 文件（如果有）
    md_file = case_path / "md" / f"{pdf_path.stem}.md"
    if md_file.exists():
        md_file.unlink()
        # 删除关联的图片目录
        images_dir = case_path / "md" / f"{pdf_path.stem}_images"
        if images_dir.exists():
            shutil.rmtree(images_dir)

    return {"success": True, "message": f"已删除 {pdf_file_name}"}


@router.post("/{case_id}/clear-stage/{stage_num}")
async def clear_stage(case_id: str, stage_num: int):
    """清除某个分析阶段的输出，允许重新运行"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    stage_dir = case_path / "analysis" / f"stage_{stage_num}"
    if not stage_dir.exists():
        return {"success": True, "message": f"阶段 {stage_num} 的输出不存在"}

    for f in stage_dir.iterdir():
        f.unlink()

    # 如果清除的是阶段 5，也清除子阶段 51/52/53
    if stage_num == 5:
        for sub in [51, 52, 53]:
            sub_dir = case_path / "analysis" / f"stage_{sub}"
            if sub_dir.exists():
                for f in sub_dir.iterdir():
                    f.unlink()

    return {"success": True, "message": f"已清除阶段 {stage_num} 的输出"}


def _parse_evidence_blocks(llm_output: str, source_file: str) -> list:
    """
    解析 LLM 返回的证据块。

    优先尝试 JSON 数组解析（LLM 返回 JSON 格式），
    失败后回退到文本格式解析（兼容已有的输出格式）。
    """
    import re
    import json
    blocks = []

    # ── 第1优先：JSON 数组解析 ──
    # 匹配 ```json ... ``` 代码块，或直接查找 JSON 数组
    json_text = llm_output
    code_block = re.search(r'```json\s*(\[.*?\])\s*```', llm_output, re.DOTALL)
    if code_block:
        json_text = code_block.group(1)
    elif llm_output.strip().startswith('['):
        # 尝试直接解析整个输出为 JSON 数组
        json_text = llm_output.strip()

    if json_text.startswith('['):
        try:
            # 尝试截断到最后一个 ] 以处理 LLM 多余输出
            bracket_end = json_text.rfind(']')
            if bracket_end > 0:
                json_text = json_text[:bracket_end + 1]
            data = json.loads(json_text)
            if isinstance(data, list) and len(data) > 0:
                for item in data:
                    if isinstance(item, dict):
                        blocks.append({
                            "name": item.get("name", item.get("证据名称", "未命名证据")),
                            "type": item.get("type", item.get("证据类型", "其他证据")),
                            "source": source_file,
                            "page_range": item.get("page_range", item.get("页码范围", "")),
                            "persons": item.get("persons", item.get("涉案人员", "")),
                            "key_facts": item.get("key_facts", item.get("关键事实", "")),
                            "summary": item.get("summary", item.get("详细摘要", ""))[:2000],
                            "original_quotes": item.get("original_quotes", item.get("原文摘录", "")),
                            "contradiction_hints": item.get("contradiction_hints", item.get("矛盾提示", "无")),
                            "related_entities": item.get("related_entities", item.get("关联信息", "")),
                            "raw_text": json.dumps(item, ensure_ascii=False, indent=2),
                        })
                if blocks:
                    logger.info(f"[证据解析] {source_file}: JSON 模式解析成功，{len(blocks)} 份证据")
                    return blocks
        except (json.JSONDecodeError, Exception) as e:
            logger.info(f"[证据解析] {source_file}: JSON 解析失败，回退到文本模式: {e}")
            blocks = []

    # ── 第2优先：文本格式解析（兼容原有格式）──
    patterns = [
        r'#{1,3}\s*证据(\d+)[：:]\s*(.+)$',           # ### 证据1：名称
        r'\*\*【证据\s*(\d+)】\*\*',                   # **【证据 1】**
        r'\*\*证据(\d+)\*\*[：:]\s*(.+)',              # **证据1**：名称
        r'#{1,3}\s*证据\s*(\d+)\s*$',                  # ### 证据 1
    ]

    used_pattern = None
    sections = None

    for pat in patterns:
        p = re.compile(pat, re.MULTILINE)
        matches = list(p.finditer(llm_output))
        if matches:
            used_pattern = pat
            # 按匹配位置拆分
            sections = []
            last_end = 0
            for m in matches:
                sections.append(llm_output[last_end:m.start()])  # 前一份证据的内容
                sections.append(m.group(0))  # 证据标题
                last_end = m.end()
            sections.append(llm_output[last_end:])  # 最后一份证据的内容
            break

    if sections is None or len(sections) < 3:
        # 没找到证据块标记，整个输出作为一份证据
        logger.info(f"[证据解析] {source_file}: 未找到证据标记，整个输出作为一份证据（LLM 可能未按格式输出）")
        blocks.append({
            "name": source_file.replace(".md", ""),
            "type": "其他证据",
            "source": source_file,
            "page_range": "",
            "persons": "",
            "key_facts": "",
            "summary": llm_output[:2000],
            "original_quotes": "",
            "contradiction_hints": "",
            "related_entities": "",
            "raw_text": llm_output,
        })
        return blocks

    # sections: [intro, title1, content1, title2, content2, ...]
    # sections[0] = intro, sections[1] = title1, sections[2] = content1+title2, etc.
    # 重新整理：奇数索引是标题，偶数索引（>=2）是内容
    for i in range(1, len(sections), 2):
        title = sections[i].strip()
        content = sections[i + 1] if i + 1 < len(sections) else ""

        # 提取证据名：优先从内容的"证据名称"字段获取，其次从标题提取
        ev_name = _extract_field(content, "证据名称")
        if ev_name:
            name = ev_name
        else:
            # 从标题中去掉格式标记
            name = re.sub(r'#+\s*', '', title).strip()
            name = re.sub(r'\*\*【|】\*\*|\*\*', '', name).strip()
            if not name:
                name = f"证据{i // 2 + 1}"

        # 去掉 LLM 输出的"证据N："前缀（如"证据1：《受案登记表》" → "《受案登记表》"）
        name = re.sub(r'^证据\s*\d+\s*[：:]\s*', '', name).strip()
        if not name:
            name = f"证据{i // 2 + 1}"

        # 提取各字段
        ev_type = _extract_field(content, "证据类型") or "其他证据"
        page_range = _extract_field(content, "页码范围") or ""
        persons = _extract_field(content, "涉案人员") or ""
        key_facts = _extract_field(content, "关键事实") or ""
        summary = _extract_field(content, "详细摘要") or content[:2000]
        original_quotes = _extract_field(content, "原文摘录") or ""
        contradiction = _extract_field(content, "矛盾提示") or "无"
        related_entities = _extract_field(content, "关联信息") or ""

        blocks.append({
            "name": name,
            "type": ev_type,
            "source": source_file,
            "page_range": page_range.strip(),
            "persons": persons.strip(),
            "key_facts": key_facts.strip(),
            "summary": summary.strip(),
            "original_quotes": original_quotes.strip(),
            "contradiction_hints": contradiction.strip(),
            "related_entities": related_entities.strip(),
            "raw_text": content.strip(),
        })

    return blocks


def _extract_field(text: str, field_name: str) -> Optional[str]:
    """从证据文本中提取指定字段"""
    import re
    # 匹配 "字段名"：值 或 **字段名**：值 或 | 字段名 | 值 |
    patterns = [
        rf'\*\*{field_name}\*\*\s*[：:]\s*(.+)',
        rf'{field_name}\s*[：:]\s*(.+)',
        rf'\|\s*{field_name}\s*\|\s*(.+?)\s*\|',
    ]
    for p in patterns:
        match = re.search(p, text)
        if match:
            return match.group(1).strip()
    return None


def _sanitize_filename(name: str) -> str:
    """清理文件名中的非法字符"""
    import re
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = re.sub(r'\s+', '_', name)
    return name[:80]




@router.post("/{case_id}/open-file")
async def open_file_endpoint(case_id: str, file_path: str):
    """打开文件（跨平台）- 支持 glob 模式"""
    import urllib.parse
    import subprocess
    import sys
    file_path = urllib.parse.unquote(file_path)

    case_root = find_case_path(case_id)
    if not case_root:
        return {"success": False, "error": "案件不存在"}

    case_root_resolved = str(Path(case_root).resolve())

    # 尝试 glob 匹配
    matched = glob.glob(file_path)
    if not matched:
        return {"success": False, "error": f"文件不存在：{file_path}"}

    actual_path = str(Path(matched[0]).resolve())

    # 安全验证：文件必须在案件目录内
    if not actual_path.startswith(case_root_resolved):
        return {"success": False, "error": "拒绝访问：文件不在案件目录内"}

    try:
        if sys.platform == "darwin":
            subprocess.run(["open", actual_path], check=True)
        elif sys.platform == "win32":
            os.startfile(actual_path)
        else:
            subprocess.run(["xdg-open", actual_path], check=True)
        return {"success": True, "message": "已打开文件", "path": actual_path}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/{case_id}/serve-file")
async def serve_file(case_id: str, file_path: str, dir: Optional[str] = None):
    """提供文件下载/预览

    Args:
        case_id: 案件 ID
        file_path: 文件名
        dir: 指定子目录（original/processed/md），不指定则递归搜索
    """
    import urllib.parse
    from fastapi.responses import FileResponse
    from fastapi import HTTPException

    file_path = urllib.parse.unquote(file_path)

    case_root = find_case_path(case_id)
    if not case_root:
        raise HTTPException(status_code=404, detail="案件不存在")

    case_root_resolved = Path(case_root).resolve()

    # 从路径中提取文件名
    p = Path(file_path)
    target_name = p.name

    # 如果指定了 dir，直接在该子目录中查找
    if dir:
        target_dir = case_root / dir
        if not target_dir.exists():
            raise HTTPException(status_code=404, detail=f"目录不存在：{dir}")
        # 在指定目录中递归搜索
        matched = list(target_dir.rglob(target_name))
        if not matched:
            raise HTTPException(status_code=404, detail=f"文件不存在：{target_name} (在 {dir} 目录)")
    else:
        # 不指定 dir，递归搜索整个案件目录
        matched = list(case_root.rglob(target_name))
        if not matched:
            raise HTTPException(status_code=404, detail=f"文件不存在：{target_name}")

    actual_path = str(matched[0])
    fp = Path(actual_path).resolve()

    # 安全验证：文件必须在案件目录内
    if not str(fp).startswith(str(case_root_resolved)):
        raise HTTPException(status_code=403, detail="拒绝访问")

    if not fp.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在：{actual_path}")

    media_type = "application/pdf" if fp.suffix == ".pdf" else "text/markdown"
    return FileResponse(str(fp), media_type=media_type, filename=fp.name, headers={"Content-Disposition": "inline"})


@router.get("/{case_id}/processed-pdfs")
async def list_processed_pdfs(case_id: str):
    """列出 processed/ 目录下的所有 PDF 文件"""
    case_root = find_case_path(case_id)
    if not case_root:
        raise HTTPException(status_code=404, detail="案件不存在")

    processed_dir = case_root / "processed"
    if not processed_dir.exists():
        return {"files": []}

    pdfs = []
    for f in sorted(processed_dir.iterdir()):
        if f.is_file() and f.suffix.lower() == ".pdf":
            pdfs.append({
                "name": f.name,
                "size": f.stat().st_size,
            })
    return {"files": pdfs}


class ConvertRequest(BaseModel):
    file_name: str


@router.post("/{case_id}/convert-to-md")
async def convert_to_md(case_id: str, request: ConvertRequest):
    """转换单个 PDF 为 MD（保留图片）"""
    file_name = request.file_name

    case_path = find_case_path(case_id)
    if not case_path:
        return {"success": False, "error": "案件不存在"}

    # 查找 PDF 文件
    pdf_file = None
    for search_dir in [case_path / "original", case_path / "processed"]:
        if search_dir.exists():
            pdf_file = search_dir / file_name
            if pdf_file.exists():
                break

    if not pdf_file or not pdf_file.exists():
        return {"success": False, "error": f"PDF 文件不存在：{file_name}"}

    # 确保 md/ 目录存在
    md_dir = case_path / "md"
    md_dir.mkdir(exist_ok=True)

    # 执行转换
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from pdf_to_md import get_evidence_text
        text, images_dir = get_evidence_text(str(pdf_file), prefer_md=True, output_dir=str(md_dir))

        if text is None:
            return {"success": False, "error": "转换失败，请查看后端日志"}

        # 保存 MD 文件
        md_file = md_dir / f"{Path(file_name).stem}.md"
        md_file.write_text(text, encoding="utf-8")

        # 保存图片目录（如果有）
        has_images = False
        image_count = 0
        if images_dir and Path(images_dir).exists():
            target_images = md_dir / f"{Path(file_name).stem}_images"
            if target_images.exists():
                shutil.rmtree(target_images)
            shutil.copytree(images_dir, target_images)
            has_images = True
            image_count = len(list(target_images.iterdir()))

        return {
            "success": True,
            "md_file": str(md_file),
            "md_size": md_file.stat().st_size,
            "md_name": md_file.name,
            "has_images": has_images,
            "image_count": image_count,
        }
    except Exception as e:
        import traceback
        return {"success": False, "error": f"{str(e)}\n{traceback.format_exc()}"}


@router.get("/{case_id}/md-images/{image_path:path}")
async def serve_md_image(case_id: str, image_path: str):
    """提供 MD 文件关联的图片（从 md/*_images/ 目录）"""
    import urllib.parse
    from fastapi.responses import FileResponse
    from fastapi import HTTPException

    image_path = urllib.parse.unquote(image_path)

    case_root = find_case_path(case_id)
    if not case_root:
        raise HTTPException(status_code=404, detail="案件不存在")

    # 安全验证：路径不能包含 ..
    if ".." in image_path:
        raise HTTPException(status_code=403, detail="非法路径")

    # 图片路径在 md/ 目录下
    actual_path = case_root / "md" / image_path
    if not actual_path.exists() or not actual_path.is_file():
        raise HTTPException(status_code=404, detail=f"图片不存在：{image_path}")

    # 确保在案件目录内
    case_root_resolved = Path(case_root).resolve()
    fp = actual_path.resolve()
    if not str(fp).startswith(str(case_root_resolved)):
        raise HTTPException(status_code=403, detail="拒绝访问")

    # 根据扩展名设置 MIME type
    mime_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
    }
    media_type = mime_map.get(fp.suffix.lower(), "application/octet-stream")

    return FileResponse(str(fp), media_type=media_type, filename=fp.name, headers={"Content-Disposition": "inline"})


# ═══════════════════════════════════════════════════════════
# 回收站 API（必须在 /{case_id} 之前定义）
# ═══════════════════════════════════════════════════════════

@router.get("/trash")
async def list_trash():
    """列出回收站中的案件"""
    import time
    items = []
    
    if not TRASH_DIR.exists():
        return items
    
    now = time.time()
    
    for item in sorted(TRASH_DIR.iterdir()):
        if item.is_dir():
            deleted_at = item.stat().st_mtime
            days_left = TRASH_RETAIN_DAYS - (now - deleted_at) / 86400
            
            # 读取 case.json（如果存在）
            case_info = {}
            for sub in item.iterdir():
                if sub.is_dir() and (sub / "case.json").exists():
                    with open(sub / "case.json", 'r', encoding='utf-8') as f:
                        case_info = json.load(f)
                    break
            
            items.append({
                "id": item.name,
                "name": case_info.get("name", "未知"),
                "defendant": case_info.get("defendant", "未知"),
                "deleted_at": datetime.fromtimestamp(deleted_at).strftime("%Y-%m-%d %H:%M"),
                "days_left": max(0, round(days_left, 1)),
                "size_mb": round(sum(f.stat().st_size for f in item.rglob("*") if f.is_file()) / (1024*1024), 1)
            })
    
    return items


@router.post("/trash/{case_id}/restore")
async def restore_case(case_id: str):
    """从回收站恢复案件"""
    trash_path = TRASH_DIR / case_id
    
    if not trash_path.exists():
        return {"success": False, "error": "回收站中不存在该案件"}
    
    # 移回原目录
    original_path = CASES_DIR / case_id
    if original_path.exists():
        return {"success": False, "error": "原案件目录已存在，请先删除"}
    
    shutil.move(str(trash_path), str(original_path))
    
    return {"success": True, "message": "案件已恢复"}


@router.delete("/trash/{case_id}")
async def permanent_delete_case(case_id: str):
    """从回收站彻底删除案件"""
    trash_path = TRASH_DIR / case_id

    if not trash_path.exists():
        return {"success": False, "error": "回收站中不存在该案件"}

    shutil.rmtree(str(trash_path))

    return {"success": True, "message": "案件已彻底删除"}


# ═══════════════════════════════════════════════════════════
# 案件详情 API
# ═══════════════════════════════════════════════════════════

@router.get("/{case_id}")
async def get_case(case_id: str) -> Optional[CaseInfo]:
    """获取案件详情"""
    for case_dir in CASES_DIR.iterdir():
        if case_dir.is_dir() and case_dir.name == case_id:
            for sub in case_dir.iterdir():
                if sub.is_dir() and (sub / "case.json").exists():
                    with open(sub / "case.json", 'r', encoding='utf-8') as f:
                        metadata = json.load(f)
                        return CaseInfo(**metadata)
    return None


@router.delete("/{case_id}")
async def delete_case(case_id: str):
    """软删除案件：移动到回收站，保留 5 天后彻底删除"""
    # 查找案件目录
    case_path = None
    for case_dir in CASES_DIR.iterdir():
        if case_dir.is_dir() and case_dir.name == case_id:
            case_path = case_dir
            break
    
    if not case_path:
        return {"success": False, "error": "案件不存在"}
    
    # 移动到回收站
    trash_path = TRASH_DIR / case_path.name
    if trash_path.exists():
        shutil.rmtree(trash_path)
    
    shutil.move(str(case_path), str(trash_path))
    
    # 记录删除时间（通过修改目录的 mtime）
    import time
    os.utime(trash_path, (time.time(), time.time()))
    
    return {"success": True, "message": f"案件已移入回收站，{TRASH_RETAIN_DAYS} 天后彻底删除"}


@router.post("/claim-cases")
async def claim_cases(owner: str):
    """将没有 owner 的案件关联给当前用户"""
    claimed = 0
    if not CASES_DIR.exists():
        return {"claimed": 0}

    for case_dir in CASES_DIR.iterdir():
        if case_dir.is_dir():
            for sub in case_dir.iterdir():
                if sub.is_dir():
                    metadata_file = sub / "case.json"
                    if metadata_file.exists():
                        try:
                            with open(metadata_file, 'r', encoding='utf-8') as f:
                                metadata = json.load(f)
                            if not metadata.get("owner"):
                                metadata["owner"] = owner
                                with open(metadata_file, 'w', encoding='utf-8') as f:
                                    json.dump(metadata, f, ensure_ascii=False, indent=2)
                                claimed += 1
                        except Exception:
                            pass

    return {"claimed": claimed}
