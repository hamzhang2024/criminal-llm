"""
刑事案卷分析 API - 案卷分析功能

提供案卷分析、证据对话、报告生成等 API
"""
import os
import json
import re
import uuid
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime

from fastapi import APIRouter, HTTPException, Body, UploadFile, File
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
import shutil

# 创建路由器
router = APIRouter(prefix="/api/analyze-case", tags=["analyze"])

# 数据目录
ANALYSIS_DIR = Path(__file__).parent.parent / "data" / "analysis"
ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)

# 分析进度缓存（内存存储）
analysis_progress: Dict[str, Dict[str, Any]] = {}


@router.get("/cases")
async def list_cases():
    """
    获取所有案件列表
    
    Returns:
        案件列表
    """
    cases = []
    
    # 扫描案件 JSON 文件
    for json_file in ANALYSIS_DIR.glob("*.json"):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                case_info = json.load(f)
            
            # 计算文件大小
            case_dir = Path(case_info.get("case_dir", ""))
            total_size = 0
            file_count = 0
            if case_dir.exists():
                for f in case_dir.rglob("*"):
                    if f.is_file():
                        total_size += f.stat().st_size
                        file_count += 1
            
            cases.append({
                "case_id": case_info.get("case_id"),
                "case_name": case_info.get("case_name", "未命名案件"),
                "defendant": case_info.get("defendant"),
                "created_at": case_info.get("created_at"),
                "status": case_info.get("status", "pending"),
                "evidence_count": len(case_info.get("evidence_list", [])),
                "evidence_list": case_info.get("evidence_list", []),  # 返回完整证据列表
                "report": case_info.get("report"),  # 返回报告数据
                "file_count": file_count,
                "total_size": total_size,
                "total_size_formatted": f"{total_size / 1024 / 1024:.2f} MB" if total_size > 0 else "0 MB"
            })
        except Exception:
            pass
    
    # 按创建时间倒序排序
    cases.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    
    return {"cases": cases, "total": len(cases)}


# LLM 客户端
from llm_client import get_llm_client
from pdf_to_md import get_evidence_text, _read_cached_md  # PDF → MD 转换模块
import context_budget  # 统一上下文预算


# ========== 数据模型 ==========

class CaseInfo(BaseModel):
    """案件信息"""
    case_id: str
    case_dir: str  # 案卷目录路径
    defendant: Optional[str] = None  # 辩护对象
    created_at: str
    status: str  # pending, analyzing, ready, chatting


class EvidenceItem(BaseModel):
    """证据项"""
    id: str
    filename: str
    filepath: str
    type: str  # 起诉意见书, 讯问笔录, 证人证言, 书证等
    pages: int
    selected: bool = False
    summary: Optional[str] = None


class AnalysisReport(BaseModel):
    """分析报告"""
    case_id: str
    defendant: str
    indictment_summary: Dict[str, Any]  # 起诉意见书摘要
    evidence_map: List[Dict[str, Any]]  # 证据-要素映射
    evidence_analysis: List[Dict[str, Any]]  # 证据三性分析
    contradictions: List[Dict[str, Any]]  # 矛盾点
    defense_points: List[str]  # 辩护要点
    sentencing_factors: Dict[str, Any]  # 量刑情节
    generated_at: str


class ChatMessage(BaseModel):
    """对话消息"""
    role: str  # user, assistant
    content: str
    evidence_refs: Optional[List[str]] = None  # 引用的证据
    timestamp: str


# ========== 数据清理 ==========

@router.delete("/cleanup")
async def cleanup_old_data(days: int = 7):
    """
    清理超过指定天数的数据
    
    Args:
        days: 保留天数，默认 7 天
    
    Returns:
        清理统计
    """
    cutoff_time = datetime.now() - timedelta(days=days)
    
    stats = {
        "deleted_cases": [],
        "deleted_files": 0,
        "freed_bytes": 0,
        "errors": []
    }
    
    # 清理上传目录（包括 uploads 和 temp）
    for subdir in ["uploads", "temp"]:
        uploads_dir = ANALYSIS_DIR / subdir
        if uploads_dir.exists():
            for case_dir in uploads_dir.iterdir():
                if not case_dir.is_dir():
                    continue
                
                try:
                    # 检查修改时间
                    mtime = datetime.fromtimestamp(case_dir.stat().st_mtime)
                    
                    if mtime < cutoff_time:
                        # 计算大小
                        dir_size = sum(f.stat().st_size for f in case_dir.rglob("*") if f.is_file())
                        
                        # 删除目录
                        shutil.rmtree(case_dir)
                        
                        stats["deleted_cases"].append(case_dir.name)
                        stats["deleted_files"] += 1
                        stats["freed_bytes"] += dir_size
                        
                except Exception as e:
                    stats["errors"].append(f"{case_dir.name}: {str(e)}")
    
    # 清理过期的 JSON 文件
    for json_file in ANALYSIS_DIR.glob("*.json"):
        try:
            mtime = datetime.fromtimestamp(json_file.stat().st_mtime)
            
            if mtime < cutoff_time:
                # 检查对应的上传目录是否已删除
                case_id = json_file.stem
                case_upload_dir = ANALYSIS_DIR / "uploads" / case_id
                
                if not case_upload_dir.exists():
                    json_file.unlink()
                    
        except Exception as e:
            stats["errors"].append(f"{json_file.name}: {str(e)}")
    
    # 格式化大小
    if stats["freed_bytes"] > 1024 * 1024 * 1024:
        stats["freed_size"] = f"{stats['freed_bytes'] / (1024*1024*1024):.2f} GB"
    elif stats["freed_bytes"] > 1024 * 1024:
        stats["freed_size"] = f"{stats['freed_bytes'] / (1024*1024):.2f} MB"
    elif stats["freed_bytes"] > 1024:
        stats["freed_size"] = f"{stats['freed_bytes'] / 1024:.2f} KB"
    else:
        stats["freed_size"] = f"{stats['freed_bytes']} bytes"
    
    return stats


