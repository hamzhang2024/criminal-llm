"""证据提取指引：罪名 → 构成要件 + 类案裁判规则（每案件一次，缓存复用）

设计：
- LLM 拆解要件（行为特征/情节要素），类案走 case_framework（无 Key 静默降级）
- 缓存到 evidence/legal_framework.json，重复提取/断点续传不重复调用
- 输出作为提取 prompt 的固定前缀（符合缓存优化：固定前缀在前）
"""
import json
import logging
from pathlib import Path

from llm_client import get_llm_client

logger = logging.getLogger(__name__)

CACHE_FILE = "legal_framework.json"


async def build_extraction_framework(evidence_dir: Path, charges: list[str], keywords: list[str]) -> dict:
    """构建（或读缓存）提取指引框架。返回 {charges, elements, case_rules}"""
    cache_path = Path(evidence_dir) / CACHE_FILE
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # 要件拆解（LLM，失败降级为空）
    elements: list[str] = []
    try:
        client = get_llm_client()
        charges_str = "、".join(charges)
        text = await client.chat([
            {"role": "system", "content": "你是刑事律师。请列出以下罪名在司法实践中认定时的关键要件/事实要素（行为特征、情节要素、对象特征），每行一个，5-8 个，只输出要素名。例如非法经营罪（支付结算类）：虚构交易、资金支付结算、信用卡套现、POS 机。"},
            {"role": "user", "content": f"罪名：{charges_str}"},
        ])
        elements = [line.strip("- •　 ") for line in text.strip().split("\n") if line.strip()][:8]
    except Exception as e:
        logger.warning(f"[提取指引] 要件拆解失败（降级为仅罪名）: {e}")

    # 类案裁判规则（无 Key 静默降级）
    case_rules: dict = {}
    try:
        from case_framework import fetch_case_rules
        case_rules = fetch_case_rules(charges, keywords=keywords, size=2)
    except Exception as e:
        logger.warning(f"[提取指引] 类案检索降级: {e}")

    framework = {"charges": charges, "elements": elements, "case_rules": case_rules}
    try:
        cache_path.write_text(json.dumps(framework, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return framework


def framework_prompt_prefix(framework: dict) -> str:
    """提取 prompt 固定前缀（空框架返回空串，保持原行为）"""
    elements = framework.get("elements") or []
    case_rules = framework.get("case_rules") or {}
    if not elements and not case_rules:
        return ""
    parts = ["\n\n**本案法律框架（供提取时关联标注，供分析参考）：**\n"]
    if elements:
        parts.append("关键要件（提取时为每份证据标注关联要件）：" + "、".join(elements))
    for charge, rules_md in case_rules.items():
        parts.append(f"\n{rules_md[:3000]}")
    parts.append("\n**提取要求（要件关联）：** 每份证据除标注关联罪名外，还必须从上述要件中选择关联要件（elements 字段，0-3 个）；无关联则为空列表。\n")
    return "\n".join(parts)
