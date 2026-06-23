#!/usr/bin/env python3
"""
PDF → Markdown 转换模块

支持两种引擎：
1. MinerU API（默认）- 高质量异步转换
2. PaddleOCR-VL API - 同步逐页转换

用法:
    from pdf_to_md import get_evidence_text

    # 根据配置自动选择引擎
    text, images_dir = get_evidence_text("/path/to/evidence.pdf")
"""

import json
import os
import shutil

# 打包后 certifi 证书路径可能失效，macOS 用系统证书
import sys
import time
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
import logging

logger = logging.getLogger(__name__)

if sys.platform == "darwin" and getattr(sys, "frozen", False):
    _SSL_VERIFY = "/etc/ssl/cert.pem"
else:
    _SSL_VERIFY = True

# ═══════════════════════════════════════════════════════════
# MinerU 配置
# ═══════════════════════════════════════════════════════════
MINERU_API = "https://mineru.net/api/v4"
MINERU_MAX_PAGES = 180  # MinerU API 限制 200 页，留 20 页缓冲


# MinerU API 限制
MINERU_MAX_FILE_SIZE = 200 * 1024 * 1024  # 200MB


def _split_pdf_pages(pdf_path: Path, chunk_size: int = MINERU_MAX_PAGES) -> list[Path]:
    """将大 PDF 按页数/文件大小分段，返回各段临时文件路径列表

    同时考虑两个限制：
    1. 页数不超过 chunk_size（默认 180 页）
    2. 每段文件大小不超过 200MB（MinerU API 限制）
    """
    import fitz
    doc = fitz.open(str(pdf_path))
    total = len(doc)
    file_size = pdf_path.stat().st_size
    doc.close()

    if total <= chunk_size and file_size <= MINERU_MAX_FILE_SIZE:
        return []  # 不需要拆分

    # 计算满足文件大小限制的每段最大页数
    if total > 0:
        avg_page_size = file_size / total
        if avg_page_size > 0:
            # 留 20% 缓冲（0.80），避免 chunk 接近 200MB 上限时因开销超限
            max_pages_by_size = int(MINERU_MAX_FILE_SIZE * 0.80 / avg_page_size)
            chunk_size = min(chunk_size, max_pages_by_size)
            chunk_size = max(chunk_size, 10)  # 至少 10 页一段

    chunks = []
    for start in range(0, total, chunk_size):
        end = min(start + chunk_size, total)
        import fitz
        new_doc = fitz.open()
        new_doc.insert_pdf(fitz.open(str(pdf_path)), from_page=start, to_page=end - 1)
        tmp_path = Path(pdf_path.parent) / f"_chunk_{start+1}-{end}_{pdf_path.name}"
        new_doc.save(str(tmp_path))
        new_doc.close()

        # 检查实际文件大小，fitz.save 可能与预期不同
        actual_size = tmp_path.stat().st_size
        if actual_size > MINERU_MAX_FILE_SIZE * 0.95:
            logger.info(f"[分段转换] chunk {start+1}-{end} 实际 {actual_size//1024//1024}MB 超限，减半重新拆分")
            tmp_path.unlink(missing_ok=True)
            # 用减半后的 chunk_size 重新拆分这段范围
            sub_size = max(chunk_size // 2, 10)
            for sub_start in range(start, end, sub_size):
                sub_end = min(sub_start + sub_size, end)
                sub_doc = fitz.open()
                sub_doc.insert_pdf(fitz.open(str(pdf_path)), from_page=sub_start, to_page=sub_end - 1)
                sub_path = Path(pdf_path.parent) / f"_chunk_{sub_start+1}-{sub_end}_{pdf_path.name}"
                sub_doc.save(str(sub_path))
                sub_doc.close()
                chunks.append(sub_path)
        else:
            chunks.append(tmp_path)

    return chunks


def _merge_mineru_texts(chunks_data: list[tuple[str, Optional[Path]]]) -> tuple[str, Optional[Path]]:
    """合并多个 MinerU 转换结果为一个"""
    texts = []
    images_dirs = []
    for text, images_dir in chunks_data:
        if text:
            texts.append(text)
            if images_dir:
                images_dirs.append(images_dir)

    merged_text = "\n\n---\n\n".join(texts) if texts else ("", None)
    # 如果有多个图片目录，返回第一个（后续会统一移动）
    merged_images = images_dirs[0] if images_dirs else None
    return merged_text, merged_images

def _get_mineru_token() -> str:
    """获取 MinerU token

    优先级：
    1. 环境变量 MINERU_TOKEN
    2. 应用配置 (DATA_DIR/criminal-llm-config.json)
    3. DATA_DIR/.env 文件
    """
    # 环境变量优先
    token = os.environ.get("MINERU_TOKEN", "")
    if token:
        return token

    # 应用配置
    try:
        from config_manager import get_config_value
        token = get_config_value("mineru_token")
        if token:
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
                break

    return token


# ═══════════════════════════════════════════════════════════
# 1. 已有 MD 缓存（最快）
# ═══════════════════════════════════════════════════════════
def _read_cached_md(pdf_path: Path, output_dir: Optional[Path] = None) -> Optional[str]:
    """
    读取已有的 MD 缓存文件

    优先检查 output_dir 中的 MD，回退到 PDF 同目录
    """
    # 1. 先检查指定的输出目录
    if output_dir:
        md_path = output_dir / f"{pdf_path.stem}.md"
        if md_path.exists() and md_path.stat().st_mtime >= pdf_path.stat().st_mtime:
            try:
                text = md_path.read_text(encoding="utf-8")
                if text.startswith("[") and "转换失败" in text:
                    return None
                return text
            except Exception:
                pass

    # 2. 回退到 PDF 同目录
    md_path = pdf_path.with_suffix(".md")
    if md_path.exists() and md_path.stat().st_mtime >= pdf_path.stat().st_mtime:
        try:
            text = md_path.read_text(encoding="utf-8")
            if text.startswith("[") and "转换失败" in text:
                return None
            return text
        except Exception:
            pass
    return None


def _save_md(pdf_path: Path, text: str) -> Optional[str]:
    """保存 MD 缓存文件（在 PDF 同目录）"""
    md_path = pdf_path.with_suffix(".md")
    try:
        md_path.write_text(text, encoding="utf-8")
        return str(md_path)
    except Exception:
        return None


def _save_to_dir(pdf_path: Path, text: str, output_dir: Path) -> Optional[str]:
    """保存 MD 文件到指定目录"""
    md_path = output_dir / f"{pdf_path.stem}.md"
    try:
        md_path.write_text(text, encoding="utf-8")
        return str(md_path)
    except Exception:
        return None
# 后处理函数从 pdf_to_md_postprocess 导入（向后兼容 re-export）
from pdf_to_md_postprocess import (  # noqa: F401
    _OCR_FIXES,
    _SIGNATURE_HTML,
    _SIGNATURE_PATTERNS,
    _MAX_IMAGE_DIM,
    _fix_ocr_errors,
    _strip_hallucinated_tables,
    _llm_fix_ocr_errors,
    _correct_chunk,
    llm_fix_ocr_sync,
    _protect_signatures_as_images,
    _detect_handwritten_pages,
    _compress_images,
    _fold_consecutive_images,
)


# 2. MinerU API 转换（最高质量）
def _mineru_convert(
    pdf_path: Path,
    output_dir: Path,
    timeout: int = 3600,
    progress_cb: Optional[callable] = None,
) -> Optional[tuple[str, Optional[Path]]]:
    """使用 MinerU API 转换 PDF → MD，自动处理超大文件

    MinerU 使用 auto 模式，内部智能判断每页是否需要 OCR。
    零额外成本，无漏页风险。
    """
    import re
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        total_pages = len(doc)
        doc.close()
    except Exception:
        total_pages = 0

    file_size = pdf_path.stat().st_size if pdf_path.exists() else 0

    # 页数或文件大小任一超限，都分段处理
    need_split = total_pages > MINERU_MAX_PAGES or file_size > MINERU_MAX_FILE_SIZE

    if need_split:
        chunks = _split_pdf_pages(pdf_path, MINERU_MAX_PAGES)
        if chunks:
            chunk_results = []
            chunk_index = 0
            all_images_dirs = []

            # 使用文件名 stem 作为前缀，确保并发转换时互不干扰
            temp_prefix = f"_temp_{pdf_path.stem}"
            logger.info(f"[分段转换] {pdf_path.name}: 共 {len(chunks)} 个 chunk")
            for chunk_path in chunks:
                chunk_output = output_dir / f"{temp_prefix}_{chunk_index}"
                chunk_output.mkdir(parents=True, exist_ok=True)
                logger.info(f"[分段转换] 开始处理 chunk {chunk_index}: {chunk_path.name}")

                # 失败重试一次，避免偶尔的网络/API错误丢失整段
                result = _mineru_convert_single(chunk_path, chunk_output, timeout, progress_cb)
                if not result or not result[0]:
                    logger.error(f"[分段转换] chunk {chunk_index} 首次失败，15s 后重试...")
                    time.sleep(15)
                    result = _mineru_convert_single(chunk_path, chunk_output, timeout, progress_cb)

                chunk_path.unlink(missing_ok=True)  # 清理临时分段文件
                if result and result[0]:
                    logger.info(f"[分段转换] chunk {chunk_index} 成功: {len(result[0])} 字符")
                    chunk_results.append(result[0])
                    if result[1]:
                        all_images_dirs.append(result[1])
                else:
                    logger.error(f"[分段转换] chunk {chunk_index} 重试后仍失败，跳过（已丢失对应页）")
                chunk_index += 1

            logger.info(f"[分段转换] 完成 {pdf_path.name}: {len(chunk_results)}/{len(chunks)} 个 chunk 成功")

            if not chunk_results:
                return None, None

            # 合并文本，并修正图片路径（chunk 图片路径是 _chunk_X-Y_卷名_images，需改为 卷名_images）
            merged_text = "\n\n---\n\n".join(chunk_results)
            merged_text = re.sub(
                r'\./(_chunk_[^/]+?)_([^/]+_images)/',
                r'\2/',
                merged_text
            )
            merged_text = _protect_signatures_as_images(merged_text)
            merged_text = _fix_ocr_errors(merged_text)
            merged_text, _ = _fold_consecutive_images(merged_text)

            # 保存图片到统一目录
            merged_images_dir = output_dir / f"{pdf_path.stem}_images"
            merged_images_dir.mkdir(parents=True, exist_ok=True)
            for src_dir in all_images_dirs:
                src_path = Path(src_dir)
                if src_path.exists():
                    for img in src_path.iterdir():
                        if img.is_file():
                            shutil.copy2(str(img), str(merged_images_dir / img.name))

            # 保存合并后的 MD 文件
            target_md = output_dir / f"{pdf_path.stem}.md"
            target_md.write_text(merged_text, encoding="utf-8")

            # 压缩图片
            _compress_images(merged_images_dir)

            # 仅清理属于当前文件的临时目录（不会误删其他正在转换的文件）
            for f in output_dir.iterdir():
                if f.is_dir() and f.name.startswith(f"{temp_prefix}_"):
                    shutil.rmtree(f, ignore_errors=True)

            return merged_text, merged_images_dir

    # 页数正常，直接调用
    return _mineru_convert_single(pdf_path, output_dir, timeout, progress_cb)


def _mineru_convert_single(
    pdf_path: Path,
    output_dir: Path,
    timeout: int = 3600,
    progress_cb: Optional[callable] = None,
) -> Optional[tuple[str, Optional[Path]]]:
    """使用 MinerU API 转换单个 PDF → MD（调用方已确保文件大小在限制内）

    失败时自动重试 2 次（共 3 次尝试），指数退避间隔 15s → 30s。
    直接使用 full.md（MinerU 最佳输出），并应用 OCR 纠错规则。
    图片目录会被保留并移动到输出目录。

    Args:
        progress_cb: 可选的进度回调，签名 (stage: str, detail: str)
    """
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        result = _do_mineru_convert(pdf_path, output_dir, timeout, progress_cb)
        if result and result[0]:
            return result
        if attempt < max_retries:
            delay = 15 * attempt  # 15s, 30s
            logger.error(f"[MinerU] 第 {attempt} 次尝试失败，{delay}s 后重试 ({attempt + 1}/{max_retries}): {pdf_path.name}")
            if progress_cb:
                progress_cb("retrying", f"第 {attempt} 次失败，{delay}s 后自动重试...")
            time.sleep(delay)
    logger.error(f"[MinerU] 已重试 {max_retries} 次，仍失败: {pdf_path.name}")
    return None, None


def _do_mineru_convert(
    pdf_path: Path,
    output_dir: Path,
    timeout: int = 3600,
    progress_cb: Optional[callable] = None,
) -> Optional[tuple[str, Optional[Path]]]:
    """执行一次 MinerU 转换调用（单次，不含重试）"""
    token = _get_mineru_token()
    if not token:
        logger.info(f"[MinerU] Token 未配置，跳过 {pdf_path.name}")
        return None, None

    stem = pdf_path.stem

    try:
        # 1. 提交转换任务
        if progress_cb:
            progress_cb("submitting", "正在提交转换任务...")
        # 文档参数放 query string（与 MinerU API 文档一致），文件信息放 body
        query_params = {
            "is_ocr": "true",
            "enable_formula": "false",
            "enable_table": "true",
            "language": "ch_server",
        }
        resp = requests.post(
            f"{MINERU_API}/file-urls/batch",
            headers={"Authorization": f"Bearer {token}"},
            params=query_params,
            json={
                "files": [{"name": pdf_path.name, "data_id": stem}],
                "model_version": "vlm",           # VLM 视觉语言模型，扫描件/手写识别精度远超 pipeline
            },
            timeout=30,
            verify=_SSL_VERIFY,
        )
        result = resp.json()
        if result.get("code") != 0:
            err_msg = result.get("msg", "未知错误")
            err_code = result.get("code")
            # 频率限制/配额限制：等待后重试
            if err_code in (429, 10020, 10021) or "limit" in err_msg.lower() or "频率" in err_msg:
                logger.info(f"[MinerU] API 频率限制，60s 后重试: {pdf_path.name}")
                time.sleep(60)
                return None, None  # 返回让上层重试
            logger.error(f"[MinerU] 获取上传链接失败: {pdf_path.name}, code={err_code}, msg={err_msg}")
            return None, None

        batch_id = result["data"]["batch_id"]
        upload_url = result["data"]["file_urls"][0]
        logger.info(f"[MinerU] 开始上传 {pdf_path.name} (batch_id={batch_id})")

        # 2. 发送文件到 OSS 预签名 URL
        # 注意：OSS 签名只覆盖 PUT/Host/Date 这类基础头，requests 自动加的
        # User-Agent/Accept-Encoding/Content-Length 等不在签名范围内，不会触发 SignatureDoesNotMatch。
        # 之前用 urllib 手动 set 空 User-Agent 反而导致 OSS 计入签名校验失败。
        if progress_cb:
            progress_cb("uploading", "正在发送文件...")
        file_size = pdf_path.stat().st_size
        logger.info(f"[MinerU] 上传文件大小: {file_size // 1024 // 1024}MB")
        try:
            with open(pdf_path, "rb") as f:
                upload_timeout = max(300, file_size // (1024 * 1024) * 20)
                r = requests.put(upload_url, data=f, timeout=upload_timeout, verify=_SSL_VERIFY)
        except Exception as upload_err:
            logger.error(f"[MinerU] 上传异常: {pdf_path.name}, {type(upload_err).__name__}: {upload_err}")
            return None, None
        if r.status_code not in (200, 201, 203, 204):
            body = r.text[:300] if r.text else ""
            logger.error(f"[MinerU] 上传失败: {pdf_path.name}, HTTP {r.status_code}, 响应: {body}")
            return None, None
        logger.info(f"[MinerU] 上传成功: {pdf_path.name}")

        # 3. 等待云端处理
        if progress_cb:
            progress_cb("processing", "正在识别文本内容...")
        waited = 0
        poll_interval = 5
        while waited < timeout:
            try:
                r = requests.get(
                    f"{MINERU_API}/extract-results/batch/{batch_id}",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=30,
                    verify=_SSL_VERIFY,
                )
                resp_json = r.json()
                # 调试日志：打印完整响应（仅首次）
                if waited == 0:
                    logger.info(f"[MinerU] 轮询响应: {json.dumps(resp_json, ensure_ascii=False)[:500]}")
                data = resp_json.get("data", {})
                results = data.get("extract_result", [])
                if not results:
                    time.sleep(poll_interval); waited += poll_interval
                    if progress_cb:
                        progress_cb("processing", f"正在识别文本内容...（已等待 {waited} 秒）")
                    continue

                state = results[0].get("state")
                if state == "done":
                    logger.info(f"[MinerU] 转换完成 {pdf_path.name}，下载结果中...")
                    if progress_cb:
                        progress_cb("processing", "正在识别文本内容...")

                    # 4. 获取结果
                    if progress_cb:
                        progress_cb("downloading", "正在生成结构化文本...")
                    temp_dir = output_dir / f"_tmp_mineru_{stem}"
                    temp_dir.mkdir(parents=True, exist_ok=True)
                    zip_path = temp_dir / f"{stem}.zip"
                    zip_url = results[0].get("full_zip_url", "")
                    if not zip_url:
                        logger.info(f"[MinerU] 未找到 full_zip_url，响应: {json.dumps(results[0], ensure_ascii=False)[:300]}")
                        shutil.rmtree(temp_dir, ignore_errors=True)
                        return None, None
                    zip_resp = requests.get(zip_url, timeout=120, verify=_SSL_VERIFY)
                    zip_path.write_bytes(zip_resp.content)
                    # 安全解压：防路径穿越与 Zip Bomb
                    from utils.zip_safe import safe_extract_zip
                    safe_extract_zip(zip_path, temp_dir)
                    zip_path.unlink()

                    # 5. 解析输出
                    if progress_cb:
                        progress_cb("parsing", "正在解析输出...")
                    full_md = temp_dir / "full.md"
                    if full_md.exists():
                        text = full_md.read_text(encoding="utf-8")
                    else:
                        text = ""

                    # 图片目录
                    src_images_dir = temp_dir / "images"
                    target_images_dir = None
                    if src_images_dir.exists() and src_images_dir.is_dir():
                        target_images_dir = output_dir / f"{stem}_images"
                        if target_images_dir.exists():
                            shutil.rmtree(target_images_dir)
                        src_images_dir.rename(target_images_dir)

                    # 保留 MinerU 结构化 JSON（layout/content_list/middle）
                    # 命名规则：<stem>_<原名>.json，与 md 同前缀便于配对
                    for json_name in ("layout.json", "content_list.json", "middle.json"):
                        src_json = temp_dir / json_name
                        if src_json.exists():
                            target_json = output_dir / f"{stem}_{json_name}"
                            if target_json.exists():
                                target_json.unlink()
                            shutil.copy2(src_json, target_json)

                    # 清理临时目录
                    shutil.rmtree(temp_dir, ignore_errors=True)

                    if text and len(text) > 100:
                        # 重写图片路径为相对路径
                        if target_images_dir:
                            text = text.replace("images/", f"./{stem}_images/")
                            text = text.replace('src="images/', f'src="./{stem}_images/')
                        # 保护签名区：替换为图片占位符，避免 OCR 乱识别手写体
                        text = _protect_signatures_as_images(text)
                        # 应用 OCR 纠错
                        text = _fix_ocr_errors(text)
                        # VLM 幻觉检测：移除重复行名的伪造表格
                        text = _strip_hallucinated_tables(text)
                        # 压缩大图
                        if target_images_dir and target_images_dir.exists():
                            _compress_images(target_images_dir)
                        # 折叠连续图片块
                        text, _ = _fold_consecutive_images(text)
                        # 写入目标文件
                        target_md = output_dir / f"{stem}.md"
                        target_md.write_text(text, encoding="utf-8")
                        return text, target_images_dir
                    logger.info(f"[MinerU] 结果文件过小或缺失: {pdf_path.name}")
                    return None, None
                elif state == "failed":
                    err_info = results[0].get("err_msg") or results[0].get("task_status_msg") or "未知错误"
                    logger.error(f"[MinerU] 云端转换失败: {pdf_path.name}, {err_info}")
                    return None, None

                time.sleep(poll_interval); waited += poll_interval

            except Exception as inner_e:
                logger.error(f"[MinerU] 轮询异常: {inner_e}")
                time.sleep(poll_interval); waited += poll_interval
                continue

        logger.info(f"[MinerU] 转换超时: {pdf_path.name}")
        return None, None

    except Exception as e:
        logger.error(f"[MinerU] 异常: {pdf_path.name}, {e}")
        return None, None


# ═══════════════════════════════════════════════════════════
# 公共接口
# ═══════════════════════════════════════════════════════════
def get_evidence_text(
    pdf_path: str,
    prefer_md: bool = True,
    output_dir: Optional[str] = None,
    progress_cb: Optional[callable] = None,
) -> Tuple[str, Optional[str]]:
    """
    获取证据文本

    优先级：
    1. 已有 .md 缓存文件 → 秒读，零延迟
    2. 根据配置选择转换引擎（MinerU 或 PaddleOCR）

    Args:
        pdf_path: PDF 文件路径
        prefer_md: 是否优先使用 MD 缓存（默认 True）
        output_dir: MD 保存目录（默认与 PDF 同目录）
        progress_cb: 可选的进度回调 (stage: str, detail: str)

    Returns:
        (Markdown 格式的文字, 图片目录路径或 None)
    """
    pdf = Path(pdf_path)
    if output_dir:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
    else:
        out = pdf.parent

    # 1. 已有 MD 缓存（最快）
    if prefer_md:
        cached = _read_cached_md(pdf, out)
        if cached is not None:
            images_dir = out / f"{pdf.stem}_images"
            needs_update = False
            if '![签名图片]' not in cached:
                cached = _protect_signatures_as_images(cached)
                needs_update = True
            if '<details>' not in cached:
                cached, _ = _fold_consecutive_images(cached)
                needs_update = True
            if images_dir.exists():
                _compress_images(images_dir)
            if needs_update:
                (out / f"{pdf.stem}.md").write_text(cached, encoding="utf-8")
            if images_dir.exists() and images_dir.is_dir():
                return cached, str(images_dir)
            return cached, None

    # 2. 根据配置选择引擎
    pdf_engine = "paddleocr"  # 默认
    try:
        from config_manager import get_config_value
        engine_val = get_config_value("pdf_engine")
        if engine_val in ("mineru", "paddleocr"):
            pdf_engine = engine_val
    except ImportError:
        pass

    if pdf_engine == "paddleocr":
        text, images_dir = _convert_with_paddleocr(pdf, out, progress_cb=progress_cb)
    else:
        text, images_dir = _mineru_convert(pdf, out, progress_cb=progress_cb)

    if text is not None:
        _save_to_dir(pdf, text, out)
        if images_dir and images_dir.exists():
            target_images = out / f"{pdf.stem}_images"
            if target_images != images_dir:
                if target_images.exists():
                    shutil.rmtree(target_images)
                images_dir.rename(target_images)
            return text, str(target_images)
        return text, None

    # 3. 转换失败
    logger.error(f"[转换失败] 所有引擎均失败: {pdf.name}")
    return None, None


def _convert_with_paddleocr(
    pdf: Path,
    out: Path,
    progress_cb: Optional[callable] = None,
) -> Tuple[Optional[str], Optional[Path]]:
    """使用 PaddleOCR 引擎转换 PDF

    配额用尽时自动回退到 MinerU。
    """
    # 先检查配额状态
    try:
        from paddleocr_remote import get_daily_quota_status, paddleocr_convert
        quota = get_daily_quota_status()
        if quota["exceeded"]:
            logger.info(f"[PaddleOCR] 每日配额已用完（{quota['used_pages']}/{quota['total_limit']} 页），回退到 MinerU")
            return _mineru_convert(pdf, out, progress_cb=progress_cb)
    except ImportError:
        pass

    try:
        from paddleocr_remote import paddleocr_convert
        result = paddleocr_convert(pdf, out, progress_cb=progress_cb)
        # 如果 PaddleOCR 转换成功，返回结果
        if result and result[0]:
            return result
        # 转换失败（非配额原因），回退到 MinerU
        logger.info("[PaddleOCR] 转换返回空结果，回退到 MinerU")
        return _mineru_convert(pdf, out, progress_cb=progress_cb)
    except ImportError:
        logger.info("[PaddleOCR] 模块未找到，回退到 MinerU")
        return _mineru_convert(pdf, out, progress_cb=progress_cb)
    except Exception as e:
        logger.error(f"[PaddleOCR] 转换异常: {e}，回退到 MinerU")
        try:
            return _mineru_convert(pdf, out, progress_cb=progress_cb)
        except Exception:
            return None, None


def convert_directory(
    dir_path: str,
    output_dir: Optional[str] = None,
    prefer_mineru: bool = True,
    recursive: bool = True,
) -> List[Dict[str, str]]:
    """
    批量转换目录下所有 PDF → MD
    
    Returns:
        [{"pdf": "...", "md": "...", "text": "...", "status": "ok/fail", "source": "cache|mineru|pdfplumber|pymupdf"}]
    """
    dir_path = Path(dir_path)
    if not dir_path.exists():
        return []

    if output_dir is None:
        output_dir = dir_path
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    glob_pattern = "**/*.pdf" if recursive else "*.pdf"
    pdf_files = sorted(dir_path.glob(glob_pattern))

    results = []
    for pdf in pdf_files:
        try:
            text, images_dir = get_evidence_text(str(pdf), prefer_md=True, output_dir=str(output_dir))
            if text is None:
                results.append({
                    "pdf": str(pdf),
                    "md": "",
                    "text": "",
                    "status": "fail",
                    "source": "",
                    "has_images": False,
                })
                continue

            md_path = pdf.with_suffix(".md")

            results.append({
                "pdf": str(pdf),
                "md": str(md_path) if md_path.exists() else "",
                "text": text[:200] + "..." if len(text) > 200 else text,
                "status": "ok",
                "source": "cache" if _read_cached_md(pdf, output_dir) else "mineru",
                "has_images": images_dir is not None and Path(images_dir).exists(),
            })
        except Exception as e:
            results.append({
                "pdf": str(pdf),
                "md": "",
                "text": "",
                "status": "fail",
                "error": str(e)
            })

    return results
