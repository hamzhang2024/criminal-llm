"""
LLM 客户端配置模块

包含：
- 百炼配置读取
- 模型上下文能力检测
- 数据量警告判断
"""
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _get_bailian_config() -> tuple[str, Optional[str], str]:
    """
    获取 LLM 配置（从 config_manager 读取，带缓存）

    Returns:
        (baseUrl, apiKey, defaultModel)
    """
    from config_manager import load_config

    config = load_config()
    api_key = config.get("llm_api_key")
    base_url = config.get("llm_base_url", "")
    default_model = config.get("llm_model", "")

    return base_url, api_key, default_model


# ═══════════════════════════════════════════════════════════════════════════
# 模型上下文能力检测
# ═══════════════════════════════════════════════════════════════════════════

# 模型上下文限制映射表（tokens）
MODEL_CONTEXT_LIMITS = {
    # 1M+ 上下文模型
    "gemini-1.5-pro": 1_000_000,
    "gemini-2.0-pro": 1_000_000,
    "gemini-2.0-flash": 1_000_000,
    "gemini-1.5-flash-exp": 1_000_000,
    "gemini-exp": 1_000_000,
    "deepseek-v3": 1_000_000,
    "deepseek-r1": 1_000_000,
    "deepseek-v4-flash": 1_000_000,
    "deepseek-v4-pro": 1_000_000,
    "claude-opus-4": 1_000_000,
    "claude-3-opus": 1_000_000,
    "claude-3.5-opus": 1_000_000,
    # 500k-1M 上下文模型
    "gemini-1.5-flash": 500_000,
    "gemini-2.0-flash-lite": 500_000,
    "claude-sonnet-4": 500_000,
    "claude-3-sonnet": 500_000,
    "claude-3.5-sonnet": 500_000,
    "gpt-4-turbo": 500_000,
    "gpt-4-1106-preview": 500_000,
    # 200k 上下文模型
    "qwen-max": 200_000,
    "qwen-plus": 200_000,
    "qwen3-plus": 200_000,
    "qwen3-max": 200_000,
    "qwen2.5-plus": 200_000,
    "qwen2.5-max": 200_000,
    "qwen-long": 200_000,
    "gpt-4o": 200_000,
    "gpt-4o-mini": 200_000,
    "claude-3-haiku": 200_000,
    "claude-3.5-haiku": 200_000,
    # 128k 上下文模型
    "gpt-4": 128_000,
    "gpt-4-0125-preview": 128_000,
    "qwen-turbo": 128_000,
    "qwen3-turbo": 128_000,
}


def _estimate_context_limit(model: str) -> int:
    """
    根据模型名称估算上下文限制

    优先精确匹配，其次模糊匹配
    """
    model_lower = model.lower()

    # 精确匹配
    for name, limit in MODEL_CONTEXT_LIMITS.items():
        if name == model_lower:
            return limit

    # 模糊匹配（模型名称包含关键词）
    # 1M+ 上下文关键词
    million_keywords = ["gemini-1.5-pro", "gemini-2.0", "deepseek-v3", "deepseek-r1", "deepseek-v4", "claude-opus-4", "claude-3-opus", "claude-3.5-opus"]
    for kw in million_keywords:
        if kw in model_lower:
            return 1_000_000

    # 500k 上下文关键词
    half_million_keywords = ["gemini-1.5-flash", "claude-sonnet-4", "claude-3-sonnet", "claude-3.5-sonnet"]
    for kw in half_million_keywords:
        if kw in model_lower:
            return 500_000

    # 200k 上下文关键词
    two_hundred_keywords = ["qwen-max", "qwen-plus", "qwen-long", "gpt-4o", "claude-haiku"]
    for kw in two_hundred_keywords:
        if kw in model_lower:
            return 200_000

    # 128k 上下文关键词
    one_twenty_eight_keywords = ["gpt-4", "qwen-turbo"]
    for kw in one_twenty_eight_keywords:
        if kw in model_lower:
            return 128_000

    # 默认假设 128k（保守估计）
    return 128_000


