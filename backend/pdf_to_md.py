#!/usr/bin/env python3
"""
PDF → Markdown 转换模块

使用 MinerU API 进行高质量 PDF 转 MD 转换。

用法:
    from pdf_to_md import get_evidence_text

    # 使用 MinerU API 转换
    text = get_evidence_text("/path/to/evidence.pdf")
"""

import os
import time
import zipfile
import shutil
import json
from pathlib import Path
from typing import Optional, Tuple, List, Dict

import requests

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
            print(f"[分段转换] chunk {start+1}-{end} 实际 {actual_size//1024//1024}MB 超限，减半重新拆分")
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


# ═══════════════════════════════════════════════════════════
# OCR 纠错规则（MinerU API 免费版模型常见错误）
# ═══════════════════════════════════════════════════════════
_OCR_FIXES = [
    # 询问/讯问 相关（含空格变体）
    ("讯间笔录", "讯问笔录"),
    ("讯 间 笔 录", "讯问笔录"),
    ("询间笔录", "询问笔录"),
    ("询 间 笔 录", "询问笔录"),
    ("讯间人", "讯问人"),
    ("讯 间人", "讯问人"),
    ("询间人", "询问人"),
    ("询 间人", "询问人"),
    ("被讯间人", "被讯问人"),
    ("被询间人", "被询问人"),
    ("被询讯问人", "被询问/讯问人"),
    ("询问/讯间人", "询问/讯问人"),
    ("询间/讯问笔录", "询问/讯问笔录"),
    ("询间/讯问人", "询问/讯问人"),
    ("被询间/讯问人", "被询问/讯问人"),
    ("讯间对象", "讯问对象"),
    # 文书相关
    ("意者隐匿", "意，隐匿"),
    ("意 者", "意，"),
    # 格式修复
    ("口是", "□是"),
    ("口否", "□否"),
    ("口是√否", "□是√否"),
    # 常见 OCR 错字
    ("曰", "日"),
    ("巳", "已"),
    ("戍", "戌"),
    ("戊", "戌"),
    ("末", "未"),
    ("土", "士"),
    ("大", "天"),
    ("百", "白"),
    ("免", "免"),
    ("己", "已"),
    ("汨", "汩"),
    ("冐", "冒"),
    ("艮", "艮"),
    ("孑", "孑"),
    # 法律/案卷高频错字
    ("犯罪事买", "犯罪事实"),
    ("犯罪事头", "犯罪事实"),
    ("供述不穩", "供述不稳"),
    ("供述不隐", "供述不隐"),
    ("作伪证", "作伪证"),
    ("投案自手", "投案自首"),
    ("投案白首", "投案自首"),
    ("坦自", "坦白"),
    ("认罪认镭", "认罪认罚"),
    ("认罪认钅罚", "认罪认罚"),
    ("刑事拘留", "刑事拘留"),
    ("逮捕", "逮捕"),
    ("保候审", "取保候审"),
    ("监视居佗", "监视居住"),
    ("羁押", "羁押"),
    ("拘役", "拘役"),
    ("有期徒刑", "有期徒刑"),
    ("无期徒刑", "无期徒刑"),
    ("死刑缓期", "死刑缓期"),
    ("减刑", "减刑"),
    ("假释", "假释"),
    ("缓刑", "缓刑"),
    ("量刑", "量刑"),
    ("从重处罚", "从重处罚"),
    ("从轻处罚", "从轻处罚"),
    ("减轻处罚", "减轻处罚"),
    ("免除处罚", "免除处罚"),
    ("数罪并罚", "数罪并罚"),
    ("累犯", "累犯"),
    ("主犯", "主犯"),
    ("从犯", "从犯"),
    ("胁从犯", "胁从犯"),
    ("教唆犯", "教唆犯"),
    ("共同犯罪", "共同犯罪"),
    ("正当防卫", "正当防卫"),
    ("紧急避险", "紧急避险"),
    ("刑事责任", "刑事责任"),
    ("民事责任", "民事责任"),
    ("行政处罚", "行政处罚"),
    ("治安管理", "治安管理"),
    # 时间/金额/数字相关
    ("二零", "20"),
    ("二一", "21"),
    ("二二", "22"),
    ("二三", "23"),
    ("二四", "24"),
    ("二五", "25"),
    ("二六", "26"),
    # 常见地名/人名 OCR 错误
    ("北京市", "北京市"),
    ("上海市", "上海市"),
    ("广州市", "广州市"),
    ("深圳市", "深圳市"),
]

def _fix_ocr_errors(text: str) -> str:
    """修复 MinerU API 常见 OCR 错误"""
    for wrong, correct in _OCR_FIXES:
        text = text.replace(wrong, correct)
    return text


