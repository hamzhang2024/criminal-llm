from case_text_utils import to_bigrams, parse_case_filename, extract_sections

SAMPLE_MD = """【第1000号】李某甲等寻衅滋事案——未成年人多次强取财物的案件如何处理

## 一、基本案情

被告人李某甲，男。

## 二、主要问题

未成年人多次强取其他未成年人少量财物的案件如何处理?

## 三、裁判理由

本案在审理过程中存在两种意见。我们赞成后一种意见。
""" + "理由正文。" * 200


def test_to_bigrams_chinese():
    assert to_bigrams("寻衅滋事") == "寻衅 衅滋 滋事"


def test_to_bigrams_mixed():
    # token 规则：连续中文 -> bigram；连续英文/数字 -> 整词转小写；单字中文保留
    # "刑法第293条" -> ["刑法第","293","条"] -> "刑法 法第" + "293" + "条"
    assert to_bigrams("刑法第293条") == "刑法 法第 293 条"


def test_to_bigrams_single_char():
    assert to_bigrams("罪") == "罪"


def test_parse_case_filename():
    case_no, title = parse_case_filename(
        "【第1000号】李某甲等寻衅滋事案——未成年人多次强取财物的案件如何处理.md"
    )
    assert case_no == "第1000号"
    assert title.startswith("李某甲等寻衅滋事案")


def test_parse_case_filename_invalid():
    assert parse_case_filename("随便一个文件.md") is None


def test_extract_sections():
    sections = extract_sections(SAMPLE_MD)
    assert "未成年人多次强取" in sections["issue"]
    assert sections["reasoning_excerpt"].startswith("本案在审理过程中")
    assert len(sections["reasoning_excerpt"]) <= 500


def test_extract_sections_missing_issue():
    assert extract_sections("## 一、基本案情\n没有章节")["issue"] is None
