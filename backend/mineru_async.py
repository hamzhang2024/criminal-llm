#!/usr/bin/env python3
"""
MinerU 异步批量转换模块

优化点：
1. 异步批量提交多个 PDF 到 MinerU
2. asyncio 并发轮询任务状态
3. 支持回调 URL 减少轮询开销
4. 自动分段处理超大文件

用法：
    from mineru_async import AsyncMinerUConverter

    converter = AsyncMinerUConverter(token="your-token")
    results = await converter.convert_batch([pdf1, pdf2, pdf3])
"""

import os
import sys
import time
import zipfile
import shutil
import json
import asyncio
import re
import ssl
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, Callable
from dataclasses import dataclass, field
from datetime import datetime

import aiohttp
import fitz
import logging

# 配置日志（PyInstaller --noconsole 模式下 print() 不可见，必须用 logger）
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
MINERU_API = "https://mineru.net/api/v4"
MINERU_MAX_PAGES = 180  # MinerU API 限制 200 页，留 20 页缓冲
MINERU_MAX_FILE_SIZE = 200 * 1024 * 1024  # 200MB
MINERU_BATCH_SIZE = 50  # 每批最多 50 个文件（API 限制）
DEFAULT_TIMEOUT = 3600  # 1 小时
POLL_INTERVAL = 5  # 轮询间隔 5 秒

# MinerU 并发建议：
# - API 无明确并发数限制，但高频请求会触发 429 限频
# - VLM 模式处理时间较长
# - 遇到 429 或 -60009 错误时自动退避重试
DEFAULT_MAX_CONCURRENT = 10


@dataclass
class ConvertResult:
    """单个文件的转换结果"""
    file_name: str
    success: bool
    text: Optional[str] = None
    images_dir: Optional[Path] = None
    error: Optional[str] = None
    source: str = "mineru"  # mineru | cache


@dataclass
class BatchProgress:
    """批量转换进度"""
    total: int = 0
    completed: int = 0
    failed: int = 0
    current_files: List[str] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())


def _get_mineru_token() -> str:
    """获取 MinerU token"""
    # 环境变量优先
    token = os.environ.get("MINERU_TOKEN", "")
    if token:
        logger.debug(f"[MinerU] Token 来源: 环境变量 MINERU_TOKEN")
        return token

    # 应用配置
    try:
        from config_manager import get_config_value
        token = get_config_value("mineru_token")
        if token:
            logger.debug(f"[MinerU] Token 来源: 配置文件 criminal-llm-config.json")
            return token
    except ImportError:
        pass

    # 回退到 .env
    from config import DATA_DIR
    env_path = DATA_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("MINERU_TOKEN="):
                token = line.split("=", 1)[1].strip()
                logger.debug(f"[MinerU] Token 来源: {env_path}")
                break

    if not token:
        logger.warning(f"[MinerU] 未找到 Token（已检查环境变量、配置文件、.env）")
    return token


