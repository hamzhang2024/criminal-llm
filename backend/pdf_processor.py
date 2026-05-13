"""
PDF 处理模块

负责 PDF 上传、缩略图生成、拆分等核心功能
使用 pypdf + pdf2image 替代 PyMuPDF
"""
import json
import uuid
import shutil
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime

from pdf2image import convert_from_path
from PIL import Image
from pypdf import PdfReader, PdfWriter

from config import (
    UPLOAD_DIR, OUTPUT_DIR, CACHE_DIR,
    THUMBNAIL_WIDTH, THUMBNAIL_DPI
)


def check_poppler():
    """检查 poppler 是否已安装"""
    try:
        result = subprocess.run(
            ['pdftoppm', '-h'],
            capture_output=True,
            check=False
        )
        return result.returncode == 0 or result.returncode == 99  # 99 是 help 的返回码
    except FileNotFoundError:
        return False


class PDFProcessor:
    """PDF 处理器"""
    
    def __init__(self, job_id: str):
        self.job_id = job_id
        self.job_dir = UPLOAD_DIR / job_id
        self.thumbnail_dir = CACHE_DIR / job_id / "thumbnails"
        self.output_dir = OUTPUT_DIR / job_id
        
        # 确保目录存在
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.thumbnail_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    async def save_upload(self, file_content: bytes, filename: str) -> Path:
        """保存上传的文件"""
        file_path = self.job_dir / filename
        with open(file_path, "wb") as f:
            f.write(file_content)
        return file_path
    
    def generate_thumbnails(self, pdf_path: Path) -> List[Dict[str, Any]]:
        """
        生成所有页面的缩略图
        
        Returns:
            缩略图信息列表
        """
        # 检查 poppler
        if not check_poppler():
            raise RuntimeError(
                "需要安装 poppler 工具来生成缩略图。\n"
                "macOS: brew install poppler\n"
                "Ubuntu: sudo apt-get install poppler-utils"
            )
        
        thumbnails = []
        
        # 使用 pdf2image 转换
        images = convert_from_path(
            pdf_path,
            dpi=THUMBNAIL_DPI,
            size=(THUMBNAIL_WIDTH, None)  # 宽度固定，高度按比例
        )
        
        for i, image in enumerate(images):
            page_num = i + 1
            
            # 保存缩略图
            thumbnail_path = self.thumbnail_dir / f"page_{page_num}.png"
            image.save(thumbnail_path, "PNG")
            
            thumbnails.append({
                "page": page_num,
                "path": str(thumbnail_path),
                "url": f"/thumbnails/{self.job_id}/thumbnails/page_{page_num}.png",
                "width": image.width,
                "height": image.height
            })
        
        return thumbnails
    
    def extract_text(self, pdf_path: Path, max_pages: int = 200) -> Dict[int, str]:
        """
        提取 PDF 文本内容
        
        Args:
            pdf_path: PDF 文件路径
            max_pages: 最大提取页数（默认200页，-1表示全部）
        
        Returns:
            页码 -> 文本内容的映射
        """
        texts = {}
        reader = PdfReader(pdf_path)
        total_pages = len(reader.pages)
        
        # 如果 max_pages 为 -1，提取全部页面
        pages_to_extract = total_pages if max_pages == -1 else min(total_pages, max_pages)
        
        for i in range(pages_to_extract):
            page = reader.pages[i]
            text = page.extract_text()
            if text and text.strip():
                texts[i + 1] = text.strip()
        
        return texts
    
    def get_page_count(self, pdf_path: Path) -> int:
        """获取 PDF 页数"""
        reader = PdfReader(pdf_path)
        return len(reader.pages)
    
    def split_pdf(
        self, 
        pdf_path: Path, 
        splits: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        按 split 方案拆分 PDF（简化版，不支持删除）
        """
        return self.split_pdf_with_deletions(pdf_path, splits, set())
    
    def split_pdf_with_deletions(
        self, 
        pdf_path: Path, 
        splits: List[Dict[str, Any]],
        deleted_pages: set
    ) -> List[Dict[str, Any]]:
        """
        按 split 方案拆分 PDF（支持删除页面和不连续页面）
        
        Args:
            pdf_path: 原始 PDF 路径
            splits: 拆分方案
            deleted_pages: 要删除的页面集合（1-indexed）
        
        Returns:
            拆分结果列表
        """
        results = []
        reader = PdfReader(pdf_path)
        total_pages = len(reader.pages)
        
        for split in splits:
            name = split["name"]
            
            # 获取要包含的页面列表
            if "pages" in split and split["pages"]:
                # 使用显式的页面列表（支持不连续）
                pages_to_include = [p for p in split["pages"] if p not in deleted_pages]
            else:
                # 使用 start_page/end_page 范围
                start = split["start_page"]
                end = split["end_page"]
                pages_to_include = [p for p in range(start, end + 1) 
                                   if p <= total_pages and p not in deleted_pages]
            
            if not pages_to_include:
                continue  # 跳过空分组
            
            # 创建新 PDF
            writer = PdfWriter()
            for page_num in pages_to_include:
                # 转为 0-indexed
                writer.add_page(reader.pages[page_num - 1])
            
            # 保存文件
            output_path = self.output_dir / f"{name}.pdf"
            with open(output_path, "wb") as f:
                writer.write(f)
            
            results.append({
                "name": name,
                "path": str(output_path),
                "pages": f"{pages_to_include[0]}-{pages_to_include[-1]}" if len(pages_to_include) > 1 else str(pages_to_include[0]),
                "page_count": len(pages_to_include),
                "actual_pages": pages_to_include
            })
        
        return results
    
    def save_split_plan(self, splits: List[Dict[str, Any]], deleted_pages: List[int] = None) -> Path:
        """保存拆分方案"""
        plan_path = self.job_dir / "split_plan.json"
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump({
                "job_id": self.job_id,
                "splits": splits,
                "deleted_pages": deleted_pages or [],
                "updated_at": datetime.now().isoformat()
            }, f, ensure_ascii=False, indent=2)
        return plan_path
    
    def load_split_plan(self) -> Optional[Dict[str, Any]]:
        """加载拆分方案"""
        plan_path = self.job_dir / "split_plan.json"
        if plan_path.exists():
            with open(plan_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None


def create_job() -> str:
    """创建新的处理任务，返回 job_id"""
    return str(uuid.uuid4())[:8]


def get_processor(job_id: str) -> PDFProcessor:
    """获取 PDF 处理器实例"""
    return PDFProcessor(job_id)