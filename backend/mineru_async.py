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

from pdf_to_md import _OCR_FIXES  # 共享完整纠错表，根除复制漂移

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
    # 真批量轮询聚合的页级进度（一次轮询拿到 batch 内所有文件的 extract_progress）
    pages_done: int = 0       # batch 内已识别页数
    pages_total: int = 0      # batch 内总页数
    batch_id: str = ""        # 当前 batch_id，用于状态持久化/中断恢复


@dataclass
class FileSpec:
    """批量提交中的单个文件描述（真批量编排用）

    一个原始 PDF 若超过 MINERU_MAX_PAGES 会被切成多个 chunk，每个 chunk 是一个
    FileSpec，通过 data_id 在批量轮询结果中精确定位回本文件。整文件提交时
    start_page/end_page 为 0、source_pdf 等于 path。
    """
    path: Path                       # 本地文件路径（chunk 文件或原始 PDF）
    name: str                        # 提交给 API 的文件名
    data_id: str                     # 唯一标识，格式 "{source_stem}__chunk{start}_{end}"
    start_page: int = 0              # chunk 起始页（0-based）；整文件为 0
    end_page: int = 0                # chunk 结束页；整文件为 0
    source_pdf: Optional[Path] = None  # 原始 PDF（多 chunk 合并用；单文件时等于 path）


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
# OCR 纠错规则（自 pdf_to_md.py 导入共享，见文件顶部 import）
# ═══════════════════════════════════════════════════════════

_SIGNATURE_HTML = '<div style="text-align:center;color:#aaa;border-bottom:1px dashed #ccc;padding:2px 20px;margin:2px 0;font-size:11px;user-select:none;">[手写签名]</div>'
_SIGNATURE_PATTERNS = [
    (r"(询问人[：:]\s*)[^\n]*", rf"\1{_SIGNATURE_HTML}"),
    (r"(讯问人[：:]\s*)[^\n]*", rf"\1{_SIGNATURE_HTML}"),
    (r"(记录人[：:]\s*)[^\n]*", rf"\1{_SIGNATURE_HTML}"),
    (r"(被询问人[：:]\s*)[^\n]*", rf"\1{_SIGNATURE_HTML}"),
    (r"(被讯问人[：:]\s*)[^\n]*", rf"\1{_SIGNATURE_HTML}"),
    (r"(捺印人[：:]\s*)[^\n]*", rf"\1{_SIGNATURE_HTML}"),
    (r"(翻译人[：:]\s*)[^\n]*", rf"\1{_SIGNATURE_HTML}"),
    (r"(法定代理人[：:]\s*)[^\n]*", rf"\1{_SIGNATURE_HTML}"),
    (r"(办案单位[：:]\s*)[^\n]*", rf"\1{_SIGNATURE_HTML}"),
    (r"(办案人[：:]\s*)[^\n]*", rf"\1{_SIGNATURE_HTML}"),
    (r"(侦查人员[：:]\s*)[^\n]*", rf"\1{_SIGNATURE_HTML}"),
    (r"(见证人[：:]\s*)[^\n]*", rf"\1{_SIGNATURE_HTML}"),
    (r"(持有人[：:]\s*)[^\n]*", rf"\1{_SIGNATURE_HTML}"),
    (r"(交出人[：:]\s*)[^\n]*", rf"\1{_SIGNATURE_HTML}"),
    (r"(接收人[：:]\s*)[^\n]*", rf"\1{_SIGNATURE_HTML}"),
]


def _fix_ocr_errors(text: str) -> str:
    """修复 MinerU API 常见 OCR 错误"""
    for wrong, correct in _OCR_FIXES:
        text = text.replace(wrong, correct)
    # 清理中文语境中的孤立日语假名の
    # 规则：汉字+の+汉字 → 汉字的汉字（の 在中文案卷中几乎一定是 OCR 误识）
    text = re.sub(r'([一-鿿])の([一-鿿])', r'\1的\2', text)
    # 行内孤立 の（前后不是日文假名）
    text = re.sub(r'(?<![ぁ-んァ-ン])の(?![ぁ-んァ-ン])', '的', text)
    return text


