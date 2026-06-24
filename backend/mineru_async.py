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

import asyncio
import logging
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import aiohttp

# 配置日志（PyInstaller --noconsole 模式下 print() 不可见，必须用 logger）
logger = logging.getLogger(__name__)
# 临时启用 DEBUG 级别以诊断 MinerU 问题
logger.setLevel(logging.DEBUG)


# 从 helpers 模块导入辅助函数、常量和数据类（向后兼容 re-export）
from mineru_async_helpers import (  # noqa: F401
    _OCR_FIXES,
    _SIGNATURE_HTML,
    _SIGNATURE_PATTERNS,
    _SSL_CONTEXT,
    DEFAULT_MAX_CONCURRENT,
    DEFAULT_TIMEOUT,
    MINERU_API,
    MINERU_BATCH_SIZE,
    MINERU_MAX_FILE_SIZE,
    MINERU_MAX_PAGES,
    POLL_INTERVAL,
    BatchProgress,
    ConvertResult,
    _fix_ocr_errors,
    _fold_consecutive_images,
    _get_mineru_local_url,
    _get_mineru_mode,
    _get_mineru_token,
    _get_ssl_context,
    _protect_signatures_as_images,
    _split_pdf_pages,
)

__all__ = [
    "AsyncMinerUConverter",
    "ConvertResult",
    "BatchProgress",
    "convert_batch_sync",
]


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
    - 支持本地部署模式

    用法：
        converter = AsyncMinerUConverter()
        results = await converter.convert_batch(
            [Path("/path/to/file1.pdf"), Path("/path/to/file2.pdf")],
            output_dir=Path("/output"),
            progress_cb=lambda p: print(f"{p.completed}/{p.total}")
        )
    """

    def __init__(self, token: str | None = None):
        self.mode = _get_mineru_mode()
        self.local_url = _get_mineru_local_url()

        if self.mode == "local":
            if not self.local_url:
                raise ValueError("MinerU 本地模式需配置 mineru_local_url，请在设置中配置")
            logger.info(f"[MinerU] 初始化: 模式=本地, 地址={self.local_url}")
            self.token = ""
        else:
            self.token = token or _get_mineru_token()
            if not self.token:
                raise ValueError("MinerU Token 未配置，请设置 MINERU_TOKEN 环境变量或在设置中配置")
            # 敏感信息脱敏：仅记录来源与长度，不输出任何 token 字符（避免经日志端点泄露）
            source = "参数传入" if token else "配置文件/环境变量"
            logger.info(f"[MinerU] 初始化: 模式=云端, token来源={source}, token长度={len(self.token)}")

    async def convert_single(
        self,
        pdf_path: Path,
        output_dir: Path,
        timeout: int = DEFAULT_TIMEOUT,
        progress_cb: Callable[[str, str], None] | None = None,
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
        # 本地模式：直接调用本地 API，无需分段
        if self.mode == "local":
            return await self._convert_single_file_local(pdf_path, output_dir, timeout, progress_cb)

        # 云端模式：检查是否需要分段
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
        pdf_paths: list[Path],
        output_dir: Path,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        timeout: int = DEFAULT_TIMEOUT,
        progress_cb: Callable[[BatchProgress], None] | None = None,
    ) -> list[ConvertResult]:
        """批量转换多个 PDF 文件（并发处理）"""
        logger.info(f"[MinerU] convert_batch 入口: {len(pdf_paths)} 个文件, output_dir={output_dir}, max_concurrent={max_concurrent}")
        output_dir.mkdir(parents=True, exist_ok=True)

        # 云端模式：优先真批量（原文件+chunks 合并提交，单 batch_id 单轮询）
        if self.mode == "cloud" and pdf_paths:
            try:
                return await self._convert_batch_cloud(pdf_paths, output_dir, timeout, progress_cb)
            except Exception as e:
                logger.exception(f"[MinerU] 真批量异常，降级为逐文件并发: {e}")
                # 降级到下方逐文件并发逻辑

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

    async def _convert_batch_cloud(
        self,
        pdf_paths: list[Path],
        output_dir: Path,
        timeout: int = DEFAULT_TIMEOUT,
        progress_cb: Callable[[BatchProgress], None] | None = None,
    ) -> list[ConvertResult]:
        """云端真批量转换：原文件+拆分chunks 合并提交，单 batch_id 单轮询。

        相比逐文件并发，N 次提交→⌈N/50⌉ 次，N 个轮询流→1 个，大幅减少请求数。
        """
        from dataclasses import dataclass

        @dataclass
        class Unit:
            file_path: Path            # 待提交文件（原文件或 chunk）
            data_id: str
            owner: Path                # 所属原文件（chunk 的 owner 是被拆分的原文件）
            is_chunk: bool
            chunk_idx: int             # chunk 在 owner 中的序号
            start_page: int
            end_page: int

        # ── 第1步：收集所有任务单元（原文件 + 大文件拆分的 chunks）──
        units: list[Unit] = []
        for pdf in pdf_paths:
            try:
                chunks = _split_pdf_pages(pdf)  # [(path, start, end), ...] 或 []
            except Exception as e:
                logger.warning(f"[MinerU-批量] 拆分失败 {pdf.name}，按单文件处理: {e}")
                chunks = []

            if not chunks:
                # 无需拆分：整体作为一个单元
                units.append(Unit(pdf, pdf.stem, pdf, False, 0, 0, 0))
            else:
                logger.info(f"[MinerU-批量] {pdf.name} 拆分为 {len(chunks)} 段")
                for idx, (cpath, start, end) in enumerate(chunks):
                    units.append(Unit(cpath, cpath.stem, pdf, True, idx, start, end))

        if not units:
            return []

        progress = BatchProgress(total=len(pdf_paths))
        progress_lock = asyncio.Lock()

        def _report():
            if progress_cb:
                progress_cb(progress)

        # ── 第2步：分批提交（≤ MINERU_BATCH_SIZE）──
        # 每批：提交 → 并发上传 → 单轮询 → 逐个下载
        # unit_result: data_id → (text, images_dir) 或 None
        unit_results: dict[str, tuple[str | None, Path | None]] = {}

        async with aiohttp.ClientSession() as session:
            for batch_start in range(0, len(units), MINERU_BATCH_SIZE):
                batch = units[batch_start:batch_start + MINERU_BATCH_SIZE]
                logger.info(f"[MinerU-批量] 提交第 {batch_start // MINERU_BATCH_SIZE + 1} 批: {len(batch)} 个文件")

                # 提交
                files_payload = [(u.file_path, u.data_id) for u in batch]
                batch_id, upload_urls, err = await self._submit_batch_task(session, files_payload)
                if not batch_id:
                    logger.error(f"[MinerU-批量] 批量提交失败: {err}，该批降级为单文件")
                    # 降级：逐个单文件转换
                    for u in batch:
                        r = await self._convert_single_file(u.file_path, output_dir, timeout, None)
                        unit_results[u.data_id] = (r.text if r.success else None, r.images_dir if r.success else None)
                        if u.is_chunk:
                            u.file_path.unlink(missing_ok=True)
                        async with progress_lock:
                            progress.completed += 1
                            if not (r.text if r.success else None):
                                progress.failed += 1
                            progress.current_files = [u.file_path.name]
                            _report()
                    continue

                # 并发上传
                upload_tasks = [
                    self._upload_file(session, upload_urls[i], batch[i].file_path)
                    for i in range(len(batch))
                ]
                upload_oks = await asyncio.gather(*upload_tasks)
                for u, ok in zip(batch, upload_oks):
                    if not ok:
                        logger.error(f"[MinerU-批量] 上传失败: {u.file_path.name}")
                        unit_results[u.data_id] = (None, None)

                # 单轮询拿全部状态
                results = await self._poll_batch_results(
                    session, batch_id, len(batch), timeout,
                    lambda stage, detail: None,
                )
                if not results:
                    logger.error(f"[MinerU-批量] 轮询失败 batch_id={batch_id}")
                    for u in batch:
                        unit_results.setdefault(u.data_id, (None, None))
                        if u.is_chunk:
                            u.file_path.unlink(missing_ok=True)
                    continue

                # 按 file_name 匹配并下载（extract_result 含 file_name）
                result_by_name = {r.get("file_name"): r for r in results}
                for u in batch:
                    r = result_by_name.get(u.file_path.name)
                    if r and r.get("state") == "done" and u.data_id not in unit_results:
                        text, images_dir = await self._download_and_parse(r, output_dir, u.data_id)
                        unit_results[u.data_id] = (text, images_dir)
                    else:
                        unit_results.setdefault(u.data_id, (None, None))
                        err_msg = r.get("err_msg") if r else "无结果"
                        logger.warning(f"[MinerU-批量] {u.file_path.name} 未成功: {err_msg}")
                    if u.is_chunk:
                        u.file_path.unlink(missing_ok=True)

        # ── 第3步：按 owner 组装结果（chunks 按页序合并回原文件）──
        final_results: list[ConvertResult] = []
        for pdf in pdf_paths:
            owner_units = [u for u in units if u.owner == pdf]
            if not owner_units:
                continue

            if len(owner_units) == 1 and not owner_units[0].is_chunk:
                # 单文件（未拆分）
                u = owner_units[0]
                text, images_dir = unit_results.get(u.data_id, (None, None))
                if text:
                    # 落盘 MD（生产流程的证据提取依赖 MD 文件存在）
                    target_md = output_dir / f"{pdf.stem}.md"
                    target_md.write_text(text, encoding="utf-8")
                    final_results.append(ConvertResult(file_name=pdf.name, success=True, text=text, images_dir=images_dir))
                else:
                    final_results.append(ConvertResult(file_name=pdf.name, success=False, error="转换失败"))
            else:
                # 多 chunk：按页序合并
                chunk_texts = []
                all_images: list[Path] = []
                for u in sorted(owner_units, key=lambda x: x.start_page):
                    text, images_dir = unit_results.get(u.data_id, (None, None))
                    if text:
                        chunk_texts.append((text, u.start_page, u.end_page))
                    if images_dir:
                        all_images.append(images_dir)

                if not chunk_texts:
                    final_results.append(ConvertResult(file_name=pdf.name, success=False, error="所有分段转换失败"))
                    continue

                # 复用 _convert_chunks 的合并逻辑（页码分隔 + 图片路径修正 + 后处理）
                merged_parts = []
                for text, start_page, end_page in chunk_texts:
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

                merged_images_dir = output_dir / f"{pdf.stem}_images"
                merged_images_dir.mkdir(parents=True, exist_ok=True)
                for src_dir in all_images:
                    if src_dir.exists():
                        for img in src_dir.iterdir():
                            if img.is_file():
                                shutil.copy2(str(img), str(merged_images_dir / img.name))

                target_md = output_dir / f"{pdf.stem}.md"
                target_md.write_text(merged_text, encoding="utf-8")
                final_results.append(ConvertResult(
                    file_name=pdf.name, success=True, text=merged_text, images_dir=merged_images_dir
                ))

            async with progress_lock:
                progress.completed += 1
                if not final_results[-1].success:
                    progress.failed += 1
                progress.current_files = [pdf.name]
                _report()

        return final_results

    async def _convert_single_file(
        self,
        pdf_path: Path,
        output_dir: Path,
        timeout: int,
        progress_cb: Callable[[str, str], None] | None = None,
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

    async def _convert_single_file_local(
        self,
        pdf_path: Path,
        output_dir: Path,
        timeout: int,
        progress_cb: Callable[[str, str], None] | None = None,
    ) -> ConvertResult:
        """本地模式：调用本地 MinerU API（异步任务模式）

        本地 API 流程：
        1. POST /tasks 提交异步任务，获取 task_id
        2. GET /tasks/{task_id} 轮询任务状态
        3. GET /tasks/{task_id}/result 获取结果
        """
        logger.debug(f"[MinerU-Local] _convert_single_file_local 入口: 文件={pdf_path.name}, timeout={timeout}秒")
        stem = pdf_path.stem

        try:
            if progress_cb:
                progress_cb("uploading", "正在发送到本地服务器...")

            with open(pdf_path, "rb") as f:
                file_content = f.read()

            ssl_ctx = _SSL_CONTEXT if self.local_url.startswith("https") else False

            # 调试日志：显示请求详情
            logger.debug(f"[MinerU-Local] 准备提交: 文件={pdf_path.name}, 大小={len(file_content)} 字节, URL={self.local_url}/tasks")

            async with aiohttp.ClientSession() as session:
                # 1. 提交异步任务
                form_data = aiohttp.FormData()
                form_data.add_field(
                    'files',
                    file_content,
                    filename=pdf_path.name,
                    content_type='application/pdf'
                )
                # 使用 pipeline 后端，速度快，适合大批量处理打印体案卷
                form_data.add_field('backend', 'pipeline')
                # 中文案卷，使用 ch_server 语言包
                form_data.add_field('lang_list', 'ch_server')
                # 案卷通常无复杂数学公式，禁用以加速处理
                form_data.add_field('formula_enable', 'false')
                # 案卷有表格，保持启用
                form_data.add_field('table_enable', 'true')

                if progress_cb:
                    progress_cb("submitting", "正在提交解析任务...")

                logger.debug(f"[MinerU-Local] 发送 POST 请求到 {self.local_url}/tasks")
                async with session.post(
                    f"{self.local_url}/tasks",
                    data=form_data,
                    timeout=aiohttp.ClientTimeout(total=60),
                    ssl=ssl_ctx,
                ) as resp:
                    logger.debug(f"[MinerU-Local] 响应状态: {resp.status}")
                    if resp.status not in (200, 201, 202):
                        text = await resp.text()
                        err_msg = f"本地 MinerU 提交失败 HTTP {resp.status}: {text[:200]}"
                        logger.error(f"[MinerU-Local] {err_msg}")
                        return ConvertResult(
                            file_name=pdf_path.name,
                            success=False,
                            error=err_msg
                        )

                    task_info = await resp.json()
                    task_id = task_info.get("task_id")
                    if not task_id:
                        err_msg = f"本地 MinerU 未返回 task_id: {task_info}"
                        logger.error(f"[MinerU-Local] {err_msg}")
                        return ConvertResult(
                            file_name=pdf_path.name,
                            success=False,
                            error=err_msg
                        )

                    logger.info(f"[MinerU-Local] 任务已提交: {pdf_path.name}, task_id={task_id}")

                # 2. 轮询任务状态
                if progress_cb:
                    progress_cb("processing", "正在识别文本内容...")

                waited = 0
                poll_interval = 5
                logger.debug(f"[MinerU-Local] 开始轮询任务状态: task_id={task_id}, timeout={timeout}秒")
                while waited < timeout:
                    logger.debug(f"[MinerU-Local] 轮询中: waited={waited}秒, task_id={task_id}")
                    async with session.get(
                        f"{self.local_url}/tasks/{task_id}",
                        timeout=aiohttp.ClientTimeout(total=30),
                        ssl=ssl_ctx,
                    ) as resp:
                        logger.debug(f"[MinerU-Local] 轮询响应状态: {resp.status}")
                        if resp.status != 200:
                            await asyncio.sleep(poll_interval)
                            waited += poll_interval
                            continue

                        status_info = await resp.json()
                        status = status_info.get("status", "")
                        logger.debug(f"[MinerU-Local] 任务状态: {status}")

                        if status == "completed":
                            logger.info(f"[MinerU-Local] 任务完成: {task_id}")
                            break
                        elif status == "failed":
                            err_msg = status_info.get("error") or "任务执行失败"
                            logger.error(f"[MinerU-Local] 任务失败: {task_id}, {err_msg}")
                            return ConvertResult(
                                file_name=pdf_path.name,
                                success=False,
                                error=f"本地 MinerU 任务失败: {err_msg}"
                            )

                        await asyncio.sleep(poll_interval)
                        waited += poll_interval

                        if progress_cb and waited % 30 == 0:
                            progress_cb("processing", f"正在处理...（已等待 {waited} 秒）")
                else:
                    # while...else: 仅当循环正常结束（非 break）时执行
                    logger.error(f"[MinerU-Local] 轮询超时: waited={waited}秒, timeout={timeout}秒, task_id={task_id}")
                    return ConvertResult(
                        file_name=pdf_path.name,
                        success=False,
                        error=f"本地 MinerU 超时（{timeout}秒）"
                    )

                # 3. 获取结果
                async with session.get(
                    f"{self.local_url}/tasks/{task_id}/result",
                    timeout=aiohttp.ClientTimeout(total=120),
                    ssl=ssl_ctx,
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        err_msg = f"获取结果失败 HTTP {resp.status}: {text[:200]}"
                        logger.error(f"[MinerU-Local] {err_msg}")
                        return ConvertResult(
                            file_name=pdf_path.name,
                            success=False,
                            error=err_msg
                        )

                    result = await resp.json()

            # 解析结果 - pipeline 后端返回格式: {"results": {"文件名": {"md_content": "..."}}}
            content = ""
            if "results" in result:
                # pipeline/hybrid 后端格式
                for file_name, file_result in result["results"].items():
                    content = file_result.get("md_content", "") or file_result.get("markdown", "")
                    if content:
                        break
            if not content:
                # 兼容其他格式
                content = result.get("markdown", "") or result.get("content", "") or result.get("md_content", "")

            if not content or len(content) < 100:
                logger.error(f"[MinerU-Local] 结果内容为空: {pdf_path.name}, result keys: {list(result.keys())}")
                return ConvertResult(
                    file_name=pdf_path.name,
                    success=False,
                    error="结果内容为空"
                )

            # 后处理
            text = content
            text = _protect_signatures_as_images(text)
            text = _fix_ocr_errors(text)
            text, _ = _fold_consecutive_images(text)

            # 处理图片路径
            images_dir = None
            if "images/" in text:
                images_dir = output_dir / f"{stem}_images"
                images_dir.mkdir(parents=True, exist_ok=True)
                text = text.replace("images/", f"./{stem}_images/")
                text = text.replace('src="images/', f'src="./{stem}_images/')

            # 保存 MD
            target_md = output_dir / f"{stem}.md"
            target_md.write_text(text, encoding="utf-8")

            logger.info(f"[MinerU-Local] 转换成功: {pdf_path.name}, {len(text)} 字符")
            return ConvertResult(
                file_name=pdf_path.name,
                success=True,
                text=text,
                images_dir=images_dir
            )

        except asyncio.TimeoutError:
            logger.error(f"[MinerU-Local] 超时: {pdf_path.name}")
            return ConvertResult(
                file_name=pdf_path.name,
                success=False,
                error="本地 MinerU 超时"
            )
        except aiohttp.ClientError as e:
            logger.error(f"[MinerU-Local] 网络错误: {e}")
            return ConvertResult(
                file_name=pdf_path.name,
                success=False,
                error=f"本地 MinerU 网络错误: {str(e)[:100]}"
            )
        except Exception as e:
            logger.error(f"[MinerU-Local] 异常: {pdf_path.name}, {e}")
            return ConvertResult(
                file_name=pdf_path.name,
                success=False,
                error=str(e)[:200]
            )

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
    ) -> tuple[str | None, str | None, str]:
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

    async def _submit_batch_task(
        self,
        session: aiohttp.ClientSession,
        files: list[tuple[Path, str]],
    ) -> tuple[str | None, list[str], str]:
        """批量提交转换任务（一次提交多个文件），返回 (batch_id, upload_urls, error_msg)

        Args:
            files: [(pdf_path, data_id), ...]，单次不超过 MINERU_BATCH_SIZE(50) 个

        Returns:
            (batch_id, [upload_url, ...], "") 成功；失败时 (None, [], error)
        """
        try:
            params = {
                "is_ocr": "true",
                "enable_formula": "false",
                "enable_table": "true",
                "language": "ch_server",
            }
            payload_files = [{"name": p.name, "data_id": did} for p, did in files]

            async with session.post(
                f"{MINERU_API}/file-urls/batch",
                headers={"Authorization": f"Bearer {self.token}"},
                params=params,
                json={"files": payload_files, "model_version": "vlm"},
                timeout=aiohttp.ClientTimeout(total=30),
                ssl=_SSL_CONTEXT,
            ) as resp:
                if resp.status == 401:
                    return None, [], "MinerU Token 无效或已过期，请在设置页面重新配置"
                if resp.status != 200:
                    body = await resp.text()
                    return None, [], f"MinerU API HTTP {resp.status}: {body[:200]}"

                result = await resp.json()
                if result.get("code") != 0:
                    err_code = result.get("code")
                    err_msg = result.get("msg", "未知错误")
                    return None, [], f"MinerU API 错误 ({err_code}): {err_msg}"

                data = result.get("data", {})
                batch_id = data.get("batch_id")
                upload_urls = data.get("file_urls", [])

                if not batch_id or len(upload_urls) != len(files):
                    logger.error(f"[MinerU] 批量返回不完整: batch_id={'有' if batch_id else '无'}, urls={len(upload_urls)}/{len(files)}")
                    return None, [], "MinerU 批量返回数据不完整"

                logger.info(f"[MinerU] 批量提交成功: {len(files)} 个文件, batch_id={batch_id}")
                return batch_id, upload_urls, ""

        except asyncio.TimeoutError:
            return None, [], "MinerU 批量提交超时"
        except aiohttp.ClientError as e:
            return None, [], f"MinerU 网络错误: {str(e)[:100]}"
        except Exception as e:
            logger.error(f"[MinerU] 批量提交异常: {e}")
            return None, [], f"MinerU 批量提交异常: {str(e)[:100]}"

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
        progress_cb: Callable[[str, str], None] | None = None,
    ) -> dict[str, Any] | None:
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
                        logger.error("[MinerU] 轮询认证失败 (401): Token 无效或已过期")
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

    async def _poll_batch_results(
        self,
        session: aiohttp.ClientSession,
        batch_id: str,
        file_count: int,
        timeout: int,
        progress_cb: Callable[[str, str], None] | None = None,
    ) -> list[dict[str, Any]] | None:
        """轮询批量任务结果，返回所有文件的 extract_result 列表。

        单次请求拿全部文件状态（替代每文件单独轮询）。
        退避轮询：2s 起步，逐步拉长到 10s 上限（短任务快感知，长任务省请求）。
        全部文件进入终态（done/failed）后返回。
        """
        waited = 0
        poll_interval = 2  # 退避起点
        last_progress_msg = ""

        while waited < timeout:
            try:
                async with session.get(
                    f"{MINERU_API}/extract-results/batch/{batch_id}",
                    headers={"Authorization": f"Bearer {self.token}"},
                    timeout=aiohttp.ClientTimeout(total=30),
                    ssl=_SSL_CONTEXT,
                ) as resp:
                    if resp.status == 401:
                        logger.error("[MinerU] 批量轮询认证失败 (401): Token 无效或已过期")
                        return None

                    result = await resp.json()
                    results = result.get("data", {}).get("extract_result", [])

                    if not results:
                        await asyncio.sleep(poll_interval)
                        waited += poll_interval
                        poll_interval = min(poll_interval + 1, 10)  # 退避
                        continue

                    # 统计进度：done/failed/running，利用 extract_progress 显示页级进度
                    done = sum(1 for r in results if r.get("state") in ("done", "failed"))
                    running = [r for r in results if r.get("state") == "running"]
                    if progress_cb and running:
                        # 取第一个 running 文件的页进度
                        prog = running[0].get("extract_progress") or {}
                        extracted = prog.get("extracted_pages", 0)
                        total_p = prog.get("total_pages", 0)
                        msg = f"批量转换 {done}/{file_count} 完成，正在解析（{extracted}/{total_p} 页）..."
                        if msg != last_progress_msg:
                            progress_cb("processing", msg)
                            last_progress_msg = msg

                    # 全部进入终态 → 返回
                    if done >= file_count:
                        logger.info(f"[MinerU] 批量转换完成: batch_id={batch_id}, {done}/{file_count}")
                        return results

                    await asyncio.sleep(poll_interval)
                    waited += poll_interval
                    poll_interval = min(poll_interval + 1, 10)  # 退避到 10s 上限

            except Exception as e:
                logger.error(f"[MinerU] 批量轮询异常: {e}")
                await asyncio.sleep(poll_interval)
                waited += poll_interval

        logger.error(f"[MinerU] 批量转换超时: batch_id={batch_id}")
        return None

    async def _download_and_parse(
        self,
        result_data: dict[str, Any],
        output_dir: Path,
        stem: str,
    ) -> tuple[str | None, Path | None]:
        """下载并解析转换结果"""
        zip_url = result_data.get("full_zip_url", "")
        if not zip_url:
            logger.error("[MinerU] 未找到 full_zip_url")
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

            # 安全解压：防路径穿越与 Zip Bomb
            from utils.zip_safe import safe_extract_zip
            safe_extract_zip(zip_path, temp_dir)
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
    progress_cb: Callable[[str, str], None] | None = None,
) -> ConvertResult:
    """异步转换单个 PDF（便捷函数）"""
    converter = AsyncMinerUConverter()
    return await converter.convert_single(pdf_path, output_dir, progress_cb=progress_cb)


async def convert_batch_async(
    pdf_paths: list[Path],
    output_dir: Path,
    max_concurrent: int = 3,
    progress_cb: Callable[[BatchProgress], None] | None = None,
) -> list[ConvertResult]:
    """异步批量转换（便捷函数）"""
    converter = AsyncMinerUConverter()
    return await converter.convert_batch(
        pdf_paths, output_dir, max_concurrent, progress_cb=progress_cb
    )


def convert_batch_sync(
    pdf_paths: list[Path],
    output_dir: Path,
    max_concurrent: int = 3,
    progress_cb: Callable[[BatchProgress], None] | None = None,
) -> list[ConvertResult]:
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
        print("\\n转换成功！")
        print(f"  文本长度: {len(result.text)} 字符")
        print(f"  图片目录: {result.images_dir}")
    else:
        print(f"\\n转换失败: {result.error}")
