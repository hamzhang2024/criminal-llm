"""
Stage API 解析函数模块

包含：
- _parse_person_relation：从 stage_2 Markdown 解析人物关系
- _parse_event_timeline：从 stage_2 Markdown 解析事件时间线
"""
import logging

logger = logging.getLogger(__name__)


def _parse_person_relation(content: str) -> dict:
    """从 stage_2 Markdown 解析人物关系数据"""
    import re

    nodes = []
    edges = []

    # 先解析表格获取角色信息（表格中有明确的角色列）
    name_role_map = {}  # 姓名 -> 角色
    role_map = {
        "被告人": "defendant",
        "嫌疑人": "defendant",
        "主犯": "defendant",
        "从犯": "co defendant",
        "同案犯": "co defendant",
        "共犯": "co defendant",
        "证人": "witness",
        "关联人": "witness",
        "关系人": "witness",
        "被害人": "victim",
        "受害人": "victim",
        "介绍人": "witness",
        "保证人": "witness",
    }

    lines = content.split('\n')
    in_table = False
    for line in lines:
        if '|---' in line or '| ---' in line:
            in_table = True
            continue
        if not in_table or not line.strip().startswith('|'):
            continue

        cols = [c.strip() for c in line.split('|') if c.strip()]
        if len(cols) < 2:
            continue

        name = cols[0].replace('*', '').strip()
        role_str = cols[1] if len(cols) > 1 else ""

        if name == "姓名" or "涉案人员" in name:
            continue

        # 确定角色
        node_role = "other"
        for key, val in role_map.items():
            if key in role_str:
                node_role = val
                break

        if name and len(name) <= 10:
            name_role_map[name] = node_role

    # 方法1：解析 mermaid graph 代码块（获取关系边）
    mermaid_match = re.search(r'```mermaid\s*graph\s*\w+\s*(.*?)```', content, re.DOTALL)
    if mermaid_match:
        mermaid_code = mermaid_match.group(1)

        # 解析节点定义：A[姓名], B[姓名], etc.
        node_pattern = r'([A-Z])\[([^\]]+)\]'
        node_matches = re.findall(node_pattern, mermaid_code)
        node_id_map = {}  # A -> 姓名

        for node_id, node_name in node_matches:
            # 每个 ID 只取第一次出现的定义，避免 subgraph 中重复定义覆盖
            if node_id not in node_id_map:
                node_id_map[node_id] = node_name
            # 从表格角色映射中获取角色，优先级最高
            role = "other"

            # 精确匹配
            if node_name in name_role_map:
                role = name_role_map[node_name]
            else:
                # 前缀匹配：戴子佳(佳诚数码) -> 戴子佳
                base_name = node_name.split('(')[0].strip() if '(' in node_name else node_name
                if base_name in name_role_map:
                    role = name_role_map[base_name]

            # 如果表格中没有，尝试从名字推断
            if role == "other":
                if "被告" in node_name or "嫌疑人" in node_name or "犯罪" in node_name:
                    role = "defendant"
                elif "(" in node_name and ")" in node_name:
                    desc = node_name[node_name.find("("):node_name.find(")")+1]
                    if any(k in desc for k in ["被告", "嫌疑人", "主犯"]):
                        role = "defendant"
                    elif any(k in desc for k in ["从犯", "同案", "共犯"]):
                        role = "co defendant"
                    elif any(k in desc for k in ["证", "关联"]):
                        role = "witness"
                    elif any(k in desc for k in ["被害", "受害人"]):
                        role = "victim"

            nodes.append({
                "id": node_name,
                "name": node_name,
                "role": role,
                "description": "",
            })

        # 解析边：A -- "关系" --> B
        edge_pattern = r'([A-Z])\s*--\s*"([^"]+)"\s*-->\s*([A-Z])'
        edge_matches = re.findall(edge_pattern, mermaid_code)

        for src_id, label, tgt_id in edge_matches:
            src_name = node_id_map.get(src_id, src_id)
            tgt_name = node_id_map.get(tgt_id, tgt_id)

            # 确定边类型
            edge_type = "other"
            if "雇佣" in label or "债务" in label:
                edge_type = "cooperation"
            elif "介绍" in label:
                edge_type = "introduction"
            elif "参赌" in label or "招募" in label:
                edge_type = "participation"

            edges.append({
                "source": src_name,
                "target": tgt_name,
                "type": edge_type,
                "label": label,
            })

    # 如果 mermaid 解析失败，尝试解析表格
    if not nodes:
        # 解析人物表格 - 匹配每行的所有列
        # 格式：| 姓名 | 角色 | 与xxx的关系 | 涉案程度 | 证据来源 | 备注 |
        lines = content.split('\n')
        in_table = False

        role_map = {
            "被告人": "defendant",
            "主犯": "defendant",
            "从犯": "co defendant",
            "同案犯": "co defendant",
            "证人": "witness",
            "关联人": "witness",
        }

        for line in lines:
            # 跳过表头分隔线
            if '|---' in line or '| ---' in line:
                in_table = True
                continue
            if not in_table or not line.strip().startswith('|'):
                continue

            # 解析表格行
            cols = [c.strip() for c in line.split('|') if c.strip()]
            if len(cols) < 2:
                continue

            name = cols[0].replace('*', '').strip()
            role_str = cols[1] if len(cols) > 1 else ""
            relation = cols[2] if len(cols) > 2 else ""

            # 跳过表头
            if name == "姓名" or "涉案人员" in name:
                continue

            # 确定角色
            node_role = "other"
            for key, val in role_map.items():
                if key in role_str:
                    node_role = val
                    break

            if name and len(name) <= 10:
                nodes.append({
                    "id": name,
                    "name": name,
                    "role": node_role,
                    "description": relation or role_str,
                })

        # 根据角色生成边
        defendant = next((n for n in nodes if n["role"] == "defendant"), None)
        if defendant:
            for node in nodes:
                if node["id"] != defendant["id"]:
                    if node["role"] == "co defendant":
                        edges.append({
                            "source": defendant["id"],
                            "target": node["id"],
                            "type": "cooperation",
                            "label": "共犯",
                        })
                    elif node["role"] == "witness":
                        edges.append({
                            "source": defendant["id"],
                            "target": node["id"],
                            "type": "other",
                            "label": "关联",
                        })

    return {"nodes": nodes, "edges": edges}


