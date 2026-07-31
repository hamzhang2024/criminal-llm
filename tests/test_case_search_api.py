"""案例检索云端代理测试

本地后端纯转发到云端案例微服务：
- 未配置 API Key → 400
- 云端不可达 → 503
- 云端其他错误（如 429 配额）→ 透传状态码与 detail

注：路由前缀为 /api/case-search（避开 case_manager 的 /api/cases/{case_id} 冲突）
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from requests.exceptions import RequestException

import case_search_api
from case_search_api import router, fetch_case_cards


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_search_requires_api_key(client, monkeypatch):
    monkeypatch.setattr(case_search_api, "_service_config", lambda: ("http://cloud", ""))
    resp = client.get("/api/case-search/search", params={"q": "盗窃"})
    assert resp.status_code == 400
    assert "API Key" in resp.json()["detail"]


def test_search_forwards_params_and_key(client, monkeypatch):
    seen = {}

    class FakeRequests:
        @staticmethod
        def get(url, params=None, headers=None, timeout=None):
            seen.update(url=url, params=params, headers=headers, timeout=timeout)
            return FakeResp(200, {"total": 1, "results": []})

    monkeypatch.setattr(case_search_api, "_service_config", lambda: ("http://cloud", "cca_x"))
    monkeypatch.setattr(case_search_api, "requests", FakeRequests)
    resp = client.get("/api/case-search/search", params={"q": "盗窃", "page": 2})
    assert resp.status_code == 200
    assert seen["url"] == "http://cloud/api/cases/search"
    assert seen["params"]["q"] == "盗窃" and seen["params"]["page"] == 2
    assert seen["headers"]["X-API-Key"] == "cca_x"


def test_upstream_down_returns_503(client, monkeypatch):
    class BrokenRequests:
        @staticmethod
        def get(*a, **kw):
            raise RequestException("down")

    monkeypatch.setattr(case_search_api, "_service_config", lambda: ("http://cloud", "cca_x"))
    monkeypatch.setattr(case_search_api, "requests", BrokenRequests)
    resp = client.get("/api/case-search/charges")
    assert resp.status_code == 503


def test_upstream_429_passthrough(client, monkeypatch):
    class QuotaRequests:
        @staticmethod
        def get(*a, **kw):
            return FakeResp(429, {"detail": "超出每日配额（200 次），明日重置"})

    monkeypatch.setattr(case_search_api, "_service_config", lambda: ("http://cloud", "cca_x"))
    monkeypatch.setattr(case_search_api, "requests", QuotaRequests)
    resp = client.get("/api/case-search/search")
    assert resp.status_code == 429
    assert "配额" in resp.json()["detail"]


def test_validate_uses_request_service_url(client, monkeypatch):
    """请求里的 service_url 优先于已保存配置"""
    seen = {}

    class FakeRequests:
        @staticmethod
        def post(url, json=None, timeout=None):
            seen["url"] = url
            return FakeResp(200, {"valid": True})

    monkeypatch.setattr(case_search_api, "_service_config", lambda: ("http://saved", "savedkey"))
    monkeypatch.setattr(case_search_api, "requests", FakeRequests)
    resp = client.post("/api/case-search/validate", json={"api_key": "cca_x", "service_url": "http://custom"})
    assert resp.status_code == 200
    assert seen["url"] == "http://custom/api/keys/validate"


def test_validate_falls_back_to_saved_url(client, monkeypatch):
    """未传 service_url 时回落到已保存配置"""
    seen = {}

    class FakeRequests:
        @staticmethod
        def post(url, json=None, timeout=None):
            seen["url"] = url
            return FakeResp(200, {"valid": True})

    monkeypatch.setattr(case_search_api, "_service_config", lambda: ("http://saved", "savedkey"))
    monkeypatch.setattr(case_search_api, "requests", FakeRequests)
    resp = client.post("/api/case-search/validate", json={"api_key": "cca_x"})
    assert seen["url"] == "http://saved/api/keys/validate"


def test_fetch_case_cards_skips_failures(monkeypatch):
    class MixedRequests:
        @staticmethod
        def get(url, headers=None, timeout=None):
            # 案号已 URL 编码，用数字部分区分存在/不存在
            if "9999" in url:
                return FakeResp(404, {"detail": "不存在"})
            return FakeResp(200, {"case_no": "第1号", "title": "甲案"})

    monkeypatch.setattr(case_search_api, "_service_config", lambda: ("http://cloud", "cca_x"))
    monkeypatch.setattr(case_search_api, "requests", MixedRequests)
    cards = fetch_case_cards(["第1号", "第9999号"])
    assert [c["case_no"] for c in cards] == ["第1号"]


def test_case_no_url_encoded(monkeypatch):
    """案号含中文必须 URL 编码"""
    seen = {}

    class EncRequests:
        @staticmethod
        def get(url, headers=None, timeout=None):
            seen["url"] = url
            return FakeResp(200, {"case_no": "第1000号"})

    monkeypatch.setattr(case_search_api, "_service_config", lambda: ("http://cloud", "cca_x"))
    monkeypatch.setattr(case_search_api, "requests", EncRequests)
    fetch_case_cards(["第1000号"])
    assert "第1000号" not in seen["url"]  # 已被编码
    assert "%" in seen["url"]


def test_fetch_case_cards_no_key_returns_empty(monkeypatch):
    """Key 未配置时直接返回空列表，不做无谓网络往返"""
    called = []

    class SpyRequests:
        @staticmethod
        def get(*a, **kw):
            called.append(1)
            return FakeResp(200, {})

    monkeypatch.setattr(case_search_api, "_service_config", lambda: ("http://cloud", ""))
    monkeypatch.setattr(case_search_api, "requests", SpyRequests)
    assert fetch_case_cards(["第1号", "第2号"]) == []
    assert called == []  # 未发起任何请求


def test_fetch_case_cards_breaks_on_connection_failure(monkeypatch):
    """连接级失败（云端整体不可达）时中断循环，避免 N×10s 线性阻塞"""
    calls = []

    class FlakyRequests:
        @staticmethod
        def get(url, headers=None, timeout=None):
            calls.append(url)
            if len(calls) == 2:
                raise RequestException("云端不可达")
            return FakeResp(200, {"case_no": "x"})

    monkeypatch.setattr(case_search_api, "_service_config", lambda: ("http://cloud", "cca_x"))
    monkeypatch.setattr(case_search_api, "requests", FlakyRequests)
    cards = fetch_case_cards(["第1号", "第2号", "第3号", "第4号"])
    assert len(calls) == 2  # 第二次失败后不再请求第3、4个
    assert len(cards) == 1
