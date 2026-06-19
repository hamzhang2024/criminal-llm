"""
案件管理 API

功能：
1. 扫描所有案件文件夹
2. 识别合法案件（有 case.json）
3. 识别待导入文件夹（无 case.json 但有 PDF）
4. 导入文件夹为合法案件
"""
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)
import asyncio
import glob
import json
import os
import time
import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile

# 导入路径验证工具
from utils.path_validator import sanitize_filename, validate_path

router = APIRouter(prefix="/api/cases", tags=["案件管理"])

# 证据提取状态追踪（并发数从 config_manager 读取）
# 结构: { case_id: { "status": "running", "total_files": N, "processed_files": N, "current_file": "xxx.md", "started_at": time.time() } }
# 当提取失败时，额外记录 "error_detail" 字段，方便前端展示具体原因
EXTRACT_TASKS: dict = {}

from config import DATA_DIR, MAX_FILE_SIZE

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
                            logger.warning(f"读取案件失败：{sub}: {e}")

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
        logger.warning(f"[find_case_path] CASES_DIR 不存在: {CASES_DIR}")
        return None

    logger.debug(f"[find_case_path] 搜索 case_id={case_id}, CASES_DIR={CASES_DIR}")

    for case_dir in CASES_DIR.iterdir():
        if case_dir.is_dir() and case_dir.name == case_id:
            logger.debug(f"[find_case_path] 找到匹配目录: {case_dir}")
            for sub in case_dir.iterdir():
                if sub.is_dir() and (sub / "case.json").exists():
                    logger.info(f"[find_case_path] case_id={case_id} -> {sub}")
                    return sub

    logger.warning(f"[find_case_path] case_id={case_id} 未找到")
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

    # 安全验证：检查路径是否合法
    try:
        folder = Path(folder_path)
        # 验证路径是否在 CASES_DIR 范围内（防止路径遍历）
        if folder.is_absolute():
            # 绝对路径：验证是否在数据目录范围内
            resolved_folder = folder.resolve()
            resolved_cases = CASES_DIR.resolve()
            if not str(resolved_folder).startswith(str(resolved_cases)):
                logger.warning(f"[安全] 导入路径越界: {folder_path}")
                return {"error": "路径越界，只能导入数据目录内的文件夹"}
        else:
            # 相对路径：基于 CASES_DIR 解析
            folder = CASES_DIR / folder
    except Exception as e:
        logger.warning(f"[安全] 导入路径验证失败: {folder_path}, 错误: {e}")
        return {"error": "路径格式无效"}

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
    logger.info(f"[upload] case_id={case_id}, file_count={len(files)}, file_names={[f.filename for f in files]}")
    case_path = find_case_path(case_id)
    if not case_path:
        logger.warning(f"[upload] case not found: {case_id}")
        return {"success": False, "error": "案件不存在"}

    original_dir = case_path / "original"
    original_dir.mkdir(exist_ok=True)

    uploaded_files = []
    for file in files:
        # 先通过 Content-Length 头快速校验大小，避免超大文件载入内存导致 OOM
        declared_size = file.size
        if declared_size is not None and declared_size > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"文件过大，最大支持 {MAX_FILE_SIZE // (1024*1024)}MB"
            )

        # 流式读取并累加，超出阈值立即中止
        file_content = bytearray()
        chunk_size = 1024 * 1024  # 1MB
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            file_content.extend(chunk)
            if len(file_content) > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=413,
                    detail=f"文件过大，最大支持 {MAX_FILE_SIZE // (1024*1024)}MB"
                )
        file_content = bytes(file_content)

        # 保持原始文件名
        original_name = file.filename or "unknown.pdf"

        # 安全验证：检查文件名是否合法（防止路径遍历和命令注入）
        try:
            sanitize_filename(original_name)
        except HTTPException:
            logger.warning(f"[安全] 文件名验证失败: {original_name}")
            raise HTTPException(status_code=400, detail=f"文件名不合法: {original_name}")

        # 安全验证：检查文件扩展名
        if not original_name.lower().endswith('.pdf'):
            raise HTTPException(status_code=400, detail=f"仅支持 PDF 文件: {original_name}")

        # 安全验证：检查文件实际类型（PDF 文件头）
        if len(file_content) >= 5:
            if not file_content.startswith(b'%PDF-'):
                logger.warning(f"[安全] 文件类型不匹配: {original_name}, header={file_content[:20]}")
                raise HTTPException(status_code=400, detail=f"文件内容不是有效的 PDF: {original_name}")

        file_path = original_dir / original_name

        # 如果文件已存在，添加后缀避免覆盖
        if file_path.exists():
            base = Path(original_name).stem
            ext = Path(original_name).suffix
            counter = 1
            while file_path.exists():
                new_name = f"{base}_{counter}{ext}"
                sanitize_filename(new_name)  # 验证新文件名
                file_path = original_dir / new_name
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

    logger.info(f"[upload] success: {[f['name'] for f in uploaded_files]}")
    return {"success": True, "files": uploaded_files}


@router.delete("/{case_id}/file/{file_name}")
async def delete_file(case_id: str, file_name: str):
    """删除案件中的文件（同时删除 processed/ 和 md/ 中的对应文件）"""
    import urllib.parse
    file_name = urllib.parse.unquote(file_name)

    case_path = find_case_path(case_id)
    if not case_path:
        return {"success": False, "error": "案件不存在"}

    # 安全验证：检查文件名是否合法
    try:
        sanitize_filename(file_name)
    except HTTPException:
        logger.warning(f"[安全] 删除文件名验证失败: {file_name}")
        raise HTTPException(status_code=400, detail="文件名不合法")

    original_dir = case_path / "original"
    # 安全验证：使用 validate_path 确保路径在目录范围内
    try:
        file_path = validate_path(original_dir, file_name)
    except HTTPException:
        logger.warning(f"[安全] 文件路径验证失败: {file_name}")
        raise HTTPException(status_code=400, detail="文件路径不合法")

    if not file_path.exists():
        return {"success": False, "error": f"文件不存在：{file_name}"}

    # 删除 original 文件
    file_path.unlink()
    logger.info(f"[delete] removed original: {file_path}")

    # 同步删除 processed/ 中的对应文件
    stem = file_path.stem
    processed_dir = case_path / "processed"
    if processed_dir.exists():
        # 可能有 _去水印 后缀的变体
        for p in processed_dir.iterdir():
            if p.suffix == ".pdf" and p.stem == stem:
                p.unlink()
                logger.info(f"[delete] removed processed: {p}")

    # 同步删除 md/ 中的对应文件
    md_dir = case_path / "md"
    if md_dir.exists():
        for p in md_dir.iterdir():
            if p.suffix == ".md" and p.stem == stem:
                p.unlink()
                logger.info(f"[delete] removed md: {p}")

    return {"success": True, "message": f"已删除 {file_name}"}


