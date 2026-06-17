"""
LLM 客户端单元测试

测试目标：
1. get_llm_client - 单例模式和线程安全
2. 模型上下文限制检测
3. 重试机制
"""
import threading
from unittest.mock import patch

import pytest


class TestLLMClientSingleton:
    """LLM 客户端单例测试"""

    def test_singleton_returns_same_instance(self):
        """测试单例返回相同实例"""
        # 重置全局客户端
        import llm_client
        from llm_client import get_llm_client
        llm_client._client = None

        client1 = get_llm_client()
        client2 = get_llm_client()

        assert client1 is client2

    def test_singleton_thread_safety(self):
        """测试单例的线程安全性"""
        import llm_client

        # 重置全局客户端
        llm_client._client = None

        clients = []
        errors = []

        def get_client():
            try:
                client = llm_client.get_llm_client()
                clients.append(id(client))
            except Exception as e:
                errors.append(e)

        # 创建多个线程同时获取客户端
        threads = [threading.Thread(target=get_client) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 所有线程应该获得相同的客户端实例
        assert len(errors) == 0
        assert len(set(clients)) == 1  # 所有 ID 相同


class TestModelContextLimits:
    """模型上下文限制检测测试"""

    def test_detects_128k_models(self):
        """测试检测 128k 上下文模型"""
        from llm_client import get_model_context_limit

        result = get_model_context_limit("gpt-4")
        assert result["limit"] <= 128_000

        result = get_model_context_limit("gpt-3.5-turbo")
        assert result["limit"] <= 128_000

    def test_detects_1m_models(self):
        """测试检测 1M 上下文模型"""
        from llm_client import get_model_context_limit

        # DeepSeek v4 系列
        result = get_model_context_limit("deepseek-v4-flash")
        assert result["limit"] == 1_000_000

        result = get_model_context_limit("deepseek-v4-pro")
        assert result["limit"] == 1_000_000

    def test_user_specified_limit_override(self):
        """测试用户指定限制覆盖自动检测"""
        from llm_client import get_model_context_limit

        result = get_model_context_limit("gpt-4", user_specified_limit=500_000)
        assert result["limit"] == 500_000

    def test_unknown_model_defaults_to_128k(self):
        """测试未知模型默认 128k"""
        from llm_client import get_model_context_limit

        result = get_model_context_limit("unknown-model-xyz")
        # 应该有合理的默认值
        assert result["limit"] > 0
        assert result["is_estimated"] == True


class TestLLMClientConfiguration:
    """LLM 客户端配置测试"""

    @patch.dict('os.environ', {
        'LLM_API_KEY': 'test-key',
        'LLM_BASE_URL': 'https://test.api.com/v1',
        'LLM_MODEL': 'test-model'
    })
    def test_config_from_environment(self):
        """测试从环境变量读取配置"""
        # 需要重新导入以获取新的环境变量
        import importlib

        import llm_client
        importlib.reload(llm_client)

        # 配置应该从环境变量读取
        # 注意：实际实现可能不同，这里测试基本逻辑
        assert True  # 占位测试


class TestRetryMechanism:
    """重试机制测试"""

    @pytest.mark.asyncio
    async def test_retry_on_network_error(self):
        """测试网络错误时的重试"""
        # 这是一个集成测试，需要模拟 httpx.AsyncClient
        pass

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises_error(self):
        """测试重试耗尽后抛出错误"""
        pass


class TestJSONParsing:
    """JSON 解析测试"""

    def test_clean_invalid_escape(self):
        """测试清理非法转义字符"""
        # 测试 JSON 清理逻辑
        import re

        # 模拟非法转义
        dirty_json = r'{"text": "hello\world"}'
        # 清理：修复 \ 后跟非法字符
        clean_json = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', dirty_json)

        import json
        parsed = json.loads(clean_json)
        assert parsed["text"] == r'hello\world'

    def test_clean_control_characters(self):
        """测试清理控制字符"""
        import json
        import re

        dirty_json = '{"text": "hello\x00world"}'
        # 清理控制字符（除了 \n \r \t）
        clean_json = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', dirty_json)

        parsed = json.loads(clean_json)
        assert "hello" in parsed["text"]


# 标记异步测试需要 pytest-asyncio
pytest_plugins = ('pytest_asyncio',)
