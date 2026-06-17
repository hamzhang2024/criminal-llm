#!/usr/bin/env python3
"""
PaddleOCR-VL 异步批量转换模块

优化点：
1. 异步并发提交多个 PDF 任务
2. asyncio 并发轮询任务状态
3. 固定 10 并发处理
4. 自动配额管理

用法：
    from paddleocr_async import AsyncPaddleOCRConverter

    converter = AsyncPaddleOCRConverter(token="your-token")
    results = await converter.convert_batch([pdf1, pdf2, pdf3])
"""

import asyncio
import json
import logging
import os
import re
import shutil
import ssl
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import aiohttp

logger = logging.getLogger(__name__)


def _get_ssl_context():
    """获取 SSL 上下文，兼容 aiohttp

    aiohttp 的 ssl 参数需要 SSLContext 对象，不能是文件路径字符串。
    macOS 打包后 certifi 证书可能失效，使用系统证书。
    """
    if sys.platform == "darwin" and getattr(sys, "frozen", False):
        try:
            ctx = ssl.create_default_context()
            ctx.load_verify_locations('/etc/ssl/cert.pem')
            logger.debug("[SSL] 使用 macOS 系统证书 /etc/ssl/cert.pem")
            return ctx
        except Exception as e:
            logger.warning(f"[SSL] 加载系统证书失败: {e}，使用默认验证")
            return True
    return True  # 非打包环境使用默认验证


# aiohttp 专用 SSL 配置
_SSL_CONTEXT = _get_ssl_context()

# ═══════════════════════════════════════════════════════════
# 配置常量
# ═══════════════════════════════════════════════════════════
PADDLEOCR_API_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
PADDLEOCR_MODEL = "PaddleOCR-VL-1.6"
PADDLEOCR_DAILY_PAGE_LIMIT = 20000  # 每日转换页数配额（PaddleOCR-VL-1.6 限制）
DEFAULT_TIMEOUT = 3600  # 1 小时
POLL_INTERVAL = 3  # 轮询间隔 3 秒（加快响应）
DEFAULT_MAX_CONCURRENT = 3  # 默认并发数（与 MinerU 一致，避免 API 限流）

# 分段限制（大文件自动拆分）
# PaddleOCR-VL-1.6 建议 100 页以内，超过会处理但可能影响质量
PADDLEOCR_MAX_PAGES = 100  # 单次提交建议页数（超过会自动拆分以保证质量）
PADDLEOCR_MAX_FILE_SIZE = 80 * 1024 * 1024  # 单次提交最大文件大小 (80MB)

# 刑事案卷专用参数（优化讯问时间等关键信息识别）
PADDLEOCR_OPTIONAL_PAYLOAD = {
    # 预处理：扫描件可能有旋转/弯曲
    "useDocOrientationClassify": True,   # 自动纠正文档旋转
    "useDocUnwarping": True,             # 修复几何弯曲

    # 版面分析
    "useLayoutDetection": True,          # 开启版面分析，识别表格/标题/段落
    "useChartRecognition": False,        # 案卷基本无图表，关闭节省资源
    "layoutThreshold": 0.5,              # 版面检测置信度阈值

    # 生成参数（优化准确性，降低幻觉）
    "repetitionPenalty": 1.2,            # 抑制重复输出（降低以减少对数字的影响）
    "temperature": 0.1,                  # 稍提高温度，改善日期/时间识别
    "topP": 0.7,                         # 提高采样范围，改善多样性
    "minPixels": 640 * 640,              # 提高最小分辨率，改善小字识别
    "maxPixels": 1600 * 1600,            # 提高最大分辨率，改善细节识别

    # 输出格式
    "restructurePages": False,           # 不跨页重构（案卷按原始页序）
    "mergeTables": True,                 # 跨页表格合并
    "relevelTitles": True,               # 自动识别标题层级
    "prettifyMarkdown": True,            # 美化 Markdown 排版
    "showFormulaNumber": False,          # 案卷无公式，关闭编号
    "visualize": False,                  # 不需要可视化结果
}


@dataclass
class ConvertResult:
    """单个文件的转换结果"""
    file_name: str
    success: bool
    text: Optional[str] = None
    images_dir: Optional[Path] = None
    error: Optional[str] = None
    pages: int = 0


@dataclass
class BatchProgress:
    """批量转换进度"""
    total: int = 0
    completed: int = 0
    failed: int = 0
    current_files: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════
