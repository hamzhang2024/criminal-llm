"""证据详细摘要：保真校验与栏目齐全性测试"""
import asyncio

from evidence_summarizer import verify_summary_fidelity, SECTION_TITLES

FULL_TEXT = """
讯问时间：2026年3月12日14时。问：你和高蓉的借贷怎么回事？
答：2022年9月底，高蓉房子抵押，我分两笔转了20万给她，月息7分也就是14000元，
分给孙琴芳6000元，我和唐鑫一人4000元。问：一共放贷多少？答：400万元不到点，
获利30万元左右，一人15万元左右。
""".strip()

GOOD_SUMMARY = """## 概述
2022年9月底高蓉房产抵押借款20万，月息7分。
## 共谋与分工
唐鑫揽客收息，与供述人平分。
## 主观明知
明知月息7分。
## 获利与分账
获利30万元左右，一人15万元左右；孙琴芳分6000元，与唐鑫各分4000元。
## 辩解与否认
无
## 关键事实
- 2022年9月｜高蓉｜20万｜月息7分（14000元）｜2026年3月12日讯问确认
- 累计放贷400万元不到点
## 态度变化
无
## 矛盾提示
无
"""


def test_good_summary_passes():
    issues = verify_summary_fidelity(FULL_TEXT, GOOD_SUMMARY, persons="张某（嫌疑人）")
    assert issues == []


def test_missing_sections_detected():
    bad = GOOD_SUMMARY.replace("## 主观明知", "## 明知")  # 栏目名被改
    issues = verify_summary_fidelity(FULL_TEXT, bad, persons="")
    assert any("栏目缺失" in i and "主观明知" in i for i in issues)


def test_low_entity_coverage_detected():
    # 摘要丢掉全部金额和日期
    bad = """## 概述
供述人承认放贷。
## 共谋与分工
无
## 主观明知
无
## 获利与分账
无
## 辩解与否认
无
## 关键事实
无
## 态度变化
无
## 矛盾提示
无
"""
    issues = verify_summary_fidelity(FULL_TEXT, bad, persons="")
    assert any("覆盖率" in i for i in issues)


def test_missing_person_detected():
    # 非供述主体角色（同案犯/民警）必须在摘要中出现
    issues = verify_summary_fidelity(FULL_TEXT, GOOD_SUMMARY, persons="李某（同案犯）、王五（民警）")
    assert any("李某" in i for i in issues)
    assert any("王五" in i for i in issues)


def test_victim_and_witness_roles_exempt():
    """被害人/证人作为供述主体时也豁免人名检查（其本人陈述的摘要不重复本人姓名）"""
    issues = verify_summary_fidelity(FULL_TEXT, GOOD_SUMMARY, persons="王某（被害人）")
    assert issues == []


def test_section_titles_are_eight():
    assert SECTION_TITLES == ["概述", "共谋与分工", "主观明知", "获利与分账",
                              "辩解与否认", "关键事实", "态度变化", "矛盾提示"]


# ---------- 单份证据摘要生成（summarize_one） ----------

def _fake_client(responses):
    """按调用次序返回响应的假 client"""
    state = {"i": 0}

    async def fake_chat(messages, **kw):
        r = responses[min(state["i"], len(responses) - 1)]
        state["i"] += 1
        return r

    return type("C", (), {"chat": staticmethod(fake_chat)})()


LONG_TEXT = "讯问笔录内容。" * 120  # 840 字，超过 800 字阈值


def test_short_evidence_copies_original():
    """短证据（<800字）不调 LLM，直接复制原文"""
    from evidence_summarizer import summarize_one
    ev = {"name": "询问通知书", "persons": "", "md_file": "001_x.md"}
    client = _fake_client(["不应被调用"])
    digest, warning = asyncio.run(summarize_one(client, ev, "短内容", "案件.md"))
    assert digest == "短内容"
    assert warning is False


def test_summary_generated_and_verified():
    """长证据调 LLM，输出通过校验"""
    from evidence_summarizer import summarize_one
    ev = {"name": "张某讯问笔录", "persons": "张某（嫌疑人）", "md_file": "002_x.md"}
    summary = """## 概述
内容。## 共谋与分工
无
## 主观明知
无
## 获利与分账
无
## 辩解与否认
无
## 关键事实
无
## 态度变化
无
## 矛盾提示
无
"""
    client = _fake_client([summary])
    digest, warning = asyncio.run(summarize_one(client, ev, LONG_TEXT, "案件.md"))
    assert "## 概述" in digest
    assert warning is False


def test_summary_retry_then_warning():
    """两轮都不达标：保留结果但标记警告"""
    from evidence_summarizer import summarize_one
    ev = {"name": "张某讯问笔录", "persons": "", "md_file": "002_x.md"}
    bad = "## 概述\n只有概述，缺栏目。"
    client = _fake_client([bad, bad])  # 两次都坏
    digest, warning = asyncio.run(summarize_one(client, ev, LONG_TEXT, "案件.md"))
    assert digest == bad
    assert warning is True
