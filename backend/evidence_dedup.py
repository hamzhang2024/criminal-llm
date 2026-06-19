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
    - 去除括号内的日期/次数（如"(2024.10.29)"、"(第一次)"），保留人名（如"(王作通)"）
    - 统一全角括号为半角
    - 转小写
    """
    if not name:
        return ""
    s = name.strip()
    # 去除数字前缀
    s = re.sub(r'^\d+[_\s]*', '', s)
    # 去除括号内的日期和次数，保留人名
    # 日期：2024.10.29 / 2024-10-29 / 2024年10月29日 / 20241029
    s = re.sub(r'[（(]\s*\d{4}[\.\-/年]\d{1,2}[\.\-/月]\d{1,2}[）)]', '', s)
    s = re.sub(r'[（(]\s*\d{8}[）)]', '', s)
    # 次数：第一次/第1次（保留人名，仅去掉次数标记）
    s = re.sub(r'第\s*\d+\s*次', '', s)
    # 清理空括号
    s = re.sub(r'[（(]\s*[）)]', '', s)
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


def _extract_interrogatee_and_date(name: str, persons: str, page_range: str = "") -> tuple:
    """从讯问笔录证据中提取被讯问人、日期、次数

    返回 (被讯问人, 日期字符串, 次数int)，无法提取则对应位置为 ("", "", 0)

    次数用于同人多笔的排序（第一次/第二次...），日期从 name 和 page_range 提取。
    """
    # 被讯问人：优先从 persons 字段取第一个，或从名称括号内取
    interrogatee = ""
    if persons:
        first = persons.replace("，", ",").split(",")[0].strip()
        if first and len(first) <= 20:
            interrogatee = first
    # 名称中的括号内容可能是人名（如"讯问笔录(张三)"）
    m = re.search(r'[（(]([^）)]+)[）)]', name or "")
    if m and not interrogatee:
        candidate = m.group(1).strip()
        if candidate and len(candidate) <= 20:
            # 排除纯日期/数字内容（如 2024.10.29、20241029）
            if not re.search(r'\d{4}', candidate):
                # 排除纯"第N次"形式（但保留含人名的"张三第一次"）
                if not re.fullmatch(r'第\s*\d+\s*次.*', candidate):
                    interrogatee = candidate

    # 次数：从 name 提取"第N次"作为排序键
    sequence = 0
    seq_match = re.search(r'第\s*(\d+)\s*次', name or "")
    if seq_match:
        sequence = int(seq_match.group(1))

    # 日期：从 name 和 page_range 中找日期模式
    date_str = ""
    date_patterns = [r'(\d{4}[\.\-/年]\d{1,2}[\.\-/月]\d{1,2})', r'(\d{4}\d{2}\d{2})']
    for source_text in [name or "", page_range or ""]:
        for pattern in date_patterns:
            m = re.search(pattern, source_text)
            if m:
                date_str = m.group(1)
                break
        if date_str:
            break

    return (interrogatee, date_str, sequence)


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

    # ── 第2步：同人多份讯问笔录关联（含程序文书附属成员）──
    # 锚点类型：供述/证言类（作为分组主体）
    anchor_types = {"犯罪嫌疑人供述和辩解", "证人证言", "被害人陈述"}
    # 附属类型：程序文书（提讯证/告知书等，按 persons[0] 匹配并入同组）
    auxiliary_keywords = ["提讯", "提解", "权利义务告知", "讯问通知", "询问通知", "诉讼权利"]

    def _is_auxiliary(ev_type: str, name: str) -> bool:
        """判断是否为讯问组的附属程序文书"""
        if "程序性文书" not in ev_type and "书证" not in ev_type:
            return False
        name_lower = name or ""
        return any(kw in name_lower for kw in auxiliary_keywords)

    # 按 (被讯问人,) 分组，同人多份合并到同一组
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for ev in evidence_list:
        ev_type = (ev.get("type") or "").strip()
        name = ev.get("name", "")
        # 跳过重复证据（只关联源证据）
        if ev.get("duplicate_of") is not None:
            continue

        is_anchor = ev_type in anchor_types
        is_aux = _is_auxiliary(ev_type, name)
        if not is_anchor and not is_aux:
            continue

        interrogatee, date_str, sequence = _extract_interrogatee_and_date(
            name, ev.get("persons", ""), ev.get("page_range", "")
        )
        if not interrogatee:
            continue
        groups.setdefault(interrogatee, []).append({
            "id": ev.get("id"),
            "date_str": date_str,
            "sequence": sequence,
            "is_anchor": is_anchor,
            "is_aux": is_aux,
        })

    # 写入关联关系：只有当组内至少有 1 个锚点 + 总成员 >= 2 时才建立关联
    link_count = 0
    for interrogatee, members in groups.items():
        anchor_count = sum(1 for m in members if m["is_anchor"])
        if anchor_count < 1 or len(members) < 2:
            continue
        # 组内按 (date_str, sequence) 排序，锚点优先
        members.sort(key=lambda m: (m["date_str"] or "", m["sequence"], not m["is_anchor"]))
        ev_ids = [m["id"] for m in members]
        for ev in evidence_list:
            if ev.get("id") in ev_ids:
                others = [i for i in ev_ids if i != ev.get("id")]
                ev["related_evidence_ids"] = others
                ev["dedup_note"] = (ev.get("dedup_note") or "") + f"; 同人({interrogatee})组合关联: {others}"
                link_count += 1

    if dup_count > 0 or link_count > 0:
        logger.info(f"[去重关联] 标记重复 {dup_count} 份，建立关联 {link_count} 处")

    return evidence_list


def group_evidence_by_chain(evidence_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """对证据列表按程序链条分组（供组合质证使用）

    首期只做讯问笔录组（interrogation）：
    - 以 related_evidence_ids 非空的讯问笔录为锚
    - 合并同组提讯证/告知书（按 persons[0] 精确匹配）
    - 至少 2 个成员才成组

    Returns:
        evidence_groups 数组，每组：
        {group_id, group_type, group_label, member_refs, anchor_evidence_id}
    """
    groups: List[Dict[str, Any]] = []
    anchor_types = {"犯罪嫌疑人供述和辩解", "证人证言", "被害人陈述"}

    # 收集有 related_evidence_ids 的锚点证据，按 interrogatee 聚合
    # related_evidence_ids 已包含同组所有成员 id（含附属文书）
    processed_ids = set()
    group_counter = 0

    for ev in evidence_list:
        ev_id = ev.get("id")
        related = ev.get("related_evidence_ids") or []
        ev_type = (ev.get("type") or "").strip()

        # 只以供述/证言类锚点为起点，且尚未处理过
        if ev_type not in anchor_types or ev_id in processed_ids or not related:
            continue

        # 收集本组所有成员（锚点 + related）
        member_ids = [ev_id] + [i for i in related if i != ev_id]
        # 去重并保持顺序
        seen = set()
        ordered_members = []
        for mid in member_ids:
            if mid not in seen:
                seen.add(mid)
                ordered_members.append(mid)

        if len(ordered_members) < 2:
            continue

        # 标记已处理
        for mid in ordered_members:
            processed_ids.add(mid)

        # 构造组标签
        interrogatee = (ev.get("persons") or "").replace("，", ",").split(",")[0].strip()
        anchor_count = sum(1 for mid in ordered_members
                           if any(e.get("id") == mid and (e.get("type") or "").strip() in anchor_types
                                  for e in evidence_list))
        aux_count = len(ordered_members) - anchor_count
        label_parts = [f"{interrogatee}讯问组"]
        detail_parts = []
        if anchor_count:
            detail_parts.append(f"{anchor_count}笔录")
        if aux_count:
            detail_parts.append(f"{aux_count}程序文书")
        if detail_parts:
            label_parts.append(f"({'+'.join(detail_parts)})")
        group_label = "".join(label_parts)

        group_counter += 1
        groups.append({
            "group_id": f"G{group_counter:03d}",
            "group_type": "interrogation",
            "group_label": group_label,
            "member_refs": ordered_members,
            "anchor_evidence_id": ev_id,
        })

    if groups:
        logger.info(f"[证据分组] 生成 {len(groups)} 个组合（讯问笔录组）")
    return groups
