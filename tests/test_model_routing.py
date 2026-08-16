"""分层模型路由测试

验证：
- chat(model_override=X) 时请求 body 的 model 字段为 X，且实例配置不被修改
- llm_model_heavy 为空时 get_heavy_model() 返回 None（不启用分层，走默认模型）
- llm_model_heavy 非空时返回该模型名
- 最终产物调用点（pipeline 5a-5f、45a-45d 控辩对抗、engine 5C 六节）确实传入 model_override
"""
import asyncio
import re
from pathlib import Path

import config_manager
from llm_client import LLMClient

BACKEND_DIR = Path(__file__).parent.parent / "backend"


class FakeResponse:
    """模拟 httpx.Response 的最小接口"""

    def __init__(self, data):
        self._data = data
        self.status_code = 200
        self.text = ""

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


class FakeAsyncClient:
    """记录最后一次请求 body"""

    def __init__(self):
        self.calls = []

    async def post(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json})
        return FakeResponse({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})


def _make_client(monkeypatch, heavy_model=""):
    """构造 LLMClient：默认模型 flash，可配 heavy 模型，替换内部 HTTP 客户端为假桩"""
    monkeypatch.setattr(config_manager, "load_config", lambda: {
        "llm_api_key": "test-key",
        "llm_base_url": "https://api.deepseek.com",
        "llm_model": "deepseek-v4-flash",
        "llm_model_heavy": heavy_model,
    })
    LLMClient._config_cache = None
    client = LLMClient()
    fake = FakeAsyncClient()
    client.client = fake
    return client, fake


MESSAGES = [{"role": "user", "content": "测试"}]


def test_chat_model_override_uses_override_model(monkeypatch):
    """chat(model_override=X) → 请求 body 的 model 为 X，实例默认模型不变"""
    client, fake = _make_client(monkeypatch)
    result = asyncio.run(client.chat(MESSAGES, model_override="deepseek-v4-pro"))
    assert result == "ok"
    assert fake.calls[-1]["json"]["model"] == "deepseek-v4-pro"
    # 实例配置不被覆盖修改，后续不带 override 的调用仍走默认模型
    assert client.model == "deepseek-v4-flash"
    asyncio.run(client.chat(MESSAGES))
    assert fake.calls[-1]["json"]["model"] == "deepseek-v4-flash"


def test_chat_without_override_uses_default_model(monkeypatch):
    """不传 model_override → 走默认模型"""
    client, fake = _make_client(monkeypatch, heavy_model="deepseek-v4-pro")
    asyncio.run(client.chat(MESSAGES))
    assert fake.calls[-1]["json"]["model"] == "deepseek-v4-flash"


def testget_heavy_model_empty_returns_none(monkeypatch):
    """llm_model_heavy 为空（含未配置/空白）→ 不启用分层，返回 None"""
    monkeypatch.setattr(config_manager, "load_config", lambda: {"llm_model_heavy": ""})
    assert config_manager.get_heavy_model() is None
    monkeypatch.setattr(config_manager, "load_config", lambda: {})
    assert config_manager.get_heavy_model() is None
    monkeypatch.setattr(config_manager, "load_config", lambda: {"llm_model_heavy": "   "})
    assert config_manager.get_heavy_model() is None


def testget_heavy_model_returns_value(monkeypatch):
    """llm_model_heavy 非空 → 返回去空白后的模型名"""
    monkeypatch.setattr(config_manager, "load_config", lambda: {"llm_model_heavy": " deepseek-v4-pro "})
    assert config_manager.get_heavy_model() == "deepseek-v4-pro"


def _read_backend_source(name: str) -> str:
    return (BACKEND_DIR / name).read_text(encoding="utf-8")


def test_pipeline_final_product_calls_pass_model_override():
    """analysis_pipeline 最终产物调用点（45a-45d 六次 + 5a-5f 循环一次）均传 model_override"""
    src = _read_backend_source("analysis_pipeline.py")
    # 控辩对抗 45a/45b + 45c 两次 + 45d 两次 = 6 处；步骤 5 循环 1 处；共 7 处
    assert src.count("model_override=get_heavy_model()") == 7


def test_engine_stage_5c_calls_pass_model_override():
    """analysis_engine 5C 六节调用点传 model_override（且仅这一处，5A/5B 等不动）"""
    src = _read_backend_source("analysis_engine.py")
    assert src.count("model_override=get_heavy_model()") == 1
    # 确认该处在 stage_5_full_defense 的 5C 循环内
    stage5_start = src.index("async def stage_5_full_defense")
    override_pos = src.index("model_override=get_heavy_model()")
    assert override_pos > stage5_start


def test_heavy_call_sites_share_prefix_structure():
    """分层只改 model 字段：override 接入点仍是 build_cached_messages 共享前缀结构"""
    src = _read_backend_source("analysis_pipeline.py")
    # 步骤 5 循环：messages 由 build_cached_messages 组装后 chat 带 override
    assert re.search(
        r"messages = build_cached_messages\(shared_system, material, instruction\)[\s\S]*?section_content = await self\.llm\.chat\(messages, model_override=get_heavy_model\(\)\)",
        src,
    )