@router.delete("/{case_id}/original-file/{file_name}")
async def delete_original_file_only(case_id: str, file_name: str):
    """仅删除 original/ 中的原始文件（处理成功后节省空间用）"""
    import urllib.parse
    file_name = urllib.parse.unquote(file_name)

    case_path = find_case_path(case_id)
    if not case_path:
        return {"success": False, "error": "案件不存在"}

    # 安全验证：检查文件名是否合法
    try:
        sanitize_filename(file_name)
    except HTTPException:
        logger.warning(f"[安全] 删除文件名验证失败: {file_name}")
        raise HTTPException(status_code=400, detail="文件名不合法")

    original_dir = case_path / "original"
    # 安全验证：使用 validate_path 确保路径在目录范围内
    try:
        file_path = validate_path(original_dir, file_name)
    except HTTPException:
        logger.warning(f"[安全] 文件路径验证失败: {file_name}")
        raise HTTPException(status_code=400, detail="文件路径不合法")

    if not file_path.exists():
        # 文件已不存在，视为成功
        return {"success": True, "message": "原始文件已不存在"}

    # 仅删除 original 文件
    file_path.unlink()
    logger.info(f"[delete] removed original only: {file_path}")

    return {"success": True, "message": f"已删除原始文件 {file_name}"}


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

    step: 0=原始文件(original/), 1=待转换PDF(processed/), 2=已转换MD(md/), 3=分析用MD(md/)
    兼容旧映射: step=2 旧拆分, step=3 旧转MD, step=4 旧分析
    """
    case_path = find_case_path(case_id)
    if not case_path:
        return []

    # 新简化流程映射
    if step == 0:
        input_dir = case_path / "original"
    elif step == 1:
        # 步骤1：证据提取，读取 processed/ 目录
        input_dir = case_path / "processed"
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

    # 对于步骤1，检查 md/ 中是否已有对应文件（标记为 done）
    md_dir = case_path / "md"

    def _has_md_file(pdf_file: Path, md_dir: Path) -> bool:
        """检查 md/ 中是否有对应 PDF 的 MD 文件（含 _去水印 后缀变体）"""
        if not md_dir.exists():
            return False
        stem = pdf_file.stem
        # 直接匹配同名 MD
        md_file = md_dir / f"{stem}.md"
        if md_file.exists():
            return True
        # 也检查带 _去水印 后缀的 PDF（去掉后缀匹配）
        if stem.endswith("_去水印"):
            base_stem = stem[:-4]  # 去掉 "_去水印"
            md_file = md_dir / f"{base_stem}.md"
            if md_file.exists():
                return True
        # 反向检查：MD 文件可能带或不带 _去水印 后缀
        for mf in md_dir.iterdir():
            if mf.is_file() and mf.suffix == ".md":
                md_stem = mf.stem
                # MD stem == PDF stem（完全匹配）
                if md_stem == stem:
                    return True
                # MD stem == PDF stem 去掉 _去水印
                if stem.endswith("_去水印") and md_stem == stem[:-4]:
                    return True
                # PDF stem == MD stem 去掉 _去水印
                if md_stem.endswith("_去水印") and stem == md_stem[:-4]:
                    return True
        return False

    for f in sorted(input_dir.iterdir(), key=natural_sort_key):
        if f.is_file() and f.suffix.lower() in allowed_suffixes:
            stat = f.stat()
            # 步骤1：检查 md/ 中是否已有对应 MD 文件
            # 步骤2/3：MD 文件已存在说明已转换完成，直接标记 done
            status = "pending"
            if step == 1 and md_dir.exists():
                if _has_md_file(f, md_dir):
                    status = "done"
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

        # 安全验证：检查所有文件名是否合法
        validated_file_names = []
        for fn in file_names:
            try:
                sanitize_filename(fn)
                validated_file_names.append(fn)
            except HTTPException:
                logger.warning(f"[安全] 批量处理文件名验证失败: {fn}")
                results.append({"file": fn, "success": False, "error": "文件名不合法"})
        file_names = validated_file_names

        # 确定哪些选项被启用
        do_watermark = remove_watermark if remove_watermark is not None else False
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
                    # 不处理水印但复制到 processed/，保证转MD模块有输入
                    target = output_dir / file_name  # 保持原名
                    shutil.copy2(str(input_file), str(target))
                    current_path = target

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
                        logger.warning(f"[DELETE] 已删除原始文件: {r['file']}")

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

{md_text[:150000]}

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
_EVIDENCE_SYSTEM_PROMPT = """你是刑事案卷审查专家，正在逐份审查案卷材料。
你需要提取详尽的证据内容，为后续分析提供完整信息。
"""

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

### 讯问/询问笔录类提取要求（重要：保留完整原文）

每份笔录必须包含以下信息：
- **讯问/询问时间**：精确到年月日时分
- **讯问/询问地点**：具体地址（如"江阴市公安局XX派出所XX讯问室"）
- **讯问/询问人**：姓名及职务
- **被讯问/被询问人**：姓名、身份证号、角色

**summary 要求：忠实记录证据内容，不做人物关系/矛盾分析（那些是后续分析阶段的任务）**

summary 字数建议 3000-8000 字（根据案件规模动态调整），字数应与证据的信息量匹配：
- 程序性文书（立案决定书、拘留证等）可精简至 100-300 字
- 讯问/询问笔录应详尽保留关键问答（建议 2000-5000 字）
- 鉴定意见保留鉴定方法和结论（500-1500 字）

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

### 矛盾提示提取（contradiction_hints）
提取本份证据内部或与其他证据可能存在的矛盾点，供后续矛盾分析参考。重点关注：
- **时间矛盾**：讯问时间与案发时间、各次供述时间前后不一
- **细节矛盾**：参与人、地点、工具、金额等关键细节在不同笔录中的差异
- **供述变化**：同一人多次讯问中供述内容的变化（如先否认后承认）
- **言词与书证矛盾**：口供与鉴定意见、书证记录不一致

格式：每条提示一行，`[矛盾类型] 具体描述`。例如：
- `[时间矛盾] 第一次讯问称案发时在家，第二次称在现场`
- `[供述变化] 首次否认动手，第三次承认持钢管戳击`
- `[细节矛盾] 供述称用木棍，鉴定意见显示为钢管`

**要求**：至少提取 1 条矛盾提示；确无明显矛盾时填"无"。

---

**输出要求：你的回答必须只包含一个 JSON 数组，不要有任何其他文字、符号或格式。**

JSON 数组格式：
```json
[
  {
    "name": "证据名称",
    "type": "物证/书证/证人证言/被害人陈述/犯罪嫌疑人供述和辩解/鉴定意见/勘验检查辨认笔录/视听资料、电子数据/程序性文书",
    "page_range": "页码范围",
    "persons": "涉案人员",
    "key_facts": "关键事实",
    "summary": "详细摘要（字数与证据信息量匹配，程序性文书可精简）",
    "original_quotes": "原文摘录（重要段落完整复制）",
    "contradiction_hints": "矛盾提示",
    "related_entities": "关联信息",
    "images": ["![](图片1.jpg)"]
  }
]
```

**关键要求：**
1. 你的回答必须只包含 JSON 数组，以 `[` 开始，以 `]` 结束
2. 不要包含 ```json 或任何代码块标记
3. 不要包含 Markdown 格式（如 ### 证据、- **类型** 等）
4. 直接输出 JSON，不要有任何前缀或后缀文字
5. **summary 字段必须详尽，字数与证据信息量匹配（讯问笔录详尽保留问答，程序性文书可精简）**

正确示例：`[{"name":"test","type":"书证","page_range":"1","persons":"","key_facts":"","summary":"","original_quotes":"","contradiction_hints":"","related_entities":"","images":[]}]`

错误示例（禁止）：
- `以下是 JSON：`
- ```json
- ### 证据1
- - **类型**：

请只输出 JSON 数组。"""