def _split_pdf_pages(pdf_path: Path, chunk_size: int = MINERU_MAX_PAGES) -> List[Tuple[Path, int, int]]:
    """将大 PDF 按页数/文件大小分段

    Returns:
        List of (chunk_path, start_page, end_page) tuples
        Empty list if no splitting needed

    Raises:
        RuntimeError: 如果 PDF 无法打开或读取
    """
    try:
        doc = fitz.open(str(pdf_path))
        total = len(doc)
        file_size = pdf_path.stat().st_size
        doc.close()
    except Exception as e:
        logger.error(f"[MinerU] 无法打开 PDF {pdf_path.name}: {type(e).__name__}: {e}")
        raise RuntimeError(f"无法打开 PDF {pdf_path.name}: {e}")

    if total <= chunk_size and file_size <= MINERU_MAX_FILE_SIZE:
        return []  # 不需要拆分

    # 计算满足文件大小限制的每段最大页数
    if total > 0:
        avg_page_size = file_size / total
        if avg_page_size > 0:
            max_pages_by_size = int(MINERU_MAX_FILE_SIZE * 0.80 / avg_page_size)
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

        # 检查实际文件大小
        actual_size = tmp_path.stat().st_size
        if actual_size > MINERU_MAX_FILE_SIZE * 0.95:
            logger.warning(f"[MinerU] chunk {start+1}-{end} 超限，减半重新拆分")
            tmp_path.unlink(missing_ok=True)
            sub_size = max(chunk_size // 2, 10)
            for sub_start in range(start, end, sub_size):
                sub_end = min(sub_start + sub_size, end)
                sub_doc = fitz.open()
                sub_doc.insert_pdf(fitz.open(str(pdf_path)), from_page=sub_start, to_page=sub_end - 1)
                sub_path = Path(pdf_path.parent) / f"_chunk_{sub_start+1}-{sub_end}_{pdf_path.name}"
                sub_doc.save(str(sub_path))
                sub_doc.close()
                chunks.append((sub_path, sub_start + 1, sub_end))  # 页码从1开始
        else:
            chunks.append((tmp_path, start + 1, end))  # 页码从1开始

    return chunks


# ═══════════════════════════════════════════════════════════
# OCR 纠错规则（复用自 pdf_to_md.py）
# ═══════════════════════════════════════════════════════════
_OCR_FIXES = [
    ("日本語の語", "日平均额"),
    ("国語の語", "增值税"),
    ("の口", "的口"),
    ("の诗", "的诗"),
    ("の菠萝", "的菠萝"),
    ("倘若の", "倘若的"),
    ("的の", "的的"),
    ("讯间笔录", "讯问笔录"),
    ("讯 间 笔 录", "讯问笔录"),
    ("询间笔录", "询问笔录"),
    ("询 间 笔 录", "询问笔录"),
    ("讯间人", "讯问人"),
    ("询间人", "询问人"),
    ("被讯间人", "被讯问人"),
    ("被询间人", "被询问人"),
    ("曰", "日"),
    ("巳", "已"),
    ("末", "未"),
]

_SIGNATURE_HTML = '<div style="text-align:center;color:#aaa;border-bottom:1px dashed #ccc;padding:2px 20px;margin:2px 0;font-size:11px;">[手写签名]</div>'
_SIGNATURE_PATTERNS = [
    (r"(询问人[：:]\\s*)[^\\n]*", rf"\\1{_SIGNATURE_HTML}"),
    (r"(讯问人[：:]\\s*)[^\\n]*", rf"\\1{_SIGNATURE_HTML}"),
    (r"(记录人[：:]\\s*)[^\\n]*", rf"\\1{_SIGNATURE_HTML}"),
]


def _fix_ocr_errors(text: str) -> str:
    """修复 MinerU API 常见 OCR 错误"""
    for wrong, correct in _OCR_FIXES:
        text = text.replace(wrong, correct)
    # 清理中文语境中的孤立日语假名の
    text = re.sub(r'([一-鿿])の([一-鿿])', r'\\1的\\2', text)
    text = re.sub(r'(?<![ぁ-んァ-ン])の(?![ぁ-んァ-ン])', '的', text)
    return text


def _protect_signatures_as_images(text: str) -> str:
    """将签名区替换为图片占位符"""
    for pattern, replacement in _SIGNATURE_PATTERNS:
        text = re.sub(pattern, replacement, text)
    return text


def _fold_consecutive_images(text: str, min_count: int = 1) -> Tuple[str, int]:
    """将连续图片折叠为 <details> 块"""
    lines = text.split('\\n')
    is_image = [bool(re.match(r'^!\\[.*\\]\\([^)]+\\)$', line.strip())) for line in lines]

    result = []
    block_count = 0
    i = 0

    while i < len(lines):
        if is_image[i]:
            block_lines = [lines[i]]
            j = i + 1
            while j < len(lines):
                if is_image[j]:
                    block_lines.append(lines[j])
                    j += 1
                elif lines[j].strip() == '':
                    k = j + 1
                    while k < len(lines) and lines[k].strip() == '':
                        k += 1
                    if k < len(lines) and is_image[k]:
                        j = k
                    else:
                        break
                else:
                    break

            if len(block_lines) >= min_count:
                result.append(f'<details><summary>📎 签名/印章图片（共 {len(block_lines)} 张，点击展开）</summary>\\n')
                result.extend(block_lines)
                result.append('</details>')
                block_count += 1
            else:
                result.extend(block_lines)
            i = j
        else:
            result.append(lines[i])
            i += 1

    return '\\n'.join(result), block_count


# ═══════════════════════════════════════════════════════════
# 异步 MinerU 转换器
# ═══════════════════════════════════════════════════════════
class AsyncMinerUConverter:
    """MinerU 异步批量转换器

    特性：
    - 异步并发提交多个 PDF
    - 批量轮询任务状态
    - 自动处理超大文件分段
    - 支持进度回调

    用法：
        converter = AsyncMinerUConverter()
        results = await converter.convert_batch(
            [Path("/path/to/file1.pdf"), Path("/path/to/file2.pdf")],
            output_dir=Path("/output"),
            progress_cb=lambda p: print(f"{p.completed}/{p.total}")
        )
    """

    def __init__(self, token: Optional[str] = None):
        self.token = token or _get_mineru_token()
        if not self.token:
            raise ValueError("MinerU Token 未配置，请设置 MINERU_TOKEN 环境变量或在设置中配置")
        # 调试日志：显示 token 来源和前几位（不泄露完整 token）
        source = "参数传入" if token else "配置文件/环境变量"
        logger.info(f"[MinerU] 初始化: token来源={source}, token前20字符={self.token[:20]}...")

    async def convert_single(
        self,
        pdf_path: Path,
        output_dir: Path,
        timeout: int = DEFAULT_TIMEOUT,
        progress_cb: Optional[Callable[[str, str], None]] = None,
    ) -> ConvertResult:
        """转换单个 PDF 文件

        Args:
            pdf_path: PDF 文件路径
            output_dir: 输出目录
            timeout: 超时时间（秒）
            progress_cb: 进度回调 (stage, detail)

        Returns:
            ConvertResult 对象
        """
        # 检查是否需要分段（捕获异常）
        try:
            chunks = _split_pdf_pages(pdf_path)
        except RuntimeError as e:
            return ConvertResult(file_name=pdf_path.name, success=False, error=str(e))
        except Exception as e:
            logger.exception(f"[MinerU] _split_pdf_pages 异常: {pdf_path.name}")
            return ConvertResult(file_name=pdf_path.name, success=False, error=f"PDF 分段失败: {e}")

        if chunks:
            # 分段处理
            return await self._convert_chunks(chunks, pdf_path, output_dir, timeout, progress_cb)

        # 单文件处理
        return await self._convert_single_file(pdf_path, output_dir, timeout, progress_cb)

    async def convert_batch(
        self,
        pdf_paths: List[Path],
        output_dir: Path,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        timeout: int = DEFAULT_TIMEOUT,
        progress_cb: Optional[Callable[[BatchProgress], None]] = None,
    ) -> List[ConvertResult]:
        """批量转换多个 PDF 文件（并发处理）"""
        logger.info(f"[MinerU] convert_batch 入口: {len(pdf_paths)} 个文件, output_dir={output_dir}, max_concurrent={max_concurrent}")
        output_dir.mkdir(parents=True, exist_ok=True)
        results = []

        # 创建信号量控制并发
        semaphore = asyncio.Semaphore(max_concurrent)

        # 进度跟踪 + 锁保护（避免并发更新导致计数不一致）
        progress = BatchProgress(total=len(pdf_paths))
        progress_lock = asyncio.Lock()

        async def _convert_with_semaphore(pdf_path: Path) -> ConvertResult:
            async with semaphore:
                # 重试逻辑：遇到 429 限频时自动退避
                max_retries = 3
                for attempt in range(max_retries):
                    result = await self.convert_single(
                        pdf_path, output_dir, timeout,
                        progress_cb=lambda stage, detail: None
                    )

                    # 检查是否需要重试（429 限频）
                    if not result.success and result.error:
                        if "429" in result.error or "限频" in result.error or "队列已满" in result.error:
                            wait_time = 30 * (attempt + 1)  # 30s, 60s, 90s
                            logger.info(f"[MinerU] 触发限频，{wait_time}s 后重试 ({attempt + 1}/{max_retries}): {pdf_path.name}")
                            await asyncio.sleep(wait_time)
                            continue

                    # 成功或其他错误，直接返回
                    break

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
        logger.info(f"[MinerU] 启动 {len(tasks)} 个并发任务, gather 开始...")
        results = await asyncio.gather(*tasks, return_exceptions=True)
        logger.info(f"[MinerU] gather 完成, 原始结果数={len(results)}, 成功={sum(1 for r in results if not isinstance(r, Exception))}, 异常={sum(1 for r in results if isinstance(r, Exception))}")

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

    async def _convert_single_file(
        self,
        pdf_path: Path,
        output_dir: Path,
        timeout: int,
        progress_cb: Optional[Callable[[str, str], None]] = None,
    ) -> ConvertResult:
        """转换单个文件（内部方法）"""
        stem = pdf_path.stem

        try:
            async with aiohttp.ClientSession() as session:
                # 1. 提交任务
                if progress_cb:
                    progress_cb("submitting", "正在提交转换任务...")

                batch_id, upload_url, submit_error = await self._submit_task(
                    session, pdf_path, stem
                )

                if not batch_id or not upload_url:
                    return ConvertResult(
                        file_name=pdf_path.name,
                        success=False,
                        error=submit_error or "获取上传链接失败"
                    )

                logger.info(f"[MinerU] 开始上传 {pdf_path.name} (batch_id={batch_id})")

                # 2. 上传文件
                if progress_cb:
                    progress_cb("uploading", "正在发送文件...")

                upload_ok = await self._upload_file(session, upload_url, pdf_path)
                if not upload_ok:
                    return ConvertResult(
                        file_name=pdf_path.name,
                        success=False,
                        error="文件上传失败"
                    )

                # 3. 异步轮询等待结果
                if progress_cb:
                    progress_cb("processing", "正在识别文本内容...")

                result_data = await self._poll_result(
                    session, batch_id, stem, timeout, progress_cb
                )

                if not result_data:
                    return ConvertResult(
                        file_name=pdf_path.name,
                        success=False,
                        error="转换超时或失败"
                    )

                # 4. 下载并解析结果
                if progress_cb:
                    progress_cb("downloading", "正在生成结构化文本...")

                text, images_dir = await self._download_and_parse(
                    result_data, output_dir, stem
                )

                if text and len(text) > 100:
                    # 后处理
                    if images_dir:
                        text = text.replace("images/", f"./{stem}_images/")
                        text = text.replace('src="images/', f'src="./{stem}_images/')
                    text = _protect_signatures_as_images(text)
                    text = _fix_ocr_errors(text)
                    text, _ = _fold_consecutive_images(text)

                    # 保存 MD
                    target_md = output_dir / f"{stem}.md"
                    target_md.write_text(text, encoding="utf-8")

                    return ConvertResult(
                        file_name=pdf_path.name,
                        success=True,
                        text=text,
                        images_dir=images_dir
                    )

                logger.error(f"[MinerU] 结果内容为空: {pdf_path.name}")
                return ConvertResult(
                    file_name=pdf_path.name,
                    success=False,
                    error="结果内容为空"
                )

        except Exception as e:
            logger.error(f"[MinerU] 异常: {pdf_path.name}, {e}")
            return ConvertResult(
                file_name=pdf_path.name,
                success=False,
                error=str(e)[:200]
            )

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
        logger.info(f"[分段转换] {original_pdf.name}: 共 {len(chunks)} 个 chunk")

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
                # 添加页码范围标记，保持与原 PDF 结构对应
                page_marker = f"\n\n<!-- 原PDF第{start_page}-{end_page}页 -->\n\n"
                chunk_results.append((result.text, start_page, end_page))
                if result.images_dir:
                    all_images_dirs.append(result.images_dir)
                logger.info(f"[分段转换] chunk {i} 成功 (第{start_page}-{end_page}页)")
            else:
                logger.info(f"[分段转换] chunk {i} 失败: {result.error}")

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
        merged_text = re.sub(
            r'\./(_chunk_[^/]+?)_([^/]+_images)/',
            r'\2/',
            merged_text
        )
        merged_text = _protect_signatures_as_images(merged_text)
        merged_text = _fix_ocr_errors(merged_text)
        merged_text, _ = _fold_consecutive_images(merged_text)

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

        logger.info(f"[MinerU] 分段转换完成 {original_pdf.name}: {len(merged_text)} 字符")

        return ConvertResult(
            file_name=original_pdf.name,
            success=True,
            text=merged_text,
            images_dir=merged_images_dir
        )

    async def _submit_task(
        self,
        session: aiohttp.ClientSession,
        pdf_path: Path,
        data_id: str,
    ) -> Tuple[Optional[str], Optional[str], str]:
        """提交转换任务，返回 (batch_id, upload_url, error_msg)

        Returns:
            Tuple of (batch_id, upload_url, error_msg)
            On success: (batch_id, upload_url, "")
            On failure: (None, None, error_description)
        """
        try:
            params = {
                "is_ocr": "true",
                "enable_formula": "false",
                "enable_table": "true",
                "language": "ch_server",
            }

            async with session.post(
                f"{MINERU_API}/file-urls/batch",
                headers={"Authorization": f"Bearer {self.token}"},
                params=params,
                json={
                    "files": [{"name": pdf_path.name, "data_id": data_id}],
                    "model_version": "vlm",
                },
                timeout=aiohttp.ClientTimeout(total=30),
                ssl=_SSL_CONTEXT,
            ) as resp:
                # 先检查 HTTP 状态码
                if resp.status == 401:
                    body = await resp.text()
                    err_msg = "MinerU Token 无效或已过期，请在设置页面重新配置"
                    logger.error(f"[MinerU] 认证失败 (401): {body[:200]}")
                    return None, None, err_msg
                if resp.status != 200:
                    body = await resp.text()
                    err_msg = f"MinerU API HTTP {resp.status}: {body[:200]}"
                    logger.error(f"[MinerU] {err_msg}")
                    return None, None, err_msg

                result = await resp.json()

                if result.get("code") != 0:
                    err_code = result.get("code")
                    err_msg = result.get("msg", "未知错误")
                    logger.error(f"[MinerU] API 错误 code={err_code}: {err_msg}")
                    return None, None, f"MinerU API 错误 ({err_code}): {err_msg}"

                data = result.get("data", {})
                batch_id = data.get("batch_id")
                file_urls = data.get("file_urls", [])
                upload_url = file_urls[0] if file_urls else None

                if not batch_id or not upload_url:
                    logger.error(f"[MinerU] 返回数据不完整: batch_id={'有' if batch_id else '无'}, upload_url={'有' if upload_url else '无'}")
                    return None, None, "MinerU 返回数据不完整"

                return batch_id, upload_url, ""

        except asyncio.TimeoutError:
            logger.error(f"[MinerU] 提交任务超时: {pdf_path.name}")
            return None, None, "MinerU 提交超时"
        except aiohttp.ClientError as e:
            logger.error(f"[MinerU] 网络错误: {e}")
            return None, None, f"MinerU 网络错误: {str(e)[:100]}"
        except Exception as e:
            logger.error(f"[MinerU] 提交任务异常: {e}")
            return None, None, f"MinerU 提交异常: {str(e)[:100]}"

    async def _upload_file(
        self,
        session: aiohttp.ClientSession,
        upload_url: str,
        pdf_path: Path,
    ) -> bool:
        """上传文件到 OSS

        注意：OSS 签名 URL 对请求头极其敏感！
        签名时可能没有包含 Content-Type 头，所以不能发送该头。
        必须使用 skip_auto_headers 禁止 aiohttp 自动添加任何头。
        """
        try:
            file_size = pdf_path.stat().st_size
            upload_timeout = max(300, file_size // (1024 * 1024) * 20)

            with open(pdf_path, "rb") as f:
                file_content = f.read()

            # OSS 签名 URL 签名时可能没有包含 Content-Type
            # 不能发送任何额外头，否则签名不匹配
            async with session.put(
                upload_url,
                data=file_content,
                headers={},  # 不发送任何额外头
                timeout=aiohttp.ClientTimeout(total=upload_timeout),
                ssl=_SSL_CONTEXT,
                skip_auto_headers=["User-Agent", "Accept", "Accept-Encoding", "Content-Type"],
            ) as resp:
                if resp.status not in (200, 201, 203, 204):
                    text = await resp.text()
                    logger.error(f"[MinerU] 上传失败: HTTP {resp.status}, {text[:200]}")
                return resp.status in (200, 201, 203, 204)

        except Exception as e:
            logger.error(f"[MinerU] 上传异常: {e}")
            return False

    async def _poll_result(
        self,
        session: aiohttp.ClientSession,
        batch_id: str,
        data_id: str,
        timeout: int,
        progress_cb: Optional[Callable[[str, str], None]] = None,
    ) -> Optional[Dict[str, Any]]:
        """异步轮询任务结果"""
        waited = 0
        poll_interval = POLL_INTERVAL

        while waited < timeout:
            try:
                async with session.get(
                    f"{MINERU_API}/extract-results/batch/{batch_id}",
                    headers={"Authorization": f"Bearer {self.token}"},
                    timeout=aiohttp.ClientTimeout(total=30),
                    ssl=_SSL_CONTEXT,
                ) as resp:
                    # 先检查 HTTP 状态码
                    if resp.status == 401:
                        logger.error(f"[MinerU] 轮询认证失败 (401): Token 无效或已过期")
                        return None

                    result = await resp.json()

                    data = result.get("data", {})
                    results = data.get("extract_result", [])

                    if not results:
                        await asyncio.sleep(poll_interval)
                        waited += poll_interval
                        if progress_cb:
                            progress_cb("processing", f"正在识别文本内容...（已等待 {waited} 秒）")
                        continue

                    state = results[0].get("state")

                    if state == "done":
                        logger.info(f"[MinerU] 转换完成: {data_id}")
                        return results[0]

                    elif state == "failed":
                        err_info = results[0].get("err_msg") or results[0].get("task_status_msg") or "未知错误"
                        logger.error(f"[MinerU] 云端转换失败: {data_id}, {err_info}")
                        return None

                    await asyncio.sleep(poll_interval)
                    waited += poll_interval

            except Exception as e:
                logger.error(f"[MinerU] 轮询异常: {e}")
                await asyncio.sleep(poll_interval)
                waited += poll_interval

        logger.error(f"[MinerU] 转换超时: {data_id}")
        return None

    async def _download_and_parse(
        self,
        result_data: Dict[str, Any],
        output_dir: Path,
        stem: str,
    ) -> Tuple[Optional[str], Optional[Path]]:
        """下载并解析转换结果"""
        zip_url = result_data.get("full_zip_url", "")
        if not zip_url:
            logger.error(f"[MinerU] 未找到 full_zip_url")
            return None, None

        # 创建临时目录
        temp_dir = output_dir / f"_tmp_mineru_{stem}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        zip_path = temp_dir / f"{stem}.zip"

        try:
            # 下载 ZIP（OSS 签名 URL，需要禁止自动添加请求头）
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    zip_url,
                    timeout=aiohttp.ClientTimeout(total=120),
                    ssl=_SSL_CONTEXT,
                    skip_auto_headers=["User-Agent", "Accept", "Accept-Encoding"],
                ) as resp:
                    zip_data = await resp.read()
                    zip_path.write_bytes(zip_data)

            # 解压
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(temp_dir)
            zip_path.unlink()

            # 读取 MD
            full_md = temp_dir / "full.md"
            text = full_md.read_text(encoding="utf-8") if full_md.exists() else ""

            # 处理图片目录
            src_images_dir = temp_dir / "images"
            target_images_dir = None
            if src_images_dir.exists() and src_images_dir.is_dir():
                target_images_dir = output_dir / f"{stem}_images"
                if target_images_dir.exists():
                    shutil.rmtree(target_images_dir)
                src_images_dir.rename(target_images_dir)

            # 保留结构化 JSON
            for json_name in ("layout.json", "content_list.json", "middle.json"):
                src_json = temp_dir / json_name
                if src_json.exists():
                    target_json = output_dir / f"{stem}_{json_name}"
                    if target_json.exists():
                        target_json.unlink()
                    shutil.copy2(src_json, target_json)

            return text, target_images_dir

        finally:
            # 清理临时目录
            shutil.rmtree(temp_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════════════════
async def convert_pdf_async(
    pdf_path: Path,
    output_dir: Path,
    progress_cb: Optional[Callable[[str, str], None]] = None,
) -> ConvertResult:
    """异步转换单个 PDF（便捷函数）"""
    converter = AsyncMinerUConverter()
    return await converter.convert_single(pdf_path, output_dir, progress_cb=progress_cb)


async def convert_batch_async(
    pdf_paths: List[Path],
    output_dir: Path,
    max_concurrent: int = 3,
    progress_cb: Optional[Callable[[BatchProgress], None]] = None,
) -> List[ConvertResult]:
    """异步批量转换（便捷函数）"""
    converter = AsyncMinerUConverter()
    return await converter.convert_batch(
        pdf_paths, output_dir, max_concurrent, progress_cb=progress_cb
    )


def convert_batch_sync(
    pdf_paths: List[Path],
    output_dir: Path,
    max_concurrent: int = 3,
    progress_cb: Optional[Callable[[BatchProgress], None]] = None,
) -> List[ConvertResult]:
    """同步批量转换（包装异步函数）"""
    return asyncio.run(convert_batch_async(pdf_paths, output_dir, max_concurrent, progress_cb))


# ═══════════════════════════════════════════════════════════
# 测试代码
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python mineru_async.py <pdf_path> [output_dir]")
        sys.exit(1)

    pdf_path = Path(sys.argv[1])
    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else pdf_path.parent

    def progress(stage: str, detail: str):
        print(f"  [{stage}] {detail}")

    result = asyncio.run(convert_pdf_async(pdf_path, output_dir, progress))

    if result.success:
        print(f"\\n转换成功！")
        print(f"  文本长度: {len(result.text)} 字符")
        print(f"  图片目录: {result.images_dir}")
    else:
        print(f"\\n转换失败: {result.error}")
