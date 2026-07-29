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
