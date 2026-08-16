"""页面旋转干预：缩略图生成 + 页面旋转"""
import os

import fitz
import pytest

from page_rotation import generate_pdf_thumbnails, rotate_pdf_page

# 候选 CJK 字体：fitz 内置 helv 不支持中文（插入后提取为乱码），
# 文本断言类测试需嵌入真实 CJK 字体；找不到则回退默认字体（其余测试不依赖文本提取）
_CJK_FONT_CANDIDATES = (
    "/System/Library/Fonts/Hiragino Sans GB.ttc",                  # macOS
    "C:/Windows/Fonts/msyh.ttc",                                   # Windows 微软雅黑
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",      # Linux Noto
)


def _make_pdf(path, pages=3):
    """生成带文字的多页 PDF（fitz）"""
    fontfile = next((p for p in _CJK_FONT_CANDIDATES if os.path.exists(p)), None)
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)
        if fontfile:
            page.insert_text((72, 72), f"第{i+1}页内容 测试文字", fontname="cjk", fontfile=fontfile)
        else:
            page.insert_text((72, 72), f"第{i+1}页内容 测试文字")
    doc.save(path)
    doc.close()


def test_thumbnails_generated_and_cached(tmp_path):
    """缩略图：逐页生成 PNG 并缓存（第二次调用不重复渲染）"""
    pdf = tmp_path / "test.pdf"
    _make_pdf(pdf, pages=3)
    cache = tmp_path / "thumb"

    result = generate_pdf_thumbnails(pdf, cache, width=200)
    assert len(result) == 3
    assert result[0]["page"] == 1
    assert (cache / "page_1.png").exists()
    assert (cache / "page_3.png").exists()

    # 第二次调用：删除 page_2.png 模拟缓存部分失效，只重渲该页
    (cache / "page_2.png").unlink()
    result2 = generate_pdf_thumbnails(pdf, cache, width=200)
    assert len(result2) == 3
    assert (cache / "page_2.png").exists()


def test_rotate_page_180(tmp_path):
    """旋转 180°：rotation 从 0 变 180，增量保存后重开仍生效"""
    pdf = tmp_path / "test.pdf"
    _make_pdf(pdf, pages=2)

    new_rot = rotate_pdf_page(pdf, 1, 180)
    assert new_rot == 180
    doc = fitz.open(pdf)
    assert doc[0].rotation == 180
    assert doc[1].rotation == 0  # 其他页不受影响
    doc.close()


def test_rotate_accumulates(tmp_path):
    """连续两次 90° = 180°"""
    pdf = tmp_path / "test.pdf"
    _make_pdf(pdf)
    rotate_pdf_page(pdf, 1, 90)
    assert rotate_pdf_page(pdf, 1, 90) == 180


def test_thumb_cache_dir_naming():
    """缩略图缓存目录命名：cache/thumb/{case_id}/{pdf_stem}/（供端点与前端 URL 一致）"""
    from page_rotation import thumb_cache_dir_for
    from pathlib import Path
    d = thumb_cache_dir_for(Path("/data/cache"), "case_abc", "第2卷_去水印.pdf")
    assert d == Path("/data/cache/thumb/case_abc/第2卷_去水印")


def test_rotate_invalid_args(tmp_path):
    """非法角度/页码抛 ValueError"""
    pdf = tmp_path / "test.pdf"
    _make_pdf(pdf, pages=1)
    with pytest.raises(ValueError):
        rotate_pdf_page(pdf, 1, 45)
    with pytest.raises(ValueError):
        rotate_pdf_page(pdf, 99, 90)


def test_detect_table_wrapped_transcript(tmp_path):
    """检测：含页码标记/问答的 <table> 块（MinerU 把倒置笔录页误判为表格）"""
    md_dir = tmp_path / "md"
    md_dir.mkdir()
    # 真实乱码样本（已脱敏简化）
    (md_dir / "第2卷_去水印.md").write_text(
        "# 讯问笔录\n\n正常内容。\n\n"
        "<table><tr><td>第6页共11页</td></tr><tr><td>装打了个电话讲了一下这个事情,之后泻叶无也给期拟打了电话</td></tr>"
        "<tr><td>问:(向其展示次嘉豪在收取团购五万块钱抵押款后的明细)通过明细来看,</td></tr></table>\n\n"
        "后续正常内容。\n", encoding="utf-8")
    (md_dir / "正常卷.md").write_text("# 卷内文书目录\n\n<table><tr><td>序号</td><td>标题</td></tr></table>\n", encoding="utf-8")

    from page_rotation import detect_md_issues
    issues = detect_md_issues(md_dir)
    assert len(issues) == 1
    assert issues[0]["md_file"] == "第2卷_去水印.md"
    assert issues[0]["page_label"] == "第6页共11页"
    assert issues[0]["start_line"] >= 0 and issues[0]["end_line"] >= issues[0]["start_line"]
    assert "泻叶无" in issues[0]["preview"]


