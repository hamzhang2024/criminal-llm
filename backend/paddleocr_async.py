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

import os
import sys
import json
import time
import shutil
import asyncio
import re
import ssl
from pathlib import Path
from datetime import date
from typing import Optional, List, Dict, Any, Tuple, Callable
from dataclasses import dataclass, field

import aiohttp
import logging

from paddleocr_remote import build_optional_payload, _apply_postprocessing

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
DEFAULT_MAX_CONCURRENT = 5  # 默认并发数（任务级共享信号量，文件与分块共用）

# 分段限制（大文件自动拆分）
# PaddleOCR-VL-1.6 建议 100 页以内，超过会处理但可能影响质量
PADDLEOCR_MAX_PAGES = 100  # 单次提交建议页数（超过会自动拆分以保证质量）
PADDLEOCR_MAX_FILE_SIZE = 50 * 1024 * 1024  # 单次提交最大文件大小（官方本地上传限制 50MB）

# optionalPayload 由 paddleocr_remote.build_optional_payload 统一构建（含图片识别参数）


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


def resolve_max_concurrent() -> int:
    """解析 PaddleOCR 并发数：读 paddleocr_concurrency 配置，clamp 1-10，默认 5

    独立于 pdf_convert_concurrency（MinerU 用，默认 10）：PaddleOCR 任务更重，
    并发过高易触发 API 限流（429）。
    """
    try:
        from config_manager import get_config_value
        raw = get_config_value("paddleocr_concurrency", 5)
        value = int(raw)
    except Exception:
        value = DEFAULT_MAX_CONCURRENT
    return max(1, min(10, value))


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
        logger.error(f"[PaddleOCR] fitz 模块未安装，无法分段 PDF")
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


def _extract_pages_for_retry(pdf_path: Path, page_indices: List[int]) -> Path:
    """抽取指定页码（0 基）到临时 PDF，用于空页重试"""
    import fitz
    doc = fitz.open(str(pdf_path))
    new_doc = fitz.open()
    for i in page_indices:
        new_doc.insert_pdf(doc, from_page=i, to_page=i)
    tmp_path = pdf_path.parent / f"_retry_{pdf_path.name}"
    new_doc.save(str(tmp_path))
    new_doc.close()
    doc.close()
    return tmp_path


