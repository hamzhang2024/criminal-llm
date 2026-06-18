"""
证据关联图谱模块

基于 persons/related_entities/contradiction_hints + 同案多笔关联，
生成 Mermaid 图谱（人物节点 + 共现/矛盾/引用边）。
供 stage_2 人物关系分析复用。
"""
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def generate_evidence_graph(evidence_dir: Path, case_id: str = "") -> Dict[str, Any]:
    """生成证据关联图谱

    Args:
        evidence_dir: evidence/ 目录路径
        case_id: 案件 ID

    Returns:
        {
            "case_id": str,
            "mermaid": str,          # Mermaid 图谱代码
            "nodes": List[dict],     # 节点列表
            "edges": List[dict],     # 边列表
            "stats": dict,           # 统计信息
        }
    """
    index_file = evidence_dir / "index.json"
    if not index_file.exists():
        return {"case_id": case_id, "mermaid": "", "nodes": [], "edges": [], "stats": {}, "error": "证据清单不存在"}

    try:
        index_data = json.loads(index_file.read_text(encoding="utf-8"))
    except Exception as e:
        return {"case_id": case_id, "mermaid": "", "nodes": [], "edges": [], "stats": {}, "error": f"证据清单读取失败: {e}"}

    evidence_list = index_data.get("evidence", [])
    if not evidence_list:
        return {"case_id": case_id, "mermaid": "", "nodes": [], "edges": [], "stats": {}, "error": "无证据数据"}

    # ── 第1步：提取人物节点（从 persons 字段） ──
    person_to_evidence: Dict[str, List[int]] = {}  # 人物 -> 出现的证据 id 列表
    for ev in evidence_list:
        persons_raw = ev.get("persons", "") or ""
        if not persons_raw.strip():
            continue
        ev_id = ev.get("id", 0)
        for name in re.split(r"[，,、]", persons_raw):
            name = name.strip()
            if name and len(name) <= 20:
                person_to_evidence.setdefault(name, []).append(ev_id)

    # 只保留出现 >=2 次的人物（避免图谱过于稀疏）
    frequent_persons = {p: eids for p, eids in person_to_evidence.items() if len(eids) >= 2}
    all_persons = list(frequent_persons.keys())

    # ── 第2步：构建共现边（两人同时出现在同一证据） ──
    co_occurrence: Dict[tuple, int] = {}  # (personA, personB) -> 共现次数
    for ev in evidence_list:
        persons_raw = ev.get("persons", "") or ""
        if not persons_raw.strip():
            continue
        names_in_ev = []
        for name in re.split(r"[，,、]", persons_raw):
            name = name.strip()
            if name in frequent_persons and name not in names_in_ev:
                names_in_ev.append(name)
        # 两两组合
        for i in range(len(names_in_ev)):
            for j in range(i + 1, len(names_in_ev)):
                pair = tuple(sorted([names_in_ev[i], names_in_ev[j]]))
                co_occurrence[pair] = co_occurrence.get(pair, 0) + 1

    # ── 第3步：构建矛盾边（contradiction_hints 中提到的人物之间） ──
    # 矛盾边为人物↔人物（hint 中提到的两人之间），避免引用未定义的证据节点
    contradiction_edges: List[dict] = []
    for ev in evidence_list:
        hint = (ev.get("contradiction_hints") or "").strip()
        if not hint or hint == "无":
            continue
        mentioned = [p for p in all_persons if p in hint]
        # 两两组合建立矛盾边
        for i in range(len(mentioned)):
            for j in range(i + 1, len(mentioned)):
                contradiction_edges.append({
                    "from": mentioned[i], "to": mentioned[j], "type": "contradiction", "label": "矛盾提示"
                })

    # ── 第4步：构建同人多笔关联边（人物节点之间的关联） ──
    # 关联边的两端都是该被讯问人，渲染为人物节点的自指标注
    relation_edges: List[dict] = []
    for ev in evidence_list:
        related = ev.get("related_evidence_ids", []) or []
        if not related:
            continue
        persons_raw = ev.get("persons", "") or ""
        if not persons_raw.strip():
            continue
        first_person = persons_raw.replace("，", ",").split(",")[0].strip()
        if first_person and first_person in all_persons:
            relation_edges.append({
                "from": first_person, "to": first_person, "type": "related",
                "label": f"多笔笔录({len(related)+1}份)"
            })

    # ── 第5步：生成 Mermaid 图谱 ──
    # 限制节点数量避免图谱过大（取前 30 个高频人物）
    top_persons = sorted(frequent_persons.items(), key=lambda x: -len(x[1]))[:30]
    top_person_names = [p[0] for p in top_persons]

    def _escape_label(s: str) -> str:
        """转义 Mermaid label 中的特殊字符"""
        return s.replace('"', '&quot;').replace(']', '&#93;').replace('[', '&#91;')

    mermaid_lines = ["graph TD"]
    # 人物节点（label 转义）
    for name in top_person_names:
        safe_id = _safe_id(name)
        ev_count = len(frequent_persons[name])
        escaped = _escape_label(name)
        mermaid_lines.append(f'    {safe_id}["{escaped}<br/><small>{ev_count}份证据</small>"]')

    # 共现边（只画共现 >=2 次的，且两端都在 top_person_names）
    mermaid_lines.append("")
    mermaid_lines.append("    %% 共现关系")
    co_edges_added = 0
    for (a, b), count in sorted(co_occurrence.items(), key=lambda x: -x[1]):
        if count < 2:
            continue
        if a not in top_person_names or b not in top_person_names:
            continue
        mermaid_lines.append(f'    {_safe_id(a)} ---|共现{count}次| {_safe_id(b)}')
        co_edges_added += 1
        if co_edges_added >= 50:  # 限制边数量
            break

    # 矛盾边（人物↔人物，过滤到 top_person_names）
    contradiction_rendered = 0
    for e in contradiction_edges:
        if e["from"] not in top_person_names or e["to"] not in top_person_names:
            continue
        mermaid_lines.append(f'    {_safe_id(e["from"])} -.->|矛盾| {_safe_id(e["to"])}')
        contradiction_rendered += 1
        if contradiction_rendered >= 20:
            break

    # 关联边（同人多笔，渲染为自指虚线标注）
    relation_rendered = 0
    for e in relation_edges:
        if e["from"] not in top_person_names:
            continue
        escaped_label = _escape_label(e["label"])
        from_id = _safe_id(e["from"])
        to_id = _safe_id(e["to"])
        mermaid_lines.append(f'    {from_id} -.->|{escaped_label}| {to_id}')
        relation_rendered += 1
        if relation_rendered >= 20:
            break

    mermaid = "\n".join(mermaid_lines)

    stats = {
        "total_evidence": len(evidence_list),
        "persons_extracted": len(person_to_evidence),
        "frequent_persons": len(frequent_persons),
        "co_occurrence_edges": co_edges_added,
        "contradiction_edges": contradiction_rendered,
        "relation_edges": relation_rendered,
    }

    logger.info(f"[证据图谱] case={case_id} 人物={len(frequent_persons)} 共现边={co_edges_added} 矛盾边={len(contradiction_edges)}")

    return {
        "case_id": case_id,
        "mermaid": mermaid,
        "nodes": [{"id": _safe_id(n), "label": n, "evidence_count": len(frequent_persons[n])} for n in top_person_names],
        # edges 仅返回 top_person_names 之间的共现边，与 Mermaid 渲染一致
        "edges": [{"from": _safe_id(a), "to": _safe_id(b), "type": "co_occurrence", "weight": c}
                  for (a, b), c in co_occurrence.items()
                  if c >= 2 and a in top_person_names and b in top_person_names][:50],
        "stats": stats,
    }


def _safe_id(name: str) -> str:
    """将人名转为安全的 Mermaid 节点 ID"""
    # 用 hash 保证唯一且安全
    return "p" + hashlib.md5(name.encode("utf-8")).hexdigest()[:8]