@router.delete("/case/{case_id}")
async def delete_case(case_id: str):
    """
    删除案件数据
    
    删除案件的临时文件和元数据
    """
    case_file = ANALYSIS_DIR / f"{case_id}.json"
    
    if not case_file.exists():
        raise HTTPException(status_code=404, detail="案件不存在")
    
    # 读取案件信息
    with open(case_file, "r", encoding="utf-8") as f:
        case_info = json.load(f)
    
    freed_bytes = 0
    
    # 删除临时文件目录
    if case_info.get("is_temp"):
        temp_dir = ANALYSIS_DIR / "temp" / case_id
        if temp_dir.exists():
            try:
                freed_bytes = sum(f.stat().st_size for f in temp_dir.rglob("*") if f.is_file())
                shutil.rmtree(temp_dir)
            except Exception:
                pass
    
    # 也检查 uploads 目录（旧数据）
    uploads_dir = ANALYSIS_DIR / "uploads" / case_id
    if uploads_dir.exists():
        try:
            freed_bytes += sum(f.stat().st_size for f in uploads_dir.rglob("*") if f.is_file())
            shutil.rmtree(uploads_dir)
        except Exception:
            pass
    
    # 删除 JSON 文件
    case_file.unlink()
    
    return {
        "case_id": case_id,
        "freed_bytes": freed_bytes,
        "freed_size": f"{freed_bytes / (1024*1024):.2f} MB" if freed_bytes > 1024*1024 else f"{freed_bytes / 1024:.2f} KB",
        "message": "案件已删除"
    }


@router.get("/storage-stats")
async def get_storage_stats():
    """
    获取存储统计信息
    
    Returns:
        存储使用情况
    """
    stats = {
        "total_cases": 0,
        "total_files": 0,
        "total_size": 0,
        "cases": []
    }
    
    uploads_dir = ANALYSIS_DIR / "uploads"
    if uploads_dir.exists():
        for case_dir in uploads_dir.iterdir():
            if not case_dir.is_dir():
                continue
            
            case_size = sum(f.stat().st_size for f in case_dir.rglob("*") if f.is_file())
            file_count = sum(1 for f in case_dir.rglob("*") if f.is_file())
            mtime = datetime.fromtimestamp(case_dir.stat().st_mtime)
            
            stats["cases"].append({
                "case_id": case_dir.name,
                "size": case_size,
                "files": file_count,
                "modified": mtime.isoformat()
            })
            
            stats["total_cases"] += 1
            stats["total_files"] += file_count
            stats["total_size"] += case_size
    
    # 格式化总大小
    if stats["total_size"] > 1024 * 1024 * 1024:
        stats["total_size_formatted"] = f"{stats['total_size'] / (1024*1024*1024):.2f} GB"
    elif stats["total_size"] > 1024 * 1024:
        stats["total_size_formatted"] = f"{stats['total_size'] / (1024*1024):.2f} MB"
    else:
        stats["total_size_formatted"] = f"{stats['total_size'] / 1024:.2f} KB"
    
    return stats


# ========== API 端点 ==========

@router.post("/upload-directory")
async def upload_directory(
    files: List[UploadFile] = File(...),
    case_name: str = None,
    defendant: str = None
):
    """
    上传目录中的 PDF 文件
    
    Args:
        files: PDF 文件列表
        case_name: 案件名称（可选，默认使用时间戳）
        defendant: 被告人姓名（可选）
    
    Returns:
        案件信息
    """
    if not files:
        raise HTTPException(status_code=400, detail="没有上传文件")
    
    # 过滤 PDF 文件
    pdf_files = [f for f in files if f.filename and f.filename.lower().endswith('.pdf')]
    
    if not pdf_files:
        raise HTTPException(status_code=400, detail="没有 PDF 文件")
    
    # 生成案件 ID 和文件夹名
    case_id = str(uuid.uuid4())[:8]
    timestamp = datetime.now().strftime("%Y%m%d")
    
    # 使用案件名称或时间戳创建文件夹
    if case_name and case_name.strip():
        # 清理非法字符
        safe_name = "".join(c for c in case_name.strip() if c not in '<>:"/\\|？*')
        folder_name = f"{timestamp}_{safe_name}"
    else:
        folder_name = f"{timestamp}_案卷_{case_id}"
    
    # 创建案件目录
    cases_dir = ANALYSIS_DIR / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    case_dir = cases_dir / folder_name
    case_dir.mkdir(parents=True, exist_ok=True)
    
    # 保存文件并构建证据列表
    evidence_list = []
    for i, file in enumerate(pdf_files):
        # 保存文件到案件目录
        file_path = case_dir / file.filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        # 推断证据类型
        filename_only = Path(file.filename).name
        evidence_type = infer_evidence_type(filename_only)
        
        evidence_list.append({
            "id": f"ev_{i+1:03d}",
            "filename": filename_only,
            "filepath": str(file_path),
            "type": evidence_type,
            "pages": get_pdf_pages(str(file_path)),
            "selected": False,
            "summary": None
        })
    
    # 保存案件信息
    case_info = {
        "case_id": case_id,
        "case_name": case_name.strip() if case_name and case_name.strip() else folder_name,
        "case_dir": str(case_dir),
        "defendant": defendant.strip() if defendant and defendant.strip() else None,
        "created_at": datetime.now().isoformat(),
        "status": "pending",
        "evidence_list": evidence_list
    }
    
    case_file = ANALYSIS_DIR / f"{case_id}.json"
    with open(case_file, "w", encoding="utf-8") as f:
        json.dump(case_info, f, ensure_ascii=False, indent=2)
    
    return {
        "case_id": case_id,
        "case_name": case_info["case_name"],
        "case_dir": str(case_dir),
        "evidence_count": len(evidence_list),
        "evidence_list": evidence_list,
        "message": f"已上传 {len(evidence_list)} 个 PDF 文件到 {folder_name}"
    }