async def _extract_single_file(
    md_file: Path,
    md_text: str,
    temp_dir: Path,
    summary_target: str = "1500-3000字",
) -> tuple:
    """
    提取单个 MD 文件的证据（不含信号量和重试控制，由调用方管理）。

    返回：(md_filename, evidence_list)
    evidence_list 中每项包含证据数据，文件保存在 temp_dir 中。
    """
    from config_manager import load_config
    from llm_client import get_llm_client, get_model_context_limit
    client = get_llm_client()

    # 无重试的直接调用，超时由调用方控制
    timeout_seconds = 600  # 10 分钟

    # 检查模型上下文限制（仅用于日志和警告）
    config = load_config()
    model = config.get("llm_model", "")
    model_info = get_model_context_limit(model)

    # 固定最大字符数（单次 LLM 调用处理的最大长度）
    max_chars = 200_000  # 20万字符

    # 记录策略信息
    logger.info(f"[证据提取] 模型 {model}: 上下文 {model_info['limit_k']}, 策略 {model_info['strategy']}")

    # 大案件显示警告
    if len(md_text) > model_info.get("small_case_limit", 0) and model_info.get("warning"):
        logger.warning(f"[证据提取] {model_info['warning']}")

    # 分段提取：超长文件按段落边界切分，每段独立 LLM 调用，合并结果
    # 避免截断导致后半段证据丢失（如王作通讯问笔录在第2卷后半段被截断）
    all_evidence_blocks = []

    if len(md_text) <= max_chars:
        # 文件不长，单次提取
        chunks = [md_text]
    else:
        # 文件超长，按段落边界分段（优先在 ## 或 --- 处切分）
        logger.info(f"[证据提取] {md_file.name}: 文件过长（{len(md_text)} 字符），分段提取")
        chunk_size = max_chars
        chunks = []
        remaining = md_text
        while remaining:
            if len(remaining) <= chunk_size:
                chunks.append(remaining)
                break
            # 在 chunk_size 附近找最近的段落分隔符
            cut_pos = remaining.rfind('\n## ', 0, chunk_size)
            if cut_pos < chunk_size * 0.5:
                cut_pos = remaining.rfind('\n---', 0, chunk_size)
            if cut_pos < chunk_size * 0.5:
                cut_pos = remaining.rfind('\n\n', 0, chunk_size)
            if cut_pos < chunk_size * 0.5:
                cut_pos = chunk_size  # 找不到就硬切
            chunks.append(remaining[:cut_pos])
            remaining = remaining[cut_pos:].lstrip('\n')
        logger.info(f"[证据提取] {md_file.name}: 分为 {len(chunks)} 段（每段约 {max_chars} 字符）")

    # 对每段独立 LLM 提取
    for chunk_idx, chunk_text in enumerate(chunks):
        chunk_label = f"（第{chunk_idx+1}/{len(chunks)}段）" if len(chunks) > 1 else ""
        logger.info(f"[证据提取] {md_file.name}: 处理第 {chunk_idx+1}/{len(chunks)} 段（{len(chunk_text)} 字符）")

        # 动态替换提示词中的 summary 字数要求（按案件规模适配）
        dynamic_rules = _EVIDENCE_EXTRACTION_RULES.replace("3000-8000字", summary_target)

        result = await asyncio.wait_for(
            client.chat([
                {"role": "system", "content": _EVIDENCE_SYSTEM_PROMPT + "\n\n" + dynamic_rules},
                {"role": "user", "content": f"## 案卷文件：{md_file.name}{chunk_label}\n\n{chunk_text}"},
            ]),
            timeout=timeout_seconds,
        )

        chunk_blocks = _parse_evidence_blocks(result, md_file.name)
        all_evidence_blocks.extend(chunk_blocks)
        logger.info(f"[证据提取] {md_file.name}: 第 {chunk_idx+1} 段提取 {len(chunk_blocks)} 份证据")

    evidence_blocks = all_evidence_blocks
    logger.info(f"[证据提取] {md_file.name}: 共提取 {len(evidence_blocks)} 份证据（{len(chunks)} 段合并）")

    # 如果 LLM 没有按格式输出（整个文件作为一条证据），使用原始 MD 内容替代
    if len(evidence_blocks) == 1 and evidence_blocks[0].get("type") == "其他证据":
        logger.info(f"[证据提取] {md_file.name}: LLM 输出格式不正确，使用原始 MD 文件内容作为证据")
        evidence_blocks = [{
            "name": md_file.name.replace(".md", ""),
            "type": "原始文件",
            "source": md_file.name,
            "page_range": "",
            "persons": "",
            "key_facts": "",
            "summary": md_text,
            "original_quotes": "",
            "contradiction_hints": "",
            "related_entities": "",
            "raw_text": md_text,
            "needs_review": True,  # 标记需人工复核（LLM 解析失败的降级证据）
        }]

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

        # 如果是原始文件（LLM 提取失败），直接保存原始 MD 内容
        if ev_block.get("type") == "原始文件":
            ev_content = f"""# {ev_name}

| 项目 | 内容 |
|------|------|
| **证据类型** | 原始文件（LLM 提取失败） |
| **来源文件** | {ev_block['source']} |

> **注意**：此证据为原始 MD 文件内容，因 LLM 无法正确提取证据格式而保留原文。

---

{ev_block['raw_text']}"""
        else:
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

