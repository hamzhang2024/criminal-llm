"""统一上下文预算管理

职责：
- 模型上下文窗口识别（设置页展示/建议用）
- 内容预算换算（token → 字符，全项目统一公式，替代各处重复的 (limit-38000)×1.35）
- 优先级截断（fit_texts：高优先级完整保留，中比例分配，低截断标注）
"""
from config_manager import get_config_value

# 模型家族 → 上下文窗口（tokens），用于设置页展示与建议；实际预算以用户配置为准
# 有序列表：先匹配先生效（deepseek-v4 先于 deepseek 通用档）
MODEL_CONTEXT_WINDOWS = [
    ("deepseek-v4", 1000000),
    ("deepseek", 128000),
    ("kimi", 262144),
    ("qwen", 131072),
    ("glm", 131072),
    ("gpt", 128000),
    ("claude", 200000),
]

DEFAULT_CONTEXT_LIMIT = 250000
PROMPT_OVERHEAD_TOKENS = 8000    # system prompt + 罪名上下文等固定开销
CHARS_PER_TOKEN = 1.35           # 中文：1 token ≈ 1.35 字符
MIN_CONTENT_BUDGET_CHARS = 30000  # 预算下限：小配置也不低于此值，杜绝负预算

# 各模型家族的最大输出 token 上限（保守值，防 API 400）
MODEL_OUTPUT_CAPS = {
    "deepseek": 65536,
    "qwen": 32768,
    "kimi": 65536,
    "glm": 32768,
    "gpt": 32768,
    "claude": 65536,
}
DEFAULT_OUTPUT_CAP = 32768


def compute_max_output_tokens(context_limit: int, model: str) -> int:
    """max_output_tokens = min(context_limit * 0.8, 模型输出上限)"""
    computed = int(context_limit * 0.8)
    model_lower = (model or "").lower()
    for family, cap in MODEL_OUTPUT_CAPS.items():
        if family in model_lower:
            return min(computed, cap)
    return min(computed, DEFAULT_OUTPUT_CAP)


def get_context_limit() -> int:
    """内容预算的基准：用户配置（model_context_limit）为唯一事实源"""
    try:
        return int(get_config_value("model_context_limit", str(DEFAULT_CONTEXT_LIMIT)))
    except Exception:
        return DEFAULT_CONTEXT_LIMIT


def get_model_window(model: str) -> int | None:
    """按模型名识别上下文窗口（设置页展示/建议），未知返回 None"""
    model_lower = (model or "").lower()
    for family, window in MODEL_CONTEXT_WINDOWS:
        if family in model_lower:
            return window
    return None


def get_output_reserve_tokens() -> int:
    """输出预留 = chat() 实际请求的 max_tokens + system prompt 固定开销

    chat() 按 compute_max_output_tokens 设置 max_tokens（deepseek 等可达 65536），
    预算若不把这部分扣除，input 打满后 input+max_tokens 必然超上下文上限，
    被 API 以 400 拒绝（如 162000 分块 + 65536 输出 > 200000 上下文）。
    """
    limit = get_context_limit()
    try:
        model = str(get_config_value("llm_model", ""))
    except Exception:
        model = ""
    return compute_max_output_tokens(limit, model) + PROMPT_OVERHEAD_TOKENS


def content_budget_chars(reserve_tokens: int | None = None) -> int:
    """内容字符预算 = (context_limit - 预留) × 1.35；小配置时保底 MIN_CONTENT_BUDGET_CHARS，杜绝负值

    预留缺省为动态计算：输出 max_tokens（随模型/上下文配置变化）+ prompt 固定开销
    """
    if reserve_tokens is None:
        reserve_tokens = get_output_reserve_tokens()
    return max(MIN_CONTENT_BUDGET_CHARS, int((get_context_limit() - reserve_tokens) * CHARS_PER_TOKEN))


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