# 配额管理
# ═══════════════════════════════════════════════════════════
def _get_quota_file() -> Path:
    """获取配额文件路径"""
    try:
        from config import DATA_DIR
        return DATA_DIR / "paddleocr_quota.json"
    except ImportError:
        import tempfile
        return Path(tempfile.gettempdir()) / "paddleocr_quota.json"


def _load_quota() -> dict:
    """读取今日配额状态"""
    quota_path = _get_quota_file()
    today = str(date.today())
    try:
        if quota_path.exists():
            data = json.loads(quota_path.read_text(encoding="utf-8"))
            if data.get("date") == today:
                return data
    except Exception:
        pass
    return {"date": today, "used_pages": 0}


def _save_quota(data: dict):
    """保存配额状态"""
    _get_quota_file().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def get_daily_quota_status() -> dict:
    """获取今日配额使用状态（PaddleOCR 每天 20000 页，实际无限制）"""
    data = _load_quota()
    used = data.get("used_pages", 0)
    return {
        "date": data.get("date", str(date.today())),
        "used_pages": used,
        "total_limit": PADDLEOCR_DAILY_PAGE_LIMIT,
        "remaining_pages": PADDLEOCR_DAILY_PAGE_LIMIT,  # 始终显示有剩余
        "exceeded": False,  # 始终不超限
    }


def _get_paddleocr_token() -> str:
    """获取 PaddleOCR Token"""
    env_token = os.environ.get("PADDLEOCR_TOKEN", "")
    if env_token:
        return env_token

    try:
        from config_manager import get_config_value
        token = get_config_value("paddleocr_token")
        if token:
            return token
    except ImportError:
        pass

    return ""


def _split_pdf_pages(pdf_path: Path, chunk_size: int = PADDLEOCR_MAX_PAGES) -> List[Tuple[Path, int, int]]:
    """将大 PDF 按页数/文件大小分段

    Args:
        pdf_path: PDF 文件路径
        chunk_size: 每段最大页数

    Returns:
        List of (chunk_path, start_page, end_page) tuples
        Empty list if no splitting needed

    Raises:
        RuntimeError: 如果 PDF 无法打开或读取
    """
    try:
        import fitz
    except ImportError:
        logger.error("[PaddleOCR] fitz 模块未安装，无法分段 PDF")
        raise RuntimeError("fitz 模块未安装")

    try:
        doc = fitz.open(str(pdf_path))
        total = len(doc)
        file_size = pdf_path.stat().st_size
        doc.close()
    except Exception as e:
        logger.error(f"[PaddleOCR] 无法打开 PDF {pdf_path.name}: {type(e).__name__}: {e}")
        raise RuntimeError(f"无法打开 PDF {pdf_path.name}: {e}")

    if total <= chunk_size and file_size <= PADDLEOCR_MAX_FILE_SIZE:
        return []  # 不需要拆分

    # 计算满足文件大小限制的每段最大页数
    if total > 0:
        avg_page_size = file_size / total
        if avg_page_size > 0:
            max_pages_by_size = int(PADDLEOCR_MAX_FILE_SIZE * 0.80 / avg_page_size)
            chunk_size = min(chunk_size, max_pages_by_size)
            chunk_size = max(chunk_size, 10)

    chunks = []
    for start in range(0, total, chunk_size):
        end = min(start + chunk_size, total)
        new_doc = fitz.open()
        new_doc.insert_pdf(fitz.open(str(pdf_path)), from_page=start, to_page=end - 1)
        tmp_path = Path(pdf_path.parent) / f"_chunk_{start+1}-{end}_{pdf_path.name}"
        new_doc.save(str(tmp_path))
        new_doc.close()
        chunks.append((tmp_path, start + 1, end))  # 页码从1开始

    logger.info(f"[PaddleOCR] 大文件分段: {pdf_path.name} → {len(chunks)} 个 chunk")
    return chunks


