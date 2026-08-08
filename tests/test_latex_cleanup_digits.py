"""后处理吞数字缺陷回归测试（实测发现：规则 8 吃掉 <td> 后的金额）"""
from paddleocr_remote import _clean_latex_markup


def test_table_cell_amount_preserved():
    """表格单元格中的金额必须完整保留"""
    text = "<td>8,051</td><td>25,578</td><td>-10,404</td>"
    assert _clean_latex_markup(text) == text


def test_table_cell_plain_digit_preserved():
    """单元格内容为纯数字时保留"""
    text = "<td>8</td><td>0</td><td>14</td>"
    assert _clean_latex_markup(text) == text


def test_noise_still_removed():
    """原目标噪声 >数字>>-) 仍被清理"""
    assert _clean_latex_markup("正文>12>>-)正文") == "正文正文"


def test_inline_gt_digit_without_trailing_gt_preserved():
    """无尾 > 的 >数字 不是目标噪声，保留（误伤面收窄的边界锁定）"""
    assert ">5人" in _clean_latex_markup("见证人>5人在场")
