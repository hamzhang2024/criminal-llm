"""进程级缓存命中率统计 + /api/llm/cache-stats 端点测试

验证：
- chat/completions 路径（prompt_cache_hit/miss_tokens）计入进程级累计
- 豆包 Responses 路径（cached_tokens）同样计入进程级累计
- get_cache_stats() 的 hit_rate 计算与无调用时的默认值
- reset_cache_stats() 重置
- GET /api/llm/cache-stats 返回结构正确
"""
import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

import config_manager
import llm_client
from llm_client import LLMClient

DOUBAO_URL = "https://ark.cn-beijing.volces.com/api/v3"
ALI_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

MESSAGES = [
    {"role": "system", "content": "固定规则"},
    {"role": "user", "content": "案件材料……本次任务指令"},
]


@pytest.fixture(autouse=True)
def _reset_stats():
    """每个用例前后重置进程级累计器，避免相互污染"""
    llm_client.reset_cache_stats()
    yield
    llm_client.reset_cache_stats()


class FakeResponse:
    """模拟 httpx.Response 的最小接口"""

    def __init__(self, data=None, status_code=200):
        self._data = data or {}
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=self
            )


class FakeAsyncClient:
    """按 URL 后缀路由到预设响应"""

    def __init__(self, routes):
        self._routes = routes

    async def post(self, url, json=None, headers=None):
        for suffix, handler in self._routes.items():
            if url.endswith(suffix):
                return handler
        raise AssertionError(f"未预设的 URL: {url}")


def _make_client(monkeypatch, base_url, fake_client):
    """构造 LLMClient 并替换内部 httpx.AsyncClient 为假客户端"""
    monkeypatch.setattr(config_manager, "load_config", lambda: {
        "llm_api_key": "test-key",
        "llm_base_url": base_url,
        "llm_model": "doubao-seed-1-6",
    })
    LLMClient._config_cache = None
    client = LLMClient()
    client.client = fake_client
    return client


def test_empty_stats_default():
    """无调用时：全部归零，hit_rate 为 0"""
    stats = llm_client.get_cache_stats()
    assert stats == {"hit_tokens": 0, "miss_tokens": 0, "hit_rate": 0, "calls": 0}


def test_chat_completions_path_accumulates(monkeypatch):
    """chat/completions 路径：prompt_cache_hit/miss_tokens 计入进程级累计"""
    fake = FakeAsyncClient({
        "/chat/completions": FakeResponse({
            "choices": [{
                "message": {"role": "assistant", "content": "结论"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_cache_hit_tokens": 600, "prompt_cache_miss_tokens": 400},
        }),
    })
    client = _make_client(monkeypatch, ALI_URL, fake)

    asyncio.run(client.chat(MESSAGES))
    asyncio.run(client.chat(MESSAGES))

    stats = llm_client.get_cache_stats()
    assert stats["hit_tokens"] == 1200
    assert stats["miss_tokens"] == 800
    assert stats["calls"] == 2
    assert stats["hit_rate"] == 0.6


def test_doubao_responses_path_accumulates(monkeypatch):
    """豆包 Responses 路径：cached_tokens 计入进程级累计"""
    fake = FakeAsyncClient({
        "/responses": FakeResponse({
            "content": "带缓存命中",
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 20,
                "cached_tokens": 800,
            },
            "caching": {"type": "enabled", "prefix": True},
        }),
    })
    client = _make_client(monkeypatch, DOUBAO_URL, fake)

    asyncio.run(client.chat(MESSAGES))

    stats = llm_client.get_cache_stats()
    assert stats["hit_tokens"] == 800
    assert stats["miss_tokens"] == 200
    assert stats["calls"] == 1
    assert stats["hit_rate"] == 0.8


def test_stats_accumulate_across_client_instances(monkeypatch):
    """进程级累计跨 LLMClient 实例（区别于实例级统计）"""
    fake = FakeAsyncClient({
        "/chat/completions": FakeResponse({
            "choices": [{
                "message": {"role": "assistant", "content": "结论"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_cache_hit_tokens": 100, "prompt_cache_miss_tokens": 100},
        }),
    })
    client1 = _make_client(monkeypatch, ALI_URL, fake)
    asyncio.run(client1.chat(MESSAGES))

    # 新实例（模拟客户端重建）继续累计
    client2 = _make_client(monkeypatch, ALI_URL, fake)
    asyncio.run(client2.chat(MESSAGES))

    stats = llm_client.get_cache_stats()
    assert stats["calls"] == 2
    assert stats["hit_tokens"] == 200


def test_no_cache_fields_not_counted(monkeypatch):
    """响应无缓存字段时不计入累计"""
    fake = FakeAsyncClient({
        "/chat/completions": FakeResponse({
            "choices": [{
                "message": {"role": "assistant", "content": "结论"},
                "finish_reason": "stop",
            }],
            "usage": {},
        }),
    })
    client = _make_client(monkeypatch, ALI_URL, fake)

    asyncio.run(client.chat(MESSAGES))

    stats = llm_client.get_cache_stats()
    assert stats["calls"] == 0
    assert stats["hit_rate"] == 0


def test_cache_stats_endpoint():
    """GET /api/llm/cache-stats 返回结构正确"""
    from main import app

    llm_client._record_process_cache_stats(300, 100)

    client = TestClient(app)
    resp = client.get("/api/llm/cache-stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["hit_tokens"] == 300
    assert data["miss_tokens"] == 100
    assert data["calls"] == 1
    assert data["hit_rate"] == 0.75