# ═══════════════════════════════════════════════════════════
# LLM 智能 OCR 纠错（异步）
# ═══════════════════════════════════════════════════════════
async def _llm_fix_ocr_errors(text: str) -> str:
    """使用 LLM 对 OCR 文本进行智能纠错

    修复规则：
    - 修复 OCR 识别错误的汉字（形近字、音近字）
    - 补全断裂的句子
    - 修复人名、地名、机构名
    - 保持原文结构和内容不变

    Args:
        text: OCR 识别后的文本

    Returns:
        纠错后的文本
    """
    try:
        from llm_client import get_llm_client
    except ImportError:
        return text

    # 如果文本太短，跳过
    if len(text) < 50:
        return text

    # 限制输入长度（LLM 处理按段落）
    max_input = 8000
    if len(text) > max_input:
        # 长文本分段处理
        paragraphs = text.split('\n\n')
        result_parts = []
        current_chunk = ""
        chunks = []
        for p in paragraphs:
            if len(current_chunk) + len(p) < max_input:
                current_chunk += p + "\n\n"
            else:
                chunks.append(current_chunk)
                current_chunk = p + "\n\n"
        if current_chunk:
            chunks.append(current_chunk)

        corrected_parts = []
        for chunk in chunks:
            corrected = await _correct_chunk(chunk)
            corrected_parts.append(corrected)
        return "\n\n".join(corrected_parts)

    return await _correct_chunk(text)


async def _correct_chunk(text: str) -> str:
    """纠错单个文本块"""
    try:
        from llm_client import get_llm_client
        client = get_llm_client()
    except Exception:
        return text

    prompt = f"""你是文本纠错专家，专门修复 OCR（光学字符识别）产生的中文文本错误。

任务：修正以下文本中的 OCR 识别错误。

**修正规则：**
1. 修正形近字错误（如"曰"→"日"、"巳"→"已"、"末"→"未"）
2. 修正音近字错误
3. 修复断裂的句子和段落
4. 修正人名、地名、机构名的 OCR 错误
5. 修正法律专业术语的错误
6. **保持原文的格式和结构不变**
7. **不要改写、总结、添加或删除内容**
8. **只修正明显是 OCR 错误的地方**

如果文本没有明显错误，直接返回原文。

请只输出修正后的文本，不要加任何解释。

---

待修正文本：
{text}

---

修正后的文本："""

    try:
        # 使用配置中的模型名称（不再写死 qwen3.6-plus）
        from config_manager import load_config
        cfg = load_config()
        model_name = cfg.get("llm_model", "")

        result = await client.chat([
            {"role": "system", "content": "你是专业的中文 OCR 文本纠错助手。只修正明显的 OCR 识别错误，保持原文内容和结构完全不变。"},
            {"role": "user", "content": prompt},
        ], model=model_name)
        return result.strip()
    except Exception as e:
        print(f"[LLM OCR 纠错] 失败: {e}")
        return text


def llm_fix_ocr_sync(pdf_path: str, output_dir: str = None) -> Optional[str]:
    """同步版本：对已有 MD 文件进行 LLM 智能纠错

    用法：
        from pdf_to_md import llm_fix_ocr_sync
        # 对已有 MD 文件重新处理
        corrected = llm_fix_ocr_sync("/path/to/evidence.pdf")

    Args:
        pdf_path: PDF 路径（自动查找同名的 .md 文件）
        output_dir: 输出目录（默认覆盖原 MD）

    Returns:
        纠错后的文本，失败返回 None
    """
    import asyncio
    pdf = Path(pdf_path)
    md_path = pdf.with_suffix(".md")
    if not md_path.exists():
        print(f"[LLM 纠错] MD 文件不存在: {md_path}")
        return None

    text = md_path.read_text(encoding="utf-8")
    print(f"[LLM 纠错] 开始处理 {md_path.name} ({len(text)} 字)...")

    loop = asyncio.new_event_loop()
    try:
        corrected = loop.run_until_complete(_llm_fix_ocr_errors(text))
    finally:
        loop.close()

    if corrected and corrected != text:
        out_dir = Path(output_dir) if output_dir else pdf.parent
        out_md = out_dir / f"{pdf.stem}.md"
        out_md.write_text(corrected, encoding="utf-8")
        print(f"[LLM 纠错] 已保存 {out_md}")
        return corrected

    print(f"[LLM 纠错] 无变化或失败")
    return None


# ═══════════════════════════════════════════════════════════
# 签名区保护：询问人/讯问人/记录人等签名保留图片，不 OCR
# ═══════════════════════════════════════════════════════════
# 将签名区替换为 HTML 占位元素，避免引用不存在的图片文件
# 使用 HTML div 而非 markdown 图片语法，因为签名图片并不存在
_SIGNATURE_HTML = '<div style="text-align:center;color:#aaa;border-bottom:1px dashed #ccc;padding:2px 20px;margin:2px 0;font-size:11px;user-select:none;">[手写签名]</div>'

