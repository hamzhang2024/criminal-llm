"""_fold_consecutive_images 正则扩展测试（支持 <img> 标签）"""
from pdf_to_md import _fold_consecutive_images


def test_markdown_images_still_folded():
    """MinerU 格式 ![]() 输入：修复前后行为一致（锁定回归）"""
    text = "前文\n\n![](a.jpg)\n\n![](b.jpg)\n\n后文"
    folded, count = _fold_consecutive_images(text)
    assert count == 1
    assert "<details>" in folded
    assert "![](a.jpg)" in folded and "![](b.jpg)" in folded
    assert "前文" in folded and "后文" in folded


def test_img_tags_folded():
    """PaddleOCR 格式 <img> 连续标签可折叠"""
    text = '前文\n\n<img src="./x_images/1.jpg">\n\n<img src="./x_images/2.jpg">\n\n后文'
    folded, count = _fold_consecutive_images(text)
    assert count == 1
    assert "<details>" in folded
    assert '<img src="./x_images/1.jpg">' in folded


def test_img_tag_with_following_text_not_folded_together():
    """<img> 标签与识别文字混合时，文字行不打断已折叠图片块之外的结构"""
    text = '<img src="./x_images/1.jpg">\n\n识别出的凭证文字\n\n<img src="./x_images/2.jpg">'
    folded, count = _fold_consecutive_images(text)
    # 两组图片被文字分隔，各自成块
    assert count == 2
    assert "识别出的凭证文字" in folded


def test_mineru_output_byte_identical():
    """MinerU 典型产物修复前后输出逐字节一致（用旧逻辑预计算期望值）"""
    text = "段落一\n\n![](images/a.jpg)\n\n![](images/b.jpg)\n\n![](images/c.jpg)\n\n段落二"
    # 期望值按旧逻辑实际行为预计算：折叠块内图片行之间不保留空行
    expected = (
        "段落一\n\n"
        "<details><summary>📎 签名/印章图片（共 3 张，点击展开）</summary>\n\n"
        "![](images/a.jpg)\n![](images/b.jpg)\n![](images/c.jpg)\n"
        "</details>\n\n段落二"
    )
    folded, count = _fold_consecutive_images(text)
    assert count == 1
    assert folded == expected