@router.post("/create")
async def create_analysis(case_dir: str = Body(..., embed=True)):
    """
    创建新的分析任务
    
    扫描案卷目录，识别所有 PDF 文件
    """
    if not os.path.isdir(case_dir):
        raise HTTPException(status_code=400, detail=f"目录不存在: {case_dir}")
    
    # 生成案件 ID
    case_id = str(uuid.uuid4())[:8]
    
    # 扫描目录
    evidence_list = []
    pdf_files = list(Path(case_dir).rglob("*.pdf"))
    
    if not pdf_files:
        raise HTTPException(status_code=400, detail="目录中没有 PDF 文件")
    
    for i, pdf_path in enumerate(pdf_files):
        # 从文件名推断证据类型
        filename = pdf_path.name
        evidence_type = infer_evidence_type(filename)
        
        evidence_list.append({
            "id": f"ev_{i+1:03d}",
            "filename": filename,
            "filepath": str(pdf_path),
            "type": evidence_type,
            "pages": get_pdf_pages(pdf_path),
            "selected": False,
            "summary": None
        })
    
    # 保存案件信息
    case_info = {
        "case_id": case_id,
        "case_dir": case_dir,
        "is_temp": False,  # 本地路径引用，不复制文件
        "defendant": None,
        "created_at": datetime.now().isoformat(),
        "status": "pending",
        "evidence_list": evidence_list,
        "supplement_dirs": []  # 记录补充案卷目录
    }
    
    case_file = ANALYSIS_DIR / f"{case_id}.json"
    with open(case_file, "w", encoding="utf-8") as f:
        json.dump(case_info, f, ensure_ascii=False, indent=2)
    
    return {
        "case_id": case_id,
        "evidence_count": len(evidence_list),
        "evidence_list": evidence_list,
        "message": f"已扫描 {len(evidence_list)} 个 PDF 文件（本地引用，不占用额外空间）"
    }


@router.post("/add-supplement/{case_id}")
async def add_supplement_evidence(
    case_id: str,
    supplement_dir: str = Body(..., embed=True)
):
    """
    向已有案件添加补充案卷（目录路径方式）
    
    Args:
        case_id: 案件 ID
        supplement_dir: 补充案卷目录路径
    
    Returns:
        更新后的证据列表
    """
    if not os.path.isdir(supplement_dir):
        raise HTTPException(status_code=400, detail=f"目录不存在: {supplement_dir}")
    
    case_file = ANALYSIS_DIR / f"{case_id}.json"
    if not case_file.exists():
        raise HTTPException(status_code=404, detail="案件不存在")
    
    with open(case_file, "r", encoding="utf-8") as f:
        case_info = json.load(f)
    
    # 扫描补充目录
    pdf_files = list(Path(supplement_dir).rglob("*.pdf"))
    
    if not pdf_files:
        raise HTTPException(status_code=400, detail="补充目录中没有 PDF 文件")
    
    # 获取现有证据数量，用于生成新 ID
    existing_count = len(case_info.get("evidence_list", []))
    
    # 添加新证据
    new_evidence = []
    for i, pdf_path in enumerate(pdf_files):
        filename = pdf_path.name
        evidence_type = infer_evidence_type(filename)
        
        # 检查是否已存在（按文件名或路径）
        existing_paths = [e["filepath"] for e in case_info.get("evidence_list", [])]
        if str(pdf_path) in existing_paths:
            continue  # 跳过已存在的文件
        
        evidence_id = f"ev_{existing_count + len(new_evidence) + 1:03d}"
        new_evidence.append({
            "id": evidence_id,
            "filename": filename,
            "filepath": str(pdf_path),
            "type": evidence_type,
            "pages": get_pdf_pages(pdf_path),
            "selected": False,
            "summary": None,
            "is_supplement": True  # 标记为补充案卷
        })
    
    if not new_evidence:
        return {
            "case_id": case_id,
            "added_count": 0,
            "evidence_list": case_info["evidence_list"],
            "message": "补充目录中的文件已存在于案件中"
        }
    
    # 更新案件信息
    case_info["evidence_list"].extend(new_evidence)
    case_info["supplement_dirs"].append({
        "path": supplement_dir,
        "added_at": datetime.now().isoformat(),
        "added_count": len(new_evidence)
    })
    
    with open(case_file, "w", encoding="utf-8") as f:
        json.dump(case_info, f, ensure_ascii=False, indent=2)
    
    return {
        "case_id": case_id,
        "added_count": len(new_evidence),
        "total_count": len(case_info["evidence_list"]),
        "evidence_list": case_info["evidence_list"],
        "message": f"已添加 {len(new_evidence)} 个补充证据"
    }