_SIGNATURE_PATTERNS = [
    # 笔录签名区（手写体，OCR 识别率低且常出错）
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


def _protect_signatures_as_images(text: str) -> str:
    """将签名区替换为图片占位符，保留手写签名图片而非 OCR 文字"""
    import re
    for pattern, replacement in _SIGNATURE_PATTERNS:
        text = re.sub(pattern, replacement, text)
    return text


def _detect_handwritten_pages(pdf_path: Path) -> set:
    """检测 PDF 中可能包含手写稿的页面

    通过检测页面是否包含大量图片区域或签名框来判断
    返回可能包含手写稿的页码集合（1-based）
    """
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        handwritten_pages = set()
        for i, page in enumerate(doc):
            # 获取页面上的图片数量
            images = page.get_images(full=True)
            # 获取页面上的绘图命令
            drawings = page.get_drawings()
            # 如果页面有大量图片（可能是手稿扫描件）
            # 或者有签名框矩形框，标记为手写页
            if len(images) > 1:
                handwritten_pages.add(i + 1)
            elif len(drawings) > 20:
                # 签名页通常有很多绘制线条（签名框、横线等）
                handwritten_pages.add(i + 1)
        doc.close()
        return handwritten_pages
    except Exception:
        return set()


# ═══════════════════════════════════════════════════════════
# 图片后处理：压缩 + 折叠
# ═══════════════════════════════════════════════════════════
_MAX_IMAGE_DIM = 800  # 图片长边最大尺寸

def _compress_images(images_dir: Path, max_dim: int = _MAX_IMAGE_DIM) -> int:
    """压缩超过阈值的图片（保持宽高比）"""
    try:
        from PIL import Image
    except ImportError:
        return 0

    count = 0
    for img_path in images_dir.iterdir():
        if img_path.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.webp'):
            continue
        try:
            img = Image.open(img_path)
            if max(img.width, img.height) <= max_dim:
                continue
            ratio = max_dim / max(img.width, img.height)
            new_w = int(img.width * ratio)
            new_h = int(img.height * ratio)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            # JPEG 保持 JPEG 格式
            save_kw = {}
            if img_path.suffix.lower() in ('.jpg', '.jpeg'):
                save_kw = {'quality': 85, 'format': 'JPEG'}
            img.save(img_path, **save_kw)
            count += 1
        except Exception:
            continue
    return count


def _fold_consecutive_images(text: str, min_count: int = 1) -> Tuple[str, int]:
    """将连续 3+ 张图片折叠为 <details> 折叠块

    图片之间可能有空行，视为同一图片块。
    返回: (修改后的文本, 折叠块数量)
    """
    import re

    # 预处理：标记每行是否为图片行
    lines = text.split('\n')
    is_image = [bool(re.match(r'^!\[.*\]\([^)]+\)$', line.strip())) for line in lines]

    # 找到连续图片块（允许中间有空行）
    result = []
    block_count = 0
    i = 0

    while i < len(lines):
        if is_image[i]:
            # 开始收集图片块
            block_lines = [lines[i]]
            j = i + 1
            while j < len(lines):
                if is_image[j]:
                    block_lines.append(lines[j])
                    j += 1
                elif lines[j].strip() == '':
                    # 空行：检查后面是否还有图片
                    k = j + 1
                    while k < len(lines) and lines[k].strip() == '':
                        k += 1
                    if k < len(lines) and is_image[k]:
                        # 后面还有图片，跳过这些空行
                        j = k
                    else:
                        # 后面没有图片了，结束块
                        break
                else:
                    # 非空非图片，结束块
                    break

            if len(block_lines) >= min_count:
                # 折叠
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
            print(f"[分段转换] {pdf_path.name}: 共 {len(chunks)} 个 chunk")
            for chunk_path in chunks:
                chunk_output = output_dir / f"{temp_prefix}_{chunk_index}"
                chunk_output.mkdir(parents=True, exist_ok=True)
                print(f"[分段转换] 开始处理 chunk {chunk_index}: {chunk_path.name}")

                # 失败重试一次，避免偶尔的网络/API错误丢失整段
                result = _mineru_convert_single(chunk_path, chunk_output, timeout, progress_cb)
                if not result or not result[0]:
                    print(f"[分段转换] chunk {chunk_index} 首次失败，15s 后重试...")
                    import time; time.sleep(15)
                    result = _mineru_convert_single(chunk_path, chunk_output, timeout, progress_cb)

                chunk_path.unlink(missing_ok=True)  # 清理临时分段文件
                if result and result[0]:
                    print(f"[分段转换] chunk {chunk_index} 成功: {len(result[0])} 字符")
                    chunk_results.append(result[0])
                    if result[1]:
                        all_images_dirs.append(result[1])
                else:
                    print(f"[分段转换] chunk {chunk_index} 重试后仍失败，跳过（已丢失对应页）")
                chunk_index += 1

            print(f"[分段转换] 完成 {pdf_path.name}: {len(chunk_results)}/{len(chunks)} 个 chunk 成功")

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
            print(f"[MinerU] 第 {attempt} 次尝试失败，{delay}s 后重试 ({attempt + 1}/{max_retries}): {pdf_path.name}")
            if progress_cb:
                progress_cb("retrying", f"第 {attempt} 次失败，{delay}s 后自动重试...")
            time.sleep(delay)
    print(f"[MinerU] 已重试 {max_retries} 次，仍失败: {pdf_path.name}")
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
        print(f"[MinerU] Token 未配置，跳过 {pdf_path.name}")
        return None, None

    stem = pdf_path.stem

    try:
        # 1. 提交转换任务
        if progress_cb:
            progress_cb("submitting", "正在提交转换任务...")
        resp = requests.post(
            f"{MINERU_API}/file-urls/batch",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "files": [{"name": pdf_path.name, "data_id": stem}],
                "model_version": "auto",
                "method": "auto",
                "enable_formula": False,
                "enable_table": True,
                "language": "ch",
                "parse_method": "auto",
            },
            timeout=30,
        )
        result = resp.json()
        if result.get("code") != 0:
            err_msg = result.get("msg", "未知错误")
            print(f"[MinerU] 获取上传链接失败: {pdf_path.name}, code={result.get('code')}, msg={err_msg}")
            return None, None

        batch_id = result["data"]["batch_id"]
        upload_url = result["data"]["file_urls"][0]
        print(f"[MinerU] 开始上传 {pdf_path.name} (batch_id={batch_id})")

        # 2. 发送文件
        if progress_cb:
            progress_cb("uploading", "正在发送文件...")
        with open(pdf_path, "rb") as f:
            r = requests.put(upload_url, data=f.read(), timeout=120)
        if r.status_code not in (200, 203):
            print(f"[MinerU] 上传失败: {pdf_path.name}, HTTP {r.status_code}")
            return None, None

        # 3. 等待云端处理
        if progress_cb:
            progress_cb("processing", "正在识别文本内容...")
        waited = 0
        while waited < timeout:
            r = requests.get(
                f"{MINERU_API}/extract-results/batch/{batch_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            data = r.json().get("data", {})
            results = data.get("extract_result", [])
            if not results:
                time.sleep(5); waited += 5
                if progress_cb:
                    progress_cb("processing", f"正在识别文本内容...（已等待 {waited} 秒）")
                continue

            state = results[0].get("state")
            if state == "done":
                print(f"[MinerU] 转换完成 {pdf_path.name}，下载结果中...")
                if progress_cb:
                    progress_cb("processing", "正在识别文本内容...")

                # 4. 获取结果
                if progress_cb:
                    progress_cb("downloading", "正在生成结构化文本...")
                temp_dir = output_dir / f"_tmp_mineru_{stem}"
                temp_dir.mkdir(parents=True, exist_ok=True)
                zip_path = temp_dir / f"{stem}.zip"
                zip_path.write_bytes(requests.get(results[0]["full_zip_url"], timeout=120).content)
                with zipfile.ZipFile(zip_path) as zf:
                    zf.extractall(temp_dir)
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
                    # 压缩大图
                    if target_images_dir and target_images_dir.exists():
                        _compress_images(target_images_dir)
                    # 折叠连续图片块
                    text, _ = _fold_consecutive_images(text)
                    # 写入目标文件
                    target_md = output_dir / f"{stem}.md"
                    target_md.write_text(text, encoding="utf-8")
                    return text, target_images_dir
                print(f"[MinerU] 结果文件过小或缺失: {pdf_path.name}")
                return None, None
            elif state == "failed":
                err_info = results[0].get("task_status_msg", "未知错误")
                print(f"[MinerU] 云端转换失败: {pdf_path.name}, {err_info}")
                return None, None

            time.sleep(5); waited += 5

        print(f"[MinerU] 转换超时: {pdf_path.name}")
        return None, None

    except Exception as e:
        print(f"[MinerU] 异常: {pdf_path.name}, {e}")
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
    2. 调用 MinerU API 转换

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

    # 2. 调用 MinerU 转换
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

    # 3. MinerU 转换失败
    print(f"[转换失败] 所有引擎均失败: {pdf.name}")
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
