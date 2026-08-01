import context_budget
from context_budget import (
    get_model_window, content_budget_chars, truncate_with_marker, fit_texts,
)


def test_model_window_mapping():
    assert get_model_window("deepseek-v4-pro") == 1000000
    assert get_model_window("kimi-k3") == 262144
    assert get_model_window("qwen3.5-plus") == 131072
    assert get_model_window("unknown-model") is None


def test_content_budget_chars(monkeypatch):
    monkeypatch.setattr(context_budget, "get_context_limit", lambda: 250000)
    assert content_budget_chars() == int((250000 - 38000) * 1.35)


def test_truncate_with_marker():
    text = "x" * 1000
    out = truncate_with_marker(text, 100, "测试证据")
    assert out.startswith("x" * 100)
    assert "已截断" in out and "1000" in out and "测试证据" in out


def test_truncate_noop_when_fits():
    assert truncate_with_marker("短文本", 100) == "短文本"


def test_fit_texts_high_priority_never_truncated():
    texts = [
        {"label": "起诉书", "text": "高" * 800, "priority": 0},
        {"label": "次要材料", "text": "低" * 800, "priority": 2},
    ]
    out = fit_texts(texts, 1000)
    assert "高" * 800 in out           # 高优先级完整保留
    assert "已截断" in out              # 低优先级被截断
    assert "低" * 800 not in out


def test_fit_texts_mid_proportional():
    texts = [
        {"label": "证据A", "text": "A" * 600, "priority": 1},
        {"label": "证据B", "text": "B" * 600, "priority": 1},
    ]
    out = fit_texts(texts, 1000)
    assert "已截断" in out
    assert "证据A" in out and "证据B" in out  # 两个标签都在


def test_fit_texts_all_fits():
    texts = [{"label": "A", "text": "短", "priority": 1}]
    assert "已截断" not in fit_texts(texts, 1000)
