"""证据 MD 关键事实格式化测试

修复背景：key_facts 为 list 时模板直接插值，MD 里显示 Python 列表 repr
（['事实1', '事实2']），应渲染为编号列表。
"""
from case_manager import _format_key_facts


def test_list_renders_numbered_lines():
    assert _format_key_facts(["事实1", "事实2"]) == "1. 事实1\n2. 事实2"


def test_string_passthrough():
    assert _format_key_facts("事实1\n事实2") == "事实1\n事实2"


def test_empty_shows_placeholder():
    assert _format_key_facts([]) == "无"
    assert _format_key_facts(None) == "无"
    assert _format_key_facts("") == "无"
