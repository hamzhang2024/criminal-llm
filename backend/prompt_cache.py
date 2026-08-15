"""缓存友好的 LLM 消息组装

DeepSeek prompt cache 按前缀命中：system + user 开头完全一致的部分才走缓存价。
分析链路的多次调用若把案件材料放在 user 末尾（指令在前），每次都全价；
统一为「system 固定规则 → user 前段材料（共享前缀）→ 末尾任务指令」后，
同一案件材料的后续调用命中缓存（命中价约为全价 1/10）。
"""


def build_cached_messages(system: str, material: str, instruction: str) -> list:
    """组装缓存友好的 messages：材料在 user 前段，指令在末尾

    Args:
        system: 固定角色/规则（同一批调用应保持一致才能共享缓存）
        material: 案件材料/阶段产物（同一案件的多次调用共享此前缀）
        instruction: 本次任务指令（变化的放最后）
    """
    if not material:
        user = instruction
    else:
        user = f"{material}\n\n---\n\n{instruction}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
