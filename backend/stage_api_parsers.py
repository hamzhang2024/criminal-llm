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

    # 统一的角色映射（按优先级排序：先匹配 defendant，再 victim，再 co_defendant，再 witness）
    # 注意：组合角色如"证人/被害人"会按优先级取第一个匹配项
    ROLE_MAP = [
        # (关键词, 角色值)
        ("被告人", "defendant"),
        ("嫌疑人", "defendant"),
        ("主犯", "defendant"),
        ("犯罪嫌疑", "defendant"),
        ("被害人", "victim"),
        ("受害人", "victim"),
        ("从犯", "co_defendant"),
        ("同案犯", "co_defendant"),
        ("共犯", "co_defendant"),
        ("被终止侦查", "co_defendant"),  # 曾为同案嫌疑人，程序终止
        ("证人", "witness"),
        ("关联人", "witness"),
        ("关系人", "witness"),
        ("介绍人", "witness"),
        ("保证人", "witness"),
        ("担保人", "witness"),
        ("鉴定人", "witness"),
        ("办案人", "witness"),
        ("侦查人员", "witness"),
        ("医生", "witness"),
    ]

    def _detect_role(role_str: str) -> str:
        """从角色字符串检测角色值，支持组合角色按优先级匹配"""
        if not role_str:
            return "other"
        # 去掉 markdown 加粗标记
        clean = role_str.replace('*', '').strip()
        for keyword, role_val in ROLE_MAP:
            if keyword in clean:
                return role_val
        return "other"

    # 表格分隔线检测：支持 |---|、| --- |、| :--- |、|---:| 等各种变体
    def _is_table_separator(line: str) -> bool:
        stripped = line.strip()
        if not stripped.startswith('|'):
            return False
        # 去掉首尾 |，检查内容是否全是 -、:、空格
        inner = stripped.strip('|').strip()
        if not inner:
            return False
        # 每个 cell 只含 :、-、空格
        cells = inner.split('|')
        return all(set(c.strip()) <= set('-:') and '-' in c for c in cells if c.strip())

    # ── 第1步：解析表格获取角色信息 ──
    name_role_map = {}   # 姓名 -> 角色
    name_relation_map = {}  # 姓名 -> 关系描述

    lines = content.split('\n')
    in_table = False
    for line in lines:
        if _is_table_separator(line):
            in_table = True
            continue
        if not in_table or not line.strip().startswith('|'):
            continue

        cols = [c.strip() for c in line.split('|') if c.strip()]
        if len(cols) < 2:
            continue

        name = cols[0].replace('*', '').strip()
        role_str = cols[1] if len(cols) > 1 else ""
        relation = cols[2] if len(cols) > 2 else ""

        # 跳过表头
        if name == "姓名" or "涉案人员" in name or "角色" in name:
            continue

        node_role = _detect_role(role_str)

        if name and len(name) <= 15:
            name_role_map[name] = node_role
            if relation:
                name_relation_map[name] = relation.replace('*', '').strip()

    # ── 第2步：解析 mermaid graph 代码块（获取关系边）──
    mermaid_match = re.search(r'```mermaid\s*graph\s*\w+\s*(.*?)```', content, re.DOTALL)
    if mermaid_match:
        mermaid_code = mermaid_match.group(1)

        # 解析节点定义：A[姓名], B[姓名], etc.（支持中文标签）
        node_pattern = r'([A-Za-z]+)\[([^\]]+)\]'
        node_matches = re.findall(node_pattern, mermaid_code)
        node_id_map = {}  # ID -> 姓名
        seen_node_names = set()

        for node_id, node_name in node_matches:
            if node_id not in node_id_map:
                node_id_map[node_id] = node_name

            # 从表格角色映射中获取角色
            role = "other"
            # 精确匹配
            if node_name in name_role_map:
                role = name_role_map[node_name]
            else:
                # 前缀匹配：戴子佳(佳诚数码) -> 戴子佳
                base_name = node_name.split('(')[0].strip() if '(' in node_name else node_name
                if base_name in name_role_map:
                    role = name_role_map[base_name]
                else:
                    # 模糊匹配：表格中的名字是 mermaid 标签的子串（或反之）
                    for tbl_name, tbl_role in name_role_map.items():
                        if tbl_name in node_name or node_name in tbl_name:
                            role = tbl_role
                            break

            # 表格中没有时，从节点标签推断
            if role == "other":
                if "被告" in node_name or "嫌疑人" in node_name or "主犯" in node_name:
                    role = "defendant"
                elif "被害" in node_name or "受害人" in node_name:
                    role = "victim"
                elif "从犯" in node_name or "同案" in node_name or "共犯" in node_name:
                    role = "co_defendant"
                elif "证" in node_name or "关联" in node_name:
                    role = "witness"

            # 去重（mermaid 中 subgraph 可能重复定义同名节点）
            if node_name not in seen_node_names:
                seen_node_names.add(node_name)
                nodes.append({
                    "id": node_name,
                    "name": node_name,
                    "role": role,
                    "description": name_relation_map.get(node_name, ""),
                })

        # 解析边：A -- "关系" --> B（支持带引号和不带引号）
        edge_pattern = r'([A-Za-z]+)\s*--\s*"?([^"\n>-]+)"?\s*-->\s*([A-Za-z]+)'
        edge_matches = re.findall(edge_pattern, mermaid_code)

        for src_id, label, tgt_id in edge_matches:
            src_name = node_id_map.get(src_id, src_id)
            tgt_name = node_id_map.get(tgt_id, tgt_id)
            label = label.strip().strip('"').strip()

            # 确定边类型
            edge_type = _detect_edge_type(label)

            edges.append({
                "source": src_name,
                "target": tgt_name,
                "type": edge_type,
                "label": label,
            })

    # ── 第3步：如果 mermaid 解析失败，回退到表格 ──
    if not nodes:
        for name, role in name_role_map.items():
            nodes.append({
                "id": name,
                "name": name,
                "role": role,
                "description": name_relation_map.get(name, ""),
            })

        # 根据角色生成边
        defendant = next((n for n in nodes if n["role"] == "defendant"), None)
        if defendant:
            for node in nodes:
                if node["id"] != defendant["id"]:
                    if node["role"] == "co_defendant":
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
                    elif node["role"] == "victim":
                        edges.append({
                            "source": defendant["id"],
                            "target": node["id"],
                            "type": "conflict",
                            "label": "侵害",
                        })

    return {"nodes": nodes, "edges": edges}