# ═══════════════════════════════════════════════════════════
# 后处理函数（复用自 paddleocr_remote.py）
# ═══════════════════════════════════════════════════════════
def _clean_latex_markup(text: str) -> str:
    """清理 LaTeX 格式标记"""
    text = re.sub(r'\\$\\s*\\\\underline\\{\\\\text\\{([^}]*)\\}\\}\\s*\\$', r'\\1', text)
    text = re.sub(r'\\$\\s*\\\\text\\{([^}]*)\\}\\s*\\$', r'\\1', text)
    text = re.sub(r'\\$\\s*\\\\textbf\\{([^}]*)\\}\\s*\\$', r'\\1', text)
    text = re.sub(r'\\$\\s*\\\\emph\\{([^}]*)\\}\\s*\\$', r'\\1', text)
    text = re.sub(r'\\$\\s*\\\\underline\\{[\\s\\\\]*\\}\\s*\\$', '___', text)
    text = re.sub(r'\\$\\s*\\\\underline\\{\\\\text\\{[\\s\\\\]*\\}\\}\\s*\\$', '___', text)

    patterns = [
        (r'\\\\underline\\{([^}]*)\\}', r'\\1'),
        (r'\\\\text\\{([^}]*)\\}', r'\\1'),
        (r'\\\\uwave\\{([^}]*)\\}', r'\\1'),
        (r'\\\\textbf\\{([^}]*)\\}', r'\\1'),
        (r'\\\\emph\\{([^}]*)\\}', r'\\1'),
        (r'\\\\textit\\{([^}]*)\\}', r'\\1'),
    ]
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)

    text = re.sub(r'\\\\[a-zA-Z]+\\{([^}]*)\\}', r'\\1', text)
    text = re.sub(r'\\\\[a-zA-Z]+\\{', '', text)
    text = re.sub(r'\\\\\\{', '', text)
    text = re.sub(r'\\$\\s*([0-9A-Za-z一-鿿\\s]+?)\\s*\\$', r'\\1', text)
    text = re.sub(r'</?[bp]?matrix[^>]*>?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<sup>([^<]*)</sup>', r'\\1', text, flags=re.IGNORECASE)
    text = re.sub(r'<sub>([^<]*)</sub>', r'\\1', text, flags=re.IGNORECASE)
    text = re.sub(r'</?sup[^>]*>?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</?sub[^>]*>?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<br[^>]*>?', ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'(?<![\\\\\\w])\\$(?!\\d)', '', text)

    return text


_CASE_OCR_FIXES = [
    ("日本語の語", "日平均额"),
    ("国語の語", "增值税"),
    ("最天", "最大"), ("天大", "最大"), ("点数最天", "点数最大"),
    ("牌面天小", "牌面大小"), ("牌面天", "牌面大"), ("天小", "大小"),
    ("天盲", "大盲"),
    ("天号", "大号"), ("天楼", "大楼"), ("天厅", "大厅"),
    ("赔客", "赌客"), ("赔场", "赌场"), ("赔局", "赌局"),
    ("赔博", "赌博"), ("赔钱", "赌钱"), ("赔资", "赌资"),
    ("嫌疑入", "嫌疑人"), ("犯罪嫌疑入", "犯罪嫌疑人"),
    ("作证入", "作证人"), ("证入", "证人"),
    ("当事入", "当事人"), ("代理入", "代理人"),
    ("辩护入", "辩护人"), ("诉讼代理入", "诉讼代理人"),
    ("取保侯审", "取保候审"),
    ("监视居", "监视居住"),
]


def _fix_case_ocr_errors(text: str) -> str:
    """修复刑事案卷特有的 OCR 错误"""
    for wrong, correct in _CASE_OCR_FIXES:
        text = text.replace(wrong, correct)
    return text


def _apply_postprocessing(text: str) -> str:
    """应用后处理"""
    text = _clean_latex_markup(text)
    try:
        from pdf_to_md import (
            _fix_ocr_errors,
            _fold_consecutive_images,
            _protect_signatures_as_images,
            _strip_hallucinated_tables,
        )
        text = _fix_ocr_errors(text)
        text = _strip_hallucinated_tables(text)
        text = _protect_signatures_as_images(text)
        text, _ = _fold_consecutive_images(text)
    except ImportError:
        pass
    text = _fix_case_ocr_errors(text)
    return text


