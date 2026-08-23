import context_budget
from context_budget import (
    get_model_window, content_budget_chars, truncate_with_marker, fit_texts,
    compute_max_output_tokens,
)


def _mock_model(monkeypatch, model: str):
    """固定配置中的模型名，让输出预留可预测"""
    monkeypatch.setattr(
        context_budget, "get_config_value",
        lambda k, d="": model if k == "llm_model" else d,
    )


def test_model_window_mapping():
    assert get_model_window("deepseek-v4-pro") == 1000000
    assert get_model_window("deepseek-v4-flash") == 1000000
    assert get_model_window("deepseek-v3") == 128000
    assert get_model_window("kimi-k3") == 262144
    assert get_model_window("qwen3.5-plus") == 131072
    assert get_model_window("unknown-model") is None


def test_content_budget_chars(monkeypatch):
    monkeypatch.setattr(context_budget, "get_context_limit", lambda *a, **kw: 250000)
    _mock_model(monkeypatch, "deepseek-v4-flash")
    # 预留 = min(250000×0.8, 65536) + 8000 prompt 开销 = 73536
    assert content_budget_chars() == int((250000 - 73536) * 1.35)


def test_content_budget_chars_explicit_reserve(monkeypatch):
    """显式指定预留时保持旧行为"""
    monkeypatch.setattr(context_budget, "get_context_limit", lambda *a, **kw: 250000)
    assert content_budget_chars(38000) == int((250000 - 38000) * 1.35)


def test_budget_plus_output_never_exceeds_context(monkeypatch):
    """内容预算 + max_tokens + prompt 开销 ≤ 上下文上限（防 API 400 回归）

    复现 2026-08-06 生产环境提取失败：旧预留 38000 < max_tokens 65536，
    162000 tokens 分块 + 65536 输出 > 200000 上下文被 vLLM 拒绝。
    """
    monkeypatch.setattr(context_budget, "get_context_limit", lambda *a, **kw: 200000)
    _mock_model(monkeypatch, "deepseek-v4-flash-0731")
    budget_tokens = content_budget_chars() / context_budget.CHARS_PER_TOKEN
    max_output = compute_max_output_tokens(200000, "deepseek-v4-flash-0731")
    total = budget_tokens + max_output + context_budget.PROMPT_OVERHEAD_TOKENS
    assert total <= 200000


def test_content_budget_chars_floor(monkeypatch):
    """小配置时预算不低于 30000 字符，杜绝负值"""
    monkeypatch.setattr(context_budget, "get_context_limit", lambda *a, **kw: 32000)
    assert content_budget_chars() == 30000


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
