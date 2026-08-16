"""缓存友好消息组装工具测试"""
import asyncio
import inspect

import analysis_pipeline
from prompt_cache import build_cached_messages
from test_debate_fixes import _make_pipeline as _make_debate_pipeline
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


def test_debate_prompts_share_context_prefix(tmp_path):
    """控辩对抗 45a/45b/45d：context 在 user 前段（公诉人/辩方两次调用共享材料前缀）

    (a) 45a/45b 的 system 逐字节相同（角色扮演引擎 + 用户指定角色）
    (b) 45a/45b 的 user 共享同一 material 前缀（同一 context），指令在末尾
    (c) 45a 指令为公诉人角色、45b 指令为辩方角色
    (d) 源码结构兜底：step45 的 prompt 组装必须走 build_cached_messages
    """
    pipe = _make_debate_pipeline(tmp_path)

    captured = []

    async def fake_chat(messages, **kw):
        captured.append(messages)
        return "对抗内容"

    pipe.llm.chat = fake_chat
    asyncio.run(pipe.step45_debate_simulation("张三", "诈骗罪"))

    # 45a/45b 各一次；45c/45d 单任务聚焦拆分后各两次
    assert len(captured) == 6, f"应调用 6 次 LLM，实际 {len(captured)}"

    msgs_45a, msgs_45b = captured[0], captured[1]

    # (a) 45a/45b system 逐字节相同（共享前缀的第一段）
    assert msgs_45a[0]["content"] == msgs_45b[0]["content"]

    user_a, user_b = msgs_45a[1]["content"], msgs_45b[1]["content"]
    # 材料与指令分界：material + "\n\n---\n\n" + instruction
    material_a, instruction_a = user_a.rsplit("\n\n---\n\n", 1)
    material_b, instruction_b = user_b.rsplit("\n\n---\n\n", 1)

    # (b) 45a/45b material 逐字节相同，且以指控要素段开头（context 在 user 最前）
    assert material_a == material_b, "45a/45b 应共享同一 context 前缀"
    assert material_a.startswith("## 指控要素"), "context 应位于 user 最前"

    # (c) 角色指令在 user 末尾
    assert "你是本案公诉人" in instruction_a
    assert "辩护律师" in instruction_b
    # 指令不得携带材料（材料已全部前置）
    assert "## 指控要素" not in instruction_a
    assert "## 指控要素" not in instruction_b

    # 45d：红蓝产物组装为 material，法官指令在末尾（45c 拆分后 45d① 在第 5 次调用）
    user_d = captured[4][1]["content"]
    material_d, instruction_d = user_d.rsplit("\n\n---\n\n", 1)
    assert material_d.startswith("## 控方指控（独立构建）")
    assert "## 交叉对决结果" in material_d
    assert "你是本案主审法官" in instruction_d

    # (d) 源码结构兜底断言：45a/45b/45d 三处 prompt 组装必须走 build_cached_messages，
    # context 不得再内嵌回指令 f-string（旧结构特征）
    src = inspect.getsource(analysis_pipeline.AnalysisPipeline.step45_debate_simulation)
    assert src.count("build_cached_messages") >= 3


def test_batch_analyze_indictment_only_first_batch(monkeypatch):
    """分批分析：起诉书全文只在第一批注入，后续批次不带（省重复输入）"""
    import analysis_engine

    calls = []

    async def fake_chat(messages, **kw):
        calls.append(messages)
        return "批结果 ```json\n{\"nodes\": [], \"edges\": []}\n```"

    # 构造 3 份证据 + 1 份起诉书，预算设小迫使分 2 批
    texts = [
        {"filename": "起诉书", "type": "起诉书", "text": "起诉书全文" * 500, "is_indictment": True},
        {"filename": "证据A", "type": "书证", "text": "内容A" * 20000},
        {"filename": "证据B", "type": "书证", "text": "内容B" * 20000},
    ]
    monkeypatch_client = type("C", (), {"chat": staticmethod(fake_chat)})()
    monkeypatch.setattr("llm_client.get_llm_client", lambda: monkeypatch_client)
    # 用小 context_limit 迫使分批
    monkeypatch.setattr("config_manager.get_config_value", lambda k, d="": "20000" if k == "model_context_limit" else d)

    results = asyncio.run(analysis_engine._batch_analyze_evidence(
        texts, "系统", "头部", "尾部", label="测试"))
    assert len(results) >= 2  # 确实分了多批
    # 起诉书全文只在第一批的 user prompt 中
    first_user = calls[0][-1]["content"]
    later_users = [c[-1]["content"] for c in calls[1:]]
    assert "起诉书全文" in first_user
    assert all("起诉书全文" not in u for u in later_users)


def test_batch_packing_full_budget_after_first_batch(monkeypatch):
    """分批打包：仅第一批为起诉书预留额度，后续批按全额预算打包（减少批数）"""
    import analysis_engine

    enc = analysis_engine._get_enc()

    def mk_text(target_tokens, ch):
        # 构造精确 token 数的文本（重复单字后按 token 截断再解码）
        ids = enc.encode(ch * target_tokens * 2)[:target_tokens]
        return enc.decode(ids)

    # 固定开销极小，预算取 24000（高于 20000 下限，避免地板值干扰）
    system, header, footer = "系统", "头部", "尾部"
    fixed = (len(enc.encode(system)) + len(enc.encode(header))
             + len(enc.encode(footer)) + 5000)
    budget = 24000
    context_limit = budget + fixed
    monkeypatch.setattr(
        "config_manager.get_config_value",
        lambda k, d="": str(context_limit) if k == "model_context_limit" else d)

    # 起诉书约 8000 tokens → 第一批证据可用额度约 16000；后续批全额 24000
    indictment = mk_text(8000, "诉")
    # 4 份证据各约 10000 tokens：
    # 旧记账（每批都扣起诉书）→ 每批只能装 1 份，共 4 批
    # 新记账（仅首批扣）→ 首批 1 份 + 第二批 2 份 + 第三批 1 份，共 3 批
    texts = [
        {"filename": "起诉书", "type": "起诉书", "text": indictment},
        {"filename": "证据A", "type": "书证", "text": mk_text(10000, "甲")},
        {"filename": "证据B", "type": "书证", "text": mk_text(10000, "乙")},
        {"filename": "证据C", "type": "书证", "text": mk_text(10000, "丙")},
        {"filename": "证据D", "type": "书证", "text": mk_text(10000, "丁")},
    ]

    calls = []

    async def fake_chat(messages, **kw):
        calls.append(messages)
        return "批结果"

    monkeypatch_client = type("C", (), {"chat": staticmethod(fake_chat)})()
    monkeypatch.setattr("llm_client.get_llm_client", lambda: monkeypatch_client)

    results = asyncio.run(analysis_engine._batch_analyze_evidence(
        texts, system, header, footer, label="测试"))

    # 后续批按全额打包 → 3 批而非 4 批
    assert len(results) == 3, f"应为 3 批，实际 {len(results)} 批"

    batch1 = calls[0][-1]["content"]
    batch2 = calls[1][-1]["content"]
    batch3 = calls[2][-1]["content"]

    # 第一批：起诉书 + 证据A（扣起诉书额度后装不下第二份）
    assert "起诉书" in batch1 and "证据A" in batch1 and "证据B" not in batch1
    # 第二批：全额预算装下 证据B + 证据C（不带起诉书）
    assert "起诉书" not in batch2
    assert "证据B" in batch2 and "证据C" in batch2
    # 第三批：剩余 证据D
    assert "证据D" in batch3 and "起诉书" not in batch3
