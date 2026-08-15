"""缓存友好消息组装工具测试"""
from prompt_cache import build_cached_messages


def test_material_before_instruction():
    """材料在 user 前段、指令在末尾（缓存前缀共享的结构保证）"""
    msgs = build_cached_messages("系统规则", "案件材料", "本次任务指令")
    assert msgs[0] == {"role": "system", "content": "系统规则"}
    assert msgs[1]["role"] == "user"
    # 材料在前、指令在后
    assert msgs[1]["content"].index("案件材料") < msgs[1]["content"].index("本次任务指令")
    assert msgs[1]["content"].startswith("案件材料")
    assert msgs[1]["content"].endswith("本次任务指令")


def test_same_prefix_across_calls():
    """同一材料不同指令：system 和 user 前缀一致（缓存命中的条件）"""
    a = build_cached_messages("规则", "材料X", "指令1")
    b = build_cached_messages("规则", "材料X", "指令2")
    assert a[0] == b[0]
    assert a[1]["content"][:len("材料X")] == b[1]["content"][:len("材料X")]


def test_empty_material():
    """材料为空时退化为纯指令（不产出多余分隔符）"""
    msgs = build_cached_messages("规则", "", "指令")
    assert msgs[1]["content"] == "指令"
