"""
Analysis Engine Mermaid 渲染模块

包含：
- _json_to_mermaid_graph：JSON → Mermaid 图
- _json_to_mermaid_timeline：JSON → Mermaid 时间线
- _legacy_fix_mermaid：修复 Mermaid 语法
- _legacy_fix_mermaid_timeline：修复时间线语法
- _extract_json_and_render：提取 JSON 并渲染
"""
import json
import logging

logger = logging.getLogger(__name__)




# ========== JSON → Mermaid 转换 ==========

def _json_to_mermaid_graph(data: dict) -> str:
    """将 JSON 关系图数据转换为合法 Mermaid 代码"""
    import re as _re
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    subgraphs = data.get("subgraphs", [])
    group_styles = {}

    # 根据 group 定义样式
    group_map = {
        "core": ("核心", "#f9d0a6", "#d48a3c", 2),
        "staff": ("执行", "#a6d8f9", "#3c8ad4", 1),
        "witness": ("证人", "#c6f9a6", "#4bd43c", 1),
        "other": ("其他", "#f0f0f0", "#999", 1),
    }

    lines = ["graph LR"]

    # subgraph 分组
    node_in_sg = {}
    for sg in subgraphs:
        sg_name = _re.sub(r'[^\w\u4e00-\u9fff\s]', '', sg["name"]).strip()
        sg_nodes = sg.get("nodes", [])
        lines.append(f"    subgraph {sg_name}")
        for nid in sg_nodes:
            node = next((n for n in nodes if n["id"] == nid), None)
            if node:
                label = _re.sub(r'[^\w\u4e00-\u9fff\s\(\)（）]', '', node["label"]).strip()
                lines.append(f"        {nid}[{label}]")
                node_in_sg[nid] = True
        lines.append("    end")

    # 不在 subgraph 中的节点
    for node in nodes:
        nid = node["id"]
        if nid not in node_in_sg:
            label = _re.sub(r'[^\w\u4e00-\u9fff\s\(\)（）]', '', node["label"]).strip()
            lines.append(f"    {nid}[{label}]")

    # 边
    for edge in edges:
        frm = edge["from"]
        to = edge["to"]
        label = edge.get("label", "")
        if label:
            label = _re.sub(r'[^\w\u4e00-\u9fff\s\(\)（）]', '', label).strip()
            lines.append(f"    {frm} -- \"{label}\" --> {to}")
        else:
            lines.append(f"    {frm} --> {to}")

    # classDef + class
    used_groups = set()
    for node in nodes:
        g = node.get("group", "other")
        if g in group_map:
            used_groups.add(g)
    for g in used_groups:
        name, fill, stroke, sw = group_map[g]
        lines.append(f"    classDef {g} fill:{fill},stroke:{stroke},stroke-width:{sw}px;")

    for g in used_groups:
        g_nodes = [n["id"] for n in nodes if n.get("group") == g]
        if g_nodes:
            lines.append(f"    class {','.join(g_nodes)} {g};")

    return "\n".join(lines)


def _json_to_mermaid_timeline(data: dict) -> str:
    """将 JSON 时间线数据转换为合法 Mermaid timeline 代码"""
    title = data.get("title", "案件时间线")
    events = data.get("events", [])

    lines = ["timeline", f"    title {title}"]

    for ev in events:
        date_str = str(ev.get("date", ""))
        # 将 HH:MM 转为 HH点MM分，避免 Mermaid timeline 冒号冲突
        import re as _re
        date_str = _re.sub(r'(\d{1,2}):(\d{2})', r'\1点\2分', date_str)

        title_ev = ev.get("title", "")
        evidence = ev.get("evidence", [])
        desc_parts = [title_ev]
        if evidence:
            desc_parts.extend(evidence)
        desc = ", ".join(desc_parts)

        lines.append(f"    {date_str} : {desc}")

    return "\n".join(lines)


def _legacy_fix_mermaid(text: str) -> str:
    """旧的后处理修复逻辑（回退方案）"""
    import re as _re
    mermaid_blocks = _re.findall(r'```mermaid\n(.*?)```', text, _re.DOTALL)
    for block in mermaid_blocks:
        lines = block.split('\n')
        new_lines = []
        for line in lines:
            m_sg = _re.match(r'(\s*subgraph\s+)([^\n\[]*)\[([^\]]*)\]', line)
            if m_sg:
                prefix, name, suffix = m_sg.groups()
                clean_name = _re.sub(r'[^\w\u4e00-\u9fff]', '', name).strip()
                line = f'{prefix}{clean_name}-{suffix}'
                new_lines.append(line)
                continue
            chained = _re.findall(r'(.+?)\s*(--\s*"[^"]*"\s*-->|-->)\s*(\w+)\s*(?:--\s*"[^"]*"\s*-->|-->)\s*(\w+)', line)
            if chained:
                for src, arrow, mid, tgt in chained:
                    new_lines.append(f'{src.strip()} {arrow.strip()} {mid}')
                    new_lines.append(f'{mid} --> {tgt}')
                continue
            m = _re.match(r'^(\s*)(.+?)(\s*--\s*"[^"]*"\s*-->|\s*-->\s*)(.+)$', line)
            if m:
                indent, sources_str, arrow, targets_str = m.groups()
                arrow_norm = arrow.strip()
                sources = [s.strip() for s in sources_str.split('&')]
                targets = [t.strip() for t in targets_str.split('&')]
                expanded = '\n'.join(
                    f'{indent}{src} {arrow_norm} {tgt}'
                    for src in sources for tgt in targets
                )
                new_lines.append(expanded)
            else:
                new_lines.append(line)
        fixed_block = '\n'.join(new_lines)
        text = text.replace(block, fixed_block)
    return text


def _legacy_fix_mermaid_timeline(text: str) -> str:
    """旧的 timeline 后处理修复逻辑（回退方案）"""
    import re as _re
    timeline_blocks = _re.findall(r'```mermaid\n(.*?)```', text, _re.DOTALL)
    for block in timeline_blocks:
        if 'timeline' not in block:
            continue
        fixed = _re.sub(r'(\d{1,2}):(\d{2})', lambda m: f'{m.group(1)}点{m.group(2)}分', block)
        text = text.replace(block, fixed)
    return text


def _extract_json_and_render(text: str, mermaid_fn) -> str:
    """
    从 LLM 输出中提取 JSON 代码块，
    生成 Mermaid 图，替换 JSON 块。
    """
    import re as _re
    json_blocks = _re.findall(r'```json\n(.*?)```', text, _re.DOTALL)
    for block in json_blocks:
        try:
            data = json.loads(block.strip())
            mermaid = mermaid_fn(data)
            replacement = f"```mermaid\n{mermaid}\n```"
            text = text.replace(f"```json\n{block}```", replacement, 1)
        except (json.JSONDecodeError, KeyError, TypeError):
            pass  # JSON 解析失败，保留原始内容
    return text

