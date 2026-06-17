"""
MinerU 异步转换辅助模块

包含：
- SSL 上下文配置
- API 常量和数据类
- Token / 模式获取
- PDF 分段
- OCR 纠错规则
"""
import logging
import os
import re
import ssl
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# SSL 配置
# ═══════════════════════════════════════════════════════════
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
# - VLM 模式处理时间较长，GPU 资源有限时串行处理更稳定
# - 遇到 429 或 -60009 错误时自动退避重试
DEFAULT_MAX_CONCURRENT = 1  # 本地 GPU 串行处理，避免资源争抢


# ═══════════════════════════════════════════════════════════
# 模式 / URL 获取
# ═══════════════════════════════════════════════════════════
def _get_mineru_mode() -> str:
    """获取 MinerU 模式：cloud 或 local"""
    try:
        from config_manager import get_config_value
        mode = get_config_value("mineru_mode")
        if mode in ("cloud", "local"):
            return mode
    except Exception:
        pass
    return "cloud"


def _get_mineru_local_url() -> str:
    """获取本地 MinerU 服务器地址"""
    try:
        from config_manager import get_config_value
        url = get_config_value("mineru_local_url", "").strip()
        # 移除末尾斜杠
        if url.endswith("/"):
            url = url[:-1]
        return url
    except Exception:
        return ""


# ═══════════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════════
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


# ═══════════════════════════════════════════════════════════
# Token 获取
# ═══════════════════════════════════════════════════════════
def _get_mineru_token() -> str:
    """获取 MinerU token"""
    # 环境变量优先
    token = os.environ.get("MINERU_TOKEN", "")
    if token:
        logger.debug("[MinerU] Token 来源: 环境变量 MINERU_TOKEN")
        return token

    # 应用配置
    try:
        from config_manager import get_config_value
        token = get_config_value("mineru_token")
        if token:
            logger.debug("[MinerU] Token 来源: 配置文件 criminal-llm-config.json")
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
        logger.warning("[MinerU] 未找到 Token（已检查环境变量、配置文件、.env）")
    return token


# ═══════════════════════════════════════════════════════════
# PDF 分段
# ═══════════════════════════════════════════════════════════
def _split_pdf_pages(pdf_path: Path, chunk_size: int = MINERU_MAX_PAGES) -> List[Tuple[Path, int, int]]:
    """将大 PDF 按页数/文件大小分段

    Returns:
        List of (chunk_path, start_page, end_page) tuples
        Empty list if no splitting needed

    Raises:
        RuntimeError: 如果 PDF 无法打开或读取
    """
    try:
        import fitz
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