def _protect_signatures_as_images(text: str) -> str:
    """将签名区替换为图片占位符"""
    for pattern, replacement in _SIGNATURE_PATTERNS:
        text = re.sub(pattern, replacement, text)
    return text


def _fold_consecutive_images(text: str, min_count: int = 1) -> Tuple[str, int]:
    """将连续图片折叠为 <details> 块"""
    lines = text.split('\n')
    # 同时匹配 MinerU 的 ![]() 和 PaddleOCR 的 <img> 单行标签
    # （原副本此处为正则双重转义，从不匹配任何内容，已一并修正为与 pdf_to_md.py 一致）
    is_image = [
        bool(
            re.match(r'^!\[.*\]\([^)]+\)$', line.strip())
            or re.match(r'^<img\s[^>]*>$', line.strip())
        )
        for line in lines
    ]

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
                result.append(f'<details><summary>📎 签名/印章图片（共 {len(block_lines)} 张，点击展开）</summary>\n')
                result.extend(block_lines)
                result.append('</details>')
                block_count += 1
            else:
                result.extend(block_lines)
            i = j
        else:
            result.append(lines[i])
            i += 1

    return '\n'.join(result), block_count


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

    def __init__(
        self,
        token: Optional[str] = None,
        model_version: Optional[str] = None,
        max_concurrent_upload: int = DEFAULT_MAX_CONCURRENT,
    ):
        self.token = token or _get_mineru_token()
        if not self.token:
            raise ValueError("MinerU Token 未配置，请设置 MINERU_TOKEN 环境变量或在设置中配置")
        # 模型版本：参数 > 配置文件 > 默认 vlm
        if not model_version:
            try:
                from config_manager import get_config_value
                model_version = get_config_value("mineru_model_version") or "vlm"
            except ImportError:
                model_version = "vlm"
        self.model_version = model_version
        self.max_concurrent_upload = max_concurrent_upload
        # 调试日志：显示 token 来源和前几位（不泄露完整 token）
        source = "参数传入" if token else "配置文件/环境变量"
        logger.info(
            f"[MinerU] 初始化: token来源={source}, model_version={self.model_version}, "
            f"max_concurrent_upload={self.max_concurrent_upload}, token前20字符={self.token[:20]}..."
        )

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
        """真批量转换：跨 PDF 聚合所有 chunk 到同一批次提交

        替代旧的"每文件独立 batch_id + Semaphore 并发"伪批量。编排：
        1. 对每个 PDF 切 chunk → 收集所有 FileSpec（一个 PDF 的多个 chunk 进同一批）
        2. 按 MINERU_BATCH_SIZE(=50) 分组
        3. 每组：_submit_batch 一次提交 → 并发 PUT 上传 → _poll_batch 单条轮询（带页级进度）
           → _download_and_parse 每个 done 结果
        4. 失败的 FileSpec 整体重提一轮（最多 3 轮，每轮退避）
        5. 同源 chunk 按 start_page 排序合并 → 每个源 PDF 一个 MD

        max_concurrent 在真批量下语义为"上传并发数"（提交与轮询已是批量聚合的）。
        """
        logger.info(
            f"[MinerU] convert_batch(真批量): {len(pdf_paths)} 个 PDF, "
            f"output={output_dir}, 上传并发={max_concurrent}"
        )
        output_dir.mkdir(parents=True, exist_ok=True)

        progress = BatchProgress(total=len(pdf_paths))
        progress_lock = asyncio.Lock()

        def emit_progress():
            if progress_cb:
                progress_cb(progress)

        # 1. 切 chunk → 收集 FileSpec
        pdf_to_specs: Dict[Path, List[FileSpec]] = {}
        chunk_temp_paths: List[Path] = []
        for pdf_path in pdf_paths:
            try:
                chunks = _split_pdf_pages(pdf_path)  # List[(path, start_1based, end)] 或 []
            except Exception:
                logger.exception(f"[MinerU] 切分失败 {pdf_path.name}")
                pdf_to_specs[pdf_path] = []
                continue

            specs: List[FileSpec] = []
            if chunks:
                for chunk_path, start_page, end_page in chunks:
                    specs.append(FileSpec(
                        path=chunk_path,
                        name=chunk_path.name,
                        data_id=f"{pdf_path.stem}__c{start_page}_{end_page}",
                        start_page=start_page,
                        end_page=end_page,
                        source_pdf=pdf_path,
                    ))
                    chunk_temp_paths.append(chunk_path)
            else:
                # 整文件（无需切分）
                specs.append(FileSpec(
                    path=pdf_path,
                    name=pdf_path.name,
                    data_id=f"{pdf_path.stem}__full",
                    source_pdf=pdf_path,
                ))
            pdf_to_specs[pdf_path] = specs

        all_specs = [s for specs in pdf_to_specs.values() for s in specs]
        if not all_specs:
            return [ConvertResult(file_name=p.name, success=False, error="切分失败") for p in pdf_paths]

        logger.info(f"[MinerU] 共 {len(all_specs)} 个文件单元（含 chunk）")

        # 页级进度回调（_poll_batch → 更新 progress → emit）
        def on_page(pages_done: int, pages_total: int):
            progress.pages_done = pages_done
            progress.pages_total = pages_total
            emit_progress()

        # 2~4. 逐批处理 + 失败重试（最多 3 轮）
        spec_results: Dict[str, Tuple[Optional[str], Optional[Path]]] = {}
        pending = list(all_specs)

        async with aiohttp.ClientSession() as session:
            for attempt in range(3):
                if not pending:
                    break
                if attempt > 0:
                    backoff = 15 * attempt
                    logger.info(f"[MinerU] 第 {attempt} 轮重试: {len(pending)} 个文件单元，退避 {backoff}s")
                    await asyncio.sleep(backoff)

                still_failed: List[FileSpec] = []
                for gi in range(0, len(pending), MINERU_BATCH_SIZE):
                    group = pending[gi:gi + MINERU_BATCH_SIZE]
                    group_results, group_failed = await self._process_specs_group(
                        session, group, output_dir, timeout, on_page,
                    )
                    spec_results.update(group_results)
                    still_failed.extend(group_failed)
                pending = still_failed

        # 5. 合并同源 chunk → 每个源 PDF 一个 ConvertResult
        results: List[ConvertResult] = []
        for pdf_path in pdf_paths:
            specs = pdf_to_specs.get(pdf_path, [])
            result = self._assemble_pdf_result(pdf_path, specs, spec_results, output_dir)
            # 照片类图片文字回填：MinerU 不转录横置照片/截图（转账凭证/流水），
            # 用 PaddleOCR 单图识别补齐（受 image_ocr_enabled 开关控制，需配置 PaddleOCR Token）
            if result.success and result.images_dir:
                try:
                    from config_manager import get_config_value
                    if get_config_value("image_ocr_enabled", True):
                        from image_ocr_backfill import backfill_image_ocr
                        md_path = output_dir / f"{pdf_path.stem}.md"
                        if md_path.exists():
                            new_text = await backfill_image_ocr(
                                md_path.read_text(encoding="utf-8"),
                                result.images_dir, pdf_path.stem,
                            )
                            if new_text != result.text:
                                md_path.write_text(new_text, encoding="utf-8")
                                result.text = new_text
                except Exception as e:
                    logger.warning(f"[图片回填] {pdf_path.name} 失败（不影响转换结果）: {e}")
            results.append(result)
            async with progress_lock:
                if result.success:
                    progress.completed += 1
                else:
                    progress.failed += 1
                progress.current_files = [pdf_path.name]
                emit_progress()

        # 6. 清理 chunk 临时 PDF
        for cp in chunk_temp_paths:
            cp.unlink(missing_ok=True)

        logger.info(
            f"[MinerU] convert_batch 完成: {progress.completed}/{progress.total} 成功, "
            f"{progress.failed} 失败"
        )
        return results

    async def _process_specs_group(
        self,
        session: aiohttp.ClientSession,
        specs: List[FileSpec],
        output_dir: Path,
        timeout: int,
        on_page: Optional[Callable[[int, int], None]] = None,
    ) -> Tuple[Dict[str, Tuple[Optional[str], Optional[Path]]], List[FileSpec]]:
        """处理一组（≤ MINERU_BATCH_SIZE）FileSpec：提交→并发上传→轮询→下载解析

        Returns:
            (spec_results, failed_specs)
            spec_results: {data_id: (text, images_dir)}；成功 text 非空，失败 (None, None)
            failed_specs: 失败的 FileSpec（供上层重试）
        """
        spec_results: Dict[str, Tuple[Optional[str], Optional[Path]]] = {}
        spec_by_id = {s.data_id: s for s in specs}

        # a. 批量提交（一个 batch_id 覆盖整组）
        batch_id, upload_urls, err = await self._submit_batch(session, specs)
        if not batch_id:
            logger.error(f"[MinerU] 批提交失败（{len(specs)} 文件）: {err}")
            for s in specs:
                spec_results[s.data_id] = (None, None)
            return spec_results, list(specs)

        # b. 并发上传（信号量限流，避免 OSS 并发过高）
        upload_sem = asyncio.Semaphore(self.max_concurrent_upload)

        async def _upload_one(spec: FileSpec, url: str):
            async with upload_sem:
                return spec.data_id, await self._upload_file(session, url, spec.path)

        upload_outcomes = await asyncio.gather(*[
            _upload_one(spec, upload_urls[i]) for i, spec in enumerate(specs)
        ])
        uploaded_ids = set()
        for data_id, ok in upload_outcomes:
            if ok:
                uploaded_ids.add(data_id)
            else:
                logger.error(f"[MinerU] 上传失败: {data_id}")
                spec_results[data_id] = (None, None)

        if not uploaded_ids:
            return spec_results, list(specs)

        # c. 单条轮询整组（聚合页级进度）
        extract_results, _, _ = await self._poll_batch(
            session, batch_id, len(specs), timeout,
            page_progress_cb=on_page,
        )
        if not extract_results:
            logger.error(f"[MinerU] batch {batch_id} 轮询超时/失败，整组判失败")
            for s in specs:
                spec_results.setdefault(s.data_id, (None, None))
            failed = [s for s in specs if not spec_results.get(s.data_id, (None, None))[0]]
            return spec_results, failed

        # d. 下载解析每个 done 结果
        for r in extract_results:
            data_id = r.get("data_id")
            spec = spec_by_id.get(data_id)
            if not spec:
                continue
            state = r.get("state")
            if state == "done" and r.get("full_zip_url") and data_id in uploaded_ids:
                try:
                    stem = self._stem_for_spec(spec)
                    text, images_dir = await self._download_and_parse(r, output_dir, stem)
                    if text and len(text) > 100:
                        text = self._post_process_text(text, stem)
                        spec_results[data_id] = (text, images_dir)
                    else:
                        logger.warning(f"[MinerU] 结果为空: {data_id}")
                        spec_results[data_id] = (None, None)
                except Exception:
                    logger.exception(f"[MinerU] 下载解析失败 {data_id}")
                    spec_results[data_id] = (None, None)
            else:
                err_msg = r.get("err_msg", "")
                logger.warning(f"[MinerU] 转换失败 {data_id}: state={state} err={err_msg}")
                spec_results[data_id] = (None, None)

        failed = [s for s in specs if not spec_results.get(s.data_id, (None, None))[0]]
        return spec_results, failed

    @staticmethod
    def _stem_for_spec(spec: FileSpec) -> str:
        """计算 _download_and_parse 使用的 stem（决定中间产物目录名）

        整文件：用 source_pdf.stem（直接产出最终 MD/images 名）
        chunk：用 {source_stem}__c{start}（中间名，合并后清理）
        """
        if spec.start_page == 0 and spec.end_page == 0:
            return spec.source_pdf.stem if spec.source_pdf else spec.path.stem
        return f"{spec.source_pdf.stem}__c{spec.start_page}"

    @staticmethod
    def _post_process_text(text: str, stem: str) -> str:
        """单文件/chunk 结果的后处理：图片路径修正 + 签名保护 + OCR 纠错 + 图片折叠"""
        text = text.replace("images/", f"./{stem}_images/")
        text = text.replace('src="images/', f'src="./{stem}_images/')
        text = _protect_signatures_as_images(text)
        text = _fix_ocr_errors(text)
        text, _ = _fold_consecutive_images(text)
        return text

    def _assemble_pdf_result(
        self,
        pdf_path: Path,
        specs: List[FileSpec],
        spec_results: Dict[str, Tuple[Optional[str], Optional[Path]]],
        output_dir: Path,
    ) -> ConvertResult:
        """把一个源 PDF 的所有 FileSpec 结果合并成一个 ConvertResult（处理单文件与多 chunk）"""
        if not specs:
            return ConvertResult(file_name=pdf_path.name, success=False, error="切分失败")

        # 单文件单元（整文件，_split_pdf_pages 返回 [] 的情形）
        if len(specs) == 1:
            spec = specs[0]
            text, images_dir = spec_results.get(spec.data_id, (None, None))
            if text and len(text) > 100:
                target_md = output_dir / f"{pdf_path.stem}.md"
                target_md.write_text(text, encoding="utf-8")
                return ConvertResult(
                    file_name=pdf_path.name, success=True, text=text, images_dir=images_dir
                )
            return ConvertResult(file_name=pdf_path.name, success=False, error="转换失败或结果为空")

        # 多 chunk：按 start_page 排序合并
        sorted_specs = sorted(specs, key=lambda s: s.start_page)
        parts: List[str] = []
        images_dirs: List[Path] = []
        all_ok = True
        for spec in sorted_specs:
            text, images_dir = spec_results.get(spec.data_id, (None, None))
            if text:
                header = f"---\n\n<!-- 原PDF第{spec.start_page}-{spec.end_page}页 -->\n\n"
                parts.append(header + text)
                if images_dir:
                    images_dirs.append(images_dir)
            else:
                all_ok = False

        if not parts:
            return ConvertResult(file_name=pdf_path.name, success=False, error="所有分段转换失败")

        merged = "\n\n".join(parts)

        # 合并各 chunk images → {source_stem}_images
        merged_images_dir = output_dir / f"{pdf_path.stem}_images"
        merged_images_dir.mkdir(parents=True, exist_ok=True)
        for src_dir in images_dirs:
            if src_dir and src_dir.exists():
                for img in src_dir.iterdir():
                    if img.is_file():
                        shutil.copy2(str(img), str(merged_images_dir / img.name))

        # 修正各 chunk 图片路径（chunk stem 为 {source_stem}__c{start}）→ 统一指向 {source_stem}_images
        merged = re.sub(
            r'\./([^/\s]+?__c\d+_images)/',
            f"./{pdf_path.stem}_images/",
            merged,
        )
        merged = merged.replace("images/", f"./{pdf_path.stem}_images/")
        merged = _protect_signatures_as_images(merged)
        merged = _fix_ocr_errors(merged)
        merged, _ = _fold_consecutive_images(merged)

        target_md = output_dir / f"{pdf_path.stem}.md"
        target_md.write_text(merged, encoding="utf-8")

        # 清理 chunk 中间产物（保留 layout.json 供浏览）
        # 1. 按新命名 {source_stem}__c{start}_* 清理
        for spec in sorted_specs:
            chunk_stem = self._stem_for_spec(spec)
            for intermediate in (
                output_dir / f"{chunk_stem}_images",
                output_dir / f"{chunk_stem}_content_list.json",
                output_dir / f"{chunk_stem}_middle.json",
                output_dir / f"{chunk_stem}.md",
            ):
                if intermediate.exists():
                    if intermediate.is_dir():
                        shutil.rmtree(intermediate, ignore_errors=True)
                    else:
                        intermediate.unlink(missing_ok=True)

        # 2. 按旧命名 _chunk_*_* 清理（兼容重构前的遗留文件，保留 _layout.json）
        for chunk_file in output_dir.glob("_chunk_*"):
            name = chunk_file.name
            if name.endswith("_layout.json"):
                continue  # 保留 layout.json
            if chunk_file.is_dir():
                shutil.rmtree(chunk_file, ignore_errors=True)
            else:
                chunk_file.unlink(missing_ok=True)

        if not all_ok:
            logger.warning(f"[MinerU] {pdf_path.name}: 部分 chunk 失败，已合并可用内容")
        return ConvertResult(
            file_name=pdf_path.name, success=True, text=merged, images_dir=merged_images_dir
        )

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

    async def _submit_batch(
        self,
        session: aiohttp.ClientSession,
        files: List["FileSpec"],
    ) -> Tuple[Optional[str], List[Optional[str]], str]:
        """真批量提交：一次向 /file-urls/batch 提交 ≤ MINERU_BATCH_SIZE 个文件

        Args:
            session: aiohttp 会话
            files: FileSpec 列表（len ≤ MINERU_BATCH_SIZE）

        Returns:
            (batch_id, upload_urls, error_msg)
            成功：(batch_id, [url1, url2, ...], "")   upload_urls 与 files 一一对应
            失败：(None, [], "错误描述")
        """
        try:
            params = {
                "is_ocr": "true",
                "enable_formula": "false",
                "enable_table": "true",
                "language": "ch_server",
            }
            json_body = {
                "files": [{"name": f.name, "data_id": f.data_id} for f in files],
                "model_version": self.model_version,
            }

            async with session.post(
                f"{MINERU_API}/file-urls/batch",
                headers={"Authorization": f"Bearer {self.token}"},
                params=params,
                json=json_body,
                timeout=aiohttp.ClientTimeout(total=30),
                ssl=_SSL_CONTEXT,
            ) as resp:
                if resp.status == 401:
                    body = await resp.text()
                    logger.error(f"[MinerU] 批量提交认证失败 (401): {body[:200]}")
                    return None, [], "MinerU Token 无效或已过期，请在设置页面重新配置"
                if resp.status != 200:
                    body = await resp.text()
                    err_msg = f"MinerU API HTTP {resp.status}: {body[:200]}"
                    logger.error(f"[MinerU] {err_msg}")
                    return None, [], err_msg

                result = await resp.json()

                if result.get("code") != 0:
                    err_code = result.get("code")
                    err_msg = result.get("msg", "未知错误")
                    logger.error(f"[MinerU] API 错误 code={err_code}: {err_msg}")
                    return None, [], f"MinerU API 错误 ({err_code}): {err_msg}"

                data = result.get("data", {})
                batch_id = data.get("batch_id")
                file_urls = data.get("file_urls", [])

                if not batch_id:
                    logger.error("[MinerU] 批量提交返回数据不完整: 无 batch_id")
                    return None, [], "MinerU 返回数据不完整（无 batch_id）"

                if len(file_urls) != len(files):
                    logger.error(
                        f"[MinerU] 上传链接数({len(file_urls)}) != 文件数({len(files)})"
                    )
                    return None, [], "MinerU 返回的上传链接数量与文件数不一致"

                logger.info(
                    f"[MinerU] 批量提交成功: batch_id={batch_id}, 文件数={len(files)}"
                )
                return batch_id, file_urls, ""

        except asyncio.TimeoutError:
            logger.error(f"[MinerU] 批量提交超时（{len(files)} 个文件）")
            return None, [], "MinerU 批量提交超时"
        except aiohttp.ClientError as e:
            logger.error(f"[MinerU] 网络错误: {e}")
            return None, [], f"MinerU 网络错误: {str(e)[:100]}"
        except Exception as e:
            logger.error(f"[MinerU] 批量提交异常: {e}")
            return None, [], f"MinerU 提交异常: {str(e)[:100]}"

    async def _poll_batch(
        self,
        session: aiohttp.ClientSession,
        batch_id: str,
        file_count: int,
        timeout: int,
        progress_cb: Optional[Callable[[str, str], None]] = None,
        page_progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> Tuple[Optional[List[Dict[str, Any]]], int, int]:
        """真批量聚合轮询：单条循环拿回 batch 内所有文件状态 + 页级进度

        一次 GET /extract-results/batch/{batch_id} 即可拿到 batch 内每个文件的
        state 与 extract_progress，相比伪批量（每文件一条轮询）请求量降一个数量级，
        且天然获得页级进度。

        Args:
            session: aiohttp 会话
            batch_id: 批次 ID
            file_count: 批次内文件数（判断是否全部结束）
            timeout: 超时秒数
            progress_cb: 文字进度回调 (stage, detail)
            page_progress_cb: 页级进度回调 (pages_done, pages_total)

        Returns:
            (extract_results, pages_done, pages_total)
            成功：(extract_result 数组, 累计已识别页, 总页)
            超时：(None, 最后一次的 pages_done, pages_total)
        """
        waited = 0
        pages_done = 0
        pages_total = 0

        while waited < timeout:
            try:
                async with session.get(
                    f"{MINERU_API}/extract-results/batch/{batch_id}",
                    headers={"Authorization": f"Bearer {self.token}"},
                    timeout=aiohttp.ClientTimeout(total=30),
                    ssl=_SSL_CONTEXT,
                ) as resp:
                    if resp.status == 401:
                        logger.error("[MinerU] 轮询认证失败 (401): Token 无效或已过期")
                        return None, pages_done, pages_total

                    result = await resp.json()
                    data = result.get("data", {})
                    extract_results = data.get("extract_result", [])

                    if not extract_results:
                        # 批次刚提交，文件尚未入列
                        await asyncio.sleep(POLL_INTERVAL)
                        waited += POLL_INTERVAL
                        if progress_cb:
                            progress_cb("processing", f"排队中...（已等待 {waited} 秒）")
                        continue

                    # 聚合 batch 内所有文件状态
                    done = sum(1 for r in extract_results if r.get("state") == "done")
                    failed = sum(1 for r in extract_results if r.get("state") == "failed")
                    pages_done = 0
                    pages_total = 0
                    for r in extract_results:
                        ep = r.get("extract_progress") or {}
                        if ep:
                            pages_done += int(ep.get("extracted_pages", 0) or 0)
                            pages_total += int(ep.get("total_pages", 0) or 0)

                    if progress_cb and pages_total > 0:
                        progress_cb(
                            "processing",
                            f"已识别 {done}/{file_count} 文件（{pages_done}/{pages_total} 页）",
                        )
                    elif progress_cb:
                        progress_cb("processing", f"已完成 {done}/{file_count} 文件")

                    if page_progress_cb:
                        page_progress_cb(pages_done, pages_total)

                    # 全部结束（含失败）则返回
                    if done + failed >= file_count:
                        logger.info(
                            f"[MinerU] batch {batch_id} 全部完成: "
                            f"成功 {done}, 失败 {failed}, 页 {pages_done}/{pages_total}"
                        )
                        return extract_results, pages_done, pages_total

            except asyncio.TimeoutError:
                logger.warning(f"[MinerU] 轮询单次超时（batch {batch_id}），继续重试")
            except aiohttp.ClientError as e:
                logger.warning(f"[MinerU] 轮询网络错误（batch {batch_id}）: {e}，继续重试")
            except Exception as e:
                logger.warning(f"[MinerU] 轮询异常（batch {batch_id}）: {e}，继续重试")

            await asyncio.sleep(POLL_INTERVAL)
            waited += POLL_INTERVAL

        logger.error(
            f"[MinerU] batch {batch_id} 轮询超时（{timeout}s）: "
            f"最后状态 页 {pages_done}/{pages_total}"
        )
        return None, pages_done, pages_total

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
        print(f"\n转换成功！")
        print(f"  文本长度: {len(result.text)} 字符")
        print(f"  图片目录: {result.images_dir}")
    else:
        print(f"\n转换失败: {result.error}")
