"""
关联信息结构化提取器

从 LLM 提取的 related_entities 文本中解析出结构化的关联信息。
支持格式：[类型] 内容 — 涉及人员/说明

输出用于前端展示和下游分析流水线。
"""

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 关联信息类型识别模式
_ENTITY_PATTERNS = {
    "手机号": r"1[3-9]\d{9}",
    "微信号": r"(?:微信[号帐]?[：:]?\s*)([\w\-]{6,20})",
    "QQ号": r"(?:QQ[号帐]?[：:]?\s*)(\d{5,12})",
    "银行卡": r"(?:银行卡?[号帐]?[：:]?\s*|账号[：:]?\s*)(\d{15,19})",
    "身份证": r"\d{6}19\d{2}[01]\d[0-3]\d\d{3}[\dXx]|\d{6}20\d{2}[01]\d[0-3]\d\d{3}[\dXx]",
    "车牌号": r"[一-龥][A-Z][A-Z0-9]{5,6}",
}

# 标记前缀识别
_PREFIX_PATTERNS = [
    ("手机号", r"手机\s*号?\s*[码帐]?[：:]?"),
    ("手机号", r"电话\s*号?\s*[码帐]?[：:]?"),
    ("微信号", r"微信\s*号?\s*[码帐]?[：:]?"),
    ("微信号", r"微\s*信[：:]?"),
    ("QQ号", r"QQ\s*号?\s*[码帐]?[：:]?"),
    ("银行卡", r"银行\s*卡?\s*号?\s*[码帐]?[：:]?"),
    ("银行卡", r"卡\s*号?[：:]?"),
    ("银行卡", r"账号[：:]?"),
    ("身份证", r"身份证?\s*号?[：:]?"),
    ("车牌号", r"车牌?\s*号?[：:]?"),
]


def parse_related_entities(related_text: str) -> list[dict[str, str]]:
    """从 related_entities 文本中提取结构化关联信息

    Args:
        related_text: LLM 返回的 related_entities 字段内容

    Returns:
        [{ "type": "手机号", "value": "13800138000", "person": "项少甫", "source_line": "[手机号] 13800138000 — 项少甫使用" }, ...]
    """
    if not related_text or related_text.strip() in ("无", "无关联信息", ""):
        return []

    entities = []

    # 尝试按行解析
    lines = related_text.strip().split("\n")
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 去除 markdown 列表前缀
        line = re.sub(r"^[-*+]\s*", "", line)

        # 尝试匹配 [类型] 内容 — 人员 格式
        m = re.match(r"\[([^\]]+)\]\s*(.*?)(?:—|[-–])\s*(.*?)$", line)
        if m:
            etype = m.group(1).strip()
            value = m.group(2).strip()
            person = m.group(3).strip()
            entities.append({
                "type": etype,
                "value": value,
                "person": person,
                "source_line": line,
            })
            continue

        # 尝试匹配 类型[：:]内容 — 人员 格式
        m = re.match(r"([^：:]+)[：:]\s*(.*?)(?:—|[-–])\s*(.*?)$", line)
        if m:
            etype = m.group(1).strip()
            value = m.group(2).strip()
            person = m.group(3).strip()
            # 如果类型太长，可能不是类型标记，跳过
            if len(etype) <= 10:
                entities.append({
                    "type": etype,
                    "value": value,
                    "person": person,
                    "source_line": line,
                })
                continue

    return entities


def extract_entities_from_text(text: str) -> list[dict[str, str]]:
    """从任意文本中直接提取关联信息（不依赖 LLM 格式化输出）

    使用正则直接扫描文本，捕获手机号、微信号、银行卡号等。
    作为 LLM 提取的补充兜底。

    Args:
        text: 证据文本（可以是 summary 或原始 MD 内容）

    Returns:
        [{ "type": "手机号", "value": "13800138000", "person": "", "method": "regex" }, ...]
    """
    entities = []
    seen = set()

    for etype, pattern in _ENTITY_PATTERNS.items():
        for m in re.finditer(pattern, text):
            value = m.group(0)
            if value not in seen:
                seen.add(value)
                entities.append({
                    "type": etype,
                    "value": value,
                    "person": "",
                    "method": "regex",
                })

    return entities


def merge_entities(parsed: list[dict], regex_extracted: list[dict]) -> list[dict]:
    """合并 LLM 解析结果和正则提取结果，去重

    Args:
        parsed: LLM 解析结果
        regex_extracted: 正则提取结果

    Returns:
        去重合并后的关联信息列表
    """
    seen_values = set()
    merged = []

    # LLM 结果优先（带人员信息）
    for e in parsed:
        val = e.get("value", "")
        if val and val not in seen_values:
            seen_values.add(val)
            merged.append(e)

    # 补充正则结果（仅补充未见过的值）
    for e in regex_extracted:
        val = e.get("value", "")
        if val and val not in seen_values:
            seen_values.add(val)
            merged.append(e)

    return merged


def build_related_entities_json(all_evidence: list[dict]) -> dict:
    """从所有证据的 index.json 数据中，汇总构建全局关联信息 JSON

    Args:
        all_evidence: index.json 中的 evidence 列表

    Returns:
        {
            "case_id": "",
            "summary": {"手机号": [...], "微信号": [...], ...},
            "entities": [{ "type": "手机号", "value": "13800138000", "person": "项少甫", "evidence_ids": [1, 3, 5] }, ...]
        }
    """
    from collections import defaultdict

    # 全局实体库：value -> { type, value, persons, evidence_ids, source_lines }
    entity_map: dict[str, dict] = {}

    for ev in all_evidence:
        ev_id = ev.get("id") or ev.get("_temp_id", 0)
        ev_name = ev.get("name", "")

        # 从相关实体文本解析
        related_text = ev.get("related_entities", "")
        if related_text:
            parsed = parse_related_entities(related_text)
            for e in parsed:
                val = e["value"]
                if val not in entity_map:
                    entity_map[val] = {
                        "type": e["type"],
                        "value": val,
                        "persons": set(),
                        "evidence_ids": set(),
                        "source_lines": [],
                    }
                if e.get("person"):
                    entity_map[val]["persons"].add(e["person"])
                entity_map[val]["evidence_ids"].add(ev_id)
                entity_map[val]["source_lines"].append(e["source_line"])

        # 从 summary 和 key_facts 用正则补充
        combined_text = f"{ev.get('summary', '')} {ev.get('key_facts', '')}"
        regex_entities = extract_entities_from_text(combined_text)
        for e in regex_entities:
            val = e["value"]
            if val not in entity_map:
                entity_map[val] = {
                    "type": e["type"],
                    "value": val,
                    "persons": set(),
                    "evidence_ids": set(),
                    "source_lines": [],
                }
            entity_map[val]["evidence_ids"].add(ev_id)

    # 去重后的按类型分组
    by_type: dict[str, list[dict]] = defaultdict(list)
    entities_list = []
    for val, data in entity_map.items():
        entry = {
            "type": data["type"],
            "value": val,
            "persons": sorted(data["persons"]) if data["persons"] else [],
            "evidence_ids": sorted(data["evidence_ids"]),
            "evidence_count": len(data["evidence_ids"]),
        }
        entities_list.append(entry)
        by_type[data["type"]].append(entry)

    return {
        "generated_at": __import__("datetime").datetime.now().isoformat(),
        "total_entities": len(entities_list),
        "summary": {
            etype: [{"value": e["value"], "persons": e["persons"], "evidence_count": e["evidence_count"]} for e in entries]
            for etype, entries in sorted(by_type.items())
        },
        "entities": sorted(entities_list, key=lambda x: (x["type"], x["value"])),
    }
