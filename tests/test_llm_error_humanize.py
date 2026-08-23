"""LLM 错误人性化映射：把供应商返回的原始错误转成用户可操作的中文提示

背景：Ollama 默认 num_ctx=8192，证据提取长文超出后每个请求都 400，
但界面只显示"提取失败"，用户无法得知真实原因（2026-08-23 实际事故）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from llm_client import humanize_llm_error


def test_context_overflow_ollama():
    """Ollama 上下文超限（本次事故的实际错误体）→ 指出 num_ctx 解法"""
    raw = ('API 请求失败：400\n{"error":{"message":"{\\"error\\":{\\"code\\":400,'
           '\\"message\\":\\"request (12010 tokens) exceeds the available context size '
           '(8192 tokens), try increasing it\\",\\"type\\":\\"exceed_context_size_error\\"}"}}')
    msg = humanize_llm_error(raw)
    assert "上下文" in msg
    assert "8192" in msg  # 保留实际窗口数字，用户能直接看到差多少
    assert "num_ctx" in msg  # 给出 Ollama 的具体解法


def test_context_overflow_generic():
    """通用 context length 超限（OpenAI 风格）"""
    raw = "API 请求失败：400\nThis model's maximum context length is 8192 tokens"
    msg = humanize_llm_error(raw)
    assert "上下文" in msg
    assert "降低" in msg or "调大" in msg


def test_unauthorized():
    """Key 无效"""
    raw = "API 请求失败：401\n{\"error\":{\"message\":\"Incorrect API key provided\"}}"
    msg = humanize_llm_error(raw)
    assert "API Key" in msg or "Key" in msg
    assert "设置" in msg


def test_rate_limit():
    """限流"""
    raw = "API 请求失败：429\nToo many requests"
    msg = humanize_llm_error(raw)
    assert "限流" in msg or "配额" in msg


def test_insufficient_balance():
    """余额不足（DeepSeek 402）"""
    raw = 'API 请求失败：402\n{"error":{"message":"Insufficient Balance"}}'
    msg = humanize_llm_error(raw)
    assert "余额" in msg


def test_connection_refused():
    """连接失败（服务未启动/地址错误）"""
    raw = "API 请求失败：connect error\nConnection refused"
    msg = humanize_llm_error(raw)
    assert "连接" in msg
    assert "Base URL" in msg or "地址" in msg


def test_timeout():
    """超时"""
    raw = "LLM 请求超时（已重试 3 次）: ReadTimeout"
    msg = humanize_llm_error(raw)
    assert "超时" in msg


def test_unknown_passthrough_truncated():
    """未识别的错误：保留原文（截断 300 字符），不丢信息"""
    raw = "API 请求失败：500\n" + "x" * 500
    msg = humanize_llm_error(raw)
    assert len(msg) <= 320
    assert "xxx" in msg


def test_empty_input():
    """空输入不崩"""
    assert isinstance(humanize_llm_error(""), str)
