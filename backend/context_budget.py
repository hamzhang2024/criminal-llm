"""统一上下文预算管理

职责：
- 模型上下文窗口识别（设置页展示/建议用）
- 内容预算换算（token → 字符，全项目统一公式，替代各处重复的 (limit-38000)×1.35）
- 优先级截断（fit_texts：高优先级完整保留，中比例分配，低截断标注）
"""
from config_manager import get_config_value

# 模型家族 → 上下文窗口（tokens），用于设置页展示与建议；实际预算以用户配置为准
MODEL_CONTEXT_WINDOWS = {
    "deepseek": 1000000,
    "kimi": 262144,
    "qwen": 131072,
    "glm": 131072,
    "gpt": 128000,
    "claude": 200000,
}

DEFAULT_CONTEXT_LIMIT = 250000
DEFAULT_RESERVE_TOKENS = 38000   # system prompt + 输出预留
CHARS_PER_TOKEN = 1.35           # 中文：1 token ≈ 1.35 字符


def get_context_limit() -> int:
    """内容预算的基准：用户配置（model_context_limit）为唯一事实源"""
    try:
        return int(get_config_value("model_context_limit", str(DEFAULT_CONTEXT_LIMIT)))
    except Exception:
        return DEFAULT_CONTEXT_LIMIT


def get_model_window(model: str) -> int | None:
    """按模型名识别上下文窗口（设置页展示/建议），未知返回 None"""
    model_lower = (model or "").lower()
    for family, window in MODEL_CONTEXT_WINDOWS.items():
        if family in model_lower:
            return window
    return None


def content_budget_chars(reserve_tokens: int = DEFAULT_RESERVE_TOKENS) -> int:
    """内容字符预算 = (context_limit - 预留) × 1.35"""
    return int((get_context_limit() - reserve_tokens) * CHARS_PER_TOKEN)


def truncate_with_marker(text: str, budget: int, label: str = "") -> str:
    """超预算截断并标注（不超原样返回）"""
    if len(text) <= budget:
        return text
    tag = f"\n\n[已截断：{label + '，' if label else ''}原文共 {len(text)} 字符，仅显示前 {budget} 字符]"
    return text[:budget] + tag


def fit_texts(texts: list[dict], budget_chars: int) -> str:
    """按优先级把多段文本装进预算。

    texts: [{"label": str, "text": str, "priority": 0|1|2}]（0=高，1=中，2=低）
    - 高优先级：完整保留（单段超预算才截断标注）
    - 中优先级：剩余预算的 80% 按比例分配
    - 低优先级：剩余预算的 20% 分配，不够则截断标注
    返回拼接后的 "## {label}\n{text}" 块
    """
    high = [t for t in texts if t.get("priority", 1) == 0]
    mid = [t for t in texts if t.get("priority", 1) == 1]
    low = [t for t in texts if t.get("priority", 1) == 2]

    parts: list[str] = []
    used = 0

    for t in high:
        block_text = truncate_with_marker(t["text"], budget_chars, t["label"])
        block = f"## {t['label']}\n{block_text}"
        parts.append(block)
        used += len(block)

    remaining = max(0, budget_chars - used)
    mid_pool = int(remaining * 0.8) if mid else 0
    low_pool = remaining - mid_pool

    for pool, group in ((mid_pool, mid), (low_pool, low)):
        if not group:
            continue
        per = max(200, pool // len(group))
        for t in group:
            block_text = truncate_with_marker(t["text"], per, t["label"])
            parts.append(f"## {t['label']}\n{block_text}")

    return "\n\n".join(parts)