{ev_block.get('contradiction_hints', '无')}"""
        ev_path.write_text(ev_content, encoding="utf-8")

        evidence_list.append({
            "name": ev_name,
            "type": ev_block["type"],
            "source": md_file.name,
            "page_range": ev_block.get("page_range", ""),
            "persons": ev_block.get("persons", ""),
            "related_entities": ev_block.get("related_entities", ""),
            "key_facts": ev_block.get("key_facts", ""),
            "summary": ev_block.get("summary", ""),
            "summary_preview": ev_block["summary"][:200],
            "has_quotes": bool(ev_block.get("original_quotes", "").strip()),
            "needs_review": ev_block.get("needs_review", False),
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
    case_id: str = "",
    summary_target: str = "1500-3000字",
) -> tuple:
    """
    包装 _extract_single_file，管理信号量和重试。
    重试等待期间释放信号量，让其他文件获得并发机会。
    """
    max_retries = 2
    last_error = None

    def _report_retry(attempt: int, reason: str, wait: int):
        """上报重试状态到 EXTRACT_TASKS，让前端可见"""
        if not case_id:
            return
        task = EXTRACT_TASKS.get(case_id)
        if isinstance(task, dict):
            task["retry_count"] = attempt
            task["retry_reason"] = reason
            task["retry_wait_seconds"] = wait

    for attempt in range(1, max_retries + 1):
        # 获取信号量，执行提取
        async with semaphore:
            try:
                result = await _extract_single_file(md_file, md_text, temp_dir, summary_target)
                return result
            except asyncio.TimeoutError:
                last_error = "LLM 调用超时（600s）"
                logger.info(f"[证据提取] {md_file.name}: 第 {attempt} 次尝试超时")
                if attempt < max_retries:
                    _report_retry(attempt, "timeout", 10 * attempt)
            except Exception as e:
                last_error = str(e)
                error_msg = str(e).lower()
                if any(kw in error_msg for kw in ['429', 'rate limit', 'too many', 'quota']):
                    logger.info(f"[证据提取] {md_file.name}: 触发限流，退避后重试")
                    if attempt < max_retries:
                        _report_retry(attempt, "rate_limit", 30 * attempt)
                        # 信号量已释放（with 块退出），其他文件可继续
                        await asyncio.sleep(30 * attempt)
                        continue
                raise

        # 重试等待在信号量之外（其他文件可在此期间获得并发机会）
        if attempt < max_retries:
            logger.info(f"[证据提取] {md_file.name}: {attempt * 10}s 退避等待中...")
            _report_retry(attempt, "general_error", 10 * attempt)
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
        logger.error(f"[证据提取] case_id={case_id} 案件不存在，find_case_path 返回 None")
        raise HTTPException(status_code=404, detail="案件不存在")

    md_dir = case_path / "md"
    logger.info(f"[证据提取] case_id={case_id}, case_path={case_path}, md_dir={md_dir}, md_dir.exists={md_dir.exists()}")

    # 检查 MD 文件是否存在
    md_files = list(md_dir.glob("*.md")) if md_dir.exists() else []
    logger.info(f"[证据提取] md/ 目录中有 {len(md_files)} 个 MD 文件: {[f.name for f in md_files]}")

    if not md_files:
        logger.error(f"[证据提取] case_id={case_id} 案件中无 MD 文件，md_dir.exists={md_dir.exists()}")
        raise HTTPException(status_code=400, detail="案件中无 MD 文件，请先完成 PDF 转 MD")

    evidence_dir = case_path / "evidence"

    # 检查是否已有提取在运行中
    if EXTRACT_TASKS.get(case_id) == "running":
        raise HTTPException(status_code=409, detail="证据提取已在运行中")

    # 统计文件总数（用于进度显示）
    total_md_count = len(md_files)

    EXTRACT_TASKS[case_id] = {
        "status": "running",
        "total_files": total_md_count,
        "processed_files": 0,
        "current_file": "",
        "started_at": time.time(),
        "error_details": [],       # 结构化错误列表
        "stopped_by_user": False,  # 是否用户主动停止
        "recoverable": True,       # 是否可恢复
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
        # 成功完成后清除任务状态
        EXTRACT_TASKS.pop(case_id, None)
    except Exception as e:
        logger.exception("[证据提取] 后台任务失败: %s", e)
        # 记录结构化错误详情到 EXTRACT_TASKS，让前端轮询能拿到
        task = EXTRACT_TASKS.get(case_id)
        if isinstance(task, dict):
            task["status"] = "error"
            # 分类错误类型，提供用户友好的提示
            err_str = str(e)[:500]
            err_type = "unknown"
            hint = "请查看后端日志排查详情"
            err_lower = err_str.lower()
            if "timeout" in err_lower or "timed out" in err_lower:
                err_type = "timeout"
                hint = "LLM 响应超时，可能是模型负载高或文件过大，建议重试或减小并发数"
            elif "rate" in err_lower and "limit" in err_lower:
                err_type = "rate_limit"
                hint = "触发 API 限流，请降低并发数后重试"
            elif "401" in err_lower or "unauthorized" in err_lower or "api key" in err_lower:
                err_type = "auth_error"
                hint = "API Key 无效或已过期，请在设置页检查 LLM 配置"
            elif "connection" in err_lower or "refused" in err_lower or "unreachable" in err_lower:
                err_type = "network"
                hint = "无法连接 LLM 服务，请检查 Base URL 是否可达"
            elif "json" in err_lower or "parse" in err_lower:
                err_type = "parse_failure"
                hint = "LLM 输出解析失败，可能模型未按格式输出，建议重试"
            task["error_details"] = [{
                "type": err_type,
                "reason": "extract_failed",
                "message": err_str,
                "hint": hint,
                "recoverable": True,
            }]
            task["recoverable"] = True
        # 错误状态保留，不清除，让前端能读取到错误信息


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
        logger.info("[证据提取] MD 目录不存在！")

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

    # ── 动态计算 summary 字数要求（按案件规模适配 1M 上下文）──
    # 检测 MD 文件总字符数和文件数，分档设定 summary 字数
    all_md_for_stats = list(md_dir.glob("*.md")) if md_dir.exists() else []
    total_md_chars = 0
    for f in all_md_for_stats:
        try:
            total_md_chars += len(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    md_file_count = len(all_md_for_stats)
    if total_md_chars <= 200_000 or md_file_count <= 50:
        summary_target = "3000-5000字"
    elif total_md_chars <= 500_000 or md_file_count <= 150:
        summary_target = "1500-3000字"
    else:
        summary_target = "800-1500字"
    logger.info(f"[证据提取] 案件规模: {md_file_count} 个文件, {total_md_chars} 字符 → summary 要求 {summary_target}")

    # 清理上次中断遗留的临时文件
    old_temp = evidence_dir / "_temp_extract"
    if old_temp.exists():
        shutil.rmtree(old_temp)
        logger.info("[证据提取] 清理上次中断的临时目录")

    # 使用电源管理器防止休眠
    from power_manager import PowerInhibitor

    with PowerInhibitor(f"证据提取: {case_id}"):
        # 检查是否被取消
        if EXTRACT_TASKS.get(case_id) == "cancelled":
            logger.info("[证据提取] 任务已被取消")
            EXTRACT_TASKS.pop(case_id, None)
            return {"success": False, "error": "用户已停止提取", "case_id": case_id}

        # 排序辅助函数
        def _is_indictment(name: str, content: str = "") -> bool:
            # 文件名含明确的"起诉书"/"起诉意见书"关键词
            if ("起诉书" in name and "意见" not in name) or "起诉意见书" in name:
                return True
            # 文件名不匹配时检查文件内容前 5000 字符
            # 起诉意见书抬头通常在文件开头或某一段落
            if content:
                head = content[:5000]
                if "起诉意见书" in head or re.search(r'起\s*诉\s*书', head[:300]):
                    return True
            return False

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
        # 识别起诉书/起诉意见书文件：先查文件名，再查文件内容
        indictment_files = []
        other_files = []
        for f in all_md_files:
            try:
                content = f.read_text(encoding="utf-8")
            except Exception:
                content = ""
            if _is_indictment(f.name, content):
                indictment_files.append(f)
            else:
                other_files.append(f)

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
                        md_file, md_text, file_temp_dir, semaphore, case_id, summary_target
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
                        logger.info("[证据提取] 检测到取消信号，停止并发提取")
                        if gather_task and not gather_task.done():
                            gather_task.cancel()
                        return

            # 创建取消监视器
            watcher = asyncio.create_task(cancel_watcher())

            # 卡死检测监视器：如果超过 N 秒没有任何文件完成，发出警告
            last_progress_time = time.time()
            # 阈值需大于单文件 LLM 超时（600s），大案卷 LLM 可能 8-10 分钟，
            # 设 1200s（20 分钟）避免误杀正常长耗时任务。心跳每 30s 更新本时间戳，
            # 只要 LLM 在正常等待就不会触发；仅当心跳也卡住（event loop 阻塞等异常）才触发。
            stall_threshold = 1200

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
                        # 标记卡死取消原因，让前端能看到
                        task = EXTRACT_TASKS.get(case_id)
                        if isinstance(task, dict):
                            task["status"] = "error"
                            task["error_details"] = [{
                                "type": "stall_cancelled",
                                "reason": "stall_detected",
                                "message": f"超过 {stall_threshold // 60} 分钟无进展自动取消（可能是 LLM 响应卡住、event loop 阻塞或大文件处理超时）",
                                "hint": "建议检查 LLM 服务状态，或降低 evidence_concurrency 并发数后重试",
                                "recoverable": True,
                            }]
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
                logger.info("[证据提取] 并发提取被取消")
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
                            "needs_review": b.get("needs_review", False),
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
                        "key_facts": ev_data.get("key_facts", ""),
                        "summary": ev_data.get("summary", ""),
                        "summary_preview": ev_data["summary_preview"],
                        "has_quotes": ev_data["has_quotes"],
                        "needs_review": ev_data.get("needs_review", False),
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
                    "id": next_id,
                    "name": md_file.stem,
                    "type": classification["doc_name"],
                    "source": md_file.name,
                    "page_range": "",
                    "persons": "",
                    "related_entities": "",
                    "key_facts": "",
                    "summary_preview": f"{md_file.name}（待案卷分析时详细提取）",
                    "has_quotes": True,
                    "needs_review": False,
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
                    "id": next_id,
                    "name": ev_path.stem.split("_", 1)[1] if "_" in ev_path.stem else ev_path.stem,
                    "type": classification["doc_name"] if classification["doc_name"] != "混合文件" else "起诉意见书",
                    "source": md_file.name,
                    "page_range": "",
                    "persons": "",
                    "related_entities": "",
                    "key_facts": "",
                    "summary_preview": ev_text[:200],
                    "has_quotes": True,
                    "needs_review": False,
                    "md_file": ev_path.name,
                })
                next_id += 1

            # 更新进度
            task = EXTRACT_TASKS.get(case_id)
            if task:
                task["current_file"] = md_file.name
                task["processed_files"] = task.get("processed_files", 0) + 1

        # ── 过滤非证据类文书（封面/目录等案卷组织性材料）──
        skipped_documents = []
        filtered_evidence = []
        for ev in all_evidence:
            if _is_non_evidence_document(ev.get("name", ""), ev.get("type", "")):
                skipped_documents.append({
                    "name": ev.get("name", ""),
                    "type": ev.get("type", ""),
                    "source": ev.get("source", ""),
                    "md_file": ev.get("md_file", ""),
                    "reason": "非证据类文书（封面/目录）",
                })
                # 删除被跳过的证据 MD 文件
                md_to_remove = evidence_dir / ev.get("md_file", "")
                if md_to_remove.exists():
                    try:
                        md_to_remove.unlink()
                    except Exception:
                        pass
                logger.info(f"[证据提取] 跳过非证据文书: {ev.get('name', '')} (来源: {ev.get('source', '')})")
            else:
                filtered_evidence.append(ev)

        if skipped_documents:
            # 重新分配连续编号（跳过的文书不占编号），同步重命名 MD 文件和更新 md_file
            import re as _re
            for i, ev in enumerate(filtered_evidence, 1):
                old_id = ev["id"]
                ev["id"] = i
                old_md_file = ev.get("md_file", "")
                if old_md_file:
                    # 从旧文件名提取名称部分（去掉 NNN_ 前缀）
                    name_part = _re.sub(r'^\d+_', '', old_md_file)
                    new_md_file = f"{i:03d}_{name_part}"
                    old_path = evidence_dir / old_md_file
                    new_path = evidence_dir / new_md_file
                    if old_path.exists() and old_path != new_path:
                        try:
                            old_path.rename(new_path)
                        except Exception as re_err:
                            logger.warning(f"[证据提取] 重命名 {old_md_file} → {new_md_file} 失败: {re_err}")
                    ev["md_file"] = new_md_file
            logger.info(f"[证据提取] 过滤非证据文书 {len(skipped_documents)} 份，剩余 {len(filtered_evidence)} 份证据，已同步重编号")
            # 记录跳过的文书到独立文件，便于审计
            skipped_file = evidence_dir / "skipped_documents.json"
            skipped_file.write_text(json.dumps({
                "case_id": case_id,
                "skipped_count": len(skipped_documents),
                "documents": skipped_documents,
                "generated_at": datetime.now().isoformat(),
            }, ensure_ascii=False, indent=2), encoding="utf-8")

        all_evidence = filtered_evidence

        # ── 跨文件去重与关联（仅关联不合并，避免误删）──
        dedup_status = "ok"
        try:
            from evidence_dedup import dedup_and_link
            all_evidence = dedup_and_link(all_evidence)
        except Exception as de:
            import traceback
            dedup_status = f"failed: {str(de)[:200]}"
            logger.error(f"[证据提取] 去重关联失败: {de}\n{traceback.format_exc()}")

        # ── 证据分组（组合质证的前提）──
        evidence_groups = []
        try:
            from evidence_dedup import group_evidence_by_chain
            evidence_groups = group_evidence_by_chain(all_evidence)
        except Exception as ge:
            logger.warning(f"[证据提取] 证据分组失败: {ge}")

        # ── 最终保存 ──
        index_data = {
            "case_id": case_id,
            "total_evidence": len(all_evidence),
            "evidence": all_evidence,
            "evidence_groups": evidence_groups,
            "dedup_status": dedup_status,
            "generated_at": datetime.now().isoformat(),
        }
        # 如果提取结果为 0，记录可能的原因供前端展示
        if len(all_evidence) == 0:
            index_data["error_hint"] = "LLM 提取全部失败（详见后端日志），可能原因：API Key 无效、Base URL 不可达、模型名称错误、或所有 MD 文件解析失败"
        index_file.write_text(json.dumps(index_data, ensure_ascii=False, indent=2), encoding="utf-8")

        # 运行质量门禁检查，生成 quality_report.json
        try:
            from evidence_quality_gate import run_quality_gate
            run_quality_gate(evidence_dir, case_id)
        except Exception as qe:
            logger.warning(f"[证据提取] 质量门禁检查失败: {qe}")

        # 清理临时文件（无论走哪个分支都清理）
        old_temp = evidence_dir / "_temp_extract"
        if old_temp.exists():
            shutil.rmtree(old_temp)
            logger.info("[证据提取] 临时目录已清理")

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


class EvidenceReviewRequest(BaseModel):
    """证据校对请求体（带字段长度限制，防止滥用）"""
    name: Optional[str] = None
    type: Optional[str] = None
    persons: Optional[str] = None
    key_facts: Optional[str] = None
    contradiction_hints: Optional[str] = None

    # 字段长度约束
    @classmethod
    def validate_lengths(cls, values: dict) -> dict:
        if values.get("name") and len(values["name"]) > 100:
            raise HTTPException(status_code=400, detail="证据名称过长（最大 100 字符）")
        if values.get("type") and len(values["type"]) > 50:
            raise HTTPException(status_code=400, detail="证据类型过长（最大 50 字符）")
        if values.get("persons") and len(values["persons"]) > 500:
            raise HTTPException(status_code=400, detail="涉案人员过长（最大 500 字符）")
        if values.get("key_facts") and len(values["key_facts"]) > 2000:
            raise HTTPException(status_code=400, detail="关键事实过长（最大 2000 字符）")
        if values.get("contradiction_hints") and len(values["contradiction_hints"]) > 2000:
            raise HTTPException(status_code=400, detail="矛盾提示过长（最大 2000 字符）")
        return values


@router.put("/{case_id}/evidence/{evidence_id}/review")
async def review_evidence(case_id: str, evidence_id: int, body: EvidenceReviewRequest):
    """人工校对单条证据

    允许编辑 name/type/persons/key_facts/contradiction_hints 字段，
    同步更新 index.json 和证据 MD 文件的头部表格。
    """
    # 转为 dict 并校验长度
    body_dict = body.model_dump(exclude_none=True)
    body_dict = EvidenceReviewRequest.validate_lengths(body_dict)

    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    evidence_dir = case_path / "evidence"
    index_file = evidence_dir / "index.json"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="证据清单不存在")

    try:
        index_data = json.loads(index_file.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"证据清单读取失败: {e}")

    # 查找目标证据
    target_ev = None
    for ev in index_data.get("evidence", []):
        if ev.get("id") == evidence_id:
            target_ev = ev
            break

    if not target_ev:
        raise HTTPException(status_code=404, detail=f"证据 {evidence_id} 不存在")

    # 可编辑字段白名单（Pydantic 模型已限制，这里二次过滤防注入）
    editable_fields = {"name", "type", "persons", "key_facts", "contradiction_hints"}
    updates = {k: v for k, v in body_dict.items() if k in editable_fields}

    if not updates:
        raise HTTPException(status_code=400, detail="无可更新字段（允许：name/type/persons/key_facts/contradiction_hints）")

    # 更新 index.json 中的字段
    for k, v in updates.items():
        target_ev[k] = v
    target_ev["reviewed"] = True
    target_ev["reviewed_at"] = datetime.now().isoformat()

    # 重写证据 MD 文件的头部表格（保持下游解析稳定）
    md_file_name = target_ev.get("md_file", "")
    # 路径安全校验：md_file_name 必须是纯文件名，不能含路径分隔符或跳转
    md_path = None
    if md_file_name and "/" not in md_file_name and "\\" not in md_file_name and ".." not in md_file_name:
        candidate = evidence_dir / md_file_name
        # 二次校验：解析后路径必须在 evidence_dir 内
        try:
            candidate.resolve().relative_to(evidence_dir.resolve())
            if candidate.exists():
                md_path = candidate
        except ValueError:
            logger.warning(f"[证据校对] 路径越界: {md_file_name}")
    elif md_file_name:
        logger.warning(f"[证据校对] 非法 md_file 路径: {md_file_name}")

    if md_path:
        try:
            md_text = md_path.read_text(encoding="utf-8")
            new_name = target_ev.get("name", "")
            new_type = target_ev.get("type", "")
            new_source = target_ev.get("source", "")
            new_page = target_ev.get("page_range", "")
            new_persons = target_ev.get("persons", "")
            new_key_facts = target_ev.get("key_facts", "")
            new_hints = target_ev.get("contradiction_hints", "")

            # 保留原文从"## 关联信息"开始的内容（含后续所有段落）
            preserved = ""
            marker = "## 关联信息"
            marker_idx = md_text.find(marker)
            if marker_idx >= 0:
                preserved = md_text[marker_idx:]
            else:
                # 降级格式（原始文件）：保留"---"之后的内容
                sep_idx = md_text.find("\n---\n")
                if sep_idx >= 0:
                    preserved = md_text[sep_idx:]

            # 组装新 MD：标题 + 表格 + 保留段落
            new_md = f"""# {new_name}

