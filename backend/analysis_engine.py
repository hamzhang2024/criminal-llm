"""
5 阶段案卷分析引擎

阶段 1：读起诉书 — 提取指控要素（罪名、事实、人员）
阶段 2：人物关系图 — 嫌疑人/被害人/证人关系
阶段 3：事件时间线 + 事件拆解（按事件归组证据）
阶段 4：涉案罪名法律法规（法条 + 司法解释 + 类案）
阶段 5：证据分析 + 矛盾分析 + 口供对比 + 三阶层辩护

输出：每个阶段生成结构化 JSON + Markdown，保存到案件 analysis/ 目录
"""
import json
import time
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List
from datetime import datetime

try:
    from legal_knowledge import get_legal_knowledge, get_dynamic_legal_knowledge, THEORY_THREE_TIERS, CONSTITUTIVE_ELEMENT_ANALYSIS
except ImportError:
    def get_legal_knowledge(): return ""
    def get_dynamic_legal_knowledge(crime_type=None): return ""
    THEORY_THREE_TIERS = ""
    CONSTITUTIVE_ELEMENT_ANALYSIS = ""

try:
    from pdf_to_md import get_evidence_text
except ImportError:
    def get_evidence_text(path, prefer_md=True): return "", None

try:
    ZHANG_CRIMINAL_DEFENSE_PATH = Path(__file__).parent.parent / "zhang-criminal-defense" / "criminal-defense.md"
    if ZHANG_CRIMINAL_DEFENSE_PATH.exists():
        with open(ZHANG_CRIMINAL_DEFENSE_PATH, "r", encoding="utf-8") as f:
            ZHANG_CRIMINAL_DEFENSE = f.read()
    else:
        ZHANG_CRIMINAL_DEFENSE = ""
except Exception:
    ZHANG_CRIMINAL_DEFENSE = ""


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


