"""缓存友好消息组装工具测试"""
import asyncio
import inspect

import analysis_pipeline
from prompt_cache import build_cached_messages
from test_defense_strategy import _make_pipeline


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


def test_5x_substeps_share_prefix(tmp_path):
    """5a-5f 六个子步骤：捕获真实 step5 的六次 chat 调用，断言运行时缓存结构

    (a) 六次调用的 system 逐字节相同
    (b) 5a-5c/5e/5f 五次调用的 user 共享同一 material 前缀（context[:20000]）
    (c) 5d 的 user 也以同一前缀开头（context[:25000] 的前 20000 字符与他人一致）
    (d) 各节指令在 user 末尾
    """
    pipe = _make_pipeline(tmp_path)
    # 充填 Wiki 产物使 context 超过 20000 字符，确保截断逻辑被真实执行
    wiki = tmp_path / "case_001" / "analysis" / "indictment_wiki"
    (wiki / "01-指控要素.md").write_text("指控要素\n" + "指" * 9000, encoding="utf-8")
    (wiki / "06-综合结论.md").write_text("综合结论\n" + "结" * 9000, encoding="utf-8")
    (wiki / "05-矛盾记录.md").write_text("矛盾记录\n" + "矛" * 9000, encoding="utf-8")

    captured = []

    async def fake_chat(messages, **kw):
        captured.append(messages)
        return "小节内容"

    pipe.llm.chat = fake_chat
    asyncio.run(pipe.step5_defense_opinion("张三", "盗窃罪"))

    assert len(captured) == 6, "5a-5f 应各调用一次 LLM"

    # (a) system 逐字节相同
    systems = [msgs[0]["content"] for msgs in captured]
    assert len(set(systems)) == 1

    users = [msgs[1]["content"] for msgs in captured]
    # 材料与指令的分界：material + "\n\n---\n\n" + instruction（指令内无分隔符，rsplit 安全）
    materials = [u.rsplit("\n\n---\n\n", 1)[0] for u in users]
    instructions = [u.rsplit("\n\n---\n\n", 1)[1] for u in users]

    # (b) 5a-5c/5e/5f 五次调用的 material 逐字节相同，且恰好是 context[:20000] 的长度
    shared_idx = [0, 1, 2, 4, 5]
    shared_material = materials[0]
    for i in shared_idx:
        assert materials[i] == shared_material, f"第 {i} 次调用 material 与其他调用不一致"
    assert len(shared_material) == 20000, "context 应被截断到 20000 字符构成共享前缀"

    # (c) 5d 的 user 也以同一前缀开头（context[:25000] 前 20000 字符一致），
    # 理论/构成要件文本追加在 context 之后而非插在 strategy_prefix 与 context 之间
    assert materials[3].startswith(shared_material)
    assert len(materials[3]) > len(shared_material)

    # (d) 各节指令在 user 末尾，且顺序对应 5a-5f
    expected_focus = ["案件概述", "证据评估", "矛盾", "三阶层辩护", "量刑情节", "结论建议"]
    for instr, focus in zip(instructions, expected_focus):
        assert instr.startswith("为被告人 **张三** 生成")
        assert f"请输出 Markdown 格式，聚焦{focus}" in instr

    # 源码结构兜底断言：prompt 组装必须走 build_cached_messages，
    # 且 context 不得再内嵌回指令 f-string（旧结构特征）
    src = inspect.getsource(analysis_pipeline.AnalysisPipeline.step5_defense_opinion)
    assert "build_cached_messages" in src
    assert "{context[:20000]}" not in src and "{context[:25000]}" not in src


def test_cross_examination_prompt_cached_structure():
    """质证 prompt：固定模板/法律依据在 system（跨证据共享前缀），证据内容在 user 前段"""
    import analysis_engine
    src = inspect.getsource(analysis_engine.AnalysisEngine._build_review_messages)
    assert "build_cached_messages" in src
    # 死截断清理：先截6000再截4000的双重截断应只剩 4000
    assert "[:6000]" not in src


def test_review_messages_cache_invariants(tmp_path):
    """质证消息缓存行为断言（逐份 N 次调用命中前缀缓存的不变量）

    (a) 两个不同类型/名称的证据：system 逐字节相同
    (b) 同 template 两次调用：user content 共享前缀（审查模板 + 法律依据部分）
    (c) 指令包含证据编号（evidence_ref），供正文引用
    """
    from analysis_engine import AnalysisEngine

    engine = AnalysisEngine("case_test", tmp_path / "case_test")
    ev_a = {"filename": "张某讯问笔录", "type": "犯罪嫌疑人供述", "evidence_ref": "证据001", "text": "讯问内容甲"}
    ev_b = {"filename": "银行流水", "type": "书证", "evidence_ref": "证据002", "text": "流水内容乙"}

    msgs_a = engine._build_review_messages(ev_a, "模板A")
    msgs_b = engine._build_review_messages(ev_b, "模板B")

    # (a) system 逐字节相同（不随证据类型/名称/模板变化）
    assert msgs_a[0]["content"] == msgs_b[0]["content"]
    # 身份字段由系统回填的说明为静态文本，不得携带动态内容
    assert "由系统回填真实值" in msgs_a[0]["content"]

    # (b) 同 template 两次调用：user 共享前缀（模板 + 法律依据，止于证据内容标题前）
    msgs_a2 = engine._build_review_messages(
        {"filename": "李某讯问笔录", "type": "犯罪嫌疑人供述", "evidence_ref": "证据003", "text": "讯问内容丙"},
        "模板A",
    )
    user_a, user_a2 = msgs_a[1]["content"], msgs_a2[1]["content"]
    marker = "# 证据内容"
    assert marker in user_a and marker in user_a2
    assert user_a[:user_a.index(marker)] == user_a2[:user_a2.index(marker)]

    # (c) 指令在 user 末尾且包含证据编号
    instruction = user_a.rsplit("\n\n---\n\n", 1)[1]
    assert "编号证据001" in instruction
    assert "张某讯问笔录" in instruction