# ═══════════════════════════════════════════════════════════
# 后处理：统一复用 paddleocr_remote._apply_postprocessing
# （本地副本曾因双重转义整体失效，批量路径 MD 残留大量 LaTeX 包裹——已删除本地副本）
# ═══════════════════════════════════════════════════════════


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

    def __init__(self, token: Optional[str] = None, max_concurrent: int = DEFAULT_MAX_CONCURRENT):
        self.token = token or _get_paddleocr_token()
        if not self.token:
            raise ValueError("PaddleOCR Token 未配置，请设置 PADDLEOCR_TOKEN 环境变量或在设置中配置")
        # 任务级共享信号量：文件与分块共用，限制同时在跑的 API 任务总数
        self._job_semaphore = asyncio.Semaphore(max_concurrent)

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
        # 检查是否需要分段（本地 CPU 操作，不占用 API 并发额度，在信号量外执行）
        try:
            chunks = _split_pdf_pages(pdf_path)
        except RuntimeError as e:
            return ConvertResult(file_name=pdf_path.name, success=False, error=str(e))
        except Exception as e:
            logger.exception(f"[PaddleOCR] _split_pdf_pages 异常: {pdf_path.name}")
            return ConvertResult(file_name=pdf_path.name, success=False, error=f"PDF 分段失败: {e}")

        if chunks:
            return await self._convert_chunks(chunks, pdf_path, output_dir, timeout, progress_cb)

        # 单文件路径：通过任务级共享信号量限制并发
        async with self._job_semaphore:
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
        output_dir.mkdir(parents=True, exist_ok=True)

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

                # 3. 下载并解析（按页返回，供空页检测）
                if progress_cb:
                    progress_cb("downloading", "正在下载结果...")

                pages_text, images_dir = await self._download_and_parse(session, jsonl_url, output_dir, stem)
                if not pages_text:
                    return ConvertResult(file_name=pdf_path.name, success=False, error="结果内容过少")

                # 4. 空页/缺页检测与重试（遗漏证据是严重事故，宁多跑一页不漏一页）
                empty_pages = [i for i in range(total_pages) if not pages_text.get(i, "").strip()]
                for attempt in (1, 2):
                    if not empty_pages:
                        break
                    logger.warning(
                        f"[PaddleOCR] {pdf_path.name} 第 {attempt} 次重试 {len(empty_pages)} 个空页"
                        f"（页码 {[p + 1 for p in empty_pages]}）"
                    )
                    if progress_cb:
                        progress_cb("processing", f"正在重试 {len(empty_pages)} 个空页（第 {attempt} 次）...")
                    retry_pdf = _extract_pages_for_retry(pdf_path, empty_pages)
                    try:
                        retry_job = await self._submit_job(session, retry_pdf)
                        if retry_job:
                            retry_url = await self._poll_job(session, retry_job, timeout, None)
                            if retry_url:
                                retry_pages, _ = await self._download_and_parse(
                                    session, retry_url, output_dir, f"{stem}_retry{attempt}")
                                recovered = 0
                                for idx, orig_i in enumerate(empty_pages):
                                    t = (retry_pages or {}).get(idx, "")
                                    if t.strip():
                                        pages_text[orig_i] = t
                                        recovered += 1
                                logger.info(f"[PaddleOCR] 第 {attempt} 次重试恢复 {recovered}/{len(empty_pages)} 页")
                    finally:
                        retry_pdf.unlink(missing_ok=True)
                    empty_pages = [i for i in range(total_pages) if not pages_text.get(i, "").strip()]

                # 5. 按原页序组装；仍空的页写警告标记，绝不静默遗漏
                md_parts = []
                for i in range(total_pages):
                    anchor = f"<!-- paddleocr-page:{i} -->"
                    t = pages_text.get(i, "")
                    if t.strip():
                        md_parts.append(f"{anchor}\n\n{t}")
                    else:
                        logger.error(f"[PaddleOCR] {pdf_path.name} 第 {i + 1} 页重试后仍为空")
                        md_parts.append(f"{anchor}\n\n> ⚠️ 本页识别为空（已自动重试 2 次），请人工核对原件")

                text = "\n\n".join(md_parts)

                if not text or len(text) < 50:
                    return ConvertResult(file_name=pdf_path.name, success=False, error="结果内容过少")

                # 6. 后处理
                text = _apply_postprocessing(text)

                # 7. 保存
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
        """分段处理超大 PDF（分块并行转换）

        Args:
            chunks: List of (chunk_path, start_page, end_page) tuples
        """
        logger.info(f"[PaddleOCR] 分段转换 {original_pdf.name}: 共 {len(chunks)} 个 chunk（并行处理）")

        temp_prefix = f"_temp_{original_pdf.stem}"

        # 并行下进度消息统一提示一次，避免错乱
        if progress_cb:
            progress_cb("processing", f"正在并行处理 {len(chunks)} 个分段...")

        async def _convert_one_chunk(i: int, chunk_path: Path, start_page: int, end_page: int):
            """转换单个分块：临时文件由外层统一清理（图片需在合并后才能删除）"""
            chunk_output = output_dir / f"{temp_prefix}_{i}"
            try:
                # mkdir 放进 try：失败时异常被 gather 捕获，由外层 finally 统一清理临时 chunk pdf
                chunk_output.mkdir(parents=True, exist_ok=True)
                async with self._job_semaphore:
                    result = await self._convert_single_file(
                        chunk_path, chunk_output, timeout, None
                    )

                if result.success and result.text:
                    logger.info(f"[PaddleOCR] 分段转换 chunk {i} 成功 (第{start_page}-{end_page}页)")
                else:
                    logger.error(f"[PaddleOCR] 分段转换 chunk {i} 失败: {result.error}")
                return result
            except Exception:
                logger.exception(f"[PaddleOCR] 分段转换 chunk {i} 协程异常 (第{start_page}-{end_page}页)")
                raise

        try:
            # 分块并行执行，每个分块各自 acquire 任务级共享信号量
            # return_exceptions=True：单个分块协程异常不影响其他分块，按失败分块处理
            results = await asyncio.gather(*(
                _convert_one_chunk(i, chunk_path, start_page, end_page)
                for i, (chunk_path, start_page, end_page) in enumerate(chunks)
            ), return_exceptions=True)

            # 汇总成功分块（异常/失败分块记录日志，其他分块照常合并）
            chunk_results = []
            all_images_dirs = []
            for i, result in enumerate(results):
                _, start_page, end_page = chunks[i]
                if isinstance(result, Exception):
                    logger.error(f"[PaddleOCR] 分段转换 chunk {i} 异常（按失败处理）: {result}")
                    continue
                if result.success and result.text:
                    chunk_results.append((result.text, start_page, end_page))
                    if result.images_dir:
                        all_images_dirs.append(result.images_dir)

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

            # 合并图片目录（必须在清理 chunk_output 之前完成）
            merged_images_dir = output_dir / f"{original_pdf.stem}_images"
            if all_images_dirs:
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
        finally:
            # 统一清理：文本+图片合并完成（或异常退出）后，删除所有临时分块 PDF 和临时输出目录
            for chunk_path, _, _ in chunks:
                chunk_path.unlink(missing_ok=True)
            for i in range(len(chunks)):
                shutil.rmtree(output_dir / f"{temp_prefix}_{i}", ignore_errors=True)

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

        # 重建任务级共享信号量：文件全部启动，由该信号量收口（文件与分块共用）
        self._job_semaphore = asyncio.Semaphore(max_concurrent)

        # 进度跟踪 + 锁保护（避免并发更新导致计数不一致）
        progress = BatchProgress(total=len(pdf_paths))
        progress_lock = asyncio.Lock()

        async def _convert_one(pdf_path: Path) -> ConvertResult:
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
        tasks = [_convert_one(pdf) for pdf in pdf_paths]
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
            data.add_field("optionalPayload", json.dumps(build_optional_payload()))

            async with session.post(
                PADDLEOCR_API_URL,
                headers=headers,
                data=data,
                timeout=aiohttp.ClientTimeout(total=submit_timeout),
                ssl=_SSL_CONTEXT,
            ) as resp:
                if resp.status == 429:
                    logger.warning(f"[PaddleOCR] API 返回 429 限频，请稍后重试")
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
    ) -> Tuple[Optional[Dict[int, str]], Optional[Path]]:
        """下载 JSONL 结果，按页返回 {页码(0基): 文本}（空文本页也保留页位）

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

        pages = {}
        page_idx = 0

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

                    # 空文本也记录页位，供空页检测与重试
                    pages[page_idx] = text
                    page_idx += 1

            except Exception:
                continue

        if not pages:
            return None, None

        return pages, images_dir


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
    converter = AsyncPaddleOCRConverter(max_concurrent=max_concurrent)
    return await converter.convert_batch(pdf_paths, output_dir, max_concurrent, progress_cb=progress_cb)


def convert_batch_sync(
    pdf_paths: List[Path],
    output_dir: Path,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    progress_cb: Optional[Callable[[BatchProgress], None]] = None,
) -> List[ConvertResult]:
    """同步批量转换（包装异步函数）"""
    return asyncio.run(convert_batch_async(pdf_paths, output_dir, max_concurrent, progress_cb))
