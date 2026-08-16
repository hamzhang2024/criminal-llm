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
import tiktoken

logger = logging.getLogger(__name__)
from fastapi import APIRouter, UploadFile, File, HTTPException, Request, Body
from doc_classifier import classify_evidence_item

# 证据结构化字段列表（用于传递和写入 index.json）
EVIDENCE_STRUCTURED_FIELDS = ["key_facts", "summary", "original_quotes", "contradiction_hints", "fund_flows"]
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

# 选择性 OCR 后台任务状态
# 结构: { case_id: { "status": "running"/"completed"/"failed", "done": N, "total": N, "current": "卷名", "failed": [] } }
OCR_TASKS: Dict[str, dict] = {}

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


def _parse_charges_from_name(case_name: str, defendant: str = "") -> list:
    """从案件文件夹名推断罪名列表。

    策略：先移除嫌疑人姓名、"涉嫌"、"案件"前缀和尾部日期，再按"罪"切分。
    例如 "案件_冯叶飞涉嫌非法经营罪诈骗罪_20260815" → ["非法经营罪", "诈骗罪"]
    """
    clean_name = case_name
    if defendant:
        clean_name = clean_name.replace(defendant, "")
    clean_name = clean_name.replace("涉嫌", "").replace("、", "").replace("_", "")
    # 剥离"案件"前缀，避免混入罪名（如 "案件非法经营罪"）
    clean_name = re.sub(r'^案件', '', clean_name)
    # 剥离尾部 6-8 位连续数字（日期后缀，如 20260815），避免解析出 "20260815罪"
    clean_name = re.sub(r'\d{6,8}$', '', clean_name)

    if not clean_name:
        return []
    # 按"罪"切分：["诈骗", "非法经营", ""] → ["诈骗罪", "非法经营罪"]
    parts = re.split(r'罪', clean_name)
    case_charges = [p + '罪' for p in parts if p and len(p) >= 2]
    case_charges = [c for c in case_charges if c not in ('犯罪', '罪犯')]
    return case_charges


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
    charges: list = []
    created_at: str
    status: str
    file_count: int = 0
    case_dir: str  # 案件文件夹路径
    search_keywords: list = []     # 律师确认的类案检索关键词
    suggested_keywords: list = []  # LLM 推荐的类案检索关键词


class CreateCaseRequest(BaseModel):
    name: str
    defendant: str
    owner: Optional[str] = None  # 创建者邮箱
    charges: List[str] = []  # 指控罪名列表（如 ["诈骗罪", "职务侵占罪"]）


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

                            # 补全缺失字段(兼容旧 case.json)
                            if 'case_dir' not in metadata:
                                metadata['case_dir'] = str(sub)

                            # 计算各阶段文件数量
                            # 只统计 original/ + processed/ + md/ 下的文件（和前端 useCaseFiles 一致）
                            file_count = 0
                            for subdir_name in ("original", "processed", "md"):
                                subdir_path = sub / subdir_name
                                if subdir_path.exists():
                                    file_count += sum(1 for _ in subdir_path.iterdir() if _.is_file())

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
    """创建新案件（同名同被告人同owner不重复创建）"""
    # 去重检查：遍历已有案件，同名同被告人同owner则返回已有
    for existing in scan_cases(owner=request.owner):
        if existing.name == request.name and existing.defendant == request.defendant:
            return existing

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
        "charges": request.charges or [],
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


@router.patch("/{case_id}")
async def update_case(case_id: str, request: Request):
    """更新案件信息（支持 charges、search_keywords）"""
    import json as _json
    body = await request.json()
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")
    meta_file = case_path / "case.json"
    if not meta_file.exists():
        raise HTTPException(status_code=404, detail="案件元数据不存在")
    with open(meta_file, 'r', encoding='utf-8') as f:
        meta = json.load(f)
    # 仅更新请求中携带的字段，避免单字段更新时误清其他字段
    if "charges" in body:
        # 输入校验
        meta["charges"] = [c.strip()[:100] for c in body["charges"] if isinstance(c, str) and c.strip()][:20]
    if "search_keywords" in body:
        # 类案检索关键词（律师编辑确认）
        meta["search_keywords"] = [k.strip()[:50] for k in body["search_keywords"] if isinstance(k, str) and k.strip()][:30]
    # 原子写入：先写临时文件再 rename，防止并发写入导致数据损坏
    import tempfile
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(case_path), suffix='.json')
    try:
        with os.fdopen(tmp_fd, 'w', encoding='utf-8') as tmp_f:
            json.dump(meta, tmp_f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, str(meta_file))
    except Exception:
        try: os.unlink(tmp_path)
        except OSError: pass
        raise
    logger.info(f"[案件更新] {case_id}: charges={meta.get('charges', [])} search_keywords={meta.get('search_keywords', [])}")
    return {"success": True, "charges": meta.get("charges", []), "search_keywords": meta.get("search_keywords", [])}


@router.post("/import")
async def import_folder(folder_path: str, name: str, defendant: str, charges: List[str] = Body(default=[])) -> CaseInfo:
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
        "charges": charges or [],
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


@router.delete("/{case_id}/original-file/{file_name}")
async def delete_original_file_only(case_id: str, file_name: str):
    """仅删除 original/ 中的原始文件（处理成功后节省空间用）"""
    import urllib.parse
    file_name = urllib.parse.unquote(file_name)

    case_path = find_case_path(case_id)
    if not case_path:
        return {"success": False, "error": "案件不存在"}

    original_dir = case_path / "original"
    file_path = original_dir / file_name
    if not file_path.exists():
        # 文件已不存在，视为成功
        return {"success": True, "message": f"原始文件已不存在"}

    # 仅删除 original 文件
    file_path.unlink()
    print(f"[delete] removed original only: {file_path}")

    return {"success": True, "message": f"已删除原始文件 {file_name}"}


