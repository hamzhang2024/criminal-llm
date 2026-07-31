"""案例检索云端代理：本地后端 -> 云端案例微服务

- API Key 存于本地配置（设置页填写），本模块纯转发不存储
- 云端不可达返回 503，Key 未配置返回 400，云端其他错误透传状态码与 detail
- 前缀用 /api/case-search 而非 /api/cases：case_manager 的 case_router 已占用
  /api/cases 且含 GET /{case_id}，与卡片查询形状相同无法靠注册顺序调和
"""
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests
from requests.exceptions import RequestException
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from config_manager import get_config_value

router = APIRouter(prefix="/api/case-search", tags=["case-search"])

DEFAULT_CASE_SERVICE_URL = "http://118.196.83.43:8001"
TIMEOUT = 10


class ValidateKeyRequest(BaseModel):
    api_key: str
    service_url: Optional[str] = None


def _service_config() -> tuple[str, str]:
    base = (get_config_value("case_service_url", "") or DEFAULT_CASE_SERVICE_URL).rstrip("/")
    key = get_config_value("case_api_key", "")
    return base, key


def _unwrap(resp) -> Any:
    """云端响应解包：200 返回 JSON，其余透传状态码与 detail"""
    if resp.status_code == 200:
        return resp.json()
    try:
        detail = resp.json().get("detail", "案例服务错误")
    except Exception:
        detail = "案例服务错误"
    raise HTTPException(status_code=resp.status_code, detail=detail)


def _get(path: str, params: Optional[dict] = None) -> Any:
    base, key = _service_config()
    if not key:
        raise HTTPException(status_code=400, detail="未配置案例检索 API Key，请前往设置页填写")
    try:
        resp = requests.get(base + path, params=params, headers={"X-API-Key": key}, timeout=TIMEOUT)
    except RequestException:
        raise HTTPException(status_code=503, detail="案例库服务暂不可用，请稍后重试")
    return _unwrap(resp)


@router.get("/search")
def search_cases(q: str = Query(default=""), charge: str = Query(default=""),
                 page: int = Query(default=1, ge=1), size: int = Query(default=20, ge=1, le=50)):
    return _get("/api/cases/search", params={"q": q, "charge": charge, "page": page, "size": size})


@router.get("/charges")
def list_charges():
    return _get("/api/charges")


@router.get("/{case_no}")
def get_case_card(case_no: str):
    return _get(f"/api/cases/{quote(case_no, safe='')}")


@router.get("/{case_no}/full")
def get_case_full(case_no: str):
    return _get(f"/api/cases/{quote(case_no, safe='')}/full")


@router.post("/validate")
def validate_key(req: ValidateKeyRequest):
    """设置页「验证」按钮：用用户输入（可能未保存）的 Key 调云端校验

    服务地址优先级：请求值 > 已保存 > 默认，避免用户改地址未保存时打到旧地址
    """
    saved_base, _ = _service_config()
    base = (req.service_url or saved_base).rstrip("/")
    try:
        resp = requests.post(f"{base}/api/keys/validate", json={"api_key": req.api_key}, timeout=TIMEOUT)
    except RequestException:
        raise HTTPException(status_code=503, detail="案例库服务暂不可用，请稍后重试")
    return _unwrap(resp)


def fetch_case_cards(case_nos: List[str]) -> List[Dict[str, Any]]:
    """供阶段 4 注入使用：批量拉取完整卡片，单篇失败跳过（调用方按实际拿到数量注入）

    - Key 未配置直接返回空，不做无谓网络往返
    - 连接级失败（云端整体不可达）中断循环，避免 N×TIMEOUT 线性阻塞；
      404 等 HTTP 错误仍跳过继续
    """
    base, key = _service_config()
    if not key:
        return []
    cards: List[Dict[str, Any]] = []
    for no in case_nos:
        try:
            resp = requests.get(f"{base}/api/cases/{quote(no, safe='')}",
                                headers={"X-API-Key": key}, timeout=TIMEOUT)
            if resp.status_code == 200:
                cards.append(resp.json())
        except RequestException:
            break
    return cards