@router.post("/add-supplement-files/{case_id}")
async def add_supplement_files(
    case_id: str,
    files: List[UploadFile] = File(...)
):
    """
    向已有案件添加补充案卷（文件上传方式）
    
    Args:
        case_id: 案件 ID
        files: 上传的 PDF 文件列表
    
    Returns:
        更新后的证据列表
    """
    case_file = ANALYSIS_DIR / f"{case_id}.json"
    if not case_file.exists():
        raise HTTPException(status_code=404, detail="案件不存在")
    
    with open(case_file, "r", encoding="utf-8") as f:
        case_info = json.load(f)
    
    # 过滤 PDF 文件
    pdf_files = [f for f in files if f.filename and f.filename.lower().endswith('.pdf')]
    
    if not pdf_files:
        raise HTTPException(status_code=400, detail="没有 PDF 文件")
    
    # 获取案卷目录
    case_dir = Path(case_info.get("case_dir", ""))
    
    # 如果是上传方式的案件，保存到 uploads 目录
    if case_info.get("is_temp") or not case_dir.exists():
        uploads_dir = ANALYSIS_DIR / "uploads" / case_id
        uploads_dir.mkdir(parents=True, exist_ok=True)
        case_dir = uploads_dir
    
    # 获取现有证据数量
    existing_count = len(case_info.get("evidence_list", []))
    existing_filenames = [e["filename"] for e in case_info.get("evidence_list", [])]
    
    # 保存文件并添加证据
    new_evidence = []
    for i, file in enumerate(pdf_files):
        filename = Path(file.filename).name
        
        # 检查是否已存在（按文件名）
        if filename in existing_filenames:
            continue
        
        # 保存文件
        file_path = case_dir / f"补充_{filename}"
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        evidence_type = infer_evidence_type(filename)
        evidence_id = f"ev_{existing_count + len(new_evidence) + 1:03d}"
        
        new_evidence.append({
            "id": evidence_id,
            "filename": filename,
            "filepath": str(file_path),
            "type": evidence_type,
            "pages": get_pdf_pages(str(file_path)),
            "selected": True,  # 补充证据默认选中
            "summary": None,
            "is_supplement": True
        })
    
    if not new_evidence:
        return {
            "case_id": case_id,
            "added_count": 0,
            "evidence_list": case_info["evidence_list"],
            "message": "上传的文件已存在于案件中"
        }
    
    # 更新案件信息
    case_info["evidence_list"].extend(new_evidence)
    
    with open(case_file, "w", encoding="utf-8") as f:
        json.dump(case_info, f, ensure_ascii=False, indent=2)
    
    return {
        "case_id": case_id,
        "added_count": len(new_evidence),
        "total_count": len(case_info["evidence_list"]),
        "evidence_list": case_info["evidence_list"],
        "message": f"已添加 {len(new_evidence)} 个补充证据"
    }


@router.get("/evidence/{case_id}")
async def get_evidence_list(case_id: str):
    """获取证据列表（包含 MD 缓存状态）"""
    case_file = ANALYSIS_DIR / f"{case_id}.json"
    if not case_file.exists():
        raise HTTPException(status_code=404, detail="案件不存在")
    
    with open(case_file, "r", encoding="utf-8") as f:
        case_info = json.load(f)
    
    # 检查每个证据的 MD 缓存状态
    evidence_list = case_info.get("evidence_list", [])
    for e in evidence_list:
        pdf_path = Path(e["filepath"])
        md_path = pdf_path.with_suffix(".md")
        e["has_md_cache"] = md_path.exists() and md_path.stat().st_mtime >= pdf_path.stat().st_mtime
        if e["has_md_cache"]:
            e["md_path"] = str(md_path)
            e["md_size"] = md_path.stat().st_size
        else:
            e["md_path"] = None
            e["md_size"] = None
    
    return {
        "case_id": case_id,
        "evidence_list": evidence_list,
        "defendant": case_info.get("defendant")
    }


