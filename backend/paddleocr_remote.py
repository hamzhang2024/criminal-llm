#!/usr/bin/env python3
"""
PaddleOCR-VL 远程 PDF → Markdown 转换模块

使用 PaddleOCR-VL 异步任务 API 进行 PDF 转 MD 转换。
API: https://paddleocr.aistudio-app.com/api/v2/ocr/jobs

用法:
    from paddleocr_remote import paddleocr_convert

    text, images_dir = paddleocr_convert(pdf_path, output_dir)
"""

import os
import json
import time
import shutil
import tempfile
from datetime import date
from pathlib import Path
from typing import Optional, Tuple, Callable

import requests

# 打包后 certifi 证书路径可能失效，macOS 用系统证书
import sys
if sys.platform == "darwin" and getattr(sys, "frozen", False):
    _SSL_VERIFY = "/etc/ssl/cert.pem"
else:
    _SSL_VERIFY = True

# PaddleOCR-VL API 配置
PADDLEOCR_API_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
PADDLEOCR_MODEL = "PaddleOCR-VL-1.6"
PADDLEOCR_DAILY_PAGE_LIMIT = 20000  # 每日转换页数配额

# 刑事案卷专用 optionalPayload 参数
# 案卷特点：文字密集、扫描件、有手写签名/印章、需准确标题层级、防止循环重复输出
# 优化讯问时间等关键信息识别
PADDLEOCR_OPTIONAL_PAYLOAD = {
    # 预处理：扫描件可能有旋转/弯曲
    "useDocOrientationClassify": True,   # 自动纠正文档旋转
    "useDocUnwarping": True,             # 修复几何弯曲

    # 版面分析
    "useLayoutDetection": True,          # 开启版面分析，识别表格/标题/段落
    "useChartRecognition": False,        # 案卷基本无图表，关闭节省资源
    "layoutThreshold": 0.5,              # 版面检测置信度阈值

    # 生成参数（优化准确性，改善日期/时间识别）
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


def build_optional_payload() -> dict:
    """构建 optionalPayload，按 image_ocr_enabled 配置注入图片识别参数

    useOcrForImageBlock：识别图片块中的文字（转账凭证/银行流水截图）
    useSealRecognition：印章识别（流水/凭证常见印章）
    """
    payload = dict(PADDLEOCR_OPTIONAL_PAYLOAD)
    try:
        from config_manager import get_config_value
        enabled = get_config_value("image_ocr_enabled", True)
    except ImportError:
        enabled = True
    if enabled:
        payload["useOcrForImageBlock"] = True
        payload["useSealRecognition"] = True
    return payload


# ═══════════════════════════════════════════════════════════
# 每日配额跟踪
# ═══════════════════════════════════════════════════════════
def _get_quota_file() -> Path:
    """获取配额文件路径"""
    try:
        from config import DATA_DIR
        return DATA_DIR / "paddleocr_quota.json"
    except ImportError:
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
# 配置读取
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
# API 调用
# ═══════════════════════════════════════════════════════════
def _submit_job(pdf_path: Path, token: str) -> Optional[str]:
    """提交 PDF 转换任务，返回 jobId"""
    headers = {"Authorization": f"bearer {token}"}
    data = {
        "model": PADDLEOCR_MODEL,
        "optionalPayload": json.dumps(build_optional_payload()),
    }

    try:
        with open(pdf_path, "rb") as f:
            files = {"file": f}
            resp = requests.post(
                PADDLEOCR_API_URL,
                headers=headers,
                data=data,
                files=files,
                timeout=120,
                verify=_SSL_VERIFY,
            )

        if resp.status_code == 429:
            print(f"[PaddleOCR] API 返回 429 限频，请稍后重试")
            return None

        if resp.status_code != 200:
            print(f"[PaddleOCR] 提交任务失败: HTTP {resp.status_code}, {resp.text[:300]}")
            return None

        job_id = resp.json()["data"]["jobId"]
        return job_id
    except Exception as e:
        print(f"[PaddleOCR] 提交任务异常: {e}")
        return None


def _poll_job(job_id: str, token: str, timeout: int = 3600, progress_cb: Optional[Callable] = None) -> Optional[str]:
    """轮询任务状态，完成后返回 result JSON URL"""
    headers = {"Authorization": f"bearer {token}"}
    waited = 0
    poll_interval = 10  # 10 秒轮询一次

    while waited < timeout:
        try:
            resp = requests.get(
                f"{PADDLEOCR_API_URL}/{job_id}",
                headers=headers,
                timeout=30,
                verify=_SSL_VERIFY,
            )
            if resp.status_code != 200:
                print(f"[PaddleOCR] 查询任务失败: HTTP {resp.status_code}")
                time.sleep(poll_interval)
                waited += poll_interval
                continue

            job_data = resp.json()["data"]
            state = job_data.get("state")

            if state == "pending":
                if progress_cb:
                    progress_cb("processing", f"正在排队中...（已等待 {waited}s）")
                print(f"[PaddleOCR] 任务排队中...（已等待 {waited}s）")

            elif state == "running":
                try:
                    prog = job_data.get("extractProgress", {})
                    total = prog.get("totalPages", "?")
                    done = prog.get("extractedPages", 0)
                    msg = f"正在识别 {done}/{total} 页...（已等待 {waited}s）"
                except (KeyError, TypeError):
                    msg = f"正在处理中...（已等待 {waited}s）"

                if progress_cb:
                    progress_cb("processing", msg)
                print(f"[PaddleOCR] {msg}")

            elif state == "done":
                prog = job_data.get("extractProgress", {})
                pages = prog.get("extractedPages", "?")
                print(f"[PaddleOCR] 任务完成，共 {pages} 页")
                json_url = job_data.get("resultUrl", {}).get("jsonUrl", "")
                if json_url:
                    return json_url
                print(f"[PaddleOCR] 未找到结果 URL")
                return None

            elif state == "failed":
                err = job_data.get("errorMsg", "未知错误")
                print(f"[PaddleOCR] 任务失败: {err}")
                return None

        except Exception as e:
            print(f"[PaddleOCR] 轮询异常: {e}")

        time.sleep(poll_interval)
        waited += poll_interval

    print(f"[PaddleOCR] 轮询超时（{timeout}s）")
    return None


def _download_and_parse_jsonl(jsonl_url: str, output_dir: Path, stem: str) -> Optional[str]:
    """下载 JSONL 结果，合并为 Markdown，并下载图片到本地"""
    try:
        resp = requests.get(jsonl_url, timeout=60, verify=_SSL_VERIFY)
        resp.raise_for_status()
        raw_jsonl = resp.text
    except Exception as e:
        print(f"[PaddleOCR] 下载结果失败: {e}")
        return None

    lines = resp.text.strip().split('\n')
    if not lines:
        print(f"[PaddleOCR] 结果为空")
        return None

    # 保存 JSONL 原始结果到 _json 目录（保留 MinerU 风格的结构化产物）
    json_dir = output_dir / f"{stem}_json"
    json_dir.mkdir(parents=True, exist_ok=True)
    (json_dir / "content_list.jsonl").write_text(raw_jsonl, encoding="utf-8")

    # 准备图片目录（与 MinerU 引擎命名一致：{stem}_images）
    images_dir = output_dir / f"{stem}_images"
    images_dir.mkdir(parents=True, exist_ok=True)

    # 合并所有页的 Markdown，下载图片并重写引用路径
    md_parts = []
    global_page = 0
    downloaded = 0
    failed = 0

    for line_num, line in enumerate(lines, start=1):
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

                # 下载本页图片到 {stem}_images/，并把 markdown 里的引用路径改写
                # 原引用：<img src="imgs/img_in_image_box_xxx.jpg">
                # 改写为：<img src="./{stem}_images/img_in_image_box_xxx.jpg">
                if isinstance(page_images, dict):
                    for img_path, img_url in page_images.items():
                        if not isinstance(img_url, str) or not img_url.startswith("http"):
                            continue
                        # 提取文件名（去掉 imgs/ 前缀）
                        img_name = Path(img_path).name
                        local_img = images_dir / img_name
                        if not local_img.exists():
                            try:
                                ir = requests.get(img_url, timeout=30, verify=_SSL_VERIFY)
                                if ir.status_code == 200 and ir.content:
                                    local_img.write_bytes(ir.content)
                                    downloaded += 1
                                else:
                                    failed += 1
                                    print(f"[PaddleOCR] 图片下载失败 HTTP {ir.status_code}: {img_name}")
                            except Exception as ie:
                                failed += 1
                                print(f"[PaddleOCR] 图片下载异常 {img_name}: {ie}")
                        # 改写 markdown 文本中的引用
                        text = text.replace(f'src="{img_path}"', f'src="./{stem}_images/{img_name}"')
                        text = text.replace(f"src='{img_path}'", f"src='./{stem}_images/{img_name}'")
                        text = text.replace(f']({img_path})', f'](./{stem}_images/{img_name})')

                if text:
                    anchor = f"<!-- paddleocr-page:{global_page} -->"
                    md_parts.append(f"{anchor}\n\n{text}")
                    global_page += 1

        except Exception as e:
            print(f"[PaddleOCR] 解析第 {line_num} 行失败: {e}")
            continue

    if downloaded or failed:
        print(f"[PaddleOCR] 图片下载完成：成功 {downloaded}，失败 {failed}")

    if not md_parts:
        print(f"[PaddleOCR] 无有效 Markdown 内容")
        return None

    full_text = "\n\n".join(md_parts)
    return full_text


# ═══════════════════════════════════════════════════════════
# LaTeX 格式清理（案卷中 $ \underline{\text{...}} $ 等标记转为纯文本）
# ═══════════════════════════════════════════════════════════
def _clean_latex_markup(text: str) -> str:
    """清理 LaTeX 格式标记，保留纯文本内容"""
    import re
    # 数学模式包裹：$ \underline{\text{xxx}} $ → xxx
    text = re.sub(r'\$\s*\\underline\{\\text\{([^}]*)\}\}\s*\$', r'\1', text)
    text = re.sub(r'\$\s*\\text\{([^}]*)\}\s*\$', r'\1', text)
    text = re.sub(r'\$\s*\\textbf\{([^}]*)\}\s*\$', r'\1', text)
    text = re.sub(r'\$\s*\\emph\{([^}]*)\}\s*\$', r'\1', text)
    # 数学模式下划线填空：$ \underline{ } $ 或 $ \underline{\ \ } $ → ___
    text = re.sub(r'\$\s*\\underline\{[\s\\]*\}\s*\$', '___', text)
    text = re.sub(r'\$\s*\\underline\{\\text\{[\s\\]*\}\}\s*\$', '___', text)
    # 普通 LaTeX 标记
    patterns = [
        (r'\\underline\{([^}]*)\}', r'\1'),
        (r'\\text\{([^}]*)\}', r'\1'),
        (r'\\uwave\{([^}]*)\}', r'\1'),
        (r'\\textbf\{([^}]*)\}', r'\1'),
        (r'\\emph\{([^}]*)\}', r'\1'),
        (r'\\textit\{([^}]*)\}', r'\1'),
        (r'\\\{([^}]*)\}', r'\1'),
    ]
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    # 清理剩余 LaTeX 命令
    text = re.sub(r'\\[a-zA-Z]+\{([^}]*)\}', r'\1', text)
    text = re.sub(r'\\[a-zA-Z]+\{', '', text)
    # 清理孤立的下划线命令头
    text = re.sub(r'\\\{', '', text)

    # ── 案卷场景特有：抑制 VLM 把日期/编号误识别成公式产生的噪声 ──
    # 1. 行内/块内公式包裹：$ 20 $24年 → 20 24年（去掉无意义 $ 符号）
    # 把单字符或纯数字的 $...$ 公式块还原成纯文本
    text = re.sub(r'\$\s*([0-9A-Za-z一-鿿\s]+?)\s*\$', r'\1', text)
    # 2. 矩阵/数组环境残留：<bmatrix>、</bmatrix>、<pmatrix> 等
    #    含未闭合的（如 "</bmatrix" 后面没有 >）也要清理
    text = re.sub(r'</?[bp]?matrix[^>]*>?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\\begin\{[bp]?matrix\}', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\\end\{[bp]?matrix\}', '', text, flags=re.IGNORECASE)
    # 3. 上下标残留：<sup>...</sup>、<sub>...</sub>，包括用中文括号"<sup）"这种异常闭合
    text = re.sub(r'<sup>([^<]*)</sup>', r'\1', text, flags=re.IGNORECASE)
    text = re.sub(r'<sub>([^<]*)</sub>', r'\1', text, flags=re.IGNORECASE)
    # 容错：<sup>/<sub> 后跟任意非 > 字符（如 ）, 】, 中文括号等）
    text = re.sub(r'</?sup[^a-zA-Z>]?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</?sub[^a-zA-Z>]?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</?sup[^>]*>?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</?sub[^>]*>?', '', text, flags=re.IGNORECASE)
    # 4. 错误的括号闭合：<br)、<br>、)-、（）残留
    text = re.sub(r'<br[^>]*>?', ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'<br[)\)）]', ' ', text)
    # 5. 残留的 $ 符号（成对清理后剩单个）
    text = re.sub(r'(?<![\\\w])\$(?!\d)', '', text)

    # ── 案卷场景：日期/时间填空残留的额外清理 ──
    # 6. LaTeX 上下标：_{xxx}^{yyy} / ^{xxx}_{yyy} / _{xxx} / ^{xxx}
    text = re.sub(r'_\{([^}]*)\}\^\{([^}]*)\}', r'\1\2', text)
    text = re.sub(r'\^\{([^}]*)\}_\{([^}]*)\}', r'\1\2', text)
    text = re.sub(r'_\{([^}]*)\}', r'\1', text)
    text = re.sub(r'\^\{([^}]*)\}', r'\1', text)
    # 7. 圆圈数字 ①…⑩ → 普通数字（笔录里常被识别成圆圈）
    for i, ch in enumerate('①②③④⑤⑥⑦⑧⑨⑩⑪⑫', 1):
        text = text.replace(ch, str(i))
    # 8. 残留乱码：>数字>>-)（OCR 把表格边框识别成尖括号序列）
    # 注意：数字后必须至少跟一个 > 才算噪声，否则会吞掉 <td>8,051</td> 中的 >8（实测教训）
    text = re.sub(r'>\d+>+-?\)?', '', text)
    # 9. 杂项数学符号：⊥（笔录里没意义）
    text = text.replace('⊥', '')
    # 10. 日期/时间字段前缀的散落标点：".1月" → "1月"，"1_月" → "1月"
    text = re.sub(r'\.(\d+)\s*(年|月|日|时|分|秒)', r'\1\2', text)
    text = re.sub(r'(\d+)\s*_+\s*(年|月|日|时|分|秒)', r'\1\2', text)
    # 11. 数字间散落空格：" 20 24 年" → "2024 年"（仅在年月日时分秒前）
    for _ in range(2):  # 跑两次处理 4 位被分 2+2 的情况
        text = re.sub(r'(\d)\s+(\d)(?=\d*\s*(年|月|日|时|分|秒))', r'\1\2', text)
    # 12. 空花括号 {} / { } 残留
    text = re.sub(r'\{\s*\}', '', text)
    # 13. 连续 3+ 下划线（填空线）→ 删除（视觉噪声多于价值）
    text = re.sub(r'_{3,}', '', text)
    # 14. 剩余孤立花括号（OCR 把表格框误识别成花括号）
    text = re.sub(r'[{}]', '', text)
    return text


# ═══════════════════════════════════════════════════════════
# 刑事案卷专用 OCR 纠错
# ═══════════════════════════════════════════════════════════
# 案卷高频错字（PaddleOCR 特有错误）
_CASE_OCR_FIXES = [
    # ── 日语假名误识别 ──
    ("日本語の語", "日平均额"),
    ("国語の語", "增值税"),
    # ── 大/天 混淆（案卷最高频错误） ──
    ("最天", "最大"), ("天大", "最大"), ("点数最天", "点数最大"),
    ("牌面天小", "牌面大小"), ("牌面天", "牌面大"), ("天小", "大小"),
    ("天盲", "大盲"),
    ("天号", "大号"), ("天楼", "大楼"), ("天厅", "大厅"),
    ("天概", "大概"),
    ("天家", "大家"),
    # 赌/赔 混淆
    ("赔客", "赌客"), ("赔场", "赌场"), ("赔局", "赌局"),
    ("赔博", "赌博"), ("赔钱", "赌钱"), ("赔资", "赌资"),
    ("赔具", "赌具"), ("赔桌", "赌桌"), ("赔台", "赌台"),
    ("赔罪", "赌罪"),
    # 人/入 混淆
    ("嫌疑入", "嫌疑人"), ("犯罪嫌疑入", "犯罪嫌疑人"),
    ("作证入", "作证人"), ("证入", "证人"),
    ("当事入", "当事人"), ("代理入", "代理人"),
    ("辩护入", "辩护人"), ("诉讼代理入", "诉讼代理人"),
    ("鉴定入", "鉴定人"), ("记录入", "记录人"),
    ("翻译入", "翻译人"), ("侦查入员", "侦查人员"),
    ("办案入员", "办案人员"), ("办案入", "办案人"),
    ("讯问入", "讯问人"), ("询问入", "询问人"),
    ("被询问入", "被询问人"), ("被讯问入", "被讯问人"),
    ("入民检察院", "人民检察院"), ("入民法院", "人民法院"),
    ("入民警察", "人民警察"), ("入民政府", "人民政府"),
    # 其他案卷高频
    ("取保侯审", "取保候审"),
    ("监视居", "监视居住"),
    ("起诉意见書", "起诉意见书"),
    ("起诉意见书记", "起诉意见书"),
    ("案件侦破经过", "刑事案件侦破经过"),
    ("收容教有", "收容教养"),
]

def _fix_case_ocr_errors(text: str) -> str:
    """修复刑事案卷特有的 OCR 错误"""
    for wrong, correct in _CASE_OCR_FIXES:
        text = text.replace(wrong, correct)
    return text


# ═══════════════════════════════════════════════════════════
# 签名保护 / OCR 纠错 / 图片折叠（复用 pdf_to_md 的逻辑）
# ═══════════════════════════════════════════════════════════
def _apply_postprocessing(text: str) -> str:
    """应用后处理：LaTeX 清理 + 通用 OCR 纠错 + 案卷专用纠错 + 签名保护 + 图片折叠"""
    # 1. 清理 LaTeX 标记
    text = _clean_latex_markup(text)
    # 2. 通用 OCR 纠错 + VLM 幻觉检测
    try:
        from pdf_to_md import (
            _protect_signatures_as_images,
            _fix_ocr_errors,
            _fold_consecutive_images,
            _strip_hallucinated_tables,
        )
        text = _fix_ocr_errors(text)
        text = _strip_hallucinated_tables(text)
    except ImportError:
        pass
    # 3. 案卷专用纠错（多字符上下文修正，在通用单字符替换之后，可覆盖错误修正）
    text = _fix_case_ocr_errors(text)
    # 4. 签名保护 + 图片折叠
    try:
        from pdf_to_md import (
            _protect_signatures_as_images,
            _fold_consecutive_images,
        )
        text = _protect_signatures_as_images(text)
        text, _ = _fold_consecutive_images(text)
    except ImportError:
        pass
    return text


# ═══════════════════════════════════════════════════════════
# 核心转换函数
# ═══════════════════════════════════════════════════════════
def paddleocr_convert(
    pdf_path: Path,
    output_dir: Path,
    timeout: int = 3600,
    progress_cb: Optional[Callable] = None,
) -> Optional[tuple[str, Optional[Path]]]:
    """使用 PaddleOCR-VL API 转换 PDF → MD

    异步模式：上传文件 → 提交任务 → 轮询结果 → 下载 JSONL → 解析

    Args:
        pdf_path: PDF 文件路径
        output_dir: 输出目录
        timeout: 总超时时间（秒）
        progress_cb: 可选的进度回调 (stage: str, detail: str)

    Returns:
        (Markdown 文本, 图片目录路径或 None)，失败返回 None
    """
    token = _get_paddleocr_token()
    if not token:
        print(f"[PaddleOCR] Token 未配置，跳过 {pdf_path.name}")
        return None, None

    stem = pdf_path.stem

    # 获取总页数（仅用于配额统计）
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        total_pages = len(doc)
        doc.close()
    except Exception as e:
        print(f"[PaddleOCR] 无法打开 PDF: {e}")
        return None, None

    if total_pages == 0:
        print(f"[PaddleOCR] PDF 无页面: {pdf_path.name}")
        return None, None

    if progress_cb:
        progress_cb("submitting", f"正在提交 {pdf_path.name}（{total_pages} 页）...")

    # 1. 提交任务
    job_id = _submit_job(pdf_path, token)
    if not job_id:
        return None, None

    print(f"[PaddleOCR] 任务已提交: {pdf_path.name}, job_id={job_id}")

    # 2. 轮询结果
    if progress_cb:
        progress_cb("processing", "正在等待处理完成...")
    jsonl_url = _poll_job(job_id, token, timeout=timeout, progress_cb=progress_cb)
    if not jsonl_url:
        return None, None

    # 3. 下载并解析
    if progress_cb:
        progress_cb("downloading", "正在下载结果...")
    full_text = _download_and_parse_jsonl(jsonl_url, output_dir, stem)
    if not full_text or len(full_text) < 50:
        print(f"[PaddleOCR] 结果内容过少，跳过: {pdf_path.name}")
        return None, None

    # 4. 后处理
    full_text = _apply_postprocessing(full_text)

    # 5. 保存图片
    images_dir = output_dir / f"{stem}_images"
    if images_dir.exists() and any(images_dir.iterdir()):
        # 压缩大图
        try:
            from pdf_to_md import _compress_images
            _compress_images(images_dir)
        except ImportError:
            pass
    else:
        images_dir = None

    # 6. 写入 MD 文件
    target_md = output_dir / f"{stem}.md"
    target_md.write_text(full_text, encoding="utf-8")
    print(f"[PaddleOCR] 转换完成 {pdf_path.name}: {len(full_text)} 字符, {total_pages} 页")

    return full_text, images_dir


# ═══════════════════════════════════════════════════════════
# 测试连接
# ═══════════════════════════════════════════════════════════
def test_connection(token: str) -> tuple[bool, str]:
    """测试 PaddleOCR API 连通性（提交一个空任务验证 token）"""
    if not token:
        return False, "Token 不能为空"

    headers = {"Authorization": f"bearer {token}"}
    try:
        # 尝试提交一个简单任务来验证 token
        # 这里只验证 token 格式和 API 可达性，不真正提交文件
        resp = requests.get(
            PADDLEOCR_API_URL,
            headers=headers,
            timeout=15,
            verify=_SSL_VERIFY,
        )
        # 405 (Method Not Allowed) 说明端点存在且 token 格式正确
        # 401/403 说明 token 无效
        # 400 可能是参数错误，不能视为 token 有效
        if resp.status_code in (200, 405):
            return True, "Token 有效，API 可达"
        elif resp.status_code in (400, 401, 403):
            return False, f"Token 无效: HTTP {resp.status_code}"
        else:
            return False, f"API 返回异常状态: HTTP {resp.status_code}"
    except requests.exceptions.Timeout:
        return False, "请求超时，请检查网络"
    except requests.exceptions.ConnectionError:
        return False, "无法连接到服务，请检查网络"
    except Exception as e:
        return False, f"请求异常: {str(e)}"
