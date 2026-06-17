"""
用户自定义法律知识库 API

提供法律知识条目的 CRUD 操作，数据存储在 ~/.criminal-llm/legal_kb/
"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from legal_search import (
    create_legal_kb_item,
    delete_legal_kb_item,
    get_legal_kb_item,
    list_legal_kb,
    search_and_merge,
    update_legal_kb_item,
)
from pydantic import BaseModel

router = APIRouter(prefix="/api/legal-knowledge", tags=["法律知识库"])


class LegalKBItem(BaseModel):
    title: str
    content: str
    crime_type: Optional[str] = ""
    id: Optional[str] = None


class LegalKBUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    crime_type: Optional[str] = None


class LegalSearchRequest(BaseModel):
    crime_type: str


@router.get("")
def api_list_legal_kb():
    """列出所有用户自定义法律知识条目"""
    items = list_legal_kb()
    return {"success": True, "count": len(items), "items": items}


@router.get("/{item_id}")
def api_get_legal_kb_item(item_id: str):
    """获取单个法律知识条目详情"""
    item = get_legal_kb_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"法律知识条目 '{item_id}' 不存在")
    return {"success": True, "item": item}


@router.post("")
def api_create_legal_kb_item(req: LegalKBItem):
    """创建新的法律知识条目

    内容格式建议：
    - 引用法条必须包含条文号（如"《刑法》第266条"）
    - 司法解释必须标注文号（如"法释〔2024〕8号"）和生效日期
    - 类案必须标注法院和案号
    """
    if not req.title or not req.content:
        raise HTTPException(status_code=400, detail="title 和 content 不能为空")

    result = create_legal_kb_item(
        title=req.title,
        content=req.content,
        crime_type=req.crime_type or "",
        item_id=req.id,
    )
    return {
        "success": True,
        "item": result,
        "format_hint": "引用的法条要准确（包含条文号），司法解释要标注文号，类案要标注法院和案号",
    }


@router.put("/{item_id}")
def api_update_legal_kb_item(item_id: str, req: LegalKBUpdate):
    """更新法律知识条目"""
    result = update_legal_kb_item(
        item_id=item_id,
        title=req.title,
        content=req.content,
        crime_type=req.crime_type,
    )
    if not result:
        raise HTTPException(status_code=404, detail=f"法律知识条目 '{item_id}' 不存在")
    return {"success": True, "item": result}


@router.delete("/{item_id}")
def api_delete_legal_kb_item(item_id: str):
    """删除法律知识条目"""
    if not delete_legal_kb_item(item_id):
        raise HTTPException(status_code=404, detail=f"法律知识条目 '{item_id}' 不存在")
    return {"success": True, "message": f"已删除 '{item_id}'"}


@router.post("/search")
def api_search_laws(req: LegalSearchRequest):
    """
    触发法律法规网络搜索 + 合并用户自定义知识

    调用 qwen3.6-plus 的联网搜索能力获取相关法律法规，
    同时合并用户自定义知识库中匹配的条目。
    """
    if not req.crime_type:
        raise HTTPException(status_code=400, detail="crime_type 不能为空")

    result = search_and_merge(req.crime_type)
    return {
        "success": True,
        "crime_type": req.crime_type,
        "content": result if result else "未能搜索到相关法律法规",
    }
