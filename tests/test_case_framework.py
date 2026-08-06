import pytest
import case_framework
from case_framework import fetch_case_rules


class FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


CARD_1000 = {
    "case_no": "第1000号", "title": "李某甲等寻衅滋事案",
    "issue": "未成年人多次强取财物如何处理",
    "holding_summary": "未成年人轻微暴力强索少量财物定寻衅滋事。",
    "reasoning_excerpt": "本案审理中存在两种意见……",
}


def _patch(monkeypatch, search_payload):
    """mock 云端搜索（full=true 一次返回完整卡片）"""
    monkeypatch.setattr(case_framework, "_service_config", lambda: ("http://cloud", "cca_x"))

    class FakeRequests:
        calls = []

        @staticmethod
        def get(url, params=None, headers=None, timeout=None):
            FakeRequests.calls.append(params)
            return FakeResp(200, search_payload)

    monkeypatch.setattr(case_framework, "requests", FakeRequests)
    return FakeRequests


def test_no_key_returns_empty(monkeypatch):
    monkeypatch.setattr(case_framework, "_service_config", lambda: ("http://cloud", ""))
    assert fetch_case_rules(["盗窃罪"]) == {}


def test_rules_formatted_with_disclaimer(monkeypatch):
    _patch(monkeypatch, {"results": [CARD_1000]})
    rules = fetch_case_rules(["寻衅滋事罪"])
    md = rules["寻衅滋事罪"]
    assert "自动检索，供分析参考" in md
    assert "【第1000号】李某甲等寻衅滋事案" in md
    assert "未成年人轻微暴力强索少量财物定寻衅滋事。" in md
    assert "本案审理中存在两种意见" in md


def test_charge_filter_size_and_full_param(monkeypatch):
    """每罪名一次请求，full=true 让云端直接返回完整卡片（不再逐篇拉详情烧配额）"""
    fr = _patch(monkeypatch, {"results": []})
    fetch_case_rules(["盗窃罪"], size=3)
    assert fr.calls[0]["charge"] == "盗窃罪"
    assert fr.calls[0]["size"] == 3
    assert fr.calls[0]["full"] == "true"


def test_keywords_joined_into_query(monkeypatch):
    fr = _patch(monkeypatch, {"results": []})
    fetch_case_rules(["寻衅滋事罪"], keywords=["未成年人", "轻微暴力"])
    assert fr.calls[0]["q"] == "未成年人 轻微暴力"
    assert fr.calls[0]["charge"] == "寻衅滋事罪"


def test_zero_results_skipped(monkeypatch):
    _patch(monkeypatch, {"results": []})
    assert fetch_case_rules(["不存在罪"]) == {}


def test_connection_error_stops_remaining_charges(monkeypatch):
    monkeypatch.setattr(case_framework, "_service_config", lambda: ("http://cloud", "cca_x"))

    class BrokenRequests:
        RequestException = __import__("requests").exceptions.RequestException

        @staticmethod
        def get(*a, **kw):
            raise BrokenRequests.RequestException("down")

    monkeypatch.setattr(case_framework, "requests", BrokenRequests)
    assert fetch_case_rules(["盗窃罪", "诈骗罪"]) == {}


def test_search_http_error_continues_next_charge(monkeypatch):
    monkeypatch.setattr(case_framework, "_service_config", lambda: ("http://cloud", "cca_x"))

    card = {"case_no": "第1号", "title": "甲案", "issue": "i", "holding_summary": "h", "reasoning_excerpt": "r"}

    class MixedRequests:
        @staticmethod
        def get(url, params=None, headers=None, timeout=None):
            if params["charge"] == "盗窃罪":
                return FakeResp(500, {})
            return FakeResp(200, {"results": [card]})

    monkeypatch.setattr(case_framework, "requests", MixedRequests)
    rules = fetch_case_rules(["盗窃罪", "诈骗罪"])
    assert "盗窃罪" not in rules
    assert "诈骗罪" in rules