class AnalysisEngine:
    """5 阶段分析引擎"""

    def __init__(self, case_id: str, case_dir: Path, indictment_file: Optional[str] = None):
        self.case_id = case_id
        self.case_dir = case_dir
        self.analysis_dir = case_dir / "analysis"
        self.analysis_dir.mkdir(parents=True, exist_ok=True)
        self.md_texts: List[Dict[str, str]] = []  # [{filename, type, text}]
        self.stage_results: Dict[int, Dict[str, Any]] = {}
        # 用户手动指定的起诉书文件名（优先级高于自动检测）
        self.selected_indictment_file = indictment_file

    def _load_evidence_texts(self) -> List[Dict[str, str]]:
        """加载案件目录下所有证据文本

        优先从 evidence/ 目录加载 LLM 生成的证据总结
        回退到 md/ 目录（如果 evidence/ 不存在或为空）
        """
        texts = []

        # 优先从 evidence/ 目录加载
        evidence_dir = self.case_dir / "evidence"
        if evidence_dir.exists():
            index_file = evidence_dir / "index.json"
            if index_file.exists():
                try:
                    index = json.loads(index_file.read_text(encoding="utf-8"))
                    for ev in index.get("evidence", []):
                        md_file = evidence_dir / ev["md_file"]
                        # 如果 index.json 中的 md_file 不存在，尝试通过文件名匹配
                        if not md_file.exists():
                            # 尝试去掉数字前缀（如 "104_xxx.md" → "xxx.md"）
                            import re
                            stem = md_file.stem
                            # 匹配 "NNN_" 或 "NN_" 前缀
                            cleaned = re.sub(r"^\d{2,3}_", "", stem)
                            if cleaned != stem:
                                for candidate in evidence_dir.glob("*.md"):
                                    if candidate.stem == cleaned or candidate.stem == stem:
                                        md_file = candidate
                                        break
                        if md_file.exists():
                            text = md_file.read_text(encoding="utf-8")
                            if text.strip():
                                ev_id = ev.get("id", 0)
                                ev_type = ev.get("type", "其他证据")
                                ev_name = ev.get("name", "")
                                # 判断是否为起诉书/起诉意见书（类型或名称包含关键词）
                                is_indictment = (
                                    "起诉书" in ev_type or "起诉意见书" in ev_type or
                                    "起诉书" in ev_name or "起诉意见书" in ev_name
                                )
                                texts.append({
                                    "filename": ev["name"],
                                    "type": ev_type,
                                    "text": text,
                                    "source": ev.get("source", ""),
                                    "page_range": ev.get("page_range", ""),
                                    "evidence_ref": f"证据{ev_id:03d}" if not is_indictment else "",
                                    "md_file": ev["md_file"],
                                    "is_indictment": is_indictment,
                                })
                except Exception:
                    pass

            # 如果 index.json 不存在或解析失败，直接扫描 .md 文件
            if not texts:
                for idx, md_file in enumerate(sorted(evidence_dir.glob("*.md")), 1):
                    try:
                        text = md_file.read_text(encoding="utf-8")
                        if text.strip():
                            # 检查文件名和内容判断是否为起诉书
                            is_indictment = (
                                "起诉书" in md_file.stem or "起诉意见书" in md_file.stem or
                                "起诉书" in text[:2000] or "起诉意见书" in text[:2000]
                            )
                            texts.append({
                                "filename": md_file.stem,
                                "type": "起诉意见书" if "起诉意见书" in text[:2000] else "起诉书" if "起诉书" in text[:2000] else "其他证据",
                                "text": text,
                                "evidence_ref": f"证据{idx:03d}" if not is_indictment else "",
                                "is_indictment": is_indictment,
                            })
                    except Exception:
                        pass

        # 额外检查：如果 evidence/ 中没有起诉意见书，从 md/ 目录补充
        has_indictment = any(t.get("is_indictment") for t in texts)
        if not has_indictment:
            md_dir = self.case_dir / "md"
            if md_dir.exists():
                for md_file in sorted(md_dir.glob("*.md")):
                    try:
                        text = md_file.read_text(encoding="utf-8")
                        if text.strip() and ("起诉书" in text[:3000] or "起诉意见书" in text[:3000]):
                            is_indictment = "起诉书" in text[:3000] or "起诉意见书" in text[:3000]
                            texts.append({
                                "filename": md_file.name,
                                "type": "起诉书" if "起诉书" in text[:3000] else "起诉意见书",
                                "text": text,
                                "is_indictment": True,
                            })
                            break  # 找到一个就够了
                    except Exception:
                        pass

        # 如果 evidence/ 不存在或为空，完全回退到 md/ 目录
        if not texts:
            md_dir = self.case_dir / "md"
            if md_dir.exists():
                for md_file in sorted(md_dir.glob("*.md")):
                    try:
                        text = md_file.read_text(encoding="utf-8")
                        if text.strip():
                            texts.append({
                                "filename": md_file.name,
                                "type": _infer_evidence_type(md_file.name),
                                "text": text,
                            })
                    except Exception:
                        pass

        # 如果没有 MD 文件，尝试从 PDF 提取
        if not texts:
            for pdf_dir_name in ["processed", "original"]:
                pdf_dir = self.case_dir / pdf_dir_name
                if not pdf_dir.exists():
                    continue
                for pdf_file in sorted(pdf_dir.glob("*.pdf")):
                    try:
                        text, _ = get_evidence_text(str(pdf_file), prefer_md=True)
                        if text and not text.startswith("[无法提取"):
                            texts.append({
                                "filename": pdf_file.name,
                                "type": _infer_evidence_type(pdf_file.name),
                                "text": text,
                            })
                    except Exception:
                        pass

        self.md_texts = texts
        return texts

    def _save_stage(self, stage: int, data: Dict[str, Any], markdown: str):
        """保存阶段结果"""
        stage_dir = self.analysis_dir / f"stage_{stage}"
        stage_dir.mkdir(parents=True, exist_ok=True)

        # 保存 Markdown
        md_file = stage_dir / "output.md"
        md_file.write_text(markdown, encoding="utf-8")

        # 保存结构化 JSON
        json_file = stage_dir / "output.json"
        json_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        self.stage_results[stage] = data

    def _find_indictment(self, texts: list) -> Optional[Dict]:
        """
        查找起诉书/起诉意见书。
        优先级：用户手动指定 > 起诉书 > 起诉意见书；同类多份时取形成时间在后面的。
        """
        import re

        # 如果用户手动指定了起诉书文件，直接使用
        if self.selected_indictment_file:
            for t in texts:
                if t.get("filename") == self.selected_indictment_file:
                    return t
            # 指定的文件不存在，回退到自动检测

        indictments = []  # (类型, 日期, 文本)
        for t in texts:
            type_val = t.get("type", "")
            filename_val = t.get("filename", "")
            text = t.get("text", "")

            if "起诉书" in type_val or ("起诉书" in filename_val and "意见" not in filename_val):
                indictments.append(("起诉书", self._extract_date_from_text(text), t))
            elif "起诉意见书" in type_val or "起诉意见书" in filename_val or "指控" in type_val or "指控" in filename_val:
                indictments.append(("起诉意见书", self._extract_date_from_text(text), t))

        if not indictments:
            return None

        # 先按类型排序（起诉书 > 起诉意见书），再按日期排序（晚的在前）
        def sort_key(item):
            doc_type, doc_date, _ = item
            type_priority = 1 if doc_type == "起诉书" else 0
            # 日期越大（越晚）越优先
            date_val = doc_date if doc_date else "0000-00-00"
            return (type_priority, date_val)

        indictments.sort(key=sort_key, reverse=True)
        return indictments[0][2]

    def _extract_date_from_text(self, text: str) -> Optional[str]:
        """从文本中提取日期，格式 YYYY-MM-DD"""
        import re
        # 匹配常见日期格式
        patterns = [
            r"(\d{4})年(\d{1,2})月(\d{1,2})日",
            r"(\d{4})-(\d{2})-(\d{2})",
            r"(\d{4})/(\d{2})/(\d{2})",
        ]
        dates = []
        for pattern in patterns:
            matches = re.findall(pattern, text)
            for m in matches:
                try:
                    year = int(m[0])
                    month = int(m[1])
                    day = int(m[2])
                    if 2000 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
                        dates.append(f"{year:04d}-{month:02d}-{day:02d}")
                except (ValueError, IndexError):
                    pass
            if dates:
                return max(dates)  # 取最晚的日期
        return None

    # ========== 阶段 1：读起诉书 ==========

    async def stage_1_read_indictment(
        self,
        defendant: str,
        crime_type: Optional[str] = None,
        progress_cb: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """
        阶段 1：从起诉意见书/起诉书中提取指控要素
        输出：指控罪名、指控事实、涉案人员、辩护对象
        """
        texts = self._load_evidence_texts()
        if not texts:
            raise ValueError("案件中未找到任何证据文件")

        # 找起诉书/起诉意见书
        # 优先级：起诉书 > 起诉意见书；同类多份时取形成时间在后面的
        indictment = self._find_indictment(texts)

        if not indictment:
            raise ValueError("案件中未找到起诉书或起诉意见书，无法提取指控要素")

        if progress_cb:
            progress_cb("正在分析起诉书，提取指控要素...")

        from llm_client import get_llm_client
        client = get_llm_client()

        system_prompt = """你是一位资深刑事辩护律师，正在阅读案卷材料。
请仔细阅读起诉书/起诉意见书及相关材料，提取指控的核心要素。

注意：
- 优先以**起诉书**为准，无起诉书时以**起诉意见书**为准
- 如起诉意见书有多份，以形成时间在**最后**的为准
- 所有信息必须基于原文，不要臆测
- 用 Markdown 格式输出，结构清晰，便于后续分析"""

        user_prompt = f"""## 辩护对象
被告人：**{defendant}**

## 罪名线索
{f"涉嫌罪名可能是：{crime_type}" if crime_type else "（未指定罪名，请从材料中推断）"}

## 指控文书
{indictment["filename"]}（{indictment["type"]}）

> 注：优先以起诉书为准，无起诉书时以起诉意见书为准。如有多份，取形成时间最后的。

{indictment["text"][:80000]}

---

## 请提取以下指控要素

### 一、指控罪名
- 明确列出指控的罪名（如有多个，逐一列出）
- 引用刑法条文（如能确定）

### 二、指控事实
- 用简洁语言概括指控的核心事实
- 包括时间、地点、人物、行为、结果

### 三、涉案人员清单
- 列出所有涉案人员及其角色
- 标注与辩护对象 {defendant} 的关系

### 四、指控行为描述
- 详细描述指控的具体行为
- 按时间顺序排列（如材料中有多个行为）

请基于上述材料，专业、准确地提取指控要素。
"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        md_output = await client.chat(messages)

        # 结构化数据
        data = {
            "stage": 1,
            "name": "指控要素",
            "defendant": defendant,
            "indictment_source": indictment["filename"],
            "generated_at": datetime.now().isoformat(),
        }

        self._save_stage(1, data, md_output)
        return data

    # ========== 阶段 2：人物关系图 ==========

    async def stage_2_character_relations(
        self,
        defendant: str,
        crime_type: Optional[str] = None,
        progress_cb: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """
        阶段 2：从全部证据中提取人物关系
        输出：人物关系图（表格形式）
        """
        texts = self._load_evidence_texts()
        if progress_cb:
            progress_cb("正在分析人物关系...")

        from llm_client import get_llm_client
        client = get_llm_client()

        indictment_catalog, indictment_text, evidence_catalog_text, evidence_only = _split_indictment_and_evidence(texts)

        all_text = _truncate_all(texts, max_total=150000)

        system_prompt = """你是一位资深刑事辩护律师，正在梳理案中人物关系。
请从全部案卷材料中识别涉案人员及其相互关系。

**重要区分**：
- 起诉书/起诉意见书是指控文书，不是证据。引用时写"据起诉书"或"据起诉意见书"，不要用"见证据XXX"格式
- 只有正式证据（笔录、证言、鉴定意见、书证等）才用"见证据XXX"格式引用

输出要求：
1. 用 Markdown 格式
2. 关系表要完整，不遗漏重要人物
3. 关系说明要具体，不要只写"认识"这种模糊描述
4. **必须输出关系图的 JSON 数据结构**（见下方示例）"""

        user_prompt = f"""## 辩护对象
被告人：**{defendant}**

## 指控文书（非证据，引用时写"据起诉书"/"据起诉意见书"）

{indictment_catalog}

## 证据目录（按编号引用）

{evidence_catalog_text}

## 全部案卷材料

{all_text}

---

## 请完成以下分析

### 一、涉案人员一览表

| 姓名 | 角色 | 与{defendant}的关系 | 涉案程度 | 证据来源 | 备注 |
|------|------|---------------------|----------|----------|------|

角色可选：被告人/嫌疑人、同案犯、被害人、证人、鉴定人、办案人员、其他。
涉案程度：核心、重要、次要、边缘。
证据来源：证据用编号格式（如"见证据009"），指控文书写"据起诉书"/"据起诉意见书"。

### 二、人物关系图

请将人物关系输出为以下 JSON 格式。系统将据此生成图形化关系图：

```json
{{
  "subgraphs": [
    {{"name": "核心合伙人", "nodes": ["A", "B"]}}
  ],
  "nodes": [
    {{"id": "A", "label": "项少甫", "group": "core"}},
    {{"id": "B", "label": "江涛", "group": "core"}}
  ],
  "edges": [
    {{"from": "A", "to": "B", "label": "合伙开赌"}}
  ]
}}
```

规则：
- `nodes`: 所有涉案人员，`id` 为单字母标识符（A, B, C...），`label` 为姓名，`group` 可选：core（核心）、staff（执行）、witness（证人）、other（其他）
- `edges`: 两人之间的关系，`label` 为关系描述（2-6 字关键词）
- `subgraphs`: 按角色层级分组，`nodes` 中为该组包含的节点 ID 列表
- 不限制节点数量，包含所有重要人物
- 连线标签不要用 emoji 或特殊符号

### 三、关系详细分析

请梳理以下关系维度：
1. **嫌疑人之间的关系**：主从犯关系、共犯关系、是否存在互相推诿
2. **嫌疑人与被害人之间的关系**：是否存在矛盾、利益冲突、熟悉程度
3. **证人与各方的关系**：是否有利害关系、立场倾向
4. **其他重要关系**：如介绍人、中间人、资金往来等

对每组关系，说明：
- 关系性质（亲属、朋友、同事、交易方等）
- 关系来源（证据用编号格式，指控文书写"据起诉书"/"据起诉意见书"）
- 对辩护的影响（有利/不利/中性）
"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        md_output = await client.chat(messages)

        # 优先尝试从 JSON 生成 Mermaid 图
        md_output = _extract_json_and_render(md_output, _json_to_mermaid_graph)

        # 如果 JSON 提取失败（LLM 直接输出了 mermaid），回退到旧的后处理逻辑
        if '```mermaid' in md_output:
            import re as _re
            md_output = _legacy_fix_mermaid(md_output)

        data = {
            "stage": 2,
            "name": "人物关系",
            "defendant": defendant,
            "generated_at": datetime.now().isoformat(),
        }

        self._save_stage(2, data, md_output)
        return data

    # ========== 阶段 3：事件时间线 + 事件拆解 ==========

    async def stage_3_event_timeline(
        self,
        defendant: str,
        crime_type: Optional[str] = None,
        progress_cb: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """
        阶段 3：构建事件时间线 + 按事件归组证据
        每个事件下挂接全部相关证据
        """
        texts = self._load_evidence_texts()
        if progress_cb:
            progress_cb("正在分析事件时间线和证据归组...")

        from llm_client import get_llm_client
        client = get_llm_client()

        indictment_catalog, indictment_text, evidence_catalog_text, evidence_only = _split_indictment_and_evidence(texts)

        all_text = _truncate_all(texts, max_total=200000)

        system_prompt = """你是一位资深刑事辩护律师，正在梳理案卷中的事件脉络。
请按时间顺序识别案件中的所有关键事件，并将相关证据归组到对应事件下。

**重要区分**：
- 起诉书/起诉意见书是指控文书，不是证据。引用时写"据起诉书"或"据起诉意见书"
- 只有正式证据（笔录、证言、鉴定意见、书证等）才用"见证据XXX"格式引用

重要原则：
1. 以"事件"为单位，不是以"文件"为单位
2. 同一事件可能涉及多份证据（供述、证言、物证、鉴定等）
3. 对每个事件，要简要概括各证据的说法
4. **必须输出时间线的 JSON 数据结构**（见下方示例）"""

        user_prompt = f"""## 辩护对象
被告人：**{defendant}**

## 指控文书（非证据，引用时写"据起诉书"/"据起诉意见书"）

{indictment_catalog}

## 证据目录（按编号引用）

{evidence_catalog_text}

## 全部案卷材料

{all_text}

---

## 请完成以下分析

### 一、事件时间线

请将事件时间线输出为以下 JSON 格式。系统将据此生成图形化时间线：

```json
{{
  "title": "案件时间线",
  "events": [
    {{"date": "2025-12-22", "title": "第一次赌局开场", "evidence": ["见证据009", "见证据013"]}},
    {{"date": "2026-01-21 19:40-23:00", "title": "终场聚赌，现场抓捕", "evidence": ["见证据001", "见证据027"]}}
  ]
}}
```

规则：
- `date`: 日期或时间范围，保留原始时间格式（如 `19:40-23:00`）
- `title`: 事件简述（不超过 30 字）
- `evidence`: 相关证据编号列表（仅正式证据，不包括起诉书/起诉意见书）

### 二、事件拆解与证据归组

对每个事件，详细列出：

#### 事件 {{N}}：[事件名称]
- **时间**：[具体时间或时间范围]
- **地点**：[如材料中有]
- **事件简述**：[简要概括发生了什么]

**相关证据：**
1. **[证据编号+名称]**（[证据类型]）：[该证据对该事件的说法，1-2 句话概括，标注页码]
2. **[证据编号+名称]**（[证据类型]）：[...]
3. ...

**初步观察：**
- 各证据说法是否一致
- 是否存在明显矛盾或疑点
- 是否有证据缺失

---

请确保：
- 不遗漏重要事件
- 每个事件都挂接全部相关证据
- 观察要客观，不要做深入辩护分析（那是阶段 5 的工作）
- 证据用"见证据XXX"格式，指控文书写"据起诉书"/"据起诉意见书"
"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        md_output = await client.chat(messages)

        # 优先尝试从 JSON 生成 Mermaid 时间线
        md_output = _extract_json_and_render(md_output, _json_to_mermaid_timeline)

        # 如果 LLM 直接输出了 mermaid（未走 JSON 路径），用旧逻辑修复
        if '```mermaid' in md_output:
            md_output = _legacy_fix_mermaid_timeline(md_output)

        data = {
            "stage": 3,
            "name": "事件拆解",
            "defendant": defendant,
            "generated_at": datetime.now().isoformat(),
        }

        self._save_stage(3, data, md_output)
        return data

    # ========== 阶段 4：法律法规梳理 ==========

    async def stage_4_legal_regulations(
        self,
        defendant: str,
        crime_type: Optional[str] = None,
        progress_cb: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """
        阶段 4：梳理涉案罪名的法律法规
        输出：刑法法条 + 司法解释 + 类案裁判要旨 + 量刑指导意见
        """
        texts = self._load_evidence_texts()

        # 获取法律知识库
        legal_kb = get_legal_knowledge()
        crime_specific = ""
        if crime_type:
            crime_specific = get_dynamic_legal_knowledge(crime_type)

        if progress_cb:
            progress_cb(f"正在梳理{crime_type or '涉案罪名'}相关法律法规...")

        from llm_client import get_llm_client
        client = get_llm_client()

        system_prompt = """你是一位资深刑事辩护律师，精通中国刑法。
请根据案件涉及的罪名，梳理相关法律法规、司法解释和类案裁判要旨。

输出要求：
1. 引用的法条要准确（包含条文号）
2. 司法解释要引用现行有效的
3. 类案要标注法院和案号（如能确定）
4. 量刑部分要列明基准刑和调节因素"""

        # 先从阶段 1 结果中获取指控罪名
        stage1_md = ""
        stage1_file = self.analysis_dir / "stage_1" / "output.md"
        if stage1_file.exists():
            stage1_md = stage1_file.read_text(encoding="utf-8")

        crime_specific_section = ""
        if crime_specific:
            crime_specific_section = f"## 罪名特定知识\n{crime_specific}"

        user_prompt = f"""## 辩护对象
被告人：**{defendant}**

{"涉嫌罪名：" + crime_type if crime_type else "（请从案卷中识别涉嫌罪名）"}

## 阶段 1：指控要素（已提取）

{stage1_md}

## 三阶层犯罪论体系知识

{THEORY_THREE_TIERS}

{crime_specific_section}

---

## 请完成以下法律梳理

**引用规则**：在分析中引用证据时，请使用编号格式，如"见证据009"、"见证据013"。

### 一、刑法法条

#### 1. 核心法条
- 列出与指控罪名直接相关的刑法条文
- 引用完整条文内容（含条文号）
- 标注构成要件（主体、主观、客观、结果）

#### 2. 相关法条
- 列出与案件相关的其他刑法条文
- 如从犯、未遂、自首、立功等量刑相关法条

### 二、司法解释
- 列出与本案罪名相关的现行有效司法解释
- 摘录关键条款
- 说明对本案的适用性

### 三、类案裁判要旨
- 列出与本案类似的指导性案例或公报案例
- 概括裁判要旨
- 说明与本案的关联

### 四、量刑指导意见
- 列明该罪名的基准刑
- 法定从重、从轻情节
- 酌定从重、从轻情节
- 认罪认罚从宽制度的适用
"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        md_output = await client.chat(messages)

        data = {
            "stage": 4,
            "name": "法律法规",
            "defendant": defendant,
            "crime_type": crime_type,
            "generated_at": datetime.now().isoformat(),
        }

        self._save_stage(4, data, md_output)
        return data

    # ========== 阶段 5：证据分析 + 矛盾分析 + 口供对比 + 三阶层辩护 ==========

    async def stage_5_full_defense(
        self,
        defendant: str,
        crime_type: Optional[str] = None,
        progress_cb: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """
        阶段 5：综合分析
        5A：逐份证据分析
        5B：矛盾分析 + 口供对比
        5C：三阶层辩护报告
        """
        texts = self._load_evidence_texts()

        # 获取之前阶段的结果
        stage1_md = _read_stage_md(self.analysis_dir, 1)
        stage2_md = _read_stage_md(self.analysis_dir, 2)
        stage3_md = _read_stage_md(self.analysis_dir, 3)
        stage4_md = _read_stage_md(self.analysis_dir, 4)

        legal_kb = get_legal_knowledge()
        crime_specific = ""
        if crime_type:
            crime_specific = get_dynamic_legal_knowledge(crime_type)

        # ----- 5A：构建证据目录 -----
        # 证据详情已在 evidence/ 目录中，5B/5C 直接引用编号即可
        if progress_cb:
            progress_cb("正在构建证据目录...")

        indictment_catalog, indictment_text, evidence_catalog_text, evidence_only = _split_indictment_and_evidence(texts)

        # 5A 输出为证据目录清单（排除指控文书）
        evidence_list_md = "# 证据目录\n\n| 编号 | 证据名称 | 类型 |\n|------|---------|------|\n"
        for ev in evidence_only:
            ref = ev.get("evidence_ref", "")
            ev_name = ev["filename"]
            ev_type = ev.get("type", "")
            evidence_list_md += f"| {ref} | {ev_name} | （{ev_type}） |\n"
        # 指控文书单独列出（非证据，仅作参考）
        if indictment_text:
            evidence_list_md += f"\n---\n\n# 指控文书（非证据）\n\n{indictment_catalog}\n"

        self._save_stage(51, {"name": "证据目录", "evidence_count": len(evidence_only)}, evidence_list_md)

        # ----- 5B：矛盾分析 + 口供对比 -----
        if progress_cb:
            progress_cb("正在进行矛盾分析和口供对比...")

        from llm_client import get_llm_client
        client = get_llm_client()

        all_text = _truncate_all(texts, max_total=200000)

        contradiction_prompt = f"""## 辩护对象
被告人：**{defendant}**

## 指控文书（非证据，引用时写"据起诉书"/"据起诉意见书"）

{indictment_catalog}

## 证据目录（按编号引用）

{evidence_catalog_text}

## 全部案卷材料

{all_text}

---

## 请完成以下分析

**引用规则**：
- 证据用编号格式，如"见证据009"、"见证据013"
- 起诉书/起诉意见书是指控文书不是证据，引用时写"据起诉书"/"据起诉意见书"，不要用"见证据XXX"格式

### 一、口供稳定性分析（同一人多次笔录对比）

对每个有多份笔录的人，按时间线列出陈述变化：

#### [姓名] 的 {{N}} 次笔录对比

| 时间 | 关键陈述 | 变化 | 可能原因 |
|------|---------|------|---------|

### 二、横向矛盾分析（不同证据对同一事实的记载）

#### （一）关键事实横向比对

| 比对维度 | 被告人供述 | 证人证言 | 书证/物证 | 是否矛盾 |
|----------|-----------|---------|----------|----------|

#### （二）矛盾类型识别
- **直接矛盾**：描述完全相反
- **间接矛盾**：推论与事实冲突
- **隐性矛盾**：表面不矛盾但逻辑上无法同时成立

#### （三）矛盾对证明力的影响
- 核心事实矛盾 → 动摇指控基础
- 细节矛盾 → 影响证据可信度
- 可合理解释的矛盾 → 不影响采信

### 三、证据链条薄弱环节
- 哪些环节仅靠言词证据，缺乏客观证据
- 证据链条中是否存在断裂
"""

        contradiction_md = await client.chat([
            {"role": "system", "content": "你是刑事辩护律师，正在识别证据间的矛盾和证据链薄弱环节。\n\n重要：起诉书/起诉意见书是指控文书不是证据，引用时写'据起诉书'/'据起诉意见书'，不要用'见证据XXX'格式。只有正式证据（笔录、证言、鉴定等）才用'见证据XXX'格式。"},
            {"role": "user", "content": contradiction_prompt},
        ])

        self._save_stage(52, {"name": "矛盾分析"}, contradiction_md)

        # ----- 5C：三阶层辩护报告 -----
        if progress_cb:
            progress_cb("正在生成三阶层辩护报告...")

        defense_prompt = f"""## 辩护对象
被告人：**{defendant}**

{f"涉嫌罪名：{crime_type}" if crime_type else ""}

## 指控文书（非证据，引用时写"据起诉书"/"据起诉意见书"）

{indictment_catalog}

## 证据目录（按编号引用）

{evidence_catalog_text}

## 阶段 1：指控要素

{stage1_md}

## 阶段 2：人物关系

{stage2_md}

## 阶段 3：事件拆解

{stage3_md}

## 阶段 4：法律法规

{stage4_md}

## 阶段 5A：证据目录

{evidence_list_md}

## 阶段 5B：矛盾分析

{contradiction_md[:8000]}

## 罪名特定知识

{crime_specific if crime_specific else "（未指定罪名）"}

## 三阶层犯罪论体系

{THEORY_THREE_TIERS}

## 法条构成要件拆解分析法

{CONSTITUTIVE_ELEMENT_ANALYSIS}

## 刑事辩护提示词

{ZHANG_CRIMINAL_DEFENSE}

---

## 请完成三阶层综合辩护分析

**引用规则**：
- 证据用编号格式，如"见证据009（江涛询问笔录）"、"见证据013（现场勘验笔录）"
- 起诉书/起诉意见书是指控文书不是证据，引用时写"据起诉书"/"据起诉意见书"，**绝不能用"见证据XXX（起诉意见书）"这种格式**
- 不要只说"案卷显示"或"据供述"这种模糊表述

### 一、辩护概要
简要概括本案的辩护方向和核心论点（200 字以内）

### 二、事实与证据支撑分析

1. **本案事实是否有证据支撑**：逐项审查指控事实是否有对应的客观证据（注意：起诉书/起诉意见书中的指控内容本身不是证据，需要独立证据来支撑）
2. **程序合法性**：本案是否存在程序违法（如超期羁押、未告知权利、非法搜查等）
3. **证据收集合法性**：证据收集是否符合法定程序（如电子数据提取程序、扣押程序、辨认程序等）
4. **证据链条完整性**：是否存在断裂，哪些环节仅靠言词证据

### 三、构成要件符合性分析

逐项分析指控罪名的构成要件是否符合法条及司法解释的描述：

| 构成要件 | 法条/司法解释要求 | 本案事实 | 证据支撑 | 是否符合 |
|----------|----------|---------|----------|----------|

### 四、违法性分析
- 是否存在违法阻却事由（正当防卫、紧急避险等）
- 是否存在程序违法（详见第二部分）
- 证据收集是否合法（详见第二部分）

### 五、有责性分析
- 责任能力
- 故意/过失的认定
- 期待可能性
- 量刑情节（自首、立功、从犯、未遂、中止等）

### 六、综合辩护意见
基于以上分析，提出完整的辩护意见，包括：
1. 无罪/罪轻/改变定性的论证
2. 量刑情节分析
3. 核心辩护要点（3-5 条）
4. 预期结果评估
5. 下一步建议（需要补充的证据、申请事项等）
"""

        defense_md = await client.chat([
            {"role": "system", "content": """你是资深刑事辩护律师，正在撰写三阶层综合辩护分析报告。
要求：
1. 分析要专业、准确，引用法律条文要准确
2. 从有利于被告人的角度出发
3. 善于发现指控的薄弱环节
4. 输出完整的 Markdown 格式报告
5. 严格遵循三阶层递进逻辑：先审查事实与证据支撑 → 再判断构成要件符合性 → 再分析违法性 → 最后审查有责性
6. 引用规则：
   - 证据用编号格式，如"见证据009（江涛询问笔录）"
   - 起诉书/起诉意见书是指控文书不是证据，引用时写"据起诉书"/"据起诉意见书"，绝不能用"见证据XXX（起诉意见书）"格式
   - 不要只说"案卷显示"或"据供述"这种模糊表述
7. 对指控的每一项事实，都要指出是否有独立证据支撑、证据是否充分（起诉书的指控不等于有证据支撑）"""},
            {"role": "user", "content": defense_prompt},
        ])

        self._save_stage(53, {"name": "三阶层辩护"}, defense_md)

        # 合并阶段 5 的三个子阶段为一个完整报告
        full_report = f"""# {defendant}案 — 综合辩护分析报告

> 生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}

---

## 证据目录

{evidence_list_md}

> 证据详情见 evidence/ 目录下各证据文件。

---

## 矛盾分析

{contradiction_md}

---

## 三阶层辩护报告

{defense_md}
"""

        data = {
            "stage": 5,
            "name": "综合辩护分析",
            "defendant": defendant,
            "crime_type": crime_type,
            "evidence_count": len(texts),
            "generated_at": datetime.now().isoformat(),
        }

        # 保存完整报告到 analysis/ 目录
        report_file = self.analysis_dir / "full_defense_report.md"
        report_file.write_text(full_report, encoding="utf-8")

        self._save_stage(5, data, full_report)
        return data


# ========== 工具函数 ==========

def _infer_evidence_type(filename: str) -> str:
    """从文件名推断证据类型"""
    lower = filename.lower()
    if "起诉书" in lower or "公诉书" in lower:
        return "起诉书"
    elif "起诉意见书" in lower or "呈请起诉" in lower:
        return "起诉意见书"
    elif "指控" in lower:
        return "指控材料"
    elif "讯问" in lower:
        return "讯问笔录"
    elif "询问" in lower or "证人" in lower:
        return "证人证言"
    elif "鉴定" in lower:
        return "鉴定意见"
    elif "勘验" in lower or "检查" in lower:
        return "勘验笔录"
    elif "辨认" in lower:
        return "辨认笔录"
    elif "银行" in lower or "流水" in lower or "转账" in lower:
        return "书证-金融"
    elif "合同" in lower or "协议" in lower:
        return "书证-合同"
    elif "拘留" in lower or "逮捕" in lower or "取保" in lower:
        return "程序性文书"
    else:
        return "其他证据"


def _split_indictment_and_evidence(texts: List[Dict[str, str]]):
    """将证据列表分为指控文书和证据两部分，分别构建目录文本"""
    indictments = [t for t in texts if t.get("is_indictment")]
    evidences = [t for t in texts if not t.get("is_indictment")]

    # 指控文书目录（不带证据编号）
    indictment_catalog = "\n".join(
        f"- {t['filename']}（{t['type']}，指控文书，非证据）"
        for t in indictments
    )
    indictment_text = "\n\n".join(
        f"### {t['filename']}（{t['type']}，指控文书）\n{t['text']}"
        for t in indictments
    )

    # 证据目录（带编号）
    evidence_catalog = "\n".join(
        f"{t.get('evidence_ref', '')}：{t['filename']}（{t['type']}）"
        for t in evidences
    )

    return indictment_catalog, indictment_text, evidence_catalog, evidences


def _truncate_all(texts: List[Dict[str, str]], max_total: int) -> str:
    """将所有证据文本合并，限制总长度"""
    total = sum(len(t["text"]) for t in texts)
    if total <= max_total:
        return "\n\n".join([
            f"### {t['filename']}（{t['type']}）\n{t['text']}"
            for t in texts
        ])

    # 按比例缩减
    ratio = max_total / total
    parts = []
    for t in texts:
        truncated = t["text"][:int(len(t["text"]) * ratio)]
        parts.append(f"### {t['filename']}（{t['type']}）\n{truncated}")
    return "\n\n".join(parts)


def _read_stage_md(analysis_dir: Path, stage: int) -> str:
    """读取指定阶段的 Markdown 输出"""
    stage_file = analysis_dir / f"stage_{stage}" / "output.md"
    if stage_file.exists():
        return stage_file.read_text(encoding="utf-8")
    return ""
