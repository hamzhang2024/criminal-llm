"""
证据去重与关联模块

合并阶段对 all_evidence 做：
1. 重复标记：按 (name 规范化 + type + page_range) 三元组哈希标记疑似重复
2. 同人关联：同人多份讯问笔录按 (被讯问人 + 讯问日期) 建立关联

原则：仅关联不合并，避免误删证据。重复证据保留但标注 duplicate_of 字段。
"""
import hashlib
import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _normalize_name(name: str) -> str:
    """规范化证据名称用于去重比较

    - 去除首尾空白
    - 去除编号前缀（如"001_"）
    - 去除括号内的日期/编号（如"(2024.10.29)"、"(第一次)"）
    - 统一全角括号为半角
    - 转小写
    """
    if not name:
        return ""
    s = name.strip()
    # 去除数字前缀
    s = re.sub(r'^\d+[_\s]*', '', s)
    # 去除括号内容（日期、次数等）
    s = re.sub(r'[（(][^）)]*[）)]', '', s)
    # 统一空白
    s = re.sub(r'\s+', '', s)
    return s.lower()


def _dedup_key(ev: Dict[str, Any]) -> str:
    """生成去重哈希键

    当 page_range 为空时，加入 key_facts 前 50 字符避免同名同类型不同内容的证据被误判为重复。
    """
    name = _normalize_name(ev.get("name", ""))
    ev_type = (ev.get("type") or "").strip()
    page_range = (ev.get("page_range") or "").strip()
    # page_range 为空时用 key_facts 前缀增强区分
    if not page_range:
        key_facts_prefix = (ev.get("key_facts") or "").strip()[:50]
        raw = f"{name}|{ev_type}|{key_facts_prefix}"
    else:
        raw = f"{name}|{ev_type}|{page_range}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _extract_interrogatee_and_date(name: str, persons: str) -> tuple:
    """从讯问笔录证据中提取被讯问人和日期

    返回 (被讯问人, 日期字符串)，无法提取则返回 ("", "")
    """
    # 被讯问人：优先从 persons 字段取第一个，或从名称括号内取
    interrogatee = ""
    if persons:
        # persons 是逗号分隔的人员列表，第一个通常是被讯问人
        first = persons.replace("，", ",").split(",")[0].strip()
        if first and len(first) <= 20:
            interrogatee = first
    # 名称中的括号内容可能是人名（如"讯问笔录(张三)"）
    m = re.search(r'[（(]([^）)]+)[）)]', name or "")
    if m and not interrogatee:
        candidate = m.group(1).strip()
        # 过滤日期、次数等非人名内容
        if candidate and len(candidate) <= 20 and "次" not in candidate:
            # 排除纯日期/数字内容（如 2024.10.29、20241029）
            if not re.search(r'\d{4}', candidate):
                interrogatee = candidate

    # 日期：从 page_range 或名称中找日期模式
    date_str = ""
    # page_range 可能含日期
    for pattern in [r'(\d{4}[\.\-/年]\d{1,2}[\.\-/月]\d{1,2})', r'(\d{4}\d{2}\d{2})']:
        m = re.search(pattern, name or "")
        if m:
            date_str = m.group(1)
            break

    return (interrogatee, date_str)


def dedup_and_link(evidence_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """对证据列表做去重标记和同人多笔关联

    Args:
        evidence_list: 证据列表（含 id/name/type/page_range/persons 等字段）

    Returns:
        处理后的证据列表，每项可能增加：
        - duplicate_of: int - 疑似重复的源证据 id（None 表示非重复）
        - related_evidence_ids: List[int] - 同人多笔关联的证据 id 列表
        - dedup_note: str - 去重/关联说明
    """
    if not evidence_list:
        return evidence_list

    # ── 第1步：重复标记 ──
    # 同一 dedup_key 的证据只保留第一个为"源"，其余标记 duplicate_of
    key_to_first_id: Dict[str, int] = {}
    dup_count = 0
    for ev in evidence_list:
        ev["duplicate_of"] = None
        ev["related_evidence_ids"] = []
        ev["dedup_note"] = ""

        key = _dedup_key(ev)
        # 空名称或空类型的不参与去重（避免误判）
        name_norm = _normalize_name(ev.get("name", ""))
        if not name_norm or not (ev.get("type") or "").strip():
            continue

        if key in key_to_first_id:
            ev["duplicate_of"] = key_to_first_id[key]
            ev["dedup_note"] = f"疑似与证据{key_to_first_id[key]}重复"
            dup_count += 1
            logger.info(f"[去重] 证据{ev.get('id')} 疑似重复 证据{key_to_first_id[key]}: {ev.get('name')}")
        else:
            key_to_first_id[key] = ev.get("id")

    # ── 第2步：同人多份讯问笔录关联 ──
    # 按 (被讯问人, 日期) 分组，同组的证据互相关联
    # 仅对"供述/证言"类证据生效
    interrogation_types = {"犯罪嫌疑人供述和辩解", "证人证言", "被害人陈述"}
    groups: Dict[tuple, List[int]] = {}
    for ev in evidence_list:
        ev_type = (ev.get("type") or "").strip()
        if ev_type not in interrogation_types:
            continue
        # 跳过重复证据（只关联源证据）
        if ev.get("duplicate_of") is not None:
            continue
        interrogatee, date_str = _extract_interrogatee_and_date(
            ev.get("name", ""), ev.get("persons", "")
        )
        if not interrogatee:
            continue
        group_key = (interrogatee, date_str) if date_str else (interrogatee, "")
        groups.setdefault(group_key, []).append(ev.get("id"))

    # 写入关联关系
    link_count = 0
    for group_key, ev_ids in groups.items():
        if len(ev_ids) < 2:
            continue
        interrogatee = group_key[0]
        for ev in evidence_list:
            if ev.get("id") in ev_ids:
                others = [i for i in ev_ids if i != ev.get("id")]
                ev["related_evidence_ids"] = others
                ev["dedup_note"] = (ev.get("dedup_note") or "") + f"; 同人({interrogatee})多笔关联: {others}"
                link_count += 1

    if dup_count > 0 or link_count > 0:
        logger.info(f"[去重关联] 标记重复 {dup_count} 份，建立关联 {link_count} 处")

    return evidence_list