# ═══════════════════════════════════════════════════════════
# 异步 PaddleOCR 转换器
# ═══════════════════════════════════════════════════════════
class AsyncPaddleOCRConverter:
    """PaddleOCR 异步批量转换器

    特性：
    - 异步并发提交多个 PDF 任务
    - 并发轮询任务状态
    - 自动配额管理

    用法：
        converter = AsyncPaddleOCRConverter()
        results = await converter.convert_batch(
            [Path("/path/to/file1.pdf"), Path("/path/to/file2.pdf")],
            output_dir=Path("/output"),
            progress_cb=lambda p: print(f"{p.completed}/{p.total}")
        )
    """

    def __init__(self, token: Optional[str] = None):
        self.token = token or _get_paddleocr_token()
        if not self.token:
            raise ValueError("PaddleOCR Token 未配置，请设置 PADDLEOCR_TOKEN 环境变量或在设置中配置")

    async def convert_single(
        self,
        pdf_path: Path,
        output_dir: Path,
        timeout: int = DEFAULT_TIMEOUT,
        progress_cb: Optional[Callable[[str, str], None]] = None,
    ) -> ConvertResult:
        """转换单个 PDF 文件

        支持大文件自动分段处理
        """
        # 检查是否需要分段（捕获异常）
        try:
            chunks = _split_pdf_pages(pdf_path)
        except RuntimeError as e:
            return ConvertResult(file_name=pdf_path.name, success=False, error=str(e))
        except Exception as e:
            logger.exception(f"[PaddleOCR] _split_pdf_pages 异常: {pdf_path.name}")
            return ConvertResult(file_name=pdf_path.name, success=False, error=f"PDF 分段失败: {e}")

        if chunks:
            return await self._convert_chunks(chunks, pdf_path, output_dir, timeout, progress_cb)

        return await self._convert_single_file(pdf_path, output_dir, timeout, progress_cb)

    async def _convert_single_file(
        self,
        pdf_path: Path,
        output_dir: Path,
        timeout: int,
        progress_cb: Optional[Callable[[str, str], None]] = None,
    ) -> ConvertResult:
        """转换单个文件（内部方法，不分段）"""
        stem = pdf_path.stem

        # 获取页数
        try:
            import fitz
            doc = fitz.open(str(pdf_path))
            total_pages = len(doc)
            doc.close()
        except Exception as e:
            return ConvertResult(file_name=pdf_path.name, success=False, error=f"无法打开 PDF: {e}")

        if total_pages == 0:
            return ConvertResult(file_name=pdf_path.name, success=False, error="PDF 无页面")

        try:
            async with aiohttp.ClientSession() as session:
                # 1. 提交任务
                if progress_cb:
                    progress_cb("submitting", f"正在提交 {pdf_path.name}（{total_pages} 页）...")

                job_id = await self._submit_job(session, pdf_path)
                if not job_id:
                    return ConvertResult(file_name=pdf_path.name, success=False, error="提交任务失败")

                logger.info(f"[PaddleOCR] 任务已提交: {pdf_path.name}, job_id={job_id}")

                # 2. 轮询结果
                if progress_cb:
                    progress_cb("processing", "正在等待处理完成...")

                jsonl_url = await self._poll_job(session, job_id, timeout, progress_cb)
                if not jsonl_url:
                    return ConvertResult(file_name=pdf_path.name, success=False, error="转换超时或失败")

                # 3. 下载并解析
                if progress_cb:
                    progress_cb("downloading", "正在下载结果...")

                text, images_dir = await self._download_and_parse(session, jsonl_url, output_dir, stem)

                if not text or len(text) < 50:
                    return ConvertResult(file_name=pdf_path.name, success=False, error="结果内容过少")

                # 4. 后处理
                text = _apply_postprocessing(text)

                # 5. 保存
                target_md = output_dir / f"{stem}.md"
                target_md.write_text(text, encoding="utf-8")

                logger.info(f"[PaddleOCR] 转换完成 {pdf_path.name}: {len(text)} 字符, {total_pages} 页")

                return ConvertResult(
                    file_name=pdf_path.name,
                    success=True,
                    text=text,
                    images_dir=images_dir,
                    pages=total_pages
                )

        except Exception as e:
            logger.error(f"[PaddleOCR] 异常: {pdf_path.name}, {e}")
            return ConvertResult(file_name=pdf_path.name, success=False, error=str(e)[:200])

    async def _convert_chunks(
        self,
        chunks: List[Tuple[Path, int, int]],
        original_pdf: Path,
        output_dir: Path,
        timeout: int,
        progress_cb: Optional[Callable[[str, str], None]] = None,
    ) -> ConvertResult:
        """分段处理超大 PDF

        Args:
            chunks: List of (chunk_path, start_page, end_page) tuples
        """
        logger.info(f"[PaddleOCR] 分段转换 {original_pdf.name}: 共 {len(chunks)} 个 chunk")

        chunk_results = []
        all_images_dirs = []
        temp_prefix = f"_temp_{original_pdf.stem}"

        for i, (chunk_path, start_page, end_page) in enumerate(chunks):
            chunk_output = output_dir / f"{temp_prefix}_{i}"
            chunk_output.mkdir(parents=True, exist_ok=True)

            if progress_cb:
                progress_cb("processing", f"正在处理分段 {i+1}/{len(chunks)} (第{start_page}-{end_page}页)...")

            result = await self._convert_single_file(
                chunk_path, chunk_output, timeout, None
            )

            chunk_path.unlink(missing_ok=True)

            if result.success and result.text:
                # 保存结果和页码范围
                chunk_results.append((result.text, start_page, end_page))
                if result.images_dir:
                    all_images_dirs.append(result.images_dir)
                logger.info(f"[PaddleOCR] 分段转换 chunk {i} 成功 (第{start_page}-{end_page}页)")
            else:
                logger.error(f"[PaddleOCR] 分段转换 chunk {i} 失败: {result.error}")

            # 清理临时目录
            shutil.rmtree(chunk_output, ignore_errors=True)

        if not chunk_results:
            return ConvertResult(
                file_name=original_pdf.name,
                success=False,
                error="所有分段转换失败"
            )

        # 按页码排序后合并结果（确保顺序正确）
        chunk_results.sort(key=lambda x: x[1])

        # 合并结果，添加页码分隔标记
        merged_parts = []
        for text, start_page, end_page in chunk_results:
            # 添加页码分隔标记
            page_header = f"---\n\n<!-- 原PDF第{start_page}-{end_page}页 -->\n\n"
            merged_parts.append(page_header + text)

        merged_text = "\n\n".join(merged_parts)

        # 修复图片路径（使用正则表达式，与 MinerU 一致）
        merged_text = re.sub(
            r'\./(_chunk_[^/]+?)_([^/]+_images)/',
            r'\2/',
            merged_text
        )

        # 后处理
        merged_text = _apply_postprocessing(merged_text)

        # 合并图片目录
        merged_images_dir = output_dir / f"{original_pdf.stem}_images"
        merged_images_dir.mkdir(parents=True, exist_ok=True)
        for src_dir in all_images_dirs:
            if src_dir.exists():
                for img in src_dir.iterdir():
                    if img.is_file():
                        shutil.copy2(str(img), str(merged_images_dir / img.name))

        # 保存合并后的 MD
        target_md = output_dir / f"{original_pdf.stem}.md"
        target_md.write_text(merged_text, encoding="utf-8")

        # 统计总页数
        import fitz
        doc = fitz.open(str(original_pdf))
        total_pages = len(doc)
        doc.close()

        logger.info(f"[PaddleOCR] 分段转换完成 {original_pdf.name}: {len(merged_text)} 字符, {total_pages} 页")

        return ConvertResult(
            file_name=original_pdf.name,
            success=True,
            text=merged_text,
            images_dir=merged_images_dir,
            pages=total_pages
        )

    async def convert_batch(
        self,
        pdf_paths: List[Path],
        output_dir: Path,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        timeout: int = DEFAULT_TIMEOUT,
        progress_cb: Optional[Callable[[BatchProgress], None]] = None,
    ) -> List[ConvertResult]:
        """批量转换多个 PDF 文件（并发处理）"""
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"[PaddleOCR] convert_batch 入口: {len(pdf_paths)} 个文件, output_dir={output_dir}, max_concurrent={max_concurrent}")
        output_dir.mkdir(parents=True, exist_ok=True)

        # 创建信号量控制并发
        semaphore = asyncio.Semaphore(max_concurrent)

        # 进度跟踪 + 锁保护（避免并发更新导致计数不一致）
        progress = BatchProgress(total=len(pdf_paths))
        progress_lock = asyncio.Lock()

        async def _convert_with_semaphore(pdf_path: Path) -> ConvertResult:
            async with semaphore:
                result = await self.convert_single(
                    pdf_path, output_dir, timeout,
                    progress_cb=lambda stage, detail: None
                )

                # 更新进度（加锁保护）
                async with progress_lock:
                    progress.completed += 1
                    if not result.success:
                        progress.failed += 1
                    progress.current_files.append(pdf_path.name)

                    if progress_cb:
                        progress_cb(progress)

                return result

        # 并发执行所有转换
        tasks = [_convert_with_semaphore(pdf) for pdf in pdf_paths]
        logger.info(f"[PaddleOCR] 启动 {len(tasks)} 个并发任务, gather 开始...")
        results = await asyncio.gather(*tasks, return_exceptions=True)
        logger.info(f"[PaddleOCR] gather 完成, 原始结果数={len(results)}, 成功={sum(1 for r in results if not isinstance(r, Exception))}, 异常={sum(1 for r in results if isinstance(r, Exception))}")

        # 处理异常结果
        final_results = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                final_results.append(ConvertResult(
                    file_name=pdf_paths[i].name,
                    success=False,
                    error=str(r)
                ))
            else:
                final_results.append(r)

        return final_results

    async def _submit_job(
        self,
        session: aiohttp.ClientSession,
        pdf_path: Path,
    ) -> Optional[str]:
        """提交 PDF 转换任务，返回 jobId"""
        headers = {"Authorization": f"bearer {self.token}"}

        try:
            # 读取文件内容
            file_size = pdf_path.stat().st_size
            # 大文件增加超时时间（每 10MB 增加 60 秒）
            submit_timeout = max(120, int(file_size / (10 * 1024 * 1024) * 60))

            with open(pdf_path, "rb") as f:
                file_content = f.read()

            # 构建 multipart 表单
            data = aiohttp.FormData()
            data.add_field("file", file_content, filename=pdf_path.name, content_type="application/pdf")
            data.add_field("model", PADDLEOCR_MODEL)
            data.add_field("optionalPayload", json.dumps(PADDLEOCR_OPTIONAL_PAYLOAD))

            async with session.post(
                PADDLEOCR_API_URL,
                headers=headers,
                data=data,
                timeout=aiohttp.ClientTimeout(total=submit_timeout),
                ssl=_SSL_CONTEXT,
            ) as resp:
                if resp.status == 429:
                    logger.warning("[PaddleOCR] API 返回 429 限频，请稍后重试")
                    return None

                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"[PaddleOCR] 提交任务失败: HTTP {resp.status}, {text[:300]}")
                    return None

                result = await resp.json()
                return result["data"]["jobId"]

        except asyncio.TimeoutError:
            logger.error(f"[PaddleOCR] 提交任务超时: {pdf_path.name} ({file_size / (1024*1024):.1f}MB)")
            return None
        except Exception as e:
            import traceback
            logger.error(f"[PaddleOCR] 提交任务异常: {pdf_path.name}, {type(e).__name__}: {e}")
            traceback.print_exc()
            return None

    async def _poll_job(
        self,
        session: aiohttp.ClientSession,
        job_id: str,
        timeout: int,
        progress_cb: Optional[Callable[[str, str], None]] = None,
    ) -> Optional[str]:
        """轮询任务状态，完成后返回结果 URL"""
        headers = {"Authorization": f"bearer {self.token}"}
        waited = 0

        while waited < timeout:
            try:
                async with session.get(
                    f"{PADDLEOCR_API_URL}/{job_id}",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                    ssl=_SSL_CONTEXT,
                ) as resp:
                    if resp.status != 200:
                        await asyncio.sleep(POLL_INTERVAL)
                        waited += POLL_INTERVAL
                        continue

                    job_data = (await resp.json())["data"]
                    state = job_data.get("state")

                    if state == "pending":
                        if waited % 15 == 0:  # 每 15 秒打印一次
                            logger.info(f"[PaddleOCR] 任务排队中...（已等待 {waited}s）")

                    elif state == "running":
                        try:
                            prog = job_data.get("extractProgress", {})
                            total = prog.get("totalPages", "?")
                            done = prog.get("extractedPages", 0)
                            if waited % 15 == 0:
                                logger.info(f"[PaddleOCR] 正在识别 {done}/{total} 页...（已等待 {waited}s）")
                        except (KeyError, TypeError):
                            pass

                    elif state == "done":
                        prog = job_data.get("extractProgress", {})
                        pages = prog.get("extractedPages", "?")
                        logger.info(f"[PaddleOCR] 任务完成，共 {pages} 页")
                        json_url = job_data.get("resultUrl", {}).get("jsonUrl", "")
                        return json_url if json_url else None

                    elif state == "failed":
                        err = job_data.get("errorMsg", "未知错误")
                        logger.error(f"[PaddleOCR] 任务失败: {err}")
                        return None

            except Exception as e:
                logger.error(f"[PaddleOCR] 轮询异常: {e}")

            await asyncio.sleep(POLL_INTERVAL)
            waited += POLL_INTERVAL

        logger.error(f"[PaddleOCR] 轮询超时（{timeout}s）")
        return None

    async def _download_and_parse(
        self,
        session: aiohttp.ClientSession,
        jsonl_url: str,
        output_dir: Path,
        stem: str,
    ) -> Tuple[Optional[str], Optional[Path]]:
        """下载 JSONL 结果，合并为 Markdown

        注意：jsonl_url 和 img_url 可能是 OSS 签名 URL，需要禁止自动添加请求头
        """
        try:
            async with session.get(
                jsonl_url,
                timeout=aiohttp.ClientTimeout(total=60),
                ssl=_SSL_CONTEXT,
                skip_auto_headers=["User-Agent", "Accept", "Accept-Encoding"],
            ) as resp:
                raw_jsonl = await resp.text()
        except Exception as e:
            logger.error(f"[PaddleOCR] 下载结果失败: {e}")
            return None, None

        lines = raw_jsonl.strip().split('\n')
        if not lines:
            return None, None

        # 保存 JSONL
        json_dir = output_dir / f"{stem}_json"
        json_dir.mkdir(parents=True, exist_ok=True)
        (json_dir / "content_list.jsonl").write_text(raw_jsonl, encoding="utf-8")

        # 准备图片目录
        images_dir = output_dir / f"{stem}_images"
        images_dir.mkdir(parents=True, exist_ok=True)

        md_parts = []
        global_page = 0

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                result = record.get("result", {})
                layout_results = result.get("layoutParsingResults", [])

                for page_res in layout_results:
                    md_data = page_res.get("markdown", {})
                    if isinstance(md_data, dict):
                        text = md_data.get("text", "")
                        page_images = md_data.get("images", {}) or {}
                    else:
                        text = md_data
                        page_images = {}

                    # 下载图片
                    if isinstance(page_images, dict):
                        for img_path, img_url in page_images.items():
                            if not isinstance(img_url, str) or not img_url.startswith("http"):
                                continue
                            img_name = Path(img_path).name
                            local_img = images_dir / img_name
                            if not local_img.exists():
                                try:
                                    async with session.get(
                                        img_url,
                                        timeout=aiohttp.ClientTimeout(total=30),
                                        ssl=_SSL_CONTEXT,
                                        skip_auto_headers=["User-Agent", "Accept", "Accept-Encoding"],
                                    ) as img_resp:
                                        if img_resp.status == 200:
                                            content = await img_resp.read()
                                            if content:
                                                local_img.write_bytes(content)
                                except Exception:
                                    pass
                            text = text.replace(f'src="{img_path}"', f'src="./{stem}_images/{img_name}"')
                            text = text.replace(f"src='{img_path}'", f"src='./{stem}_images/{img_name}'")
                            text = text.replace(f']({img_path})', f'](./{stem}_images/{img_name})')

                    if text:
                        anchor = f"<!-- paddleocr-page:{global_page} -->"
                        md_parts.append(f"{anchor}\n\n{text}")
                        global_page += 1

            except Exception:
                continue

        if not md_parts:
            return None, None

        full_text = "\n\n".join(md_parts)
        return full_text, images_dir


# ═══════════════════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════════════════
async def convert_pdf_async(
    pdf_path: Path,
    output_dir: Path,
    progress_cb: Optional[Callable[[str, str], None]] = None,
) -> ConvertResult:
    """异步转换单个 PDF（便捷函数）"""
    converter = AsyncPaddleOCRConverter()
    return await converter.convert_single(pdf_path, output_dir, progress_cb=progress_cb)


async def convert_batch_async(
    pdf_paths: List[Path],
    output_dir: Path,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    progress_cb: Optional[Callable[[BatchProgress], None]] = None,
) -> List[ConvertResult]:
    """异步批量转换（便捷函数）"""
    converter = AsyncPaddleOCRConverter()
    return await converter.convert_batch(pdf_paths, output_dir, max_concurrent, progress_cb=progress_cb)


def convert_batch_sync(
    pdf_paths: List[Path],
    output_dir: Path,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    progress_cb: Optional[Callable[[BatchProgress], None]] = None,
) -> List[ConvertResult]:
    """同步批量转换（包装异步函数）"""
    return asyncio.run(convert_batch_async(pdf_paths, output_dir, max_concurrent, progress_cb))
