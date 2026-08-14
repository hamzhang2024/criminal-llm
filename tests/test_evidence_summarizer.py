"""证据详细摘要：保真校验与栏目齐全性测试"""
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
    issues = verify_summary_fidelity(FULL_TEXT, GOOD_SUMMARY, persons="李某（证人）、王五（同案犯）")
    assert any("李某" in i for i in issues)
    assert any("王五" in i for i in issues)


def test_section_titles_are_eight():
    assert SECTION_TITLES == ["概述", "共谋与分工", "主观明知", "获利与分账",
                              "辩解与否认", "关键事实", "态度变化", "矛盾提示"]
