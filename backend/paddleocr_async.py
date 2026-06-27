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
# 延迟求值注解：使 `aiohttp.ClientSession` 等类型注解在 def 时不触发 import，
# 配合下方模块级 __getattr__ 才能让顶部不再 import aiohttp。
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from collections.abc import Callable
from pathlib import Path

# 性能优化：aiohttp 是重 native 依赖，启动时不需要立即加载
# （转换 PDF 时才用到）。采用模块级 lazy 加载：首次实际使用时才 import。
_aiohttp = None


def _get_aiohttp():
    """惰性加载 aiohttp，避免启动时加载重依赖"""
    global _aiohttp
    if _aiohttp is None:
        import aiohttp as _a  # noqa: WPS433
        _aiohttp = _a
    return _aiohttp


def __getattr__(name):
    """模块级 lazy：当代码引用 `aiohttp.X` 时，先取到 aiohttp 模块对象。

    这样模块内所有 `aiohttp.ClientSession` 等引用无需在每个函数里单独 import，
    启动时也不触发 aiohttp 加载（仅首次运行时才加载）。
    """
    if name == "aiohttp":
        return _get_aiohttp()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# 从 helpers 模块导入辅助函数、常量和数据类
from paddleocr_async_helpers import (
    _SSL_CONTEXT,
    DEFAULT_MAX_CONCURRENT,
    DEFAULT_TIMEOUT,
    PADDLEOCR_API_URL,
    PADDLEOCR_MODEL,
    PADDLEOCR_OPTIONAL_PAYLOAD,
    POLL_INTERVAL,
    BatchProgress,
    ConvertResult,
    _apply_postprocessing,
    _get_paddleocr_token,
    _split_pdf_pages,
    get_daily_quota_status,
)

logger = logging.getLogger(__name__)

__all__ = [
    # 类
    "AsyncPaddleOCRConverter",
    "ConvertResult",
    "BatchProgress",
    # 便捷函数
    "convert_pdf_async",
    "convert_batch_async",
    "convert_batch_sync",
    # 配额
    "get_daily_quota_status",
]


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

    def __init__(self, token: str | None = None):
        self.token = token or _get_paddleocr_token()
        if not self.token:
            raise ValueError("PaddleOCR Token 未配置，请设置 PADDLEOCR_TOKEN 环境变量或在设置中配置")

    async def convert_single(
        self,
        pdf_path: Path,
        output_dir: Path,
        timeout: int = DEFAULT_TIMEOUT,
        progress_cb: Callable[[str, str], None] | None = None,
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
        progress_cb: Callable[[str, str], None] | None = None,
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
        chunks: list[tuple[Path, int, int]],
        original_pdf: Path,
        output_dir: Path,
        timeout: int,
        progress_cb: Callable[[str, str], None] | None = None,
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
        pdf_paths: list[Path],
        output_dir: Path,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        timeout: int = DEFAULT_TIMEOUT,
        progress_cb: Callable[[BatchProgress], None] | None = None,
    ) -> list[ConvertResult]:
        """批量转换多个 PDF 文件（并发处理）"""
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
    ) -> str | None:
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
        progress_cb: Callable[[str, str], None] | None = None,
    ) -> str | None:
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
    ) -> tuple[str | None, Path | None]:
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
    progress_cb: Callable[[str, str], None] | None = None,
) -> ConvertResult:
    """异步转换单个 PDF（便捷函数）"""
    converter = AsyncPaddleOCRConverter()
    return await converter.convert_single(pdf_path, output_dir, progress_cb=progress_cb)


async def convert_batch_async(
    pdf_paths: list[Path],
    output_dir: Path,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    progress_cb: Callable[[BatchProgress], None] | None = None,
) -> list[ConvertResult]:
    """异步批量转换（便捷函数）"""
    converter = AsyncPaddleOCRConverter()
    return await converter.convert_batch(pdf_paths, output_dir, max_concurrent, progress_cb=progress_cb)


def convert_batch_sync(
    pdf_paths: list[Path],
    output_dir: Path,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    progress_cb: Callable[[BatchProgress], None] | None = None,
) -> list[ConvertResult]:
    """同步批量转换（包装异步函数）"""
    return asyncio.run(convert_batch_async(pdf_paths, output_dir, max_concurrent, progress_cb))