async def query_model_info_from_api(base_url: str, api_key: str, model: str) -> Dict[str, Any] | None:
    """
    尝试从 API 查询模型信息

    支持：
    - OpenAI 兼容 API: GET /models
    - 阿里云百炼: 模型列表接口

    Returns:
        {"context_limit": int, ...} 或 None（查询失败）
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # 尝试 OpenAI 兼容的 /models 接口
            url = f"{base_url.rstrip('/')}/models"
            headers = {"Authorization": f"Bearer {api_key}"}

            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                # 在模型列表中查找
                models = data.get("data", data.get("models", []))
                for m in models:
                    model_id = m.get("id", m.get("model", ""))
                    if model_id == model or model in model_id:
                        # 部分返回 context_length
                        context_length = m.get("context_length") or m.get("max_context_length")
                        if context_length:
                            return {"context_limit": context_length}

            return None
    except Exception:
        return None


def get_model_context_limit(model: str, user_specified_limit: int | None = None) -> Dict[str, Any]:
    """
    根据模型名称返回上下文限制和处理策略

    Args:
        model: 模型名称
        user_specified_limit: 用户手动指定的上下文限制（优先使用）

    Returns:
        {
            "limit": int,           # 上下文限制（tokens）
            "limit_k": str,         # 上下文限制（显示用，如 "200k"）
            "strategy": str,        # 策略名称
            "warning": str | None,  # 警告信息
            "small_case_limit": int, # 小案件阈值（低于此值无警告）
            "is_estimated": bool,   # 是否为估算值
        }
    """
    # 用户手动指定的优先
    if user_specified_limit and user_specified_limit > 0:
        limit = user_specified_limit
        is_estimated = False
    else:
        # 从映射表估算
        limit = _estimate_context_limit(model)
        is_estimated = limit == 128_000  # 默认值表示估算

    # 根据上下文限制确定策略（仅用于显示和警告，不再动态调整摘要长度）
    if limit >= 1_000_000:
        return {
            "limit": limit,
            "limit_k": "1M+",
            "strategy": "完整模式",
            "warning": None,
            "small_case_limit": 0,
            "is_estimated": is_estimated,
        }
    elif limit >= 500_000:
        return {
            "limit": limit,
            "limit_k": f"{limit // 1000}k",
            "strategy": "标准模式",
            "warning": "建议升级到 1M 上下文模型以获得最佳体验",
            "small_case_limit": int(limit * 0.4),
            "is_estimated": is_estimated,
        }
    elif limit >= 200_000:
        return {
            "limit": limit,
            "limit_k": f"{limit // 1000}k",
            "strategy": "精简模式",
            "warning": "大案件可能遗漏细节，建议升级到 gemini-1.5-pro 或 deepseek-v3",
            "small_case_limit": int(limit * 0.3),
            "is_estimated": is_estimated,
        }
    else:
        # 128k 及以下
        return {
            "limit": limit,
            "limit_k": f"{limit // 1000}k",
            "strategy": "小案件模式",
            "warning": "上下文较小，仅适合小案件（证据少、文件小）。大案件请切换到 200k+ 上下文模型",
            "small_case_limit": int(limit * 0.3),
            "is_estimated": is_estimated,
        }


def check_data_size_warning(data_size: int, model_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    根据数据量和模型信息判断是否需要警告

    Args:
        data_size: 数据大小（字符数）
        model_info: get_model_context_limit() 返回的模型信息

    Returns:
        {
            "need_warning": bool,      # 是否需要警告
            "warning_level": str,      # 警告级别：none/info/warning
            "warning_message": str,    # 警告信息
            "can_proceed": bool,       # 是否可以继续（始终为 True）
        }
    """
    small_case_limit = model_info.get("small_case_limit", 0)

    # 小案件，无需警告
    if data_size <= small_case_limit:
        return {
            "need_warning": False,
            "warning_level": "none",
            "warning_message": "",
            "can_proceed": True,
        }

    # 有预定义警告信息则显示
    if model_info.get("warning"):
        return {
            "need_warning": True,
            "warning_level": "info",
            "warning_message": f"当前案件数据量 {data_size//1000}k 字符，{model_info['warning']}",
            "can_proceed": True,
        }

    return {
        "need_warning": False,
        "warning_level": "none",
        "warning_message": "",
        "can_proceed": True,
    }