def test_detect_ignores_normal_tables(tmp_path):
    """正常表格（卷内目录等）不误报"""
    md_dir = tmp_path / "md"
    md_dir.mkdir()
    (md_dir / "a.md").write_text("<table><tr><td>序号</td><td>责任者</td></tr><tr><td>1</td><td>告知书</td></tr></table>\n", encoding="utf-8")
    from page_rotation import detect_md_issues
    assert detect_md_issues(md_dir) == []


def test_detect_unclosed_table_not_false_positive(tmp_path):
    """未闭合 <table> 后跟 60 行正常笔录（含「问：」和页脚）→ 不误报（MinerU 表格碎片）"""
    md_dir = tmp_path / "md"
    md_dir.mkdir()
    body = ["<table>", "<tr><td>表格碎片</td></tr>"]
    for n in range(60):
        body.append(f"问：第{n}个问题？答：正常笔录内容。")
        body.append(f"第{n+1}页共60页")
    (md_dir / "a.md").write_text("\n".join(body) + "\n", encoding="utf-8")
    from page_rotation import detect_md_issues
    assert detect_md_issues(md_dir) == []


def test_detect_resumes_scan_after_unclosed_table(tmp_path):
    """未闭合 <table> 的块内后方存在一个真正的单行乱码块 → 后者仍被检出（start+1 继续扫描不错漏）"""
    md_dir = tmp_path / "md"
    md_dir.mkdir()
    body = ["<table>"]  # 未闭合的表格碎片
    body.extend(f"第{n}行正常笔录内容。" for n in range(60))
    body.append("<table><tr><td>第3页共9页</td></tr><tr><td>问：展示泻叶无相关明细</td></tr></table>")
    (md_dir / "a.md").write_text("\n".join(body) + "\n", encoding="utf-8")
    from page_rotation import detect_md_issues
    issues = detect_md_issues(md_dir)
    assert len(issues) == 1
    assert issues[0]["page_label"] == "第3页共9页"
    assert issues[0]["end_line"] == issues[0]["start_line"]  # 单行块


def test_detect_skips_unreadable_file(tmp_path):
    """单个损坏文件记 warning 后继续扫描其他文件"""
    md_dir = tmp_path / "md"
    md_dir.mkdir()
    (md_dir / "bad.md").write_bytes(b"\xff\xfe\x80\x81")
    (md_dir / "good.md").write_text(
        "<table><tr><td>第1页共2页</td></tr><tr><td>问：正常检出</td></tr></table>\n", encoding="utf-8")
    from page_rotation import detect_md_issues
    issues = detect_md_issues(md_dir)
    assert len(issues) == 1
    assert issues[0]["md_file"] == "good.md"


def test_extract_single_page(tmp_path):
    """抽取单页为新 PDF（供单页重转提交 MinerU）"""
    from page_rotation import extract_single_page
    pdf = tmp_path / "test.pdf"
    _make_pdf(pdf, pages=5)
    out = tmp_path / "page3.pdf"
    extract_single_page(pdf, 3, out)
    doc = fitz.open(out)
    assert len(doc) == 1
    assert "第3页内容" in doc[0].get_text()
    doc.close()


def test_splice_md_block(tmp_path):
    """md 拼接：新文本替换 [start_line, end_line] 行区间"""
    from page_rotation import splice_md_block
    md = tmp_path / "a.md"
    md.write_text("行0\n行1垃圾开始\n行2垃圾结束\n行3\n", encoding="utf-8")
    splice_md_block(md, 1, 2, "修复后的内容")
    assert md.read_text(encoding="utf-8") == "行0\n修复后的内容\n行3\n"


def test_invalidate_evidence_for_source(tmp_path):
    """证据失效：删除指定 source 的证据条目+证据md+摘要缓存，保留其他卷"""
    import json
    from page_rotation import invalidate_evidence_for_source
    case_path = tmp_path / "case"
    ev = case_path / "evidence"
    (ev / "summaries").mkdir(parents=True)
    idx = {"evidence": [
        {"name": "笔录A", "source": "第2卷_去水印.md", "md_file": "005_笔录A.md"},
        {"name": "笔录B", "source": "第3卷_去水印.md", "md_file": "030_笔录B.md"},
    ]}
    (ev / "index.json").write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    (ev / "005_笔录A.md").write_text("内容A", encoding="utf-8")
    (ev / "030_笔录B.md").write_text("内容B", encoding="utf-8")
    (ev / "summaries" / "005_笔录A.md").write_text("摘要A", encoding="utf-8")
    (ev / "summaries" / "005_笔录A.meta.json").write_text("{}", encoding="utf-8")

    removed = invalidate_evidence_for_source(case_path, "第2卷_去水印.md")
    assert removed == ["笔录A"]
    assert not (ev / "005_笔录A.md").exists()
    assert not (ev / "summaries" / "005_笔录A.md").exists()
    assert not (ev / "summaries" / "005_笔录A.meta.json").exists()
    assert (ev / "030_笔录B.md").exists()
    kept = json.loads((ev / "index.json").read_text(encoding="utf-8"))["evidence"]
    assert len(kept) == 1 and kept[0]["name"] == "笔录B"
