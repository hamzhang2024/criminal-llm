"""上下文统一事实源 + 百分比分块

背景（2026-08-23 冯叶飞案）：profile 配了 64K 窗口，但分块读全局旧字段
model_context_limit=250000 → 按 176K tokens 切块/整卷塞入 → Ollama 400
（182841 tokens exceeds 65536）。两类修复：
A. profile 的 context_limit 成为唯一事实源（回退：全局旧字段 → 250K 默认）
B. 分块 = 窗口×25%（封顶 200K），且满足不变式：块 + 输出预留 + 固定开销 ≤ 窗口
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import context_budget
import llm_client
from llm_client import LLMClient


_PROFILES = {
    "llm_profiles": [
        {"id": "fast9b", "base_url": "http://x/v1", "api_key": "k",
         "model": "qwythos-9b-64k", "context_limit": 65536, "read_timeout": 600},
        {"id": "big27b", "base_url": "http://x/v1", "api_key": "k",
         "model": "qwen3.8-27b-32k", "context_limit": 32768, "read_timeout": 600},
    ],
    "llm_profile_evidence": "fast9b",
    "llm_profile_analysis": "big27b",
    "model_context_limit": 250000,  # 全局旧字段（应被 profile 覆盖）
}


def _patch_config(monkeypatch, cfg):
    import config_manager
    monkeypatch.setattr(config_manager, "load_config", lambda: dict(cfg))
    monkeypatch.setattr(llm_client.LLMClient, "_config_cache", None)
    monkeypatch.setattr(llm_client.LLMClient, "_config_cache_time", 0)
    monkeypatch.setattr(llm_client, "_clients", {})


# ── A：事实源 ──

def test_client_context_limit_from_profile(monkeypatch):
    """客户端从各自用途的 profile 拿 context_limit，而非全局旧字段"""
    _patch_config(monkeypatch, _PROFILES)
    from llm_client import get_llm_client
    assert get_llm_client("evidence").context_limit == 65536
    assert get_llm_client("analysis").context_limit == 32768


def test_context_limit_fallback_to_global(monkeypatch):
    """无 profile 的老配置：回退全局 model_context_limit"""
    _patch_config(monkeypatch, {"llm_model": "qwen3.5-plus", "model_context_limit": 250000})
    from llm_client import get_llm_client
    assert get_llm_client("evidence").context_limit == 250000


def test_context_limit_fallback_default(monkeypatch):
    """啥都没有：默认 250000"""
    _patch_config(monkeypatch, {})
    from llm_client import get_llm_client
    assert get_llm_client("evidence").context_limit == 250000


def test_get_context_limit_by_purpose(monkeypatch):
    """context_budget.get_context_limit(purpose) 按用途解析 profile"""
    import config_manager
    monkeypatch.setattr(config_manager, "get_config_value",
                        lambda k, d=None: {"model_context_limit": "250000"}.get(k, d))
    monkeypatch.setattr(config_manager, "load_config", lambda: dict(_PROFILES))
    assert context_budget.get_context_limit("evidence") == 65536
    assert context_budget.get_context_limit("analysis") == 32768
    # 不传用途 = 旧全局字段（向后兼容）
    assert context_budget.get_context_limit() == 250000


# ── B：百分比分块 ──

def test_chunk_tokens_matches_proven_sizes():
    """已验证档位移扎：1M→200K（封顶）、250K→约50-62K"""
    assert context_budget.compute_input_chunk_tokens(1000000, "deepseek-v4-flash") == 200000
    chunk_250k = context_budget.compute_input_chunk_tokens(250000, "qwen3.5-plus")
    assert 45000 <= chunk_250k <= 65000


def test_chunk_tokens_small_windows_safe():
    """小窗口安全：64K/32K 窗口的块大小不越界"""
    for window in (65536, 32768):
        chunk = context_budget.compute_input_chunk_tokens(window, "qwythos-9b-64k")
        reserve = context_budget._effective_reserve(window, "qwythos-9b-64k")
        assert chunk + reserve + context_budget.PROMPT_OVERHEAD_TOKENS <= window, \
            f"窗口 {window}: 块 {chunk} + 预留 {reserve} + 开销越界"
        assert chunk >= 4000  # 再小也有基本内容量


def test_chunk_invariant_all_windows():
    """不变式：块 + 输出预留 + 开销 ≤ 窗口（所有典型窗口）"""
    for window in (32768, 65536, 131072, 250000, 1000000):
        chunk = context_budget.compute_input_chunk_tokens(window, "deepseek-v4-flash")
        reserve = context_budget._effective_reserve(window, "deepseek-v4-flash")
        assert chunk + reserve + context_budget.PROMPT_OVERHEAD_TOKENS <= window
