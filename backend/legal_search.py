"""
法律法规搜索模块

策略：
1. 通过 qwen3.6-plus 的内置联网搜索（enable_search）获取法律法规条文
2. 用户自定义法律知识库（本地存储，持久化）
3. 分析时自动合并网络搜索 + 用户自定义知识
"""

import json
import logging
import sys
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# 打包后 certifi 证书路径可能失效，macOS 用系统证书
if sys.platform == "darwin" and getattr(sys, "frozen", False):
    _SSL_VERIFY = "/etc/ssl/cert.pem"
else:
    _SSL_VERIFY = True

# 用户自定义法律知识库目录
from config import DATA_DIR

LEGAL_KB_DIR = DATA_DIR / "legal_kb"
LEGAL_KB_DIR.mkdir(parents=True, exist_ok=True)

# 百炼 API 配置（复用 llm_client 的配置）
# 不缓存，每次从 config_manager 读取最新值


# 罪名搜索关键词映射
CRIME_KEYWORDS = {
    "开设赌场罪": ["开设赌场罪 刑法 第三百零三条 司法解释", "赌博罪 量刑标准 抽头渔利"],
    "盗窃罪": ["盗窃罪 刑法 第二百六十四条 司法解释 数额标准"],
    "诈骗罪": ["诈骗罪 刑法 第二百六十六条 司法解释 数额标准"],
    "抢劫罪": ["抢劫罪 刑法 第二百六十三条 司法解释"],
    "故意伤害罪": ["故意伤害罪 刑法 第二百三十四条 轻伤 重伤 量刑"],
    "故意杀人罪": ["故意杀人罪 刑法 第二百三十二条 量刑"],
    "贪污罪": ["贪污罪 刑法 第三百八十二条 司法解释 数额标准"],
    "受贿罪": ["受贿罪 刑法 第三百八十五条 司法解释 数额标准"],
    "行贿罪": ["行贿罪 刑法 第三百八十九条 司法解释"],
    "职务侵占罪": ["职务侵占罪 刑法 第二百七十一条 司法解释 数额标准"],
    "挪用公款罪": ["挪用公款罪 刑法 第三百八十四条 司法解释"],
    "寻衅滋事罪": ["寻衅滋事罪 刑法 第二百九十三条 司法解释"],
    "非法拘禁罪": ["非法拘禁罪 刑法 第二百三十八条 司法解释"],
    "敲诈勒索罪": ["敲诈勒索罪 刑法 第二百七十四条 司法解释 数额标准"],
    "帮信罪": ["帮助信息网络犯罪活动罪 刑法 第二百八十七条之二 司法解释"],
    "掩饰隐瞒犯罪所得罪": ["掩饰隐瞒犯罪所得罪 刑法 第三百一十二条 司法解释"],
    "危险驾驶罪": ["危险驾驶罪 刑法 第一百三十三条之一 司法解释 醉驾"],
    "交通肇事罪": ["交通肇事罪 刑法 第一百三十三条 司法解释"],
}


def search_laws_by_llm(crime_type: str, timeout: int = 120) -> str:
    """
    通过 qwen3.6-plus 的内置联网搜索功能获取相关法律法规。

    Args:
        crime_type: 罪名（如"开设赌场罪"）
        timeout: 超时时间（秒）

    Returns:
        搜索到的法律法规条文文本，如果搜索失败则返回空字符串
    """
    # 每次搜索前读取最新配置
    from config_manager import load_config
    from llm_client import _get_bailian_config as llm_get_config
    cfg = load_config()
    base_url, api_key, _ = llm_get_config()
    model = cfg.get("llm_model", "")
    if not api_key:
        logger.info("[法律搜索] 百炼 API Key 未配置，跳过网络搜索")
        return ""

    # 获取搜索关键词
    keywords = CRIME_KEYWORDS.get(crime_type, [f"{crime_type} 刑法 法条 司法解释 量刑标准"])
    keyword = keywords[0]

    system_prompt = """你是法律检索专家。请根据用户提供的罪名，搜索并引用中国现行有效的法律法规。

**要求**：
1. 引用《中华人民共和国刑法》相关条文的**原文**
2. 引用最高人民法院、最高人民检察院关于该罪名的**司法解释原文**（特别是入罪标准、量刑档次）
3. 引用相关的**立案追诉标准规定**
4. 标注每条法规的来源和生效日期
5. 如果无法联网搜索，请基于你的知识提供最相关法条，但需要注明"基于训练数据，请核实最新版本"
6. 输出格式清晰，条文明晰"""

    user_prompt = f"""请检索以下罪名相关的中国法律法规：

罪名：{crime_type}
搜索关键词：{keyword}

请提供：
1. 《刑法》相关条文原文（含条号）
2. 相关司法解释全文或核心条款
3. 立案追诉标准
4. 量刑指导意见相关内容"""

    url = f"{base_url}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    # qwen 模型支持 enable_search 参数，开启内置联网搜索
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        # 开启百炼内置的联网搜索能力
        "enable_search": True,
    }

    try:
        logger.info(f"[法律搜索] 正在搜索: {keyword}")
        with httpx.Client(timeout=timeout, verify=_SSL_VERIFY) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

            if "choices" in data and len(data["choices"]) > 0:
                result = data["choices"][0]["message"]["content"]
                logger.info(f"[法律搜索] 成功获取 {len(result)} 字")
                return result
            else:
                logger.error(f"[法律搜索] 返回异常: {data}")
                return ""
    except Exception as e:
        logger.error(f"[法律搜索] 搜索失败: {e}")
        return ""


