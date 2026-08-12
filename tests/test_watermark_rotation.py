"""旋转水印混合流移除测试（实测失效复现：/Fm1 Do 与水印同流时水印残留）"""
import fitz
from watermark_remover import remove_rotation_watermark, filter_rotation_watermark_blocks

# 真实案卷混合流的简化结构：主内容 /Fm1 Do + 2 个旋转水印瓦片（q 在旋转行前一行）
MIXED_STREAM = (
    "q\n1 0 0 1 0 0 cm\nq\n1 0 0 1 0 0 cm\n/Fm1 Do\nQ\nQ\n"
    "q\n/Gs1 gs\n0.25098 0.25098 0.25098 rg\n"
    "q\n0.70711 -0.70711 0.70711 0.70711 -284.12 -284.12 cm\nBT\n/F1 29.98 Tf\n"
    "<0012001400130011001300130011001200170012001100150013001a001a001300130000432b3cf8>Tj\nET\nQ\n"
    "q\n0.70711 -0.70711 0.70711 0.70711 142.06 -284.12 cm\nBT\n/F1 29.98 Tf\n"
    "<0012001400130011001300130011001200170012001100150013001a001a001300130000432b3cf8>Tj\nET\nQ\n"
    "Q\n"
)


def _make_pdf_with_stream(stream_text: str) -> fitz.Document:
    """构造单页 PDF 并替换其内容流为指定文本"""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "x")  # 确保存在内容流
    xref = page.get_contents()[0]
    doc.update_stream(xref, stream_text.encode('latin-1'))
    return doc


def test_filter_preserves_fm1_and_balances_q():
    """混合流块过滤：水印块被移除，/Fm1 Do 保留，q/Q 配平"""
    filtered, removed_bytes, blocks = filter_rotation_watermark_blocks(MIXED_STREAM)
    assert blocks == 2
    assert '/Fm1 Do' in filtered
    assert '0012001400' not in filtered
    lines = [l.strip() for l in filtered.split('\n')]
    assert lines.count('q') == lines.count('Q'), 'q/Q 必须配平'


def test_remove_rotation_watermark_on_mixed_pdf():
    """端到端：含 /Fm1 Do + 旋转水印块的 PDF，水印被移除且主内容保留"""
    doc = _make_pdf_with_stream(MIXED_STREAM)
    remove_rotation_watermark(doc)
    content = doc.xref_stream(doc[0].get_contents()[0]).decode('latin-1')
    assert '0012001400' not in content, '水印文字块应被移除'
    assert '/Fm1 Do' in content, '主内容 /Fm1 Do 必须保留'
    lines = [l.strip() for l in content.split('\n')]
    assert lines.count('q') == lines.count('Q'), 'q/Q 必须配平'
    doc.close()


def test_pure_watermark_stream_still_cleared():
    """纯水印流（无 Do 调用 + rg + Gs）：整个清空（既有行为不回归）"""
    pure = (
        "/Gs1 gs\n0.25098 0.25098 0.25098 rg\n"
        "q\n0.70711 -0.70711 0.70711 0.70711 -284.12 -284.12 cm\nBT\n/F1 29.98 Tf\n"
        "<0012001400130011001300130011001200170012001100150013001a001a001300130000432b3cf8>Tj\nET\nQ\n"
    )
    doc = _make_pdf_with_stream(pure)
    remove_rotation_watermark(doc)
    raw = doc.xref_stream(doc[0].get_contents()[0])
    assert not raw or '0012001400' not in raw.decode('latin-1', errors='ignore')
    doc.close()