def _detect_edge_type(label: str) -> str:
    """根据边标签推断关系类型

    基于关键词匹配，按优先级检查。LLM 输出多样，关键词尽量覆盖同义表述，
    无法覆盖的归为 other（前端用灰色显示，不影响图谱结构）。

    检查顺序：participation（纠集/指使）优先于 cooperation（同事/雇佣），
    因为"纠集同事"这类标签中行为动词比关系性质更重要。
    """
    if not label:
        return "other"
    # 纠集/指使/共犯关系（优先检查，行为动词优先）
    if any(k in label for k in [
        "纠集", "指使", "指派", "安排", "召集", "招募", "共犯", "同案",
        "下达", "分配", "分派", "派遣", "授意", "主使", "参赌", "参股",
    ]):
        return "participation"
    # 合作/雇佣/业务关系
    if any(k in label for k in [
        "雇佣", "雇用", "老板", "上级", "下属", "同事", "合伙", "合作", "业务",
        "协作", "对接", "统筹", "共管", "协调", "配合", "联络", "代理", "接口",
    ]):
        return "cooperation"
    # 亲属/家庭关系（扩充亲属称谓）
    if any(k in label for k in [
        "夫妻", "兄弟", "姐妹", "父子", "母女", "亲属", "家人", "父亲", "母亲",
        "儿子", "女儿", "妻子", "丈夫", "舅", "甥", "叔", "侄", "婆", "翁",
        "岳", "婶", "姨", "表", "堂",
    ]):
        return "family"
    # 朋友/社交关系
    if any(k in label for k in ["朋友", "好友", "认识", "邻里", "邻居", "旧识", "相识"]):
        return "friendship"
    # 冲突/侵害关系
    if any(k in label for k in ["冲突", "侵害", "殴打", "伤害", "纠纷", "对抗", "被害", "打架", "斗殴"]):
        return "conflict"
    # 介绍关系
    if "介绍" in label or "引荐" in label:
        return "introduction"
    # 债务/金钱关系（扩充交易/分润等）
    if any(k in label for k in ["债务", "借款", "欠款", "转账", "金钱", "交易", "换钱", "分润", "分成", "薪资", "工资", "报酬", "费用"]):
        return "financial"
    return "other"


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

