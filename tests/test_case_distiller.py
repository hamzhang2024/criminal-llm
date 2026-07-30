import json
import pytest
from case_distiller import validate_card, distill_case

# post() 的形参 json 会遮蔽模块名，提前取别名供 FakeSession 使用
_json_dumps = json.dumps


def good_card():
    return {
        "charges": ["寻衅滋事罪"],
        "holding_summary": "未成年人以轻微暴力强索少量财物的，" + "应认定为寻衅滋事罪。" * 20,
        "keywords": ["寻衅滋事", "未成年人", "强拿硬要", "轻微暴力"],
    }


def test_validate_card_ok():
    assert validate_card(good_card()) == []


def test_validate_card_empty_charges():
    card = good_card()
    card["charges"] = []
    assert any("charges" in e for e in validate_card(card))


def test_validate_card_summary_too_short():
    card = good_card()
    card["holding_summary"] = "太短"
    assert any("holding_summary" in e for e in validate_card(card))


def test_validate_card_keywords_count():
    card = good_card()
    card["keywords"] = ["只有一个"]
    assert any("keywords" in e for e in validate_card(card))


def test_validate_card_keywords_element_type():
    card = good_card()
    card["keywords"] = ["a", 2, None]
    assert any("keywords" in e for e in validate_card(card))


def good_card_with_issue():
    card = good_card()
    card["issue"] = "本案的核心法律问题：未成年人强索财物应如何定性？"
    return card


def test_validate_card_need_issue_ok():
    """need_issue=True：含合法 issue 的卡片通过校验"""
    assert validate_card(good_card_with_issue(), need_issue=True) == []


def test_validate_card_need_issue_missing():
    """need_issue=True 但无 issue 字段：校验报错"""
    assert any("issue" in e for e in validate_card(good_card(), need_issue=True))


def test_validate_card_need_issue_too_short():
    """need_issue=True 且 issue 过短：校验报错"""
    card = good_card_with_issue()
    card["issue"] = "太短"
    assert any("issue" in e for e in validate_card(card, need_issue=True))


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    """模拟百炼 API：第一次返回非法 JSON，第二次返回合法卡片"""

    def __init__(self):
        self.calls = 0

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls += 1
        if self.calls == 1:
            content = "这不是JSON"
        else:
            content = _json_dumps(good_card(), ensure_ascii=False)
        return FakeResponse({"choices": [{"message": {"content": content}}]})


def test_distill_case_retries_once_then_succeeds():
    session = FakeSession()
    card = distill_case(session, "http://fake/v1", "key", "model", "标题", "正文" * 100)
    assert session.calls == 2
    assert card["charges"] == ["寻衅滋事罪"]


class BrokenSession:
    def post(self, url, headers=None, json=None, timeout=None):
        return FakeResponse({"choices": [{"message": {"content": "非JSON"}}]})


def test_distill_case_fails_after_retry():
    with pytest.raises(RuntimeError, match="提炼失败"):
        distill_case(BrokenSession(), "http://fake/v1", "key", "model", "标题", "正文" * 100)


class IssueSession:
    """need_issue=True：返回含 issue 的合法卡片，并记录 prompt 供断言"""

    def __init__(self):
        self.calls = 0
        self.prompts = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls += 1
        self.prompts.append(json["messages"][0]["content"])
        content = _json_dumps(good_card_with_issue(), ensure_ascii=False)
        return FakeResponse({"choices": [{"message": {"content": content}}]})


def test_distill_case_need_issue_returns_issue():
    """need_issue=True：prompt 追加 issue 字段要求，返回卡片含 issue 且通过校验"""
    session = IssueSession()
    card = distill_case(session, "http://fake/v1", "key", "model", "标题", "正文" * 100,
                       need_issue=True)
    assert card["issue"] == good_card_with_issue()["issue"]
    assert '"issue"' in session.prompts[0]


def test_distill_case_default_prompt_unchanged():
    """need_issue=False：prompt 不含 issue 字段要求（行为完全不变）"""
    session = IssueSession()
    distill_case(session, "http://fake/v1", "key", "model", "标题", "正文" * 100)
    assert '"issue"' not in session.prompts[0]


class NoIssueSession:
    """始终不返回 issue 字段：need_issue=True 时校验失败，重试一次后抛 RuntimeError"""

    def __init__(self):
        self.calls = 0

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls += 1
        content = _json_dumps(good_card(), ensure_ascii=False)
        return FakeResponse({"choices": [{"message": {"content": content}}]})


def test_distill_case_need_issue_missing_retries():
    """need_issue=True 但 LLM 没返回 issue：校验失败重试，最终抛 RuntimeError"""
    session = NoIssueSession()
    with pytest.raises(RuntimeError, match="提炼失败"):
        distill_case(session, "http://fake/v1", "key", "model", "标题", "正文" * 100,
                     need_issue=True)
    assert session.calls == 2
