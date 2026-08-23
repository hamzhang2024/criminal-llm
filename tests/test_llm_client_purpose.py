"""按用途分离的 LLM 客户端缓存：证据提取与案卷分析必须用各自分配的模型

背景（2026-08-23 实际事故）：全局单例 _client 的 purpose 由首次调用决定且终身不变。
用户在设置页验证完 27B 分析模型后立即开始证据提取，提取全程错用 27B
（9B 提取模型被旁路），14 卷案卷每卷耗时 20-30 分钟。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import llm_client
from llm_client import get_llm_client


_FAKE_CONFIG = {
    "llm_profiles": [
        {"id": "fast9b", "base_url": "http://localhost:11434/v1", "api_key": "x",
         "model": "qwythos-9b-64k", "context_limit": 65536, "read_timeout": 600},
        {"id": "big27b", "base_url": "http://localhost:11434/v1", "api_key": "x",
         "model": "qwen3.8-27b-32k", "context_limit": 32768, "read_timeout": 600},
    ],
    "llm_profile_evidence": "fast9b",
    "llm_profile_analysis": "big27b",
}


def _reset(monkeypatch):
    """重置客户端缓存并注入伪配置"""
    monkeypatch.setattr(llm_client, "_clients", {})
    monkeypatch.setattr(llm_client.LLMClient, "_config_cache", None)
    import config_manager
    monkeypatch.setattr(config_manager, "load_config", lambda: dict(_FAKE_CONFIG))


def test_evidence_and_analysis_get_own_models(monkeypatch):
    """核心回归：analysis 先调用后，evidence 仍须拿到 9B 提取模型（不被单例污染）"""
    _reset(monkeypatch)
    # 复现事故顺序：先 analysis（设置页验证/分析页），后 evidence（开始提取）
    analysis_client = get_llm_client("analysis")
    evidence_client = get_llm_client("evidence")
    assert analysis_client.model == "qwen3.8-27b-32k"
    assert evidence_client.model == "qwythos-9b-64k"


def test_same_purpose_reuses_instance(monkeypatch):
    """同用途复用实例（连接池复用，不重复建 httpx client）"""
    _reset(monkeypatch)
    assert get_llm_client("evidence") is get_llm_client("evidence")


def test_default_purpose_is_evidence(monkeypatch):
    """无参调用 = evidence（extraction_framework 等裸调用方的隐含契约）"""
    _reset(monkeypatch)
    assert get_llm_client().model == "qwythos-9b-64k"
