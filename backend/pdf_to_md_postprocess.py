"""
PDF → Markdown 后处理模块

包含：
- OCR 错误修复规则
- VLM 幻觉表格检测
- LLM 智能纠错
- 签名区保护
- 手写页检测
- 图片压缩与折叠
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)



# ═══════════════════════════════════════════════════════════
# OCR 纠错规则（MinerU API 免费版模型常见错误）
# ═══════════════════════════════════════════════════════════
_OCR_FIXES = [
    # ── 日语假名误识别（pipeline 模型最常见） ──
    # 表格指标名："日平均额"→"日本語の語"，"增值税"→"国語の語"
    ("日本語の語", "日平均额"),
    ("国語の語", "增值税"),
    # 微信备注中的 の 替代中文字符
    ("の口", "的口"),
    ("の诗", "的诗"),
    ("の菠萝", "的菠萝"),
    ("倘若の", "倘若的"),
    ("的の", "的的"),
    # 孤立的 の 在中文语境中几乎一定是 OCR 错误
    # 只替换前后都是汉字的情况，避免误伤
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
    import re
    for wrong, correct in _OCR_FIXES:
        text = text.replace(wrong, correct)
    # 清理中文语境中的孤立日语假名の
    # 规则：汉字+の+汉字 → 汉字的汉字（の 在中文案卷中几乎一定是 OCR 误识）
    text = re.sub(r'([一-鿿])の([一-鿿])', r'\1的\2', text)
    # 行内孤立 の（前后不是日文假名）
    text = re.sub(r'(?<![ぁ-んァ-ン])の(?![ぁ-んァ-ン])', '的', text)
    return text


# ═══════════════════════════════════════════════════════════
# VLM 幻觉检测：表格中同一单元格内容重复超阈值视为幻觉
# ═══════════════════════════════════════════════════════════
def _strip_hallucinated_tables(text: str) -> str:
    """检测并移除 VLM 模型产生的整页幻觉表格

    判据：HTML 表格中，若同一个 <td> 内容在同一列重复出现 ≥5 次，
    且该内容不是纯数字/常见标签（如年份、合计），则视为幻觉，
    将整个 <table>...</table> 块替换为注释标记。
    """
    import re

    def _check_table(match: re.Match) -> str:
        table_html = match.group(0)
        # 提取所有 <td> 内容
        cells = re.findall(r'<td>(.*?)</td>', table_html, re.DOTALL)
        if len(cells) < 10:
            return table_html  # 小表格不检测

        # 统计每个单元格文本出现次数
        cell_counts: dict[str, int] = {}
        for c in cells:
            c_stripped = c.strip()
            if not c_stripped:
                continue
            # 跳过纯数字、常见年份、百分比等
            if re.match(r'^[\d,.\s%年]+$', c_stripped):
                continue
            cell_counts[c_stripped] = cell_counts.get(c_stripped, 0) + 1

        # 如果某个非数字单元格重复 ≥5 次，判定为幻觉
        for content, count in cell_counts.items():
            if count >= 5 and len(content) < 20:
                logger.info(f"[幻觉检测] 表格中「{content}」重复 {count} 次，移除幻觉表格")
                return f'\n<!-- 幻觉表格已移除：行名「{content}」重复 {count} 次 -->\n'

        return table_html

    return re.sub(r'<table>.*?</table>', _check_table, text, flags=re.DOTALL)


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
        from llm_client import get_llm_client  # noqa: F401
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
        logger.error(f"[LLM OCR 纠错] 失败: {e}")
        return text


def llm_fix_ocr_sync(pdf_path: str, output_dir: str = None) -> str | None:
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
        logger.info(f"[LLM 纠错] MD 文件不存在: {md_path}")
        return None

    text = md_path.read_text(encoding="utf-8")
    logger.info(f"[LLM 纠错] 开始处理 {md_path.name} ({len(text)} 字)...")

    loop = asyncio.new_event_loop()
    try:
        corrected = loop.run_until_complete(_llm_fix_ocr_errors(text))
    finally:
        loop.close()

    if corrected and corrected != text:
        out_dir = Path(output_dir) if output_dir else pdf.parent
        out_md = out_dir / f"{pdf.stem}.md"
        out_md.write_text(corrected, encoding="utf-8")
        logger.info(f"[LLM 纠错] 已保存 {out_md}")
        return corrected

    logger.error("[LLM 纠错] 无变化或失败")
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


def _fold_consecutive_images(text: str, min_count: int = 1) -> tuple[str, int]:
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