| 项目 | 内容 |
|------|------|
| **证据类型** | {new_type} |
| **来源文件** | {new_source} |
| **页码范围** | {new_page or '未标注'} |
| **涉案人员** | {new_persons or '未识别'} |

{preserved}"""

            # 替换"## 关键事实"/"## 矛盾提示"段落内容（按段落标记定位）
            def _replace_section(text: str, header: str, new_content: str) -> str:
                """替换 Markdown 中指定 ## 段落的内容"""
                idx = text.find(header)
                if idx < 0:
                    return text  # 段落不存在，不处理
                line_end = text.find("\n", idx)
                if line_end < 0:
                    line_end = len(text)
                next_section = text.find("\n## ", line_end)
                if next_section < 0:
                    next_section = len(text)
                return text[:line_end] + f"\n\n{new_content}" + text[next_section:]

            if new_key_facts:
                new_md = _replace_section(new_md, "## 关键事实", new_key_facts)
            if new_hints:
                new_md = _replace_section(new_md, "## 矛盾提示", new_hints)

            md_path.write_text(new_md, encoding="utf-8")
        except Exception as e:
            logger.warning(f"[证据校对] 重写 MD 文件失败 {md_file_name}: {e}")

    # 写回 index.json
    index_file.write_text(json.dumps(index_data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"[证据校对] case={case_id} evidence={evidence_id} 已校对字段: {list(updates.keys())}")

    return {"success": True, "evidence_id": evidence_id, "updated_fields": list(updates.keys())}


@router.get("/{case_id}/evidence/{evidence_id}/review-status")
async def get_review_status(case_id: str, evidence_id: int):
    """获取单条证据的校对状态"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    index_file = case_path / "evidence" / "index.json"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="证据清单不存在")

    try:
        index_data = json.loads(index_file.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(status_code=500, detail="证据清单读取失败")

    for ev in index_data.get("evidence", []):
        if ev.get("id") == evidence_id:
            return {
                "evidence_id": evidence_id,
                "reviewed": ev.get("reviewed", False),
                "reviewed_at": ev.get("reviewed_at"),
                "needs_review": ev.get("needs_review", False),
            }
    raise HTTPException(status_code=404, detail=f"证据 {evidence_id} 不存在")


@router.get("/{case_id}/evidence-quality-report")
async def get_evidence_quality_report(case_id: str):
    """获取证据提取质量门禁报告"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    report_file = case_path / "evidence" / "quality_report.json"
    if not report_file.exists():
        return {"case_id": case_id, "overall_status": "not_run", "alerts": [], "stats": {}, "message": "质量门禁尚未运行"}
    try:
        return json.loads(report_file.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"质量报告读取失败: {e}")


@router.get("/{case_id}/evidence-graph")
async def get_evidence_graph(case_id: str):
    """获取证据关联图谱（基于 persons/contradiction_hints 生成 Mermaid）"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    evidence_dir = case_path / "evidence"
    if not evidence_dir.exists():
        raise HTTPException(status_code=404, detail="证据目录不存在")

    try:
        from evidence_graph import generate_evidence_graph
        return generate_evidence_graph(evidence_dir, case_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"图谱生成失败: {e}")


@router.get("/{case_id}/extract-status")
async def get_extract_status(case_id: str):
    """获取证据提取状态（含进度信息）"""
    task = EXTRACT_TASKS.get(case_id)
    if task == "cancelled":
        return {"case_id": case_id, "status": "cancelled"}
    if task and isinstance(task, dict):
        elapsed = time.time() - task.get("started_at", time.time())
        total = task.get("total_files", 0)
        processed = task.get("processed_files", 0)
        # ETA 预估：基于已处理文件均速，至少处理 2 个文件才计算（避免前期波动）
        eta_seconds = None
        if total > 0 and processed >= 2:
            avg_per_file = elapsed / processed
            remaining = total - processed
            eta_seconds = round(avg_per_file * remaining)
        result = {
            "case_id": case_id,
            "status": task.get("status", "running"),
            "total_files": total,
            "processed_files": processed,
            "current_file": task.get("current_file", ""),
            "elapsed_seconds": round(elapsed),
            "eta_seconds": eta_seconds,
            "llm_waiting": task.get("llm_waiting", False),
            "llm_latency_ms": task.get("llm_latency", 0),
            "retry_count": task.get("retry_count", 0),
            "retry_reason": task.get("retry_reason", ""),
            "retry_wait_seconds": task.get("retry_wait_seconds", 0),
            "stopped_by_user": task.get("stopped_by_user", False),
            "recoverable": task.get("recoverable", True),
        }
        # 传递错误详情给前端
        if task.get("error_details"):
            result["error_details"] = task["error_details"]
        elif task.get("error_detail"):
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
    task = EXTRACT_TASKS.get(case_id)
    if isinstance(task, dict):
        task["stopped_by_user"] = True
        task["recoverable"] = True
    EXTRACT_TASKS[case_id] = "cancelled"
    logger.info(f"[停止提取] 已取消 {case_id} 的提取任务，保留已提取的证据")
    return {"success": True, "message": "提取任务已取消"}


@router.post("/{case_id}/rebuild-evidence-links")
async def rebuild_evidence_links(case_id: str):
    """重建证据关联与分组（不重新提取证据）

    旧案件（index.json 缺 related_evidence_ids/evidence_groups）一键补关联。
    读取现有 index.json，重跑 dedup_and_link + group_evidence_by_chain，写回。
    """
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    evidence_dir = case_path / "evidence"
    index_file = evidence_dir / "index.json"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="证据清单不存在，请先提取证据")

    try:
        index_data = json.loads(index_file.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"证据清单读取失败: {e}")

    evidence_list = index_data.get("evidence", [])
    if not evidence_list:
        raise HTTPException(status_code=400, detail="证据列表为空，无法重建关联")

    # 清除旧的关联字段
    for ev in evidence_list:
        ev.pop("related_evidence_ids", None)
        ev.pop("dedup_note", None)
        ev.pop("duplicate_of", None)

    # 重跑去重关联
    dedup_status = "ok"
    try:
        from evidence_dedup import dedup_and_link, group_evidence_by_chain
        evidence_list = dedup_and_link(evidence_list)
        evidence_groups = group_evidence_by_chain(evidence_list)
    except Exception as de:
        import traceback
        dedup_status = f"failed: {str(de)[:200]}"
        evidence_groups = []
        logger.error(f"[重建关联] 失败: {de}\n{traceback.format_exc()}")

    index_data["evidence"] = evidence_list
    index_data["evidence_groups"] = evidence_groups
    index_data["dedup_status"] = dedup_status
    index_data["links_rebuilt_at"] = datetime.now().isoformat()
    index_file.write_text(json.dumps(index_data, ensure_ascii=False, indent=2), encoding="utf-8")

    related_count = sum(1 for e in evidence_list if e.get("related_evidence_ids"))
    logger.info(f"[重建关联] case={case_id} 关联证据 {related_count} 份，分组 {len(evidence_groups)} 个")

    return {
        "success": True,
        "case_id": case_id,
        "dedup_status": dedup_status,
        "related_evidence_count": related_count,
        "evidence_groups_count": len(evidence_groups),
        "groups": [{"group_id": g["group_id"], "group_label": g["group_label"], "member_count": len(g["member_refs"])} for g in evidence_groups],
    }


@router.post("/{case_id}/clear-evidence")
async def clear_evidence(case_id: str):
    """清除证据目录和卡死的提取任务状态，允许重新提取"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")

    # 标记为取消状态，让正在运行的提取任务退出
    task = EXTRACT_TASKS.get(case_id)
    if isinstance(task, dict):
        task["stopped_by_user"] = True
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

    # 同时删除关联的图片目录、JSON 目录、结构化 JSON 文件
    stem = md_file.stem
    for suffix in ("_images", "_json"):
        assoc_dir = md_dir / f"{stem}{suffix}"
        if assoc_dir.exists():
            shutil.rmtree(assoc_dir)
    for json_name in (f"{stem}_layout.json", f"{stem}_content_list.json", f"{stem}_middle.json"):
        assoc_json = md_dir / json_name
        if assoc_json.exists():
            assoc_json.unlink()

    # 返回对应的 PDF 文件名（用于重新转换）
    pdf_name = stem + ".pdf"
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

    # 同时删除对应的 MD 文件及关联产物
    stem = pdf_path.stem
    md_file = case_path / "md" / f"{stem}.md"
    if md_file.exists():
        md_file.unlink()
    for suffix in ("_images", "_json"):
        assoc_dir = case_path / "md" / f"{stem}{suffix}"
        if assoc_dir.exists():
            shutil.rmtree(assoc_dir)
    for json_name in (f"{stem}_layout.json", f"{stem}_content_list.json", f"{stem}_middle.json"):
        assoc_json = case_path / "md" / json_name
        if assoc_json.exists():
            assoc_json.unlink()

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
# 辅助函数从 case_manager_helpers 导入（向后兼容 re-export）
from case_manager_helpers import (  # noqa: F401
    _parse_evidence_blocks,
    _extract_field,
    _is_non_evidence_document,
    _sanitize_filename,
)


