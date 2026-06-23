"""
llm_client_config 单元测试

测试目标：
1. _estimate_context_limit - 模型上下文限制估算（精确+模糊匹配）
2. get_model_context_limit - 上下文限制+处理策略（用户指定优先）
3. check_data_size_warning - 数据量警告判断

query_model_info_from_api 为异步 HTTP，不在此测。
"""

import pytest

from llm_client_config import (
    _estimate_context_limit,
    check_data_size_warning,
    get_model_context_limit,
)


class TestEstimateContextLimit:
    """模型上下文限制估算"""

    @pytest.mark.parametrize("model,expected", [
        # 精确匹配
        ("gemini-1.5-pro", 1_000_000),
        ("deepseek-v3", 1_000_000),
        ("claude-opus-4", 1_000_000),
        ("gemini-1.5-flash", 500_000),
        ("qwen-max", 200_000),
        ("qwen-plus", 200_000),
        ("gpt-4o", 200_000),
        ("gpt-4", 128_000),
        ("qwen-turbo", 128_000),
    ])
    def test_exact_match(self, model, expected):
        assert _estimate_context_limit(model) == expected

    @pytest.mark.parametrize("model,expected", [
        # 模糊匹配（模型名包含关键词）
        ("gemini-2.0-pro-preview", 1_000_000),
        ("deepseek-v3-chat", 1_000_000),
        ("claude-3.5-sonnet-2024", 500_000),
        ("my-qwen-max-custom", 200_000),
        ("gpt-4-turbo-preview", 128_000),
    ])
    def test_fuzzy_match(self, model, expected):
        assert _estimate_context_limit(model) == expected

    def test_unknown_model_defaults_128k(self):
        assert _estimate_context_limit("totally-unknown-model") == 128_000

    def test_case_insensitive(self):
        assert _estimate_context_limit("QWEN-MAX") == 200_000
        assert _estimate_context_limit("GPT-4O") == 200_000


class TestGetModelContextLimit:
    """上下文限制 + 处理策略"""

    def test_user_specified_takes_priority(self):
        """用户手动指定优先于模型估算"""
        info = get_model_context_limit("qwen-max", user_specified_limit=500_000)
        assert info["limit"] == 500_000
        assert info["is_estimated"] is False

    def test_user_specified_zero_ignored(self):
        """user_specified_limit=0 或负数时回退到估算"""
        info = get_model_context_limit("qwen-max", user_specified_limit=0)
        assert info["limit"] == 200_000

    def test_million_context_strategy(self):
        info = get_model_context_limit("gemini-1.5-pro")
        assert info["limit_k"] == "1M+"
        assert info["strategy"] == "完整模式"
        assert info["warning"] is None
        assert info["small_case_limit"] == 0

    def test_500k_context_strategy(self):
        info = get_model_context_limit("gemini-1.5-flash")
        assert info["strategy"] == "标准模式"
        assert info["warning"]  # 有警告
        assert info["small_case_limit"] > 0

    def test_200k_context_strategy(self):
        info = get_model_context_limit("qwen-max")
        assert info["strategy"] == "精简模式"
        assert info["limit_k"] == "200k"
        assert info["warning"]

    def test_128k_context_strategy(self):
        info = get_model_context_limit("gpt-4")
        assert info["strategy"] == "小案件模式"
        assert info["limit_k"] == "128k"
        assert info["warning"]

    def test_unknown_model_estimated_flag(self):
        info = get_model_context_limit("unknown-model")
        assert info["is_estimated"] is True

    def test_known_model_not_estimated(self):
        info = get_model_context_limit("qwen-max")
        assert info["is_estimated"] is False


class TestCheckDataSizeWarning:
    """数据量警告判断"""

    def test_small_data_no_warning(self):
        """数据量小于 small_case_limit 无警告"""
        model_info = get_model_context_limit("qwen-max")  # small_case_limit=60000
        result = check_data_size_warning(30000, model_info)
        assert result["need_warning"] is False
        assert result["warning_level"] == "none"
        assert result["can_proceed"] is True

    def test_large_data_triggers_warning(self):
        """数据量超过 small_case_limit 触发警告"""
        model_info = get_model_context_limit("qwen-max")  # 200k 模型有警告
        result = check_data_size_warning(100000, model_info)
        assert result["need_warning"] is True
        assert result["warning_level"] == "info"
        assert "100k" in result["warning_message"]

    def test_million_model_no_warning_regardless_of_size(self):
        """1M+ 模型无警告信息，大数据也不警告"""
        model_info = get_model_context_limit("gemini-1.5-pro")
        assert model_info["warning"] is None
        result = check_data_size_warning(500000, model_info)
        assert result["need_warning"] is False

    def test_can_proceed_always_true(self):
        """始终可以继续（警告不阻断）"""
        model_info = get_model_context_limit("gpt-4")
        result = check_data_size_warning(999999, model_info)
        assert result["can_proceed"] is True

    def test_warning_message_contains_data_size(self):
        model_info = get_model_context_limit("qwen-plus")
        result = check_data_size_warning(150000, model_info)
        assert "150k" in result["warning_message"]
