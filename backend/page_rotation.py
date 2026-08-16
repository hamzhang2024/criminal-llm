"""页面旋转干预：缩略图生成、页面旋转、乱码页检测、单页抽取与 md 拼接

背景：案卷扫描件偶有倒置页（如整页旋转 180° 扫描），MinerU 对倒置页会误判
版面（笔录页识别为 <table> 乱码块），产生"泻叶无/次嘉豪"级乱码。MinerU API
无旋转参数，须在本地 PDF 上修正页面方向后再转换。
本模块全部为纯函数，FastAPI 端点在 case_manager.py 中做薄封装。
"""
import logging
import re
from pathlib import Path

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# 缩略图默认宽度（供预览页网格浏览，能辨认页面朝向即可）
THUMB_DEFAULT_WIDTH = 200


def generate_pdf_thumbnails(pdf_path: Path, cache_dir: Path, width: int = THUMB_DEFAULT_WIDTH) -> list[dict]:
    """逐页生成缩略图 PNG 到 cache_dir（已存在则跳过，断点续渲）

    Returns: [{"page": 1, "file": "page_1.png"}, ...]（url 由端点层拼接）
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    try:
        zoom = width / 595  # A4 宽度约 595pt
        mat = fitz.Matrix(zoom, zoom)
        result = []
        for i, page in enumerate(doc):
            png = cache_dir / f"page_{i + 1}.png"
            if not png.exists():
                pix = page.get_pixmap(matrix=mat)
                pix.save(png)
            result.append({"page": i + 1, "file": png.name})
        return result
    finally:
        doc.close()


def thumb_cache_dir_for(cache_root: Path, case_id: str, pdf_name: str) -> Path:
    """案件 PDF 的缩略图缓存目录（与 /thumbnails 静态挂载下的 URL 一一对应）"""
    return cache_root / "thumb" / case_id / Path(pdf_name).stem


def rotate_pdf_page(pdf_path: Path, page_no: int, degrees: int,
                    thumb_cache_dir: Path | None = None) -> int:
    """将 page_no（1 基）顺时针旋转 degrees（90/180/270），增量保存

    Returns: 旋转后的新 rotation 值（0/90/180/270）
    """
    if degrees not in (90, 180, 270):
        raise ValueError("degrees 只支持 90/180/270")
    doc = fitz.open(pdf_path)
    try:
        if not 1 <= page_no <= len(doc):
            raise ValueError(f"页码 {page_no} 超出范围（共 {len(doc)} 页）")
        page = doc[page_no - 1]
        new_rot = (page.rotation + degrees) % 360
        page.set_rotation(new_rot)
        doc.saveIncr()  # 增量保存：只追加 rotation 变更，大文件秒级完成
    finally:
        doc.close()
    # 旋转后该页缩略图缓存失效
    if thumb_cache_dir:
        stale = thumb_cache_dir / f"page_{page_no}.png"
        if stale.exists():
            stale.unlink()
    logger.info(f"[页面旋转] {pdf_path.name} 第 {page_no} 页旋转 {degrees}° → {new_rot}°")
    return new_rot


_PAGE_LABEL_RE = re.compile(r"第\s*\d+\s*页\s*共\s*\d+\s*页")

# 扫描 </table> 闭合标签的最大前瞻行数。
# 依据：真实乱码块（倒置页产物）是单行 <table>...</table>；超长未闭合是
# MinerU 表格碎片而非倒置页特征，50 行内仍不闭合则放弃该开口行。
_MAX_TABLE_BLOCK_LINES = 50


def detect_md_issues(md_dir: Path) -> list[dict]:
    """扫描 md/*.md，找出被 MinerU 误判为表格的笔录页（倒置/异常扫描页的特征）

    判定：整块 <table>...</table> 内含「第N页共M页」页码标记或 ≥1 个「问：」。
    正常表格（卷内目录等）不含这些特征，不误报。
    Returns: [{"md_file", "page_label", "start_line", "end_line", "preview"}]
    （行号为 0 基，end_line 含；供重转拼接定位）
    """
    issues = []
    for md in sorted(md_dir.glob("*.md")):
        try:
            lines = md.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as e:
            logger.warning(f"[乱码检测] 读取 {md.name} 失败，跳过: {e}")
            continue
        i = 0
        while i < len(lines):
            if lines[i].strip().startswith("<table>"):
                start = i
                j = i
                # 最多向前看 _MAX_TABLE_BLOCK_LINES 行寻找 </table>
                while j < len(lines) and "</table>" not in lines[j] and j - start < _MAX_TABLE_BLOCK_LINES:
                    j += 1
                if j >= len(lines) or "</table>" not in lines[j]:
                    # 超上限未闭合：MinerU 表格碎片而非倒置页特征，跳过开口行，
                    # 从 start+1 继续扫描，避免漏掉块内后续真正的乱码开口行
                    i = start + 1
                    continue
                end = j
                text = "\n".join(lines[start:end + 1])
                if _PAGE_LABEL_RE.search(text) or "问：" in text or "问:" in text:
                    m = _PAGE_LABEL_RE.search(text)
                    issues.append({
                        "md_file": md.name,
                        "page_label": m.group(0) if m else "",
                        "start_line": start,
                        "end_line": end,
                        "preview": re.sub(r"<[^>]+>", " ", text)[:120].strip(),
                    })
                i = end + 1
            else:
                i += 1
    return issues


def extract_single_page(pdf_path: Path, page_no: int, out_path: Path) -> Path:
    """抽取 PDF 单页（1 基）为独立 PDF 文件（供单页重转提交 MinerU）"""
    doc = fitz.open(pdf_path)
    try:
        if not 1 <= page_no <= len(doc):
            raise ValueError(f"页码 {page_no} 超出范围（共 {len(doc)} 页）")
        out = fitz.open()
        out.insert_pdf(doc, from_page=page_no - 1, to_page=page_no - 1)
        out.save(out_path)
        out.close()
        return out_path
    finally:
        doc.close()


def splice_md_block(md_path: Path, start_line: int, end_line: int, new_text: str) -> None:
    """用新文本替换 md 的 [start_line, end_line] 行区间（0 基含两端）"""
    lines = md_path.read_text(encoding="utf-8").splitlines()
    if not (0 <= start_line <= end_line < len(lines)):
        raise ValueError(f"行区间 [{start_line}, {end_line}] 超出范围（共 {len(lines)} 行）")
    lines[start_line:end_line + 1] = [new_text]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(f"[md拼接] {md_path.name}: 行 {start_line}-{end_line} 已替换（{len(new_text)} 字符）")


def invalidate_evidence_for_source(case_path: Path, md_filename: str) -> list[str]:
    """删除源自指定 md 的全部证据条目（含证据 md 与摘要缓存）

    断点续传机制（case_manager.py 提取入口按 index.json 的 source 跳过已完成文件）
    会在下次提取时自动重跑该卷。Returns: 被移除的证据名列表。
    """
    import json
    evidence_dir = case_path / "evidence"
    index_file = evidence_dir / "index.json"
    if not index_file.exists():
        return []
    idx = json.loads(index_file.read_text(encoding="utf-8"))
    items = idx.get("evidence", [])
    removed = [e for e in items if e.get("source") == md_filename]
    for e in removed:
        md_name = e.get("md_file", "")
        # 安全：index.json 可能被污染（md_file 含 ../），非纯文件名只移条目不删文件，
        # 防误删 evidence 目录外文件
        if not md_name or Path(md_name).name != md_name:
            logger.warning(f"[证据失效] 跳过非法 md_file（含路径分隔符）: {md_name}")
            continue
        md_file = evidence_dir / md_name
        if md_file.exists():
            md_file.unlink()
        stem = Path(md_name).stem
        for suffix in (".md", ".meta.json"):
            cache = evidence_dir / "summaries" / f"{stem}{suffix}"
            if cache.exists():
                cache.unlink()
    idx["evidence"] = [e for e in items if e.get("source") != md_filename]
    index_file.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"[证据失效] {md_filename}: 移除 {len(removed)} 份证据，待重新提取")
    return [e.get("name", "") for e in removed]