# ========== 用户自定义法律知识库 ==========

def list_legal_kb() -> list[dict[str, Any]]:
    """列出所有用户自定义法律条目"""
    items = []
    if not LEGAL_KB_DIR.exists():
        return items

    for f in sorted(LEGAL_KB_DIR.glob("*.md")):
        meta_path = f.with_suffix(".meta.json")
        meta = {}
        if meta_path.exists():
            try:
                with open(meta_path, encoding="utf-8") as mf:
                    meta = json.load(mf)
            except Exception:
                pass

        items.append({
            "id": f.stem,
            "title": meta.get("title", f.stem),
            "crime_type": meta.get("crime_type", ""),
            "created_at": meta.get("created_at", ""),
            "updated_at": meta.get("updated_at", ""),
            "size": f.stat().st_size,
        })

    return items


def get_legal_kb_item(item_id: str) -> dict[str, Any] | None:
    """获取单个法律知识条目"""
    md_path = LEGAL_KB_DIR / f"{item_id}.md"
    meta_path = LEGAL_KB_DIR / f"{item_id}.meta.json"

    if not md_path.exists():
        return None

    content = md_path.read_text(encoding="utf-8")
    meta = {}
    if meta_path.exists():
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            pass

    return {
        "id": item_id,
        "title": meta.get("title", item_id),
        "crime_type": meta.get("crime_type", ""),
        "content": content,
        "created_at": meta.get("created_at", ""),
        "updated_at": meta.get("updated_at", ""),
    }


def create_legal_kb_item(
    title: str,
    content: str,
    crime_type: str = "",
    item_id: str | None = None,
) -> dict[str, Any]:
    """创建新的法律知识条目"""
    if not item_id:
        # 生成 ID：拼音或简短名称 + 时间戳
        import hashlib
        hash_part = hashlib.md5(f"{title}{datetime.now().isoformat()}".encode()).hexdigest()[:8]
        item_id = f"law_{hash_part}"

    md_path = LEGAL_KB_DIR / f"{item_id}.md"
    meta_path = LEGAL_KB_DIR / f"{item_id}.meta.json"

    now = datetime.now().isoformat()
    meta = {
        "title": title,
        "crime_type": crime_type,
        "created_at": now,
        "updated_at": now,
    }

    md_path.write_text(content, encoding="utf-8")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "id": item_id,
        "title": title,
        "crime_type": crime_type,
        "created_at": now,
        "updated_at": now,
        "size": len(content.encode("utf-8")),
    }


def update_legal_kb_item(
    item_id: str,
    title: str | None = None,
    content: str | None = None,
    crime_type: str | None = None,
) -> dict[str, Any] | None:
    """更新法律知识条目"""
    md_path = LEGAL_KB_DIR / f"{item_id}.md"
    meta_path = LEGAL_KB_DIR / f"{item_id}.meta.json"

    if not md_path.exists():
        return None

    # 更新内容
    if content is not None:
        md_path.write_text(content, encoding="utf-8")

    # 更新元数据
    meta = {}
    if meta_path.exists():
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            pass

    if title is not None:
        meta["title"] = title
    if crime_type is not None:
        meta["crime_type"] = crime_type
    meta["updated_at"] = datetime.now().isoformat()

    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "id": item_id,
        "title": meta.get("title", item_id),
        "crime_type": meta.get("crime_type", ""),
        "updated_at": meta["updated_at"],
    }


def delete_legal_kb_item(item_id: str) -> bool:
    """删除法律知识条目"""
    md_path = LEGAL_KB_DIR / f"{item_id}.md"
    meta_path = LEGAL_KB_DIR / f"{item_id}.meta.json"

    deleted = False
    if md_path.exists():
        md_path.unlink()
        deleted = True
    if meta_path.exists():
        meta_path.unlink()
        deleted = True

    return deleted


def get_legal_kb_by_crime(crime_type: str) -> str:
    """
    根据罪名获取匹配的用户自定义法律知识

    Args:
        crime_type: 罪名

    Returns:
        格式化的法律知识文本
    """
    items = list_legal_kb()
    if not items:
        return ""

    # 匹配罪名或通用条目
    matched = []
    for item in items:
        ct = item.get("crime_type", "")
        if ct == crime_type or ct == "" or crime_type in ct or ct in crime_type:
            matched.append(item["id"])

    if not matched:
        return ""

    parts = []
    for item_id in matched:
        detail = get_legal_kb_item(item_id)
        if detail:
            parts.append(f"## 用户法律知识库：{detail['title']}\n\n{detail['content']}")

    return "\n\n".join(parts)


def search_and_merge(crime_type: str) -> str:
    """
    综合搜索并合并法律知识：
    1. LLM 联网搜索法律法规
    2. 用户自定义法律知识库
    3. 合并返回
    """
    parts = []

    # 1. 网络搜索
    net_result = search_laws_by_llm(crime_type)
    if net_result:
        parts.append("## 网络检索法律法规（来自国家法律法规数据库等权威来源）\n")
        parts.append(net_result)
        parts.append("")

    # 2. 用户自定义知识库
    user_kb = get_legal_kb_by_crime(crime_type)
    if user_kb:
        parts.append("## 用户自定义法律知识库")
        parts.append(user_kb)
        parts.append("")

    if not parts:
        return ""

    return "\n\n".join(parts)