@router.get("/{case_id}/files")
async def list_case_files(case_id: str):
    """列出案件的所有文件"""
    case_path = find_case_path(case_id)
    if not case_path:
        return []
    
    files = []

    # 扫描 original/ 目录（未处理原始文件，pending）
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

    # 扫描 processed/ 目录（去水印后待转换，done——original 可能已被清理，
    # 前端 files 全 done 才能激活「提取证据」按钮）
    processed_dir = case_path / "processed"
    if processed_dir.exists():
        for pdf in sorted(processed_dir.glob("*.pdf"), key=natural_sort_key):
            stat = pdf.stat()
            files.append({
                "id": f"file_{pdf.stem}",
                "name": pdf.name,
                "size": stat.st_size,
                "status": "done",
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


# 原文全文定位调用的输入预算（字符数）
_FULLTEXT_LOCATE_BUDGET = 60000


# 原文全文切片最小长度（字符数）：低于此长度视为定位失败（LLM 可能只定位到残片）
_MIN_FULLTEXT_SLICE_CHARS = 200


def _slice_section_by_markers(raw_text: str, first_line: str, last_line: str) -> str | None:
    """按原文首行/末行切片（LLM 只定位，文本不经转述）。找不到、顺序颠倒或切片过短返回 None"""
    first_line = first_line.strip()
    last_line = last_line.strip()
    if not first_line or not last_line:
        return None
    start = raw_text.find(first_line)
    if start < 0:
        return None
    end = raw_text.find(last_line, start)
    if end < 0:
        return None
    end += len(last_line)
    sliced = raw_text[start:end].strip()
    if len(sliced) < _MIN_FULLTEXT_SLICE_CHARS:
        return None
    return sliced


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

{md_text}

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

    # 原文全文切片：LLM 只给首行/末行定位，代码从原文切（不经转述）
    fulltext_section = ""
    try:
        locate = await client.chat([
            {"role": "system", "content": "你是案卷整理员。"},
            {"role": "user", "content": f"给定以下文件内容，找出其中《{doc_type}》正文的**第一行原文**（首行）和**最后一行原文**（末行）（逐字引用，不要改写）。只输出两行：\n首行：xxx\n末行：xxx\n\n文件内容：\n{md_text[:_FULLTEXT_LOCATE_BUDGET]}"},
        ])
        first_line = last_line = ""
        for line in locate.strip().split("\n"):
            if line.startswith("首行"):
                first_line = re.split(r"[：:]", line, maxsplit=1)[-1].strip()
            elif line.startswith("末行"):
                last_line = re.split(r"[：:]", line, maxsplit=1)[-1].strip()
        sliced = _slice_section_by_markers(md_text, first_line, last_line)
        if sliced:
            fulltext_section = f"\n\n## 原文全文\n\n{sliced}\n"
        else:
            logger.warning(f"[证据提取] {md_file.name}: 起诉书原文定位失败，仅保留结构化提取")
    except Exception as e:
        logger.warning(f"[证据提取] {md_file.name}: 起诉书原文切片失败（不影响结构化提取）: {e}")

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
{fulltext_section}"""
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

### 封面/目录/三面照

封面（刑事侦查卷宗信息）、卷内文书目录、封底、备考表：照常提取为独立条目（如"卷宗封面""卷内目录"），它们会在后续被标注为非证据，但必须保留在提取结果中以保证案卷完整性。
嫌疑人三面照/照片是证据，提取为"嫌疑人三面照"。

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

**识别讯问边界的规则（重要）：**
- 每出现一次"被讯问人：XXX"或"被询问人：XXX"字段，就是一份新的独立笔录
- 即使多次笔录的标题相同（如都是"## 讯问笔录"），只要"被讯问人"字段重复出现，就必须拆分为多份
- 标题可能不统一（"讯问笔录"/"讯 问 笔 录"/"询问/讯问笔录"），以"被讯问人"字段为准识别边界
- 同一人的第N次笔录，在证据名称中标注次序（如"张某第三次讯问笔录"）

### 讯问/询问笔录类提取要求

每份笔录必须包含以下信息：
- **讯问/询问时间**：精确到年月日时分
- **讯问/询问地点**：具体地址（如"江阴市公安局XX派出所XX讯问室"）
- **讯问/询问人**：姓名及职务
- **被讯问/被询问人**：姓名、身份证号、角色
- **笔录全文要点（原文摘录要求）：以问答形式完整保留全部问答原文**——从第一问第一答到最后一问最后一答，不得筛选、不得省略、不得概括。只有与案情完全无关的程序性问答（如告知权利义务的固定问答）可省略，并标注"[程序性问答略]"。特别注意：
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

### 附属程序文书处理

以下文书是附属程序文书，不是独立证据，应与主证据合并提取或概括提及：
- 询问通知书、拘留通知书、逮捕通知书 → 附属，不单独提取
- 扣押清单、接受证据材料清单 → 附属，不单独提取
- 权利义务告知书 → 附属，不单独提取
- 释放通知书 → 附属，不单独提取
- 提讯证、移送起诉告知书 → 附属，不单独提取

### 证据合并规则

以下情况应合并为一份证据：
- 扣押笔录 + 扣押清单 + 扣押决定书 → 合并为"扣押手续"
- 询问通知书 + 询问笔录 → 合并为"询问笔录（附通知书）"
- 受案登记表 + 立案决定书 → 合并为"受案立案手续"
- 拘留证 + 拘留通知书 → 合并为"拘留手续"
- 逮捕证 + 逮捕通知书 → 合并为"逮捕手续"
- 取保候审决定书 + 被取保候审人义务告知书 → 合并为"取保手续"

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
- **涉案人员**：[列出涉及的人员姓名及角色，区分主从犯/证人/被害人等]
- **关键事实**：[按时间顺序列出关键事实，每条带"时间+主体+行为+结果"，保留具体金额、时间、地点等数据，不少于3条]
- **详细摘要**：[对讯问/询问笔录，用问答形式保留关键原文摘录（不少于3个问答对）；对书证，列明具体数据；对文书，概括核心内容]
- **原文摘录**：[关键原文的直接引用，不少于2段，标注页码或原文位置。对起诉意见书/起诉书，必须摘录每笔犯罪事实的完整原文段落（包括时间、地点、人员、行为、金额），不得概括简化]
- **矛盾提示**：[供述前后是否一致？有无自相矛盾之处？]
- **关联信息**：[列出所有关键关联信息，见上方"关键关联信息提取"要求。如无则填"无"]
- **资金往来**：[仅当本证据涉及资金往来时列出，每笔一条"转出人→转入人｜金额｜时间｜账号/渠道｜用途"；如无则填"无"]

**注意（起诉意见书/起诉书/多次供述专用）：**
- 必须逐笔提取全部犯罪事实，每笔必须包含：**时间、地点（详细到门牌号/街道）、涉案人员及角色、行为方式、金额/结果、简要案情**
- **地址必须从原文提取出来，不得写"不详"或"未提及"**
- **多笔犯罪必须逐笔提取，不得合并概括。但多笔犯罪事实仍属于同一份证据记录，在"详细摘要"中逐笔列出即可，不需要拆分为多条证据**

**注意（讯问/询问笔录专用）：**
- **每次讯问/询问 = 一份独立证据，不得合并**
- 每份笔录必须包含：**讯问时间、讯问地点、讯问人、被讯问人、关键问答、涉及案件事实的详细内容（时间+地点+人物+事件）**
- **禁止任何形式的省略或概括**：
  - 不得写"后续还有多次讯问笔录，因篇幅限制仅展示关键几份"
  - 不得写"仅展示关键证据"或"省略部分内容"
  - 不得因"篇幅限制"、"空间不足"、"内容过长"等原因省略任何证据
  - **必须逐份提取每一份讯问笔录、每一份询问笔录，无论数量多少**

**【最高优先级】必须提取所有证据**：

- **你必须提取文件中的每一份独立文书/笔录，不得遗漏任何一份**
- 如果文件包含 20 份讯问笔录，你必须输出 20 份证据；如果包含 30 份，你必须输出 30 份
- **严禁以"代表性""关键""典型"等理由只提取部分证据**
- **严禁分批输出**：你必须在当前回复中一次性输出所有证据
- **严禁输出以下任何表述**：
  - "后续还有...将继续提取"
  - "因篇幅限制，省略部分"
  - "仅展示关键/代表性证据"
  - 或任何暗示省略、分批的措辞
- **如果你发现证据数量与文件中的实际文书数量不符，必须补充遗漏的证据**

**完成确认**：
- 在所有证据输出完毕后，必须在最后一行添加：
  `[完成确认] 本文件共提取 N 份证据，全部输出完毕`

注意：
- 如果文件包含多份独立文书（如起诉意见书+告知书），分别提取为多份证据
- **保持原文的关键细节，不要过度概括**
- 页码引用必须准确
- 金额、时间、人名等数据必须精确，不要用"约"、"左右"等模糊词
- **关联信息是重点**：手机号、微信号、银行账号、车牌号等是证据互相关联印证的关键线索，务必逐一提取"""


def _format_key_facts(key_facts) -> str:
    """关键事实渲染：list → 编号多行文本；字符串原样；空 → 无
    （模板直接插值 list 会显示 Python repr ['...', '...']）"""
    if not key_facts:
        return "无"
    if isinstance(key_facts, list):
        return "\n".join(f"{i}. {f}" for i, f in enumerate(key_facts, 1))
    return str(key_facts)


async def _extract_single_file(
    md_file: Path,
    md_text: str,
    temp_dir: Path,
    charges: list = None,
    framework_prefix: str = "",
    progress_cb=None,
    skip_names: set = None,
) -> tuple:
    """
    提取单个 MD 文件的证据（不含信号量和重试控制，由调用方管理）。

    progress_cb(done, total)：按份提取时每份笔录完成后回调（前端卷内进度条）。
    skip_names：卷内已存在于 index.json 的文书名集合（失败重提时按名称跳过，避免重复提取）。

    返回：(md_filename, evidence_list)
    evidence_list 中每项包含证据数据，文件保存在 temp_dir 中。
    """
    from llm_client import get_llm_client, LLMRetryExhaustedError
    from doc_classifier import classify_evidence_item
    client = get_llm_client()

    # 不做预过滤，保留完整原始内容（封面、目录等由LLM自行判断）
    # 无重试的直接调用，超时由调用方控制
    timeout_seconds = 600  # 10 分钟

    # 罪名上下文（传给 LLM 做证据-罪名关联）
    charges_str = ""
    if charges:
        charges_str = f"当前案件指控罪名：{'、'.join(charges)}"

    # 按 token 预算分块（用 tiktoken 精确计算，按 ## 标题边界拆分）
    import context_budget
    # 字符预算转 token 预算（分块按 tiktoken 计数）：与统一公式保持一致
    content_budget = int(context_budget.content_budget_chars() / context_budget.CHARS_PER_TOKEN)
    if content_budget < 50000:
        content_budget = 50000  # 最少保证 50K tokens

    chunks = _split_content_by_tokens(md_text, content_budget, md_file.name)

    # 块缓存失效机制：预算或文本长度变化后，旧块缓存（按块下标键控）会错配，需全部失效
    meta_file = temp_dir / "_chunking_meta.json"
    current_meta = {"budget": content_budget, "text_len": len(md_text), "chunks": len(chunks)}
    if len(chunks) > 1 and meta_file.exists():
        stale = True
        try:
            stale = json.loads(meta_file.read_text(encoding="utf-8")) != current_meta
        except Exception:
            stale = True  # meta 损坏视为失效
        if stale:
            for f in temp_dir.glob(".chunk_*.done"):
                f.unlink(missing_ok=True)
            for f in temp_dir.glob("_chunk_*_blocks.json"):
                f.unlink(missing_ok=True)
            logger.info(f"[证据提取] {md_file.name}: 分块参数变化，旧块缓存已失效，重新提取")
    try:
        meta_file.write_text(json.dumps(current_meta, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

    # 统计讯问笔录次数，提示LLM逐份提取
    interrog_count = len(re.findall(r'被讯问人[：:]\s*\S', md_text))
    ask_count = len(re.findall(r'被询问人[：:]\s*\S', md_text))
    total_count = interrog_count + ask_count
    hint = ""
    if total_count > 0:
        hint = (
            f"\n\n**⚠️ 本文件检测到 {interrog_count} 次讯问 + {ask_count} 次询问 = {total_count} 份独立笔录证据。**\n"
            f"**❗ 你必须在本次回复中完整输出全部 {total_count} 份笔录证据，禁止分批、禁止省略、禁止推迟到后续回复。**\n"
            f"**❗ 输出完毕后必须确认：[完成确认] 本文件共提取 N 份证据，全部输出完毕**\n"
        )

    # 多笔录文件走两级按份提取：整卷一次调用时"全部笔录全文保留"物理不可达，
    # LLM 会保数量砍内容（占位符敷衍）。按份提取 + 目录日期校验可根治
    evidence_blocks = None
    if total_count >= 2:
        try:
            from evidence_perdoc import extract_by_document
            evidence_blocks = await extract_by_document(client, md_file, md_text, charges_str, temp_dir, progress_cb=progress_cb, skip_names=skip_names)
            if evidence_blocks:
                logger.info(f"[证据提取] {md_file.name}: 按份提取产出 {len(evidence_blocks)} 份证据")
        except Exception as e:
            logger.warning(f"[证据提取] {md_file.name}: 按份提取异常，回退整卷路径: {e}")
            evidence_blocks = None

    if evidence_blocks is None and len(chunks) == 1:
        # 单块，直接发送
        result = await asyncio.wait_for(
            client.chat([
                {"role": "system", "content": _EVIDENCE_SYSTEM_PROMPT + "\n\n" + _EVIDENCE_EXTRACTION_RULES + framework_prefix},
                {"role": "user", "content": f"## 案卷文件：{md_file.name}\n\n{charges_str}{hint}\n\n{md_text}"},
            ]),
            timeout=timeout_seconds,
        )
        evidence_blocks = _parse_evidence_blocks(result, md_file.name)
    elif evidence_blocks is None:
        # 多块并发提取（块级断点续传：已完成块跳过），合并保持块顺序
        chunk_sem = asyncio.Semaphore(2)

        async def extract_chunk(ci: int, chunk: dict) -> list:
            chunk_label = chunk["label"]
            done_marker = temp_dir / f".chunk_{ci}.done"
            blocks_file = temp_dir / f"_chunk_{ci}_blocks.json"
            if done_marker.exists() and blocks_file.exists():
                try:
                    cached = json.loads(blocks_file.read_text(encoding="utf-8"))
                    logger.info(f"[证据提取] {chunk_label}: 已完成，跳过（缓存 {len(cached)} 份）")
                    return cached
                except Exception:
                    pass  # 缓存损坏则重提该块
            async with chunk_sem:
                chunk_text = chunk["text"]
                logger.info(f"[证据提取] {chunk_label}: 发送 {_count_tokens(chunk_text)} tokens")
                result = await asyncio.wait_for(
                    client.chat([
                        {"role": "system", "content": _EVIDENCE_SYSTEM_PROMPT + "\n\n" + _EVIDENCE_EXTRACTION_RULES + framework_prefix},
                        {"role": "user", "content": f"## 案卷文件：{chunk_label}\n\n{charges_str}\n\n{chunk_text}"},
                    ]),
                    timeout=timeout_seconds,
                )
            blocks = _parse_evidence_blocks(result, chunk_label)
            logger.info(f"[证据提取] {chunk_label}: 提取 {len(blocks)} 份证据")
            try:
                blocks_file.write_text(json.dumps(blocks, ensure_ascii=False), encoding="utf-8")
                done_marker.write_text("", encoding="utf-8")
            except Exception:
                pass
            return blocks

        chunk_results = await asyncio.gather(*[extract_chunk(ci, c) for ci, c in enumerate(chunks)])
        all_evidence_blocks = [b for blocks in chunk_results for b in blocks]
        evidence_blocks = _merge_evidence_blocks(all_evidence_blocks)
        logger.info(f"[证据提取] {md_file.name}: {len(chunks)} 块合并后 {len(evidence_blocks)} 份证据")

    # 调试：将解析结果保存到 debug 文件
    debug_file = temp_dir / f"_debug_{md_file.stem}.txt"
    try:
        debug_file.write_text(f"=== 解析结果 ({len(evidence_blocks)} 份证据) ===\n\n" + "\n---\n".join(
            f"[{e.get('name','')}]\n{e.get('raw_text','')}" for e in evidence_blocks
        ), encoding="utf-8")
    except Exception:
        pass
    logger.info(f"[证据提取] {md_file.name}: 解析为 {len(evidence_blocks)} 份证据")

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
| **涉案人员** | {ev_block.get('persons', '未识别')} |
| **关联要件** | {'、'.join(ev_block.get('elements', [])) or '无'} |

## 关联信息

{ev_block.get('related_entities', '无')}

## 关键事实

{_format_key_facts(ev_block.get('key_facts'))}

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
            "doc_type": classify_evidence_item(ev_name),
            "page_range": ev_block.get("page_range", ""),
            "persons": ev_block.get("persons", ""),
            "related_entities": ev_block.get("related_entities", ""),
            "charges": ev_block.get("charges", []),
            "elements": ev_block.get("elements", []),
            "proves_facts": ev_block.get("proves_facts", []),
            "proves_details": ev_block.get("proves_details", {}),
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
    charges: list = None,
    framework_prefix: str = "",
    progress_cb=None,
    skip_names: set = None,
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
                result = await _extract_single_file(md_file, md_text, temp_dir, charges, framework_prefix, progress_cb=progress_cb, skip_names=skip_names)
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
        "phase": "extracting",       # extracting（提取中）| summarizing（摘要中）
        "total_files": total_md_count,
        "processed_files": 0,
        "current_file": "",
        "current_file_done": 0,      # 当前卷内已完成的笔录份数（按份提取）
        "current_file_total": 0,     # 当前卷笔录总份数
        "summary_done": 0,           # 摘要阶段已完成份数
        "summary_total": 0,          # 摘要阶段总份数
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
            task["error_details"] = [{
                "reason": "extract_failed",
                "message": str(e)[:500],
                "recoverable": True,
            }]
            task["recoverable"] = True
        # 错误状态保留，不清除，让前端能读取到错误信息


def _locate_evidence_file(evidence_dir: Path, md_name: str) -> Optional[Path]:
    """定位证据文件：优先按 index.json 记录名，兼容实际文件名无数字前缀的情况"""
    p = evidence_dir / md_name
    if p.exists():
        return p
    stripped = re.sub(r"^\d+_", "", md_name)
    if stripped != md_name:
        alt = evidence_dir / stripped
        if alt.exists():
            return alt
    return None


def _is_failed_evidence_entry(evidence_dir: Path, ev: dict) -> bool:
    """判定证据条目是否为按份提取失败的空壳（文件存在且内容含失败标记）"""
    md_name = ev.get("md_file", "")
    if not md_name:
        return False
    md_path = _locate_evidence_file(evidence_dir, md_name)
    if md_path is None:
        return False  # 文件不存在则无法确认失败，保守保留
    try:
        return "按份提取失败" in md_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False


def prune_failed_evidence(case_path: Path) -> list:
    """清理按份提取失败的空壳证据条目，返回被移除的文书名列表

    按份提取失败的文书会留下空壳证据文件（头部 + "按份提取失败"标记），
    且 index.json 中保留条目，导致文件级断点续传误判该卷已处理、
    失败文档永远不被重试。本函数移除这些条目并删除空壳文件（含其摘要缓存），
    使所属卷在下次提取时重新进入待提取（卷内已成功文书按名称跳过，不重复提取）。
    """
    evidence_dir = case_path / "evidence"
    index_file = evidence_dir / "index.json"
    if not index_file.exists():
        return []
    try:
        index_data = json.loads(index_file.read_text(encoding="utf-8"))
    except Exception:
        return []

    kept, failed = [], []
    for ev in index_data.get("evidence", []):
        (failed if _is_failed_evidence_entry(evidence_dir, ev) else kept).append(ev)
    if not failed:
        return []

    index_data["evidence"] = kept
    index_data["total_evidence"] = len(kept)
    index_file.write_text(json.dumps(index_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 删除空壳证据文件及其摘要缓存（若有）
    summaries_dir = evidence_dir / "summaries"
    for ev in failed:
        md_name = ev.get("md_file", "")
        if not md_name:
            continue
        # 安全：index.json 可能被污染（md_file 含 ../），非纯文件名只移条目不删文件，
        # 防误删 evidence 目录外文件
        if Path(md_name).name != md_name:
            logger.warning(f"[证据提取] 跳过非法 md_file（含路径分隔符）: {md_name}")
            continue
        md_path = _locate_evidence_file(evidence_dir, md_name)
        if md_path is not None:
            md_path.unlink(missing_ok=True)
        (summaries_dir / md_name).unlink(missing_ok=True)
        (summaries_dir / (Path(md_name).stem + ".meta.json")).unlink(missing_ok=True)

    removed_names = [ev.get("name", "") for ev in failed]
    logger.info(f"[证据提取] 清理 {len(failed)} 份失败空壳证据: {removed_names}")
    return removed_names


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

    # 读取案件 charges（多罪名支持）
    case_charges = []
    case_json = case_path / "case.json"
    if case_json.exists():
        try:
            case_meta = json.loads(case_json.read_text(encoding="utf-8"))
            case_charges = case_meta.get("charges", []) or []
        except Exception:
            pass

    # 如果 case_charges 为空，从案件名称推断可能的罪名
    if not case_charges:
        case_name = case_path.name
        # 从 case.json 获取嫌疑人姓名
        defendant = ""
        if case_json.exists():
            try:
                meta = json.loads(case_json.read_text(encoding="utf-8"))
                defendant = meta.get("defendant", "")
            except Exception:
                pass

        case_charges = _parse_charges_from_name(case_name, defendant)
        if case_charges:
            case_charges = list(dict.fromkeys(case_charges))
            # 更新 case.json 保存推断的罪名
            try:
                meta = json.loads(case_json.read_text(encoding="utf-8"))
                meta["charges"] = case_charges
                case_json.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
            logger.info(f"[证据提取] 从案件名称推断罪名: {case_charges}")

    if case_charges:
        logger.info(f"[证据提取] 案件罪名: {case_charges}")

    # 提取指引法律框架（要件 + 类案裁判规则，每案件一次缓存）
    from extraction_framework import build_extraction_framework, framework_prompt_prefix
    _fw_keywords = []
    try:
        meta_for_kw = json.loads(case_json.read_text(encoding="utf-8")) if case_json.exists() else {}
        _fw_keywords = meta_for_kw.get("search_keywords") or meta_for_kw.get("suggested_keywords") or []
    except Exception:
        pass
    extraction_fw = await build_extraction_framework(evidence_dir, case_charges, _fw_keywords)
    extraction_fw_prefix = framework_prompt_prefix(extraction_fw)
    if extraction_fw_prefix:
        logger.info(f"[证据提取] 法律框架已注入（要件 {len(extraction_fw.get('elements', []))} 个，类案 {len(extraction_fw.get('case_rules', {}))} 个罪名）")

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

    # 失败条目自动重提：按份提取失败的空壳条目占住 source，导致整卷被断点续传跳过。
    # 清理空壳条目后其所属卷重新进入待提取；卷内已成功文书按名称跳过，不重复提取
    if existing_evidence:
        retried_sources = {ev["source"] for ev in existing_evidence
                           if _is_failed_evidence_entry(evidence_dir, ev)}
        if retried_sources:
            removed_names = prune_failed_evidence(case_path)
            try:
                old_index = json.loads(index_file.read_text(encoding="utf-8"))
                existing_evidence = old_index.get("evidence", [])
            except Exception:
                existing_evidence = []
            processed_sources = {ev["source"] for ev in existing_evidence} - retried_sources
            logger.info(f"[证据提取] 清理 {len(removed_names)} 份失败空壳，"
                        f"{len(retried_sources)} 个卷将重提失败文书: {sorted(retried_sources)}")

    # 卷内跳过名册：重提卷中已成功的文书名（按份提取时按名称跳过，避免重复提取）
    existing_names_by_source = {}
    for ev in existing_evidence:
        if ev.get("source") and ev.get("name"):
            existing_names_by_source.setdefault(ev["source"], set()).add(ev["name"])

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
            # 精确匹配：必须是独立的起诉书/起诉意见书文件,而不是名称中含有这些词的普通卷宗文件
            # 排除明显是卷宗文件名的常见模式：含有"去水印"、"笔录"、"证言"、"陈述"、"鉴定"、"证据"、"报告"、"通知"、"决定"、"说明"、"清单"、"单"、"信"、"函"
            _EXCLUDE_PATTERNS = ("去水印", "笔录", "证言", "陈述", "鉴定", "证据", "报告", "通知", "决定", "说明", "清单", "单", "信", "函")
            if any(p in name for p in _EXCLUDE_PATTERNS):
                return False
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

        # 文书分类：非证据（封面/目录/封底/备考表）标注后跳过提取（文件保留）
        from doc_classifier import classify_document
        file_classifications = {}  # filename -> doc_type
        evidence_md_files = []
        for f in all_md_files:
            # 流式读取文件头，避免整文件载入内存只为取前 500 字
            with f.open(encoding="utf-8", errors="ignore") as fh:
                head_text = fh.read(2000)
            doc_type = await classify_document(f.name, head_text[:500], f.stat().st_size)
            file_classifications[f.name] = doc_type
            if doc_type.startswith("non_evidence"):
                logger.info(f"[证据提取] {f.name} 标注为非证据（{doc_type.split(':')[1]}），保留文件不入提取")
                # 非证据也计入进度，避免进度条缺格
                task = EXTRACT_TASKS.get(case_id)
                if task:
                    task["processed_files"] = task.get("processed_files", 0) + 1
            else:
                evidence_md_files.append(f)

        indictment_files = [f for f in evidence_md_files if _is_indictment(f.name)]
        other_files = [f for f in evidence_md_files if not _is_indictment(f.name)]

        # ── 第1步：普通文件并发提取（先处理，让用户快速看到进度）──
        pending_files = [f for f in other_files if f.name not in processed_sources]
        all_evidence = list(existing_evidence)
        next_id = len(all_evidence) + 1

        # 分支外（起诉意见书结果合并、起诉书兜底分类）仍使用的名字，须在分支前初始化，
        # 否则断点续传全部跳过（不进入 if pending_files 分支）时触发 UnboundLocalError
        indictment_extracted = {}  # {md_file.name: (ev_path, classification)}

        # 内容判断辅助函数：读取文件内容，判断文书类型和处理方式
        def _classify_indictment_doc(md_file: Path) -> dict:
            text = md_file.read_text(encoding="utf-8")
            head = text[:5000]
            has_police_number = bool(re.search(r'.+公(刑|治|行|刑立|刑强|刑诉)\w*字', head[:2000]))
            has_procuratorate_number = bool(re.search(r'.+检(刑诉|公诉|刑执)\w*字', head[:2000]))
            has_police_title = bool(re.search(r'起诉意见书', head[:1000]))
            has_procuratorate_title = bool(re.search(r'起\s*诉\s*书', head[:300]))
            is_police_doc = has_police_number or has_police_title
            is_procuratorate_doc = has_procuratorate_number or has_procuratorate_title
            if is_procuratorate_doc and not is_police_doc:
                return {"type": "procuratorate_standalone", "doc_name": "起诉书"}
            if is_procuratorate_doc and is_police_doc:
                return {"type": "procuratorate_mixed", "doc_name": "起诉书（混合文件）"}
            if "起诉书" in md_file.name and "意见" not in md_file.name:
                return {"type": "procuratorate_standalone", "doc_name": "起诉书"}
            return {"type": "police", "doc_name": "起诉意见书"}

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
                        task["current_file_done"] = 0
                        task["current_file_total"] = 0
                        task["llm_waiting"] = True

                    def _doc_progress(done: int, total: int):
                        """按份提取的卷内进度（前端进度条：当前卷 done/total 份）"""
                        t = EXTRACT_TASKS.get(case_id)
                        if t:
                            t["current_file_done"] = done
                            t["current_file_total"] = total

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
                        md_file, md_text, file_temp_dir, semaphore, case_charges, extraction_fw_prefix,
                        progress_cb=_doc_progress,
                        skip_names=existing_names_by_source.get(md_file.name),
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
            stall_threshold = 900  # 15 分钟无进展视为可能卡死

            async def stall_detector():
                """检测长时间无进展，自动取消任务（LLM 调用进行中不算卡死）"""
                nonlocal last_progress_time
                while True:
                    await asyncio.sleep(10)
                    if gather_task and gather_task.done():
                        return  # gather 已完成，自动退出
                    task = EXTRACT_TASKS.get(case_id)
                    if isinstance(task, dict) and task.get("llm_waiting"):
                        continue  # LLM 调用进行中（慢模型单次可达数分钟），不算卡死
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

                # ── 起诉意见书专用规则并发提取（和普通文件一起 gather，不再串行等最后）──
                # 预分类：找出需要 LLM 提取的起诉意见书（非 standalone 直接复制）
                indictment_llm_coros = []
                indictment_llm_results = {}  # {md_file.name: Path}
                for md_file in indictment_files:
                    if md_file.name in processed_sources:
                        continue
                    classification = _classify_indictment_doc(md_file)
                    is_standalone = classification["type"] == "procuratorate_standalone"
                    if not is_standalone:
                        md_text = md_file.read_text(encoding="utf-8")
                        logger.info(f"[证据提取] {md_file.name} → LLM 提取并入并发池（{classification['doc_name']}）")

                        async def _indictment_llm_coro(mf=md_file, mt=md_text, nid=next_id):
                            """包装专用规则为并发 coro，返回 (source_name, evidence_dir, ev_path, classification)"""
                            try:
                                ev_path = await _process_indictment_single(mf, mt, evidence_dir, nid)
                                return (mf.name, evidence_dir, ev_path, classification, None)
                            except Exception as e:
                                return (mf.name, evidence_dir, None, classification, str(e))

                        coros.append(_indictment_llm_coro())
                        indictment_llm_coros.append(len(coros) - 1)  # 记录 coros 中的索引

                # 用 asyncio.gather 并发执行（普通文件 + 起诉意见书专用规则，一起跑）
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
            indictment_llm_indices = set(indictment_llm_coros)
            for i, result in enumerate(gather_results):
                if i in indictment_llm_indices:
                    # 起诉意见书专用规则结果：返回 (source_name, evidence_dir, ev_path, classification, error)
                    if isinstance(result, Exception):
                        logger.error(f"[证据提取] 起诉意见书并发提取异常: {result}")
                        continue
                    src_name, ev_dir, ev_path, classification, err = result
                    if err or ev_path is None:
                        logger.warning(f"[证据提取] {src_name}: 起诉意见书提取失败: {err}")
                        continue
                    indictment_extracted[src_name] = (ev_path, classification)
                    continue

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

            logger.info(f"[证据提取] 提取汇总：成功 {success_count} 个文件，失败 {fail_count} 个文件，0 份证据 {zero_count} 个文件"
                        + (f"，起诉意见书 {len(indictment_extracted)} 个" if indictment_extracted else ""))

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
                            **{field: b.get(field, "") for field in EVIDENCE_STRUCTURED_FIELDS},
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
                        # 条目级非证据标注（封面/目录等）：上游已标注则沿用，否则现算
                        "doc_type": ev_data.get("doc_type") or classify_evidence_item(ev_data["name"]),
                        "persons": ev_data.get("persons", ""),
                        "related_entities": ev_data.get("related_entities", ""),
                        **{field: ev_data.get(field, "") for field in EVIDENCE_STRUCTURED_FIELDS},
                        "summary_preview": ev_data.get("summary_preview", ev_data.get("summary", "")[:200]),
                        "has_quotes": ev_data.get("has_quotes", bool(ev_data.get("original_quotes", "").strip())),
                        "md_file": new_name,
                    })
                    next_id += 1
        else:
            logger.info("[证据提取] 所有文件已提取，跳过并发处理")

        # ── 第2步：起诉意见书并发提取结果处理（已在 gather 中完成）──
        for src_name, (ev_path, classification) in indictment_extracted.items():
            ev_text = ev_path.read_text(encoding="utf-8")
            all_evidence.append({
                "name": ev_path.stem.split("_", 1)[1] if "_" in ev_path.stem else ev_path.stem,
                "type": classification.get("doc_name", "起诉意见书"),
                "source": src_name,
                "page_range": "",
                "persons": "",
                "related_entities": "",
                "summary_preview": ev_text[:200],
                "has_quotes": True,
                "md_file": ev_path.name,
            })

        # ── 第3步：起诉书处理（仅 standalone 直接复制，LLM 提取已在 gather 中完成）──
        indictment_files.sort(key=lambda f: (0 if "起诉意见书" not in f.name else 1))

        for md_file in indictment_files:
            if EXTRACT_TASKS.get(case_id) == "cancelled":
                logger.info(f"[证据提取] 任务已被取消（处理 {md_file.name} 前）")
                EXTRACT_TASKS.pop(case_id, None)
                return {"success": False, "error": "用户已停止提取", "case_id": case_id}

            if md_file.name in processed_sources:
                logger.info(f"[证据提取] 跳过已处理: {md_file.name}")
                continue

            # 已在 gather 中通过 LLM 提取完成的，跳过
            if md_file.name in indictment_extracted:
                continue

            # 读内容判断类型
            classification = _classify_indictment_doc(md_file)
            is_standalone = classification["type"] == "procuratorate_standalone"

            if not is_standalone:
                # 非 standalone 但未在 gather 中处理（兜底），重试一次 LLM 提取
                md_text = md_file.read_text(encoding="utf-8")
                logger.info(f"[证据提取] {md_file.name} → LLM 提取兜底（{classification['doc_name']}）")
                try:
                    ev_path = await _process_indictment_single(md_file, md_text, evidence_dir, next_id)
                    ev_text = ev_path.read_text(encoding="utf-8")
                    all_evidence.append({
                        "name": ev_path.stem.split("_", 1)[1] if "_" in ev_path.stem else ev_path.stem,
                        "type": classification.get("doc_name", "起诉意见书"),
                        "source": md_file.name,
                        "page_range": "",
                        "persons": "",
                        "related_entities": "",
                        "summary_preview": ev_text[:200],
                        "has_quotes": True,
                        "md_file": ev_path.name,
                    })
                    next_id += 1
                except Exception as e:
                    logger.error(f"[证据提取] {md_file.name}: 兜底 LLM 提取失败: {e}")
                continue

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
                "charges": case_charges,
                "summary_preview": f"{md_file.name}（待案卷分析时详细提取）",
                "has_quotes": True,
                "md_file": dest_name,
            })
            next_id += 1

        # ── 最终保存 ──
        # 标记按份提取失败的空壳条目（前端列表 ⚠️ 展示；prune 判定仍按文件内容，不依赖该字段）
        failed_count = 0
        for ev in all_evidence:
            ev["failed"] = _is_failed_evidence_entry(evidence_dir, ev)
            if ev["failed"]:
                failed_count += 1
        index_data = {
            "case_id": case_id,
            "total_evidence": len(all_evidence),
            "evidence": all_evidence,
            "case_charges": case_charges,
            "files": [{"name": n, "doc_type": t} for n, t in file_classifications.items()],
            "generated_at": datetime.now().isoformat(),
        }
        # 失败数写入任务状态，供 extract-status 返回给前端汇总展示
        task = EXTRACT_TASKS.get(case_id)
        if isinstance(task, dict):
            task["failed_count"] = failed_count
        # 如果提取结果为 0，记录可能的原因供前端展示
        if len(all_evidence) == 0:
            index_data["error_hint"] = "LLM 提取全部失败（详见后端日志），可能原因：API Key 无效、Base URL 不可达、模型名称错误、或所有 MD 文件解析失败"
        index_file.write_text(json.dumps(index_data, ensure_ascii=False, indent=2), encoding="utf-8")

        # 完整性校验（规则对账 + LLM 抽检关键文书）
        try:
            from completeness import check_completeness
            source_texts = {}
            for f in evidence_md_files:
                source_texts[f.name] = f.read_text(encoding="utf-8")
            extracted_by_file: dict = {}
            for ev in index_data.get("evidence", []):
                extracted_by_file.setdefault(ev.get("source", ""), []).append(ev.get("name", ""))
            # 全案件证据名：全局交叉核对（本文件未提取但他卷已覆盖的不误报）
            all_names = [ev.get("name", "") for ev in index_data.get("evidence", [])]
            completeness_report = await check_completeness(source_texts, extracted_by_file, all_names)
            (evidence_dir / "completeness_report.json").write_text(
                json.dumps(completeness_report, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"[证据提取] 完整性校验: {completeness_report['summary']}")
        except Exception as e:
            logger.warning(f"[证据提取] 完整性校验失败（不影响提取结果）: {e}")

        # 清理临时文件（无论走哪个分支都清理）
        old_temp = evidence_dir / "_temp_extract"
        if old_temp.exists():
            shutil.rmtree(old_temp)
            logger.info(f"[证据提取] 临时目录已清理")

        # 详细摘要层：提取完成后自动生成（失败不阻塞分析，分析端对无 digest 的证据回退全文）
        # should_abort：提取被停止/证据被清除时不写回 index.json（防目录清空后复活）
        try:
            from evidence_summarizer import summarize_evidence
            from llm_client import get_llm_client
            conc = int(cfg.get("evidence_concurrency", 3) or 3)

            def _summary_progress(done: int, total: int, name: str):
                """摘要阶段进度（前端进度条）"""
                t = EXTRACT_TASKS.get(case_id)
                if t:
                    t["phase"] = "summarizing"
                    t["summary_done"] = done
                    t["summary_total"] = total
                    t["current_file"] = name

            sum_stats = await summarize_evidence(
                get_llm_client(), case_path, concurrency=conc,
                should_abort=lambda: EXTRACT_TASKS.get(case_id) == "cancelled",
                progress_cb=_summary_progress)
            logger.info(f"[证据摘要] 完成: {sum_stats}")
        except Exception as e:
            logger.warning(f"[证据摘要] 生成失败（不影响提取与分析，将回退全文）: {e}")

        logger.info(f"[证据提取] 提取完成：共 {len(all_evidence)} 份证据，{failed_count} 份失败待重提（下次提取自动重试）")

    EXTRACT_TASKS.pop(case_id, None)

    return {
        "success": True,
        "case_id": case_id,
        "total_evidence": len(all_evidence),
        "failed_count": failed_count,
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

    index_data = json.loads(index_file.read_text(encoding="utf-8"))

    # 兼容旧版 index.json：条目缺少 failed 字段时按文件内容补齐并一次性回写，
    # 避免每次请求逐条读文件的 IO 开销（prune 判定仍按文件内容，不依赖该字段）
    dirty = False
    for ev in index_data.get("evidence", []):
        if "failed" not in ev:
            ev["failed"] = _is_failed_evidence_entry(evidence_dir, ev)
            dirty = True
    if dirty:
        try:
            index_file.write_text(json.dumps(index_data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"[证据索引] failed 字段回写失败（不影响返回）: {e}")

    return index_data


@router.get("/{case_id}/evidence/completeness")
async def get_evidence_completeness(case_id: str):
    """提取完整性报告"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")
    report_file = case_path / "evidence" / "completeness_report.json"
    if not report_file.exists():
        return {"files": {}, "summary": {}}
    return json.loads(report_file.read_text(encoding="utf-8"))


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
            "phase": task.get("phase", "extracting"),
            "total_files": task.get("total_files", 0),
            "processed_files": task.get("processed_files", 0),
            "current_file": task.get("current_file", ""),
            "current_file_done": task.get("current_file_done", 0),
            "current_file_total": task.get("current_file_total", 0),
            "summary_done": task.get("summary_done", 0),
            "summary_total": task.get("summary_total", 0),
            "elapsed_seconds": round(elapsed),
            "llm_waiting": task.get("llm_waiting", False),
            "llm_latency_ms": task.get("llm_latency", 0),
            "stopped_by_user": task.get("stopped_by_user", False),
            "recoverable": task.get("recoverable", True),
            "failed_count": task.get("failed_count", 0),  # 按份提取失败份数（写 index.json 时统计）
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


_tiktoken_enc = None


def _count_tokens(text: str) -> int:
    """用 tiktoken 精确计算 token 数"""
    global _tiktoken_enc
    if _tiktoken_enc is None:
        _tiktoken_enc = tiktoken.get_encoding("cl100k_base")
    return len(_tiktoken_enc.encode(text))


# 块间重叠字符数（约 370 tokens），防止跨块事实被切断
OVERLAP_CHARS = 500


def _overlap_tail(text: str) -> str:
    """取文本尾部 ≤OVERLAP_CHARS 字符作为下一块的重叠前缀（尽量段落边界）"""
    if len(text) <= OVERLAP_CHARS:
        return ""
    tail = text[-OVERLAP_CHARS:]
    para = tail.find("\n\n")
    if para > 0:
        tail = tail[para + 2:]
    return tail.strip()


def _split_content_by_tokens(text: str, budget: int, source_name: str) -> list:
    """
    按 token 预算分块，优先按 ## 标题边界拆分，不破坏文书结构。

    返回：[{"label": "xxx - 分块 1/3", "text": "..."}, ...]
    """
    total_tokens = _count_tokens(text)
    if total_tokens <= budget:
        return [{"label": source_name, "text": text}]

    # 按 \n## 拆分（二级标题 = 文书边界）
    sections = re.split(r'\n(?=## )', text)
    if len(sections) <= 1:
        # 无二级标题，降级按三级标题拆分
        sections = re.split(r'\n(?=### )', text)
    if len(sections) <= 1:
        # 仍然无法拆分，降级按固定 token 数硬切
        return _split_by_token_count(text, budget, source_name)

    chunks = []
    current_parts: list[str] = []
    current_tokens = 0
    pending_overlap = ""  # 上一个已发出块的尾部，作为下一块的重叠前缀

    def _emit(text: str) -> None:
        nonlocal pending_overlap
        chunks.append({"label": source_name, "text": text})
        pending_overlap = _overlap_tail(text)

    def _start_parts(section: str, sec_tokens: int) -> None:
        """开始新的 current_parts，带上前一块的重叠前缀（若有）"""
        nonlocal current_tokens
        if pending_overlap:
            current_parts.append(pending_overlap + "\n\n" + section)
            current_tokens = _count_tokens(pending_overlap) + sec_tokens
        else:
            current_parts.append(section)
            current_tokens = sec_tokens

    for section in sections:
        sec_tokens = _count_tokens(section)
        if sec_tokens > budget:
            # 单段超过预算，先把前面积累的发出
            if current_parts:
                _emit("\n".join(current_parts))
                current_parts = []
                current_tokens = 0
            # 超长段内部按三级标题再拆
            sub_chunks = _split_content_by_tokens(section, budget, source_name)
            chunks.extend(sub_chunks)
            if sub_chunks:
                pending_overlap = _overlap_tail(sub_chunks[-1]["text"])
            continue
        if current_tokens + sec_tokens > budget:
            # 加上这段会超预算，先把当前积累发出
            _emit("\n".join(current_parts))
            current_parts = []
            _start_parts(section, sec_tokens)
        else:
            if not current_parts:
                _start_parts(section, sec_tokens)
            else:
                current_parts.append(section)
                current_tokens += sec_tokens

    if current_parts:
        chunks.append({"label": source_name, "text": "\n".join(current_parts)})

    # 标注分块序号
    if len(chunks) > 1:
        for i, chunk in enumerate(chunks):
            chunk["label"] = f"{source_name} - 分块 {i+1}/{len(chunks)}"

    logger.info(f"[分块] {source_name}: {total_tokens:,} tokens → {len(chunks)} 块")
    return chunks


def _split_by_token_count(text: str, budget: int, source_name: str) -> list:
    """降级方案：无标题边界时按固定 token 数硬切（块间回退 250 tokens 重叠）"""
    global _tiktoken_enc
    if _tiktoken_enc is None:
        _tiktoken_enc = tiktoken.get_encoding("cl100k_base")

    tokens = _tiktoken_enc.encode(text)
    chunks = []
    step = max(1, budget - 250)  # 每块步进 budget-250，重叠 250 tokens
    for i in range(0, len(tokens), step):
        chunk_tokens = tokens[i:i + budget]
        chunk_text = _tiktoken_enc.decode(chunk_tokens)
        chunks.append({"label": source_name, "text": chunk_text})

    if len(chunks) > 1:
        for i, chunk in enumerate(chunks):
            chunk["label"] = f"{source_name} - 分块 {i+1}/{len(chunks)}"

    logger.info(f"[分块] {source_name}: 硬切为 {len(chunks)} 块")
    return chunks


def _merge_evidence_blocks(blocks: list) -> list:
    """合并多块提取结果，按 (name, source) 去重；未命名证据全部保留"""
    seen: dict[tuple, dict] = {}
    unnamed: list = []
    for block in blocks:
        name = block.get("name", "")
        source = block.get("source", "")
        if not name or name == "未命名证据":
            unnamed.append(block)
            continue
        key = (name, source)
        if key not in seen:
            seen[key] = block
        else:
            # 保留更长的摘要，合并 raw_text
            existing = seen[key]
            if len(block.get("summary", "")) > len(existing.get("summary", "")):
                block_copy = dict(block)
                if existing.get("raw_text"):
                    block_copy["raw_text"] = existing["raw_text"] + "\n" + block_copy.get("raw_text", "")
                seen[key] = block_copy
            else:
                if block.get("raw_text"):
                    existing["raw_text"] = existing.get("raw_text", "") + "\n" + block["raw_text"]
    return list(seen.values()) + unnamed


def _parse_evidence_blocks(llm_output: str, source_file: str) -> list:
    """
    解析 LLM 返回的证据块。

    优先尝试 JSON 数组解析（LLM 返回 JSON 格式），
    失败后回退到文本格式解析（兼容已有的输出格式）。
    """
    import re
    import json
    blocks = []

    # 分批输出检测关键词
    _DEFERRED_PATTERNS = [
        "后续证据", "后续还有", "将继续", "此处省略", "分批输出",
        "后续回复", "下一轮", "推迟", "因篇幅限制", "因内容较多",
        "确保所有文书均被覆盖", "重复性内容"
    ]

    def _check_deferred_output(blocks_to_check):
        """检查证据块是否包含分批输出提示"""
        for block in blocks_to_check:
            raw = block.get("raw_text", "")
            for pattern in _DEFERRED_PATTERNS:
                if pattern in raw:
                    logger.warning(f"[证据解析] {source_file}: 检测到分批输出提示「{pattern}」，LLM 可能未完整输出所有证据")
                    return True
        return False

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
                            "fund_flows": item.get("fund_flows", item.get("资金往来", [])) or [],
                            "elements": item.get("elements", item.get("关联要件", [])) or [],
                            "proves_facts": item.get("proves_facts", []),
                            "proves_details": item.get("proves_details", {}),
                            "raw_text": json.dumps(item, ensure_ascii=False, indent=2),
                        })
                if blocks:
                    logger.info(f"[证据解析] {source_file}: JSON 模式解析成功，{len(blocks)} 份证据")
                    _check_deferred_output(blocks)
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
        fund_flows_str = _extract_field(content, "资金往来") or ""
        fund_flows = [f.strip() for f in fund_flows_str.replace("；", "\n").split("\n") if f.strip() and f != "无"] if fund_flows_str else []
        ev_charges_str = _extract_field(content, "关联罪名") or ""
        ev_charges = [c.strip() for c in ev_charges_str.replace("、", ",").split(",") if c.strip()] if ev_charges_str else []
        ev_elements_str = _extract_field(content, "关联要件") or ""
        ev_elements = [c.strip() for c in ev_elements_str.replace("、", ",").split(",") if c.strip()] if ev_elements_str else []

        # ── 解析"证明对象"：LLM 对6个待证事实的逐项判断 ──
        proves_facts = []
        proves_details = {}
        _fact_ids = [
            ("主体事实", "fact_subject"), ("主观事实", "fact_subjective"),
            ("行为事实", "fact_behavior"), ("结果事实", "fact_result"),
            ("因果关系", "fact_causation"), ("情节事实", "fact_circumstance"),
        ]
        for fact_name_cn, fact_id in _fact_ids:
            # 匹配 "- 主体事实：是 — xxx" 或 "- **主体事实**：是 — xxx"
            pat = re.compile(
                rf'{re.escape(fact_name_cn)}\s*[：:]\s*(是|否)\s*[—\-–]\s*(.+?)(?=\n\s*[-*]\s*[一-鿿]|$)',
                re.DOTALL,
            )
            m = pat.search(content)
            if m:
                answer, detail = m.group(1).strip(), m.group(2).strip()
                if answer == "是" and detail and len(detail) > 2:
                    proves_facts.append(fact_id)
                    proves_details[fact_id] = detail[:500]

        blocks.append({
            "name": name,
            "type": ev_type,
            "source": source_file,
            "page_range": page_range.strip(),
            "persons": persons.strip(),
            "key_facts": key_facts.strip(),
            "summary": summary.strip(),
            "charges": ev_charges,
            "elements": ev_elements,
            "proves_facts": proves_facts,
            "proves_details": proves_details,
            "original_quotes": original_quotes.strip(),
            "contradiction_hints": contradiction.strip(),
            "related_entities": related_entities.strip(),
            "fund_flows": fund_flows,
            "raw_text": content.strip(),
        })

    _check_deferred_output(blocks)
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
        if target_dir.exists():
            # 注意：rglob 把 [ ] 当通配符，需转义或直接遍历比较
            matched = [f for f in target_dir.rglob("*") if f.name == target_name]
        if not matched:
            # fallback：指定目录找不到 → 全局搜索（evidence/ 等其他子目录）
            matched = [f for f in case_root.rglob("*") if f.name == target_name]
        if not matched:
            raise HTTPException(status_code=404, detail=f"文件不存在：{target_name}")
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


@router.get("/{case_id}/pdf-thumbnails")
async def pdf_thumbnails(case_id: str, file_path: str, dir: str = "processed", width: int = 200):
    """生成并返回案件 PDF 的逐页缩略图（缓存于 DATA_DIR/cache/thumb/，复用 /thumbnails 挂载）"""
    from config import CACHE_DIR
    from page_rotation import generate_pdf_thumbnails, thumb_cache_dir_for
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")
    pdf = (case_path / dir / file_path).resolve()
    if not pdf.is_relative_to(case_path.resolve()) or not pdf.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    width = max(100, min(width, 800))
    cache_dir = thumb_cache_dir_for(CACHE_DIR, case_id, pdf.name)
    thumbs = generate_pdf_thumbnails(pdf, cache_dir, width)
    base = f"/thumbnails/thumb/{case_id}/{pdf.stem}"
    return {"thumbnails": [{"page": t["page"], "url": f"{base}/{t['file']}"} for t in thumbs],
            "total_pages": len(thumbs)}


class RotatePageRequest(BaseModel):
    file_path: str
    dir: str = "processed"
    page: int
    degrees: int  # 90/180/270，顺时针累加


@router.post("/{case_id}/rotate-page")
async def rotate_page(case_id: str, req: RotatePageRequest):
    """旋转 processed/ 下 PDF 的指定页（只改显示朝向，不动 original/ 原件）"""
    from config import CACHE_DIR
    from page_rotation import rotate_pdf_page, thumb_cache_dir_for
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")
    if req.dir != "processed":
        raise HTTPException(status_code=400, detail="仅支持旋转 processed/ 下的文件")
    # 安全：file_path 仅限纯文件名，防目录穿越到 original/ 原地修改电子证据原件
    if Path(req.file_path).name != req.file_path:
        raise HTTPException(status_code=400, detail="文件名不能含路径分隔符")
    pdf = (case_path / req.dir / req.file_path).resolve()
    if not pdf.is_relative_to((case_path / "processed").resolve()) or not pdf.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    try:
        new_rot = rotate_pdf_page(pdf, req.page, req.degrees,
                                  thumb_cache_dir_for(CACHE_DIR, case_id, pdf.name))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "page": req.page, "rotation": new_rot}


@router.get("/{case_id}/md-issues")
async def md_issues(case_id: str):
    """扫描案件 md/ 下的识别异常页（MinerU 把倒置/异常页误判为表格的乱码块）"""
    from page_rotation import detect_md_issues
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")
    md_dir = case_path / "md"
    if not md_dir.exists():
        return {"issues": []}
    return {"issues": detect_md_issues(md_dir)}


class ReconvertBlockRequest(BaseModel):
    file_path: str          # processed/ 下 PDF 文件名
    page: int               # 需重转的 PDF 页码（1 基）
    md_file: str            # 待拼接的 md 文件名
    start_line: int         # 乱码块起始行（md-issues 返回）
    end_line: int           # 乱码块结束行（含）
    invalidate_evidence: bool = False  # 是否同时失效该卷证据（供重新提取）


@router.post("/{case_id}/reconvert-block")
async def reconvert_block(case_id: str, req: ReconvertBlockRequest):
    """单页重转修复：抽取旋转后的单页 → MinerU 转换 → 替换 md 乱码块 → 可选证据失效

    成本：MinerU 1 页额度 + 0 次 LLM 调用（对比整卷重转 170 页+全卷重提取）。
    注意：证据失效后重新提取时，新证据编号从现有最大编号之后继续分配（追加到清单
    末尾，不回填空缺），重提取后该卷证据的编号与清单顺序可能变化。
    """
    import tempfile
    from mineru_async import AsyncMinerUConverter
    from page_rotation import extract_single_page, splice_md_block, invalidate_evidence_for_source

    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")
    # 安全：md_file/file_path 仅限纯文件名，防目录穿越写坏 case.json/index.json
    if Path(req.md_file).name != req.md_file or Path(req.file_path).name != req.file_path:
        raise HTTPException(status_code=400, detail="文件名不能含路径分隔符")
    pdf = (case_path / "processed" / req.file_path).resolve()
    md_path = (case_path / "md" / req.md_file).resolve()
    if not pdf.is_relative_to((case_path / "processed").resolve()) or not pdf.exists():
        raise HTTPException(status_code=404, detail="PDF 不存在")
    if not md_path.is_relative_to((case_path / "md").resolve()) or not md_path.exists():
        raise HTTPException(status_code=404, detail="MD 文件不存在")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        try:
            single = extract_single_page(pdf, req.page, tmp / "page.pdf")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        converter = AsyncMinerUConverter()
        results = await converter.convert_batch([single], tmp, max_concurrent=1)
        # 产物取 output_dir 下的 md 文件（convert_batch 写盘规则为 {stem}.md，
        # 用 glob 兜底 chunk 命名差异）
        md_files = sorted(tmp.glob("*.md"))
        if not results or not results[0].success or not md_files:
            raise HTTPException(status_code=502, detail="单页转换失败，请稍后重试")
        new_text = md_files[0].read_text(encoding="utf-8").strip()
        if not new_text:
            raise HTTPException(status_code=502, detail="单页转换结果为空，请稍后重试")

    try:
        splice_md_block(md_path, req.start_line, req.end_line, new_text)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    invalidated = []
    if req.invalidate_evidence:
        invalidated = invalidate_evidence_for_source(case_path, req.md_file)

    return {"success": True, "spliced_chars": len(new_text), "invalidated": invalidated}


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


# ═══════════════════════════════════════════════════════════
# 选择性 OCR（列表 / 启动 / 进度）
# ═══════════════════════════════════════════════════════════

async def _run_ocr_task(case_id: str, case_dir: Path, selected: Dict[str, list]):
    """后台执行选择性 OCR：对选中的图片做单图识别并回填对应卷 md

    逐卷独立 try/except：单卷失败记入 failed、不中断其他卷。
    done 只累计成功处理的图片数；外层 try/except 保证任何异常都落到终止态。
    """
    md_dir = case_dir / "md"
    total = sum(len(v) for v in selected.values())
    OCR_TASKS[case_id] = {"status": "running", "done": 0, "total": total, "current": "", "failed": []}
    done = 0
    try:
        from image_ocr_backfill import backfill_image_ocr
        for vol_name, names in selected.items():
            md_file = md_dir / f"{vol_name}.md"
            images_dir = md_dir / f"{vol_name}_images"
            if not md_file.exists() or not images_dir.exists():
                OCR_TASKS[case_id]["failed"].append(vol_name)
                continue
            OCR_TASKS[case_id]["current"] = vol_name
            try:
                md_text = md_file.read_text(encoding="utf-8")
                new_text = await backfill_image_ocr(md_text, images_dir, vol_name, only_names=set(names))
                if new_text != md_text:
                    md_file.write_text(new_text, encoding="utf-8")
                done += len(names)
            except Exception as e:
                logger.warning(f"[选择性OCR] {vol_name} 失败: {e}")
                OCR_TASKS[case_id]["failed"].append(vol_name)
            OCR_TASKS[case_id]["done"] = done
        OCR_TASKS[case_id]["status"] = "completed"
    except Exception as e:
        logger.warning(f"[选择性OCR] {case_id} 失败: {e}")
        OCR_TASKS[case_id]["status"] = "failed"
        OCR_TASKS[case_id]["error"] = str(e)[:200]


@router.get("/{case_id}/ocr-images")
async def list_ocr_images(case_id: str):
    """预筛全部卷的图片（排除印章+小图），返回按卷分组列表"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")
    from image_ocr_backfill import preselect_ocr_images
    grouped = preselect_ocr_images(case_path)
    return {"success": True, "groups": grouped}


@router.post("/{case_id}/ocr-images")
async def start_ocr_images(case_id: str, body: dict = Body(...)):
    """启动选择性 OCR 后台任务。body: {groups: {卷名: [图片名...]}}"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")
    selected = body.get("groups", body) if isinstance(body, dict) else {}
    selected = {k: v for k, v in selected.items() if v}
    if not selected:
        return {"success": False, "error": "未选择任何图片"}
    if OCR_TASKS.get(case_id, {}).get("status") == "running":
        return {"success": False, "error": "OCR 任务进行中"}
    asyncio.create_task(_run_ocr_task(case_id, case_path, selected))
    return {"success": True, "task_started": True}


@router.get("/{case_id}/ocr-status")
async def get_ocr_status(case_id: str):
    """OCR 任务进度"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")
    task = OCR_TASKS.get(case_id, {"status": "idle", "done": 0, "total": 0})
    return {"success": True, "task": task}
