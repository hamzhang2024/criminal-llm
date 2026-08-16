"""豆包（火山方舟）Responses API 前缀缓存集成测试

验证 llm_client 在 base_url 指向火山方舟（volces.com）时：
- 走 /responses 端点并携带 caching prefix（不带 store：案卷材料不持久化到方舟服务器）
- 响应解析兼容扁平 content 形态与 output 列表形态
- 4xx 时本进程内降级 chat/completions 且不再尝试 /responses
- 非豆包 base_url 行为不回归（仍走 chat/completions）
- 缓存命中字段（usage.input_tokens_details.cached_tokens，顶层 cached_tokens 回退）
  并入会话级缓存命中率统计
"""
import asyncio

import httpx

import config_manager
from llm_client import LLMClient

DOUBAO_URL = "https://ark.cn-beijing.volces.com/api/v3"
ALI_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class FakeResponse:
    """模拟 httpx.Response 的最小接口"""

    def __init__(self, data=None, status_code=200):
        self._data = data or {}
        self.status_code = status_code
        self.text = "" if status_code < 400 else '{"error": {"message": "unsupported"}}'

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=self
            )


class FakeAsyncClient:
    """记录请求并按 URL 后缀路由到预设响应"""

    def __init__(self, routes):
        # routes: {url_suffix: FakeResponse | callable}
        self._routes = routes
        self.calls = []

    async def post(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        for suffix, handler in self._routes.items():
            if url.endswith(suffix):
                if callable(handler):
                    return handler(json)
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


MESSAGES = [
    {"role": "system", "content": "固定规则"},
    {"role": "user", "content": "案件材料……本次任务指令"},
]


def test_doubao_uses_responses_api_with_prefix_caching(monkeypatch):
    """豆包 base_url → 请求发往 /responses，body 含 caching prefix 且不带 store，messages 原样在 input"""
    fake = FakeAsyncClient({
        "/responses": FakeResponse({
            "id": "resp_001",
            "object": "response",
            "status": "completed",
            "content": "分析结论文本",
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
        }),
    })
    client = _make_client(monkeypatch, DOUBAO_URL, fake)

    result = asyncio.run(client.chat(MESSAGES))

    assert result == "分析结论文本"
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"] == f"{DOUBAO_URL}/responses"
    body = call["json"]
    assert body["caching"] == {"type": "enabled", "prefix": True}
    # store 会把案卷材料持久化到火山方舟服务器：不使用 previous_response_id 就必须不带
    assert "store" not in body or body["store"] is False
    assert body["input"] == MESSAGES  # messages 原样传递
    assert body["model"] == "doubao-seed-1-6"
    assert call["headers"]["Authorization"] == "Bearer test-key"


def test_responses_output_list_form(monkeypatch):
    """output 列表形态：取 role=assistant 项的 content[0].text"""
    fake = FakeAsyncClient({
        "/responses": FakeResponse({
            "id": "resp_002",
            "object": "response",
            "status": "completed",
            "output": [
                {"type": "reasoning", "id": "rs_1"},
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "列表形态结论"}],
                },
            ],
            "usage": {"input_tokens": 50, "output_tokens": 5},
        }),
    })
    client = _make_client(monkeypatch, DOUBAO_URL, fake)

    result = asyncio.run(client.chat(MESSAGES))

    assert result == "列表形态结论"


def test_doubao_4xx_falls_back_to_chat_completions(monkeypatch):
    """/responses 返回 4xx → 回退 chat/completions，且本进程后续调用不再尝试 /responses"""
    chat_payload = FakeResponse({
        "choices": [{
            "message": {"role": "assistant", "content": "降级路径结论"},
            "finish_reason": "stop",
        }],
        "usage": {},
    })
    fake = FakeAsyncClient({
        "/responses": FakeResponse(status_code=400),
        "/chat/completions": chat_payload,
    })
    client = _make_client(monkeypatch, DOUBAO_URL, fake)

    # 第一次调用：/responses 400 → 回退 chat/completions 成功
    result1 = asyncio.run(client.chat(MESSAGES))
    assert result1 == "降级路径结论"

    urls_after_first = [c["url"] for c in fake.calls]
    assert urls_after_first[0].endswith("/responses")
    assert any(u.endswith("/chat/completions") for u in urls_after_first)

    # 第二次调用：不再尝试 /responses
    calls_before = len(fake.calls)
    result2 = asyncio.run(client.chat(MESSAGES))
    assert result2 == "降级路径结论"
    new_urls = [c["url"] for c in fake.calls[calls_before:]]
    assert new_urls and all(u.endswith("/chat/completions") for u in new_urls)


def test_non_doubao_still_uses_chat_completions(monkeypatch):
    """非豆包 base_url → 仍走 chat/completions（现有行为不回归）"""
    fake = FakeAsyncClient({
        "/chat/completions": FakeResponse({
            "choices": [{
                "message": {"role": "assistant", "content": "百炼结论"},
                "finish_reason": "stop",
            }],
            "usage": {},
        }),
    })
    client = _make_client(monkeypatch, ALI_URL, fake)

    result = asyncio.run(client.chat(MESSAGES))

    assert result == "百炼结论"
    assert len(fake.calls) == 1
    assert fake.calls[0]["url"] == f"{ALI_URL}/chat/completions"
    body = fake.calls[0]["json"]
    assert body["messages"] == MESSAGES
    assert "caching" not in body


def test_doubao_cached_tokens_counted_in_stats(monkeypatch):
    """嵌套 usage.input_tokens_details.cached_tokens（火山方舟真实字段路径）并入会话级缓存命中率统计"""
    fake = FakeAsyncClient({
        "/responses": FakeResponse({
            "content": "带缓存命中",
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 20,
                "total_tokens": 1020,
                "input_tokens_details": {"cached_tokens": 800},
            },
            "caching": {"type": "enabled", "prefix": True},
        }),
    })
    client = _make_client(monkeypatch, DOUBAO_URL, fake)

    asyncio.run(client.chat(MESSAGES))

    stats = client.get_cache_stats()
    assert stats["hit_tokens"] == 800
    assert stats["miss_tokens"] == 200  # prompt_tokens - cached_tokens
    assert stats["total_requests"] == 1
    assert stats["hit_rate"] == 80.0


def test_doubao_cached_tokens_top_level_fallback(monkeypatch):
    """顶层 usage.cached_tokens 作回退兼容（旧形态/扁平化响应）仍被统计"""
    fake = FakeAsyncClient({
        "/responses": FakeResponse({
            "content": "顶层缓存字段",
            "usage": {
                "prompt_tokens": 500,
                "completion_tokens": 10,
                "total_tokens": 510,
                "cached_tokens": 300,
            },
        }),
    })
    client = _make_client(monkeypatch, DOUBAO_URL, fake)

    asyncio.run(client.chat(MESSAGES))

    stats = client.get_cache_stats()
    assert stats["hit_tokens"] == 300
    assert stats["miss_tokens"] == 200
