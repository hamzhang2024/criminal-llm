"""缓存统计可用性标记：Ollama 等不返回缓存字段的供应商，界面应显示"不适用"而非误导性的 0%

背景：2026-08-23 用户反馈"缓存命中率都是 0%，数据来源有问题吧"——
实际是用量统计对 Ollama 永远 0%（其 usage 只有 prompt/completion/total 三个字段）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from llm_client import get_cache_stats, reset_cache_stats, _record_process_llm_stats


def test_unsupported_when_no_cache_fields():
    """Ollama 风格 usage（仅 3 个字段）→ cache_supported=False"""
    reset_cache_stats()
    _record_process_llm_stats(100, 10, 0, usage={
        "prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110,
    })
    stats = get_cache_stats()
    assert stats["cache_supported"] is False
    assert stats["hit_rate"] == 0


def test_supported_with_deepseek_flat_fields():
    """DeepSeek 风格（即使命中 0 也有 hit/miss 字段）→ True"""
    reset_cache_stats()
    _record_process_llm_stats(100, 10, 0, usage={
        "prompt_tokens": 100, "completion_tokens": 10,
        "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 100,
    })
    assert get_cache_stats()["cache_supported"] is True


def test_supported_with_nested_details():
    """千问/豆包嵌套形态 prompt_tokens_details.cached_tokens → True"""
    reset_cache_stats()
    _record_process_llm_stats(100, 10, 0, usage={
        "prompt_tokens": 100, "completion_tokens": 10,
        "prompt_tokens_details": {"cached_tokens": 0},
    })
    assert get_cache_stats()["cache_supported"] is True


def test_flag_sticky_across_calls():
    """一旦见过缓存字段就保持 True（供应商切换后不回退）"""
    reset_cache_stats()
    _record_process_llm_stats(100, 10, 0, usage={"prompt_cache_hit_tokens": 5})
    _record_process_llm_stats(100, 10, 0, usage={"prompt_tokens": 100})
    assert get_cache_stats()["cache_supported"] is True


def test_reset_clears_flag():
    """重置后标记清零"""
    reset_cache_stats()
    _record_process_llm_stats(100, 10, 0, usage={"prompt_cache_hit_tokens": 5})
    reset_cache_stats()
    assert get_cache_stats()["cache_supported"] is False
