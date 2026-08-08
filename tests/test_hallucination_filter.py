"""幻觉表格过滤器误伤修复测试（真实台账同列重复不应被删）"""
from pdf_to_md import _strip_hallucinated_tables


def _make_ledger_table(rows: int) -> str:
    """构造真实台账：交易对方同列重复，但单号/金额逐行不同"""
    body = "".join(
        f"<tr><td>2400{i:02d}</td><td>3G时尚莫尼卡</td><td>唐某</td>"
        f"<td>{8000 + i},051</td><td>未付</td></tr>"
        for i in range(rows)
    )
    return f"<table><tr><td>单号</td><td>对方</td><td>经手</td><td>金额</td><td>状态</td></tr>{body}</table>"


def test_real_ledger_preserved():
    """同列重复 ≥5 次但逐行内容不同的真实台账必须保留（实测误伤回归）"""
    text = f"前文\n\n{_make_ledger_table(10)}\n\n后文"
    result = _strip_hallucinated_tables(text)
    assert "3G时尚莫尼卡" in result
    assert "幻觉表格已移除" not in result


def test_identical_rows_hallucination_removed():
    """整行完全重复 ≥5 次（VLM 循环幻觉特征）仍判定幻觉并移除"""
    row = "<tr><td>项目</td><td>3G时尚莫尼卡</td><td>8,051</td><td>未付</td></tr>"
    table = f"<table>{row * 6}</table>"
    result = _strip_hallucinated_tables(f"前文\n\n{table}\n\n后文")
    assert "幻觉表格已移除" in result
    assert "前文" in result and "后文" in result


def test_small_table_untouched():
    """行数不足 5 的小表格不检测"""
    table = _make_ledger_table(3)
    assert _strip_hallucinated_tables(table) == table


def test_paddle_style_table_attributes_supported():
    """PaddleOCR 风格（<table border=1 ...> / <td style=...>）也纳入行级检测"""
    row = ("<tr><td style='text-align: center;'>项目</td>"
           "<td style='text-align: center;'>幻觉内容</td></tr>")
    table = f"<table border=1 style='margin: auto;'>{row * 6}</table>"
    result = _strip_hallucinated_tables(table)
    assert "幻觉表格已移除" in result