@router.get("/evidence/{case_id}/file/{evidence_id}")
async def get_evidence_file(case_id: str, evidence_id: str):
    """获取证据 PDF 文件"""
    case_file = ANALYSIS_DIR / f"{case_id}.json"
    if not case_file.exists():
        raise HTTPException(status_code=404, detail="案件不存在")
    
    with open(case_file, "r", encoding="utf-8") as f:
        case_info = json.load(f)
    
    # 查找证据
    evidence = next((e for e in case_info["evidence_list"] if e["id"] == evidence_id), None)
    if not evidence:
        raise HTTPException(status_code=404, detail="证据不存在")
    
    file_path = Path(evidence["filepath"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    
    return FileResponse(
        file_path,
        media_type="application/pdf",
        filename=evidence["filename"]
    )


@router.put("/evidence/{case_id}/select")
async def select_evidence(
    case_id: str,
    evidence_ids: List[str] = Body(..., embed=True)
):
    """选择/取消选择证据"""
    case_file = ANALYSIS_DIR / f"{case_id}.json"
    if not case_file.exists():
        raise HTTPException(status_code=404, detail="案件不存在")
    
    with open(case_file, "r", encoding="utf-8") as f:
        case_info = json.load(f)
    
    # 更新选择状态
    for evidence in case_info["evidence_list"]:
        evidence["selected"] = evidence["id"] in evidence_ids
    
    with open(case_file, "w", encoding="utf-8") as f:
        json.dump(case_info, f, ensure_ascii=False, indent=2)
    
    selected_count = sum(1 for e in case_info["evidence_list"] if e["selected"])
    
    return {
        "case_id": case_id,
        "selected_count": selected_count,
        "message": f"已选择 {selected_count} 个证据"
    }


@router.get("/progress/{case_id}")
async def get_progress(case_id: str):
    """
    获取分析进度
    
    Returns:
        进度信息 {current, total, stage, message}
    """
    progress = analysis_progress.get(case_id, {})
    return {
        "case_id": case_id,
        "current": progress.get("current", 0),
        "total": progress.get("total", 0),
        "stage": progress.get("stage", "pending"),  # pending, extracting, analyzing, complete
        "message": progress.get("message", "等待开始")
    }


@router.post("/analyze/{case_id}")
async def analyze_case(
    case_id: str,
    defendant: str = Body(..., embed=True),
    use_ai: bool = Body(default=True, embed=True),  # 是否调用 AI 分析
    crime_type: Optional[str] = Body(default=None, embed=True)  # 新增：罪名类型，用于动态加载知识
):
    """
    分析案卷
    
    Args:
        case_id: 案件 ID
        defendant: 辩护对象姓名
        use_ai: 是否调用 AI 分析（默认 True）
    
    Returns:
        分析报告
    """
    case_file = ANALYSIS_DIR / f"{case_id}.json"
    if not case_file.exists():
        raise HTTPException(status_code=404, detail="案件不存在")
    
    with open(case_file, "r", encoding="utf-8") as f:
        case_info = json.load(f)
    
    # 更新辩护对象和状态
    case_info["defendant"] = defendant
    case_info["status"] = "analyzing"
    
    with open(case_file, "w", encoding="utf-8") as f:
        json.dump(case_info, f, ensure_ascii=False, indent=2)
    
    # 获取选中的证据（如果没有选择，则使用全部）
    selected_evidence = [e for e in case_info["evidence_list"] if e.get("selected")]
    if not selected_evidence:
        selected_evidence = case_info["evidence_list"]
    
    total = len(selected_evidence)
    
    # 初始化进度
    analysis_progress[case_id] = {
        "current": 0,
        "total": total,
        "stage": "extracting",
        "message": f"开始提取 {total} 个证据文本..."
    }
    
    # 提取证据文本（PDF → MD，优先使用已缓存的 MD 文件）
    evidence_texts = []
    for i, evidence in enumerate(selected_evidence):
        try:
            # 使用 PDF → MD 转换模块
            # 优先级：1. 已有 MD 缓存 → 2. MinerU API → 3. pdfplumber → 4. PyMuPDF
            text, _ = get_evidence_text(evidence["filepath"], prefer_md=True)
            evidence_texts.append({
                "id": evidence["id"],
                "filename": evidence["filename"],
                "type": evidence["type"],
                "text": text,  # Markdown 格式文本，保留结构
                "format": "markdown"  # 标记为 Markdown 格式
            })
        except Exception as e:
            # 单个文件失败不影响整体
            evidence_texts.append({
                "id": evidence["id"],
                "filename": evidence["filename"],
                "type": evidence["type"],
                "text": f"[无法提取文本：{e}]",
                "format": "error"
            })
        
        # 更新进度
        analysis_progress[case_id]["current"] = i + 1
        analysis_progress[case_id]["message"] = f"已转换 {i + 1}/{total} 个证据为 Markdown ({(i + 1) / total * 100:.0f}%)"
    
    if use_ai:
        # 调用 LLM 进行分析
        try:
            # 更新进度：开始 AI 分析
            analysis_progress[case_id]["stage"] = "analyzing"
            analysis_progress[case_id]["message"] = "正在调用 AI 进行智能分析..."
            
            client = get_llm_client("analysis")
            report_text = await client.analyze_case(defendant, evidence_texts, crime_type=crime_type)
            
            # 解析报告为结构化数据
            # 更新进度：分析完成
            analysis_progress[case_id]["stage"] = "complete"
            analysis_progress[case_id]["message"] = "分析完成，正在生成报告..."
            
            report = parse_report(report_text)
            report["raw_markdown"] = report_text
            report["generated_at"] = datetime.now().isoformat()
            
            # 保存报告
            case_info["report"] = report
            case_info["status"] = "ready"
            
            # 保存报告到原始目录（本地路径方式）
            if not case_info.get("is_temp"):
                # 本地路径方式：报告保存到案卷目录
                case_dir = Path(case_info.get("case_dir", ""))
                if case_dir.exists():
                    report_file = case_dir / f"辩护分析报告_{defendant}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
                    try:
                        with open(report_file, "w", encoding="utf-8") as f:
                            f.write(report_text)
                        case_info["report_file"] = str(report_file)
                    except Exception:
                        pass  # 保存失败不影响主流程
            
            with open(case_file, "w", encoding="utf-8") as f:
                json.dump(case_info, f, ensure_ascii=False, indent=2)
            
            # 清理进度
            del analysis_progress[case_id]
            
            return {
                "case_id": case_id,
                "defendant": defendant,
                "evidence_count": len(selected_evidence),
                "report": report,
                "report_file": case_info.get("report_file"),
                "message": "分析完成"
            }
        except Exception as e:
            case_info["status"] = "error"
            with open(case_file, "w", encoding="utf-8") as f:
                json.dump(case_info, f, ensure_ascii=False, indent=2)
            raise HTTPException(status_code=500, detail=f"分析失败: {str(e)}")
    else:
        # 返回提示词，由前端调用
        prompt = build_analysis_prompt(defendant, evidence_texts)
        
        case_info["status"] = "ready"
        
        return {
            "case_id": case_id,
            "defendant": defendant,
            "evidence_count": len(selected_evidence),
            "prompt": prompt,
            "evidence_texts": evidence_texts,
            "message": "准备就绪，请调用 LLM 进行分析"
        }


@router.post("/report/{case_id}")
async def save_report(
    case_id: str,
    report: Dict[str, Any] = Body(...)
):
    """保存分析报告"""
    case_file = ANALYSIS_DIR / f"{case_id}.json"
    if not case_file.exists():
        raise HTTPException(status_code=404, detail="案件不存在")
    
    with open(case_file, "r", encoding="utf-8") as f:
        case_info = json.load(f)
    
    # 保存报告
    case_info["report"] = report
    case_info["status"] = "ready"
    
    with open(case_file, "w", encoding="utf-8") as f:
        json.dump(case_info, f, ensure_ascii=False, indent=2)
    
    return {
        "case_id": case_id,
        "message": "报告已保存"
    }


@router.get("/report/{case_id}")
async def get_report(case_id: str):
    """获取分析报告"""
    case_file = ANALYSIS_DIR / f"{case_id}.json"
    if not case_file.exists():
        raise HTTPException(status_code=404, detail="案件不存在")
    
    with open(case_file, "r", encoding="utf-8") as f:
        case_info = json.load(f)
    
    if "report" not in case_info:
        raise HTTPException(status_code=404, detail="报告尚未生成")
    
    return {
        "case_id": case_id,
        "report": case_info["report"],
        "defendant": case_info.get("defendant")
    }


# ========== 报告页对话 API（必须在 /chat/{case_id} 之前注册，避免被通配匹配拦截） ==========

@router.post("/chat/report")
async def chat_report(
    message: str = Body(...),
    report_context: str = Body(default=''),
    evidence_context: str = Body(default=''),
    history: list = Body(default=[]),
):
    """报告页对话 — 基于案卷分析报告和证据回答"""
    client = get_llm_client("analysis")

    history_messages = []
    for h in history[-10:]:
        role = h.get('role', 'user')
        content = h.get('content', '')
        if role in ('user', 'assistant'):
            history_messages.append({'role': role, 'content': content})

    user_prompt = f"## 辩护分析报告\n{report_context}\n\n## 证据材料\n{evidence_context}\n\n## 用户问题\n{message}"

    system_prompt = """你是刑事辩护律师助手，正在协助律师查阅案卷材料。
你有以下背景信息：
1. 辩护分析报告（如已生成）
2. 证据材料（案卷拆分文件或PDF摘要）

回答要求：
- 如果用户要求总结材料，直接按证据内容逐条总结，不要做辩护分析
- 如果用户要求分析矛盾，聚焦内容差异即可
- 如果用户询问法律问题，结合辩护报告给出专业意见
- 回答应简洁、聚焦用户问题本身，不要自动展开全面辩护分析
- 引用的内容必须来自提供的证据材料，不要编造"""

    messages = [{'role': 'system', 'content': system_prompt}]
    messages.extend(history_messages)
    messages.append({'role': 'user', 'content': user_prompt})

    try:
        answer = await client.chat(messages)
        return {'answer': answer}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'对话失败: {str(e)}')


@router.post("/chat/update")
async def chat_update(
    current_report: str = Body(...),
    update_instruction: str = Body(...),
    context: str = Body(default=''),
):
    """修改报告 — LLM 根据意见更新辩护报告内容"""
    client = get_llm_client("analysis")

    system_prompt = """你是刑事辩护律师助手。用户需要对已有的辩护分析报告进行修改。
请根据用户的修改意见，保留报告的有效内容，融入新的意见，生成更新后的完整报告。
要求：
1. 保持报告的原有结构和专业性
2. 将用户的修改意见自然地融入相关内容
3. 如果修改意见涉及原有内容的矛盾，以用户意见为准
4. 输出完整的更新后报告，不要只输出修改部分"""

    user_prompt = f"## 当前报告\n{current_report}\n\n## 修改意见\n{update_instruction}\n\n## 其他分析上下文\n{context}"

    messages = [{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': user_prompt}]

    try:
        updated = await client.chat(messages)
        summary = updated[:200] + '...' if len(updated) > 200 else updated
        return {'updated_report': updated, 'summary': '已按要求更新报告'}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'更新失败: {str(e)}')


@router.post("/chat/{case_id}")
async def chat(
    case_id: str,
    message: str = Body(..., embed=True),
    history: List[Dict[str, str]] = Body(default=[], embed=True),
    evidence_ids: List[str] = Body(default=[], embed=True),
    use_ai: bool = Body(default=True, embed=True),
    include_report: bool = Body(default=False, embed=True)  # 是否包含原报告上下文（用于更新报告）
):
    """
    对话分析
    
    基于证据进行对话问答
    
    Args:
        case_id: 案件 ID
        message: 用户消息
        history: 对话历史
        evidence_ids: 指定对话的证据 ID 列表（为空则使用全部）
        use_ai: 是否调用 AI
        include_report: 是否包含原报告（用于更新报告场景）
    """
    case_file = ANALYSIS_DIR / f"{case_id}.json"
    if not case_file.exists():
        raise HTTPException(status_code=404, detail="案件不存在")
    
    with open(case_file, "r", encoding="utf-8") as f:
        case_info = json.load(f)
    
    if use_ai:
        # 调用 LLM 进行对话
        try:
            # 构建上下文：始终基于原始证据，不引用报告结论
            if evidence_ids:
                # 只使用指定的证据
                selected_evidence = [
                    e for e in case_info["evidence_list"] 
                    if e["id"] in evidence_ids
                ]
            else:
                # 使用全部选中的证据
                selected_evidence = [
                    e for e in case_info["evidence_list"] 
                    if e.get("selected", True)
                ]
            
            # 提取证据文本（PDF → MD）
            evidence_texts = []
            for e in selected_evidence:
                text, _ = get_evidence_text(e["filepath"], prefer_md=True)
                evidence_texts.append({
                    "filename": e["filename"],
                    "type": e["type"],
                    "text": text,
                    "format": "markdown"
                })
            
            evidence_context = "\n\n".join([
                f"### {t['filename']} ({t['type']})\n{t['text']}"
                for t in evidence_texts
            ])
            
            # 更新报告场景：需要提供原报告供 LLM 参考修改
            report_context = None
            if include_report and "report" in case_info:
                report = case_info["report"]
                report_context = report.get("raw_markdown", json.dumps(report, ensure_ascii=False))
            
            client = get_llm_client("analysis")
            answer = await client.chat_about_case(message, evidence_context, report_context)
            
            return {
                "case_id": case_id,
                "answer": answer,
                "message": "对话完成"
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"对话失败: {str(e)}")
    else:
        # 返回提示词，由前端调用
        # 构建证据上下文（不引用报告）
        selected_evidence = [
            e for e in case_info["evidence_list"] 
            if e.get("selected", True)
        ]
        evidence_texts = []
        for e in selected_evidence:
            text, _ = get_evidence_text(e["filepath"], prefer_md=True)
            evidence_texts.append({
                "filename": e["filename"],
                "type": e["type"],
                "text": text[:15000],  # Markdown 格式，保留结构
                "format": "markdown"
            })
        
        evidence_context = "\n\n".join([
            f"### {t['filename']} ({t['type']})\n{t['text']}"
            for t in evidence_texts
        ])
        
        prompt = f"""你是一个专业的刑事辩护律师助手。基于以下案卷证据材料，回答用户的问题。

**重要**：分析必须基于原始证据，不能引用分析报告的结论作为既定事实。

案卷证据材料：
{evidence_context[:context_budget.content_budget_chars()]}

用户问题：{message}

请基于证据和法律条文，独立分析问题，提供专业、准确的回答。
"""
        
        return {
            "case_id": case_id,
            "prompt": prompt,
            "message": "请调用 LLM 进行对话"
        }


# ========== 增量更新报告 API ==========

@router.post("/update-report/{case_id}")
async def update_report(
    case_id: str,
    instruction: str = Body(..., embed=True),
    use_ai: bool = Body(default=True, embed=True)
):
    """
    增量更新报告 - 只输出修改部分，节省 token
    
    Args:
        case_id: 案件 ID
        instruction: 修改指令
        use_ai: 是否调用 AI
    
    Returns:
        更新后的完整报告
    """
    case_file = ANALYSIS_DIR / f"{case_id}.json"
    if not case_file.exists():
        raise HTTPException(status_code=404, detail="案件不存在")
    
    with open(case_file, "r", encoding="utf-8") as f:
        case_info = json.load(f)
    
    if "report" not in case_info:
        raise HTTPException(status_code=400, detail="报告尚未生成")
    
    if use_ai:
        try:
            # 获取选中的证据
            selected_evidence = [
                e for e in case_info["evidence_list"] 
                if e.get("selected", True)
            ]
            
            # 提取证据文本（PDF → MD，限制字符数节省 token）
            evidence_texts = []
            for e in selected_evidence:
                text, _ = get_evidence_text(e["filepath"], prefer_md=True)
                evidence_texts.append({
                    "filename": e["filename"],
                    "type": e["type"],
                    "text": text[:25000],  # Markdown 格式，每份证据最多 2.5 万字
                    "format": "markdown"
                })
            
            evidence_context = "\n\n".join([
                f"### {t['filename']} ({t['type']})\n{t['text']}"
                for t in evidence_texts
            ])
            
            # 获取原报告
            report = case_info["report"]
            original_markdown = report.get("raw_markdown", json.dumps(report, ensure_ascii=False))
            
            # 调用增量更新
            client = get_llm_client("analysis")
            update_result = await client.update_report_section(
                instruction, 
                original_markdown, 
                evidence_context
            )
            
            # 应用更新到原报告
            updated_markdown = apply_report_update(original_markdown, update_result)
            
            # 解析新报告为结构化数据
            updated_report = parse_report(updated_markdown)
            updated_report["raw_markdown"] = updated_markdown
            updated_report["generated_at"] = datetime.now().isoformat()
            updated_report["last_update"] = {
                "instruction": instruction,
                "timestamp": datetime.now().isoformat(),
                "action": update_result.get("action"),
                "target_section": update_result.get("target_section")
            }
            
            # 保存更新后的报告
            case_info["report"] = updated_report
            case_info["status"] = "ready"
            
            with open(case_file, "w", encoding="utf-8") as f:
                json.dump(case_info, f, ensure_ascii=False, indent=2)
            
            return {
                "case_id": case_id,
                "report": updated_report,
                "update_info": update_result,
                "message": "报告已更新"
            }
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"更新失败：{str(e)}")
    else:
        raise HTTPException(status_code=400, detail="非 AI 模式暂不支持")


def apply_report_update(original_markdown: str, update_result: Dict[str, Any]) -> str:
    """
    应用增量更新到原报告（支持多个更新批量应用）
    
    Args:
        original_markdown: 原报告 Markdown
        update_result: LLM 返回的更新结果（包含 updates 数组）
    
    Returns:
        更新后的 Markdown
    """
    import re
    
    result = original_markdown
    updates = update_result.get("updates", [])
    
    # 如果没有 updates 数组，兼容旧的单更新格式
    if not updates:
        action = update_result.get("action", "replace")
        target_section = update_result.get("target_section", "")
        new_content = update_result.get("new_content", "")
        position = update_result.get("position", "")
        updates = [{"action": action, "target_section": target_section, "new_content": new_content, "position": position}]
    
    # 按顺序应用每个更新
    for update in updates:
        action = update.get("action", "replace")
        target_section = update.get("target_section", "")
        new_content = update.get("new_content", "")
        position = update.get("position", "")
        
        if action == "delete":
            # 删除章节
            if target_section:
                pattern = rf"###\s*{re.escape(target_section)}.*?(?=###\s|$)"
                result = re.sub(pattern, "", result, flags=re.DOTALL)
        
        elif action == "replace":
            # 替换章节
            if target_section and new_content:
                pattern = rf"(###\s*{re.escape(target_section)}).*?(?=###\s|$)"
                replacement = f"\\1\n\n{new_content}"
                new_result = re.sub(pattern, replacement, result, flags=re.DOTALL)
                if new_result != result:
                    result = new_result
                else:
                    # 如果正则匹配失败，追加到末尾
                    result = result + f"\n\n### {target_section}\n\n{new_content}"
        
        elif action == "insert":
            # 插入新章节
            if new_content:
                if position.startswith("after:"):
                    target = position[6:]
                    pattern = rf"(###\s*{re.escape(target)}.*?)(?=###\s|$)"
                    match = re.search(pattern, result, flags=re.DOTALL)
                    if match:
                        insert_pos = match.end()
                        result = result[:insert_pos] + f"\n\n### {target_section}\n\n{new_content}" + result[insert_pos:]
                        continue
                # 默认追加到末尾
                result = result + f"\n\n### {target_section}\n\n{new_content}"
    
    return result


def infer_evidence_type(filename: str) -> str:
    """从文件名推断证据类型"""
    filename_lower = filename.lower()
    
    if "起诉" in filename or "指控" in filename:
        return "起诉意见书"
    elif "讯问" in filename or "供述" in filename or "笔录" in filename:
        return "讯问笔录"
    elif "证言" in filename or "证人" in filename:
        return "证人证言"
    elif "鉴定" in filename:
        return "鉴定意见"
    elif "勘验" in filename or "检查" in filename:
        return "勘验笔录"
    elif "辨认" in filename:
        return "辨认笔录"
    elif "银行" in filename or "流水" in filename or "转账" in filename:
        return "书证-金融"
    elif "合同" in filename or "协议" in filename:
        return "书证-合同"
    elif "身份" in filename or "户籍" in filename:
        return "书证-身份"
    elif "拘留" in filename or "逮捕" in filename or "取保" in filename:
        return "程序性文书"
    else:
        return "其他证据"


def get_pdf_pages(pdf_path: str) -> int:
    """获取 PDF 页数"""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(pdf_path)
        pages = doc.page_count
        doc.close()
        return pages
    except Exception:
        return 0


def extract_pdf_text(pdf_path: str, max_pages: int = 50) -> str:
    """提取 PDF 文本"""
    try:
        import fitz
        doc = fitz.open(pdf_path)
        text_parts = []
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            text_parts.append(page.get_text())
        doc.close()
        return "\n".join(text_parts)
    except Exception as e:
        return f"[文本提取失败: {e}]"


def build_analysis_prompt(defendant: str, evidence_texts: List[Dict]) -> str:
    """构建分析提示词"""
    evidence_section = "\n\n".join([
        f"### {e['filename']} ({e['type']})\n{e['text']}"
        for e in evidence_texts
    ])
    
    return f"""你是一个专业的刑事辩护律师。请分析以下案卷材料，为被告人 **{defendant}** 提供辩护分析。

## 案卷材料

{evidence_section}

## 分析要求

请按照以下结构输出分析报告：

### 一、指控要素分析
基于起诉意见书，提取指控的核心要素：
- 罪名
- 涉案金额
- 涉案时间
- 涉案人员
- 指控行为

### 二、证据-要素映射表
列出每份证据证明的指控要素，格式：

| 证据名称 | 证明的要素 | 关键内容摘要 | 证明力评估 |

### 三、证据三性分析
对每份证据进行合法性、真实性、关联性分析：

| 证据名称 | 合法性 | 真实性 | 关联性 | 综合评价 |

### 四、矛盾识别
对比供述之间、供述与书证之间的矛盾点

### 五、辩护要点
为 {defendant} 提出具体的辩护要点

### 六、量刑情节
评估可能影响量刑的因素

请基于《刑事诉讼法》的规定，提供专业、准确的分析。
"""


def parse_report(markdown_text: str) -> Dict[str, Any]:
    """
    解析 Markdown 报告为结构化数据
    
    Args:
        markdown_text: Markdown 格式的报告文本
    
    Returns:
        结构化的报告数据
    """
    import re
    
    report = {
        "indictment_summary": {},
        "evidence_map": [],
        "evidence_analysis": [],
        "contradictions": [],
        "defense_points": [],
        "sentencing_factors": {}
    }
    
    # 提取各章节内容
    sections = {}
    current_section = None
    current_content = []
    
    for line in markdown_text.split("\n"):
        # 检测章节标题
        section_match = re.match(r"^###\s*(一|二|三|四|五|六)[、.．]\s*(.+)$", line)
        if section_match:
            if current_section:
                sections[current_section] = "\n".join(current_content)
            current_section = section_match.group(2).strip()
            current_content = []
        else:
            current_content.append(line)
    
    if current_section:
        sections[current_section] = "\n".join(current_content)
    
    # 解析指控要素
    if "指控要素分析" in sections:
        content = sections["指控要素分析"]
        for key in ["罪名", "涉案金额", "涉案时间", "涉案人员", "指控行为"]:
            match = re.search(rf"[-*]\s*\*\*{key}[：:]\*\*\s*(.+)", content)
            if match:
                report["indictment_summary"][key] = match.group(1).strip()
    
    # 解析辩护要点
    if "辩护要点" in sections:
        content = sections["辩护要点"]
        for match in re.finditer(r"[-*]\s*(\d+[.．])?\s*(.+)", content):
            point = match.group(2).strip()
            if point and not point.startswith("|"):  # 排除表格行
                report["defense_points"].append(point)
    
    # 解析矛盾点
    if "矛盾识别" in sections:
        content = sections["矛盾识别"]
        for match in re.finditer(r"[-*]\s*(\d+[.．])?\s*(.+)", content):
            contradiction = match.group(2).strip()
            if contradiction and not contradiction.startswith("|"):
                report["contradictions"].append(contradiction)
    
    # 添加原始章节内容
    report["sections"] = sections
    
    return report