def _parse_event_timeline(content: str) -> dict:
    """从 stage_3 Markdown 解析事件时间线数据

    从 mermaid timeline 代码块提取所有细粒度事件（10个），
    并尝试从"事件拆解与证据归组"部分匹配详细描述。
    """
    import re

    events = []

    def normalize_event_date(raw: str) -> str:
        if not raw:
            return '1900-01-01'

        # 去掉时间部分（如 "15点00分"、"22点00分"）
        raw = re.sub(r'\s*\d+点\d+分.*$', '', raw)
        raw = re.sub(r'\s*\d+:\d+.*$', '', raw)

        # 取时间范围的第一个日期
        raw = re.split(r'至|\s*-\s+', raw)[0].strip()

        if "年中" in raw:
            raw = raw.replace("年中", "06")
        elif "年底" in raw:
            raw = raw.replace("年底", "12")

        raw = re.sub(r'起$', '', raw)
        raw = raw.replace("年", "-").replace("月", "-").replace("日", "").replace(".", "-").strip("-")
        raw = re.sub(r'-+', '-', raw)
        parts = raw.strip("-").split("-")

        if len(parts) == 1:
            return f"{parts[0]}-01-01"
        elif len(parts) == 2:
            return f"{parts[0]}-{parts[1].zfill(2)}-01"
        else:
            return f"{parts[0]}-{parts[1].zfill(2)}-{parts[2].zfill(2)}"

    def infer_event_type(text: str) -> str:
        if any(kw in text for kw in ["诈骗", "骗取", "投资", "借款", "转账", "倒卖", "吸金", "犯罪", "名借"]):
            return "crime"
        elif any(kw in text for kw in ["拘留", "逮捕", "取保", "立案", "移送", "起诉", "抓获"]):
            return "procedure"
        elif any(kw in text for kw in ["证据", "笔录", "鉴定", "辨认"]):
            return "evidence"
        elif any(kw in text for kw in ["辩护", "律师", "申诉"]):
            return "defense"
        return "other"

    # 从 mermaid timeline 代码块提取细粒度事件
    block_match = re.search(r'```(?:mermaid\s+)?timeline(.*?)```', content, re.DOTALL)
    if block_match:
        block = block_match.group(1)
        timeline_pattern = r'(\d{4}[年0-9\-./][^:\n]{0,25}?)\s*[:：]\s*([^\n]+)'
        matches = re.findall(timeline_pattern, block)

        for date_raw, desc in matches[:50]:
            desc = desc.strip()
            if not desc or len(desc) < 3:
                continue

            date_str = normalize_event_date(date_raw)

            # 提取相关证据
            evidence_refs = re.findall(r'证据\d+', desc)

            # 截取标题
            title = desc.split("，")[0] if "，" in desc else desc
            if len(title) > 30:
                title = title[:30] + "..."

            events.append({
                "id": f"event_{len(events)}",
                "date": date_str,
                "title": title,
                "description": desc,
                "type": infer_event_type(desc),
                "evidenceRefs": evidence_refs[:5],
            })

    return {"events": events}