@router.post("/{case_id}/open-file")
async def open_file_endpoint(case_id: str, file_path: str):
    """打开文件（跨平台）- 支持 glob 模式"""
    import subprocess
    import sys
    import urllib.parse
    file_path = urllib.parse.unquote(file_path)

    case_root = find_case_path(case_id)
    if not case_root:
        return {"success": False, "error": "案件不存在"}

    case_root_resolved = str(Path(case_root).resolve())

    # 安全验证：禁止绝对路径
    if Path(file_path).is_absolute():
        logger.warning(f"[安全] 拒绝绝对路径: {file_path}")
        return {"success": False, "error": "拒绝访问：不允许绝对路径"}

    # 安全验证：禁止路径跳转
    if ".." in file_path:
        logger.warning(f"[安全] 拒绝路径跳转: {file_path}")
        return {"success": False, "error": "拒绝访问：不允许路径跳转"}

    # 安全验证：glob 模式只允许安全字符（中文、字母、数字、下划线、横杠、点、星号、问号、方括号）
    # 注意：星号(*)、问号(?)、方括号([]) 是 glob 通配符
    if not re.match(r'^[\w\-\.\*\?\[\]一-龥/\\]+$', file_path):
        logger.warning(f"[安全] glob 模式包含非法字符: {file_path}")
        return {"success": False, "error": "拒绝访问：路径包含非法字符"}

    # 构建安全的 glob 模式：基于案件目录
    safe_pattern = str(Path(case_root) / file_path)

    # 尝试 glob 匹配
    matched = glob.glob(safe_pattern)
    if not matched:
        return {"success": False, "error": f"文件不存在：{file_path}"}

    actual_path = str(Path(matched[0]).resolve())

    # 安全验证：文件必须在案件目录内（双重检查）
    if not actual_path.startswith(case_root_resolved):
        logger.warning(f"[安全] 文件路径越界: {actual_path} 不在 {case_root_resolved}")
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

    from fastapi import HTTPException
    from fastapi.responses import FileResponse

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

    # 根据后缀推断 media_type，避免图片等二进制文件被当作 markdown 返回
    suffix = fp.suffix.lower()
    media_type_map = {
        ".pdf": "application/pdf",
        ".md": "text/markdown",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        ".json": "application/json",
    }
    media_type = media_type_map.get(suffix, "application/octet-stream")
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

    from fastapi import HTTPException
    from fastapi.responses import FileResponse

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
