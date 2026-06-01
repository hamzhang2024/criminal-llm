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
from pathlib import Path
from datetime import date
from typing import Optional, List, Dict, Any, Tuple, Callable
from dataclasses import dataclass, field

import aiohttp

# 打包后 certifi 证书路径可能失效，macOS 用系统证书
if sys.platform == "darwin" and getattr(sys, "frozen", False):
    _SSL_VERIFY = "/etc/ssl/cert.pem"
else:
    _SSL_VERIFY = True

# ═══════════════════════════════════════════════════════════
# 配置常量
# ═══════════════════════════════════════════════════════════
PADDLEOCR_API_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
PADDLEOCR_MODEL = "PaddleOCR-VL-1.6"
PADDLEOCR_DAILY_PAGE_LIMIT = 20000  # 每日转换页数配额
DEFAULT_TIMEOUT = 3600  # 1 小时
POLL_INTERVAL = 10  # 轮询间隔 10 秒
DEFAULT_MAX_CONCURRENT = 10  # 默认并发数

# 刑事案卷专用参数
PADDLEOCR_OPTIONAL_PAYLOAD = {
    "useDocOrientationClassify": True,
    "useDocUnwarping": True,
    "useLayoutDetection": True,
    "useChartRecognition": False,
    "layoutThreshold": 0.5,
    "repetitionPenalty": 1.5,
    "temperature": 0.01,
    "topP": 0.5,
    "minPixels": 512 * 512,
    "maxPixels": 1280 * 1280,
    "restructurePages": False,
    "mergeTables": True,
    "relevelTitles": True,
    "prettifyMarkdown": True,
    "showFormulaNumber": False,
    "visualize": False,
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
    """获取今日配额使用状态"""
    data = _load_quota()
    used = data.get("used_pages", 0)
    return {
        "date": data.get("date", str(date.today())),
        "used_pages": used,
        "total_limit": PADDLEOCR_DAILY_PAGE_LIMIT,
        "remaining_pages": max(0, PADDLEOCR_DAILY_PAGE_LIMIT - used),
        "exceeded": used >= PADDLEOCR_DAILY_PAGE_LIMIT,
    }


def _check_and_record_quota(pages_needed: int) -> bool:
    """检查配额是否充足，如果充足则记录消耗"""
    data = _load_quota()
    used = data.get("used_pages", 0)

    if used >= PADDLEOCR_DAILY_PAGE_LIMIT:
        return False

    new_used = min(used + pages_needed, PADDLEOCR_DAILY_PAGE_LIMIT)
    data["used_pages"] = new_used
    _save_quota(data)
    return True


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
            _protect_signatures_as_images,
            _fix_ocr_errors,
            _fold_consecutive_images,
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
        """转换单个 PDF 文件"""
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

        # 检查配额
        if not _check_and_record_quota(total_pages):
            return ConvertResult(
                file_name=pdf_path.name,
                success=False,
                error=f"每日 {PADDLEOCR_DAILY_PAGE_LIMIT} 页配额已用完"
            )

        try:
            async with aiohttp.ClientSession() as session:
                # 1. 提交任务
                if progress_cb:
                    progress_cb("submitting", f"正在提交 {pdf_path.name}（{total_pages} 页）...")

                job_id = await self._submit_job(session, pdf_path)
                if not job_id:
                    return ConvertResult(file_name=pdf_path.name, success=False, error="提交任务失败")

                print(f"[PaddleOCR] 任务已提交: {pdf_path.name}, job_id={job_id}")

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

                print(f"[PaddleOCR] 转换完成 {pdf_path.name}: {len(text)} 字符, {total_pages} 页")

                return ConvertResult(
                    file_name=pdf_path.name,
                    success=True,
                    text=text,
                    images_dir=images_dir,
                    pages=total_pages
                )

        except Exception as e:
            print(f"[PaddleOCR] 异常: {pdf_path.name}, {e}")
            return ConvertResult(file_name=pdf_path.name, success=False, error=str(e)[:200])

    async def convert_batch(
        self,
        pdf_paths: List[Path],
        output_dir: Path,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        timeout: int = DEFAULT_TIMEOUT,
        progress_cb: Optional[Callable[[BatchProgress], None]] = None,
    ) -> List[ConvertResult]:
        """批量转换多个 PDF 文件（并发处理）

        Args:
            pdf_paths: PDF 文件路径列表
            output_dir: 输出目录
            max_concurrent: 最大并发数（默认 10）
            timeout: 单文件超时时间
            progress_cb: 进度回调

        Returns:
            ConvertResult 列表
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        # 创建信号量控制并发
        semaphore = asyncio.Semaphore(max_concurrent)

        # 进度跟踪
        progress = BatchProgress(total=len(pdf_paths))

        async def _convert_with_semaphore(pdf_path: Path) -> ConvertResult:
            async with semaphore:
                result = await self.convert_single(
                    pdf_path, output_dir, timeout,
                    progress_cb=lambda stage, detail: None
                )

                progress.completed += 1
                if not result.success:
                    progress.failed += 1
                progress.current_files.append(pdf_path.name)

                if progress_cb:
                    progress_cb(progress)

                return result

        # 并发执行所有转换
        tasks = [_convert_with_semaphore(pdf) for pdf in pdf_paths]
        results = await asyncio.gather(*tasks, return_exceptions=True)

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
                timeout=aiohttp.ClientTimeout(total=120),
                ssl=_SSL_VERIFY if isinstance(_SSL_VERIFY, bool) else _SSL_VERIFY,
            ) as resp:
                if resp.status == 429:
                    quota = _load_quota()
                    quota["used_pages"] = PADDLEOCR_DAILY_PAGE_LIMIT
                    _save_quota(quota)
                    print(f"[PaddleOCR] 每日配额已用完（服务端返回 429）")
                    return None

                if resp.status != 200:
                    text = await resp.text()
                    print(f"[PaddleOCR] 提交任务失败: HTTP {resp.status}, {text[:300]}")
                    return None

                result = await resp.json()
                return result["data"]["jobId"]

        except Exception as e:
            print(f"[PaddleOCR] 提交任务异常: {e}")
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
                    ssl=_SSL_VERIFY if isinstance(_SSL_VERIFY, bool) else _SSL_VERIFY,
                ) as resp:
                    if resp.status != 200:
                        await asyncio.sleep(POLL_INTERVAL)
                        waited += POLL_INTERVAL
                        continue

                    job_data = (await resp.json())["data"]
                    state = job_data.get("state")

                    if state == "pending":
                        if waited % 30 == 0:  # 每 30 秒打印一次
                            print(f"[PaddleOCR] 任务排队中...（已等待 {waited}s）")

                    elif state == "running":
                        try:
                            prog = job_data.get("extractProgress", {})
                            total = prog.get("totalPages", "?")
                            done = prog.get("extractedPages", 0)
                            if waited % 30 == 0:
                                print(f"[PaddleOCR] 正在识别 {done}/{total} 页...（已等待 {waited}s）")
                        except (KeyError, TypeError):
                            pass

                    elif state == "done":
                        prog = job_data.get("extractProgress", {})
                        pages = prog.get("extractedPages", "?")
                        print(f"[PaddleOCR] 任务完成，共 {pages} 页")
                        json_url = job_data.get("resultUrl", {}).get("jsonUrl", "")
                        return json_url if json_url else None

                    elif state == "failed":
                        err = job_data.get("errorMsg", "未知错误")
                        print(f"[PaddleOCR] 任务失败: {err}")
                        return None

            except Exception as e:
                print(f"[PaddleOCR] 轮询异常: {e}")

            await asyncio.sleep(POLL_INTERVAL)
            waited += POLL_INTERVAL

        print(f"[PaddleOCR] 轮询超时（{timeout}s）")
        return None

    async def _download_and_parse(
        self,
        session: aiohttp.ClientSession,
        jsonl_url: str,
        output_dir: Path,
        stem: str,
    ) -> Tuple[Optional[str], Optional[Path]]:
        """下载 JSONL 结果，合并为 Markdown"""
        try:
            async with session.get(
                jsonl_url,
                timeout=aiohttp.ClientTimeout(total=60),
                ssl=_SSL_VERIFY if isinstance(_SSL_VERIFY, bool) else _SSL_VERIFY,
            ) as resp:
                raw_jsonl = await resp.text()
        except Exception as e:
            print(f"[PaddleOCR] 下载结果失败: {e}")
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
                                        ssl=_SSL_VERIFY if isinstance(_SSL_VERIFY, bool) else _SSL_VERIFY,
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
