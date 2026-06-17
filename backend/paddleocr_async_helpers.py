"""
PaddleOCR 异步转换辅助模块

包含：
- SSL 上下文配置
- API 常量
- 数据类（ConvertResult, BatchProgress）
- 配额管理
- Token 获取
- PDF 分段
- 后处理（LaTeX 清理、OCR 错误修复）
"""
import json
import logging
import os
import re
import ssl
import sys
from dataclasses import dataclass, field
from datetime import date
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


# ═══════════════════════════════════════════════════════════
# Token 获取
# ═══════════════════════════════════════════════════════════
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


# ═══════════════════════════════════════════════════════════
# PDF 分段
# ═══════════════════════════════════════════════════════════
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
# 后处理函数
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
