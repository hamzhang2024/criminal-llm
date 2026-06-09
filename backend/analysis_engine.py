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
import logging
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    from legal_knowledge import (
        get_legal_knowledge, get_dynamic_legal_knowledge, THEORY_THREE_TIERS,
        CONSTITUTIVE_ELEMENT_ANALYSIS, EVIDENCE_REVIEW_TEMPLATES,
        LEGAL_BASIS_FOR_REVIEW, CROSS_EXAMINATION_STRATEGIES, CROSS_EXAMINATION_TEMPLATE
    )
except ImportError:
    def get_legal_knowledge(): return ""
    def get_dynamic_legal_knowledge(crime_type=None): return ""
    THEORY_THREE_TIERS = ""
    CONSTITUTIVE_ELEMENT_ANALYSIS = ""
    EVIDENCE_REVIEW_TEMPLATES = {}
    LEGAL_BASIS_FOR_REVIEW = ""
    CROSS_EXAMINATION_STRATEGIES = {}
    CROSS_EXAMINATION_TEMPLATE = ""

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

    # ========== 证据质证意见生成（合并三性审查） ==========

    def _get_review_template(self, evidence_type: str) -> str:
        """根据证据类型获取对应的审查模板"""
        # 标准化证据类型名称
        type_mapping = {
            "讯问笔录": "被告人供述",
            "询问笔录": "证人证言",
            "证人证言": "证人证言",
            "被害人陈述": "被害人陈述",
            "被告人供述": "被告人供述",
            "物证": "物证",
            "书证": "书证",
            "鉴定意见": "鉴定意见",
            "鉴定书": "鉴定意见",
            "勘验笔录": "勘验检查笔录",
            "检查笔录": "勘验检查笔录",
            "勘验检查笔录": "勘验检查笔录",
            "视听资料": "视听资料",
            "电子数据": "电子数据",
            "辨认笔录": "辨认笔录",
        }
        normalized_type = type_mapping.get(evidence_type, "其他证据")
        return EVIDENCE_REVIEW_TEMPLATES.get(normalized_type, EVIDENCE_REVIEW_TEMPLATES.get("其他证据", ""))

    def _build_review_prompt(self, evidence: Dict[str, Any], template: str) -> str:
        """构建差异化的证据审查提示词"""
        ev_name = evidence.get("filename", "未知证据")
        ev_ref = evidence.get("evidence_ref", "")
        ev_type = evidence.get("type", "其他证据")
        ev_text = evidence.get("text", "")[:6000]  # 截断长文本

        prompt = f"""你是一名经验丰富的刑事辩护律师，正在对证据进行严格的三性审查并生成质证意见。

# 证据信息
- 证据名称：{ev_name}
- 证据编号：{ev_ref}
- 证据类型：{ev_type}

# 证据内容摘要
{ev_text[:4000]}

# 审查模板（请按此模板逐项审查）
{template}

# 法律依据参考
{LEGAL_BASIS_FOR_REVIEW[:3000]}

# 输出要求
请严格按照以下 JSON 格式输出审查结果，每个维度都要有具体的审查发现和法律依据：

{{
  "evidence_name": "{ev_name}",
  "evidence_ref": "{ev_ref}",
  "evidence_type": "{ev_type}",
  "legality": {{
    "conclusion": "采信/不采信/存疑",
    "score": 0-100,
    "findings": [
      {{
        "issue": "发现的具体问题",
        "legal_basis": "对应法条（如：刑诉法第117条）",
        "details": "问题详细说明"
      }}
    ],
    "cross_opinion": "可当庭陈述的质证意见（一句话）",
    "strategy": ["质证策略1", "质证策略2"]
  }},
  "authenticity": {{
    "conclusion": "采信/不采信/存疑",
    "score": 0-100,
    "findings": [
      {{
        "issue": "发现的具体问题",
        "legal_basis": "对应法条",
        "details": "问题详细说明"
      }}
    ],
    "cross_opinion": "可当庭陈述的质证意见",
    "strategy": ["质证策略1"]
  }},
  "relevance": {{
    "conclusion": "采信/不采信/存疑",
    "score": 0-100,
    "findings": [
      {{
        "issue": "发现的具体问题",
        "legal_basis": "对应法条",
        "details": "问题详细说明"
      }}
    ],
    "cross_opinion": "可当庭陈述的质证意见",
    "strategy": ["质证策略1"]
  }},
  "final_conclusion": "综合结论：采信/不采信/存疑",
  "cross_examination_summary": "综合质证意见（可当庭陈述，200字以内）"
}}

# 评分标准
- 90-100分：无明显问题，建议采信
- 70-89分：存在轻微问题，可采信但需注意
- 50-69分：存在明显问题，建议存疑
- 0-49分：存在严重问题，建议不采信

# 注意事项
1. 合法性审查重点关注：取证主体资格、取证程序、证据形式、非法证据排除
2. 真实性审查重点关注：来源可靠性、内容客观性、保管链条、同一性确认
3. 关联性审查重点关注：与待证事实的关系、证明价值、证据间印证
4. 每个问题都要有具体的法律依据引用
5. 质证意见要具体、可操作，能直接用于庭审

只输出 JSON，不要其他内容。"""
        return prompt

    def _parse_review_result(self, response: str, evidence: Dict[str, Any]) -> Dict[str, Any]:
        """解析 LLM 返回的审查结果"""
        import re
        ev_name = evidence.get("filename", "未知证据")
        ev_ref = evidence.get("evidence_ref", "")

        try:
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                result = json.loads(json_match.group())
                result["evidence_name"] = ev_name
                result["evidence_ref"] = ev_ref
                return result
        except Exception as e:
            logger.error(f"[证据审查] JSON 解析失败: {e}")

        # 解析失败，返回默认结果
        return {
            "evidence_name": ev_name,
            "evidence_ref": ev_ref,
            "evidence_type": evidence.get("type", "其他证据"),
            "legality": {
                "conclusion": "存疑",
                "score": 70,
                "findings": [],
                "cross_opinion": "审查失败，需人工复核",
                "strategy": ["人工复核"]
            },
            "authenticity": {
                "conclusion": "存疑",
                "score": 70,
                "findings": [],
                "cross_opinion": "审查失败，需人工复核",
                "strategy": ["人工复核"]
            },
            "relevance": {
                "conclusion": "存疑",
                "score": 70,
                "findings": [],
                "cross_opinion": "审查失败，需人工复核",
                "strategy": ["人工复核"]
            },
            "final_conclusion": "存疑",
            "cross_examination_summary": "审查失败，需人工复核该证据的三性。",
            "error": "解析失败"
        }

    async def generate_cross_examination_opinion(self) -> Dict[str, Any]:
        """生成证据质证意见（合并三性审查）

        审查过程即质证过程，每一项审查结论都包含：
        1. 审查结论（采信/不采信/存疑）
        2. 法律依据（具体法条引用）
        3. 质证意见（可当庭陈述的质证理由）
        4. 质证策略（申请/请求/主张）

        结果保存到 evidence/evidence_review.json 和 analysis/cross_examination.md
        """
        from llm_client import LLMClient

        texts = self._load_evidence_texts()

        # 过滤掉起诉书/起诉意见书，只审查证据
        evidence_texts = [t for t in texts if not t.get("is_indictment")]

        if not evidence_texts:
            return {
                "case_id": self.case_id,
                "total_evidence": 0,
                "reviews": [],
                "error": "没有可审查的证据",
            }

        llm = LLMClient()
        reviews = []

        for ev in evidence_texts:
            ev_name = ev.get("filename", "未知证据")
            ev_type = ev.get("type", "其他证据")
            ev_text = ev.get("text", "")

            if not ev_text.strip():
                continue

            # 获取对应证据类型的审查模板
            template = self._get_review_template(ev_type)

            # 构建差异化审查提示词
            prompt = self._build_review_prompt(ev, template)

            try:
                response = await llm.chat([
                    {"role": "system", "content": "你是一名资深刑事辩护律师，精通证据法和庭审质证技巧。你的任务是严格审查证据的三性（合法性、真实性、关联性），并生成可直接用于庭审的质证意见。审查要具体、有针对性，法律依据要准确。"},
                    {"role": "user", "content": prompt}
                ])

                # 解析审查结果
                review = self._parse_review_result(response, ev)
                reviews.append(review)

            except Exception as e:
                logger.error(f"[证据审查] {ev_name} 审查失败: {e}")
                reviews.append({
                    "evidence_name": ev_name,
                    "evidence_ref": ev.get("evidence_ref", ""),
                    "evidence_type": ev_type,
                    "legality": {"conclusion": "存疑", "score": 70, "findings": [], "cross_opinion": f"审查失败: {str(e)}", "strategy": ["人工复核"]},
                    "authenticity": {"conclusion": "存疑", "score": 70, "findings": [], "cross_opinion": f"审查失败: {str(e)}", "strategy": ["人工复核"]},
                    "relevance": {"conclusion": "存疑", "score": 70, "findings": [], "cross_opinion": f"审查失败: {str(e)}", "strategy": ["人工复核"]},
                    "final_conclusion": "存疑",
                    "cross_examination_summary": f"审查失败: {str(e)}",
                    "error": str(e)
                })

        # 生成质证意见 Markdown
        cross_md = self._generate_cross_examination_markdown(reviews)

        # 保存审查结果
        evidence_dir = self.case_dir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)

        review_file = evidence_dir / "evidence_review.json"
        result = {
            "case_id": self.case_id,
            "total_evidence": len(evidence_texts),
            "reviews": reviews,
            "generated_at": datetime.now().isoformat(),
        }
        review_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        # 同时保存到 analysis/ 目录（兼容旧代码）
        analysis_review_file = self.analysis_dir / "evidence_review.json"
        analysis_review_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        # 保存质证意见 Markdown
        cross_file = self.analysis_dir / "cross_examination.md"
        cross_file.write_text(cross_md, encoding="utf-8")

        return result

    def _generate_cross_examination_markdown(self, reviews: List[Dict[str, Any]]) -> str:
        """生成质证意见 Markdown 文档"""
        md = "# 证据质证意见\n\n"
        md += f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        md += f"> 审查证据数量：{len(reviews)}\n\n"

        # 统计问题证据
        problematic = []
        for rev in reviews:
            issues = []
            leg_score = rev.get("legality", {}).get("score", 100)
            auth_score = rev.get("authenticity", {}).get("score", 100)
            rel_score = rev.get("relevance", {}).get("score", 100)

            if leg_score < 70:
                issues.append(("合法性", leg_score, rev.get("legality", {})))
            if auth_score < 70:
                issues.append(("真实性", auth_score, rev.get("authenticity", {})))
            if rel_score < 70:
                issues.append(("关联性", rel_score, rev.get("relevance", {})))

            if issues:
                problematic.append((rev, issues))

        # 概览
        md += "## 一、审查概览\n\n"
        md += f"- 审查证据总数：{len(reviews)}\n"
        md += f"- 问题证据数量：{len(problematic)}\n"
        md += f"- 无明显问题证据：{len(reviews) - len(problematic)}\n\n"

        if not problematic:
            md += "**结论**：经审查，本案证据总体质量较好，未发现明显问题。建议重点审查证据收集程序是否合法、证据之间的印证关系、言词证据的稳定性。\n\n"
        else:
            md += f"**结论**：发现 {len(problematic)} 份证据存在问题，需要重点质证。\n\n"

        # 问题证据清单
        if problematic:
            md += "## 二、问题证据清单\n\n"
            md += "| 序号 | 证据名称 | 证据编号 | 问题类型 | 分数 | 质证要点 |\n"
            md += "|------|---------|---------|---------|------|----------|\n"

            for i, (rev, issues) in enumerate(problematic, 1):
                name = rev.get("evidence_name", "未知证据")
                ref = rev.get("evidence_ref", "")
                for issue_type, score, issue_data in issues:
                    cross_opinion = issue_data.get("cross_opinion", "详见审查报告")
                    md += f"| {i} | {name} | {ref} | {issue_type} | {score} | {cross_opinion[:40]}... |\n"

            md += "\n"

        # 详细质证意见
        md += "## 三、详细质证意见\n\n"

        for i, rev in enumerate(reviews, 1):
            name = rev.get("evidence_name", "未知证据")
            ref = rev.get("evidence_ref", "")
            ev_type = rev.get("evidence_type", "其他证据")

            md += f"### {i}. {name}\n\n"
            md += f"**证据编号**：{ref}\n\n"
            md += f"**证据类型**：{ev_type}\n\n"

            # 合法性审查
            legality = rev.get("legality", {})
            leg_score = legality.get("score", 0)
            leg_conclusion = legality.get("conclusion", "存疑")
            md += f"#### （一）合法性审查\n\n"
            md += f"**审查结论**：{leg_conclusion}（{leg_score}分）\n\n"

            findings = legality.get("findings", [])
            if findings:
                md += "**发现问题**：\n\n"
                for f in findings:
                    md += f"- **{f.get('issue', '问题')}**\n"
                    if f.get('legal_basis'):
                        md += f"  - 法条依据：{f['legal_basis']}\n"
                    if f.get('details'):
                        md += f"  - 详细说明：{f['details']}\n"
                md += "\n"

            cross_opinion = legality.get("cross_opinion", "")
            if cross_opinion:
                md += f"**质证意见**：{cross_opinion}\n\n"

            strategy = legality.get("strategy", [])
            if strategy:
                md += "**质证策略**：\n"
                for s in strategy:
                    md += f"- {s}\n"
                md += "\n"

            # 真实性审查
            authenticity = rev.get("authenticity", {})
            auth_score = authenticity.get("score", 0)
            auth_conclusion = authenticity.get("conclusion", "存疑")
            md += f"#### （二）真实性审查\n\n"
            md += f"**审查结论**：{auth_conclusion}（{auth_score}分）\n\n"

            findings = authenticity.get("findings", [])
            if findings:
                md += "**发现问题**：\n\n"
                for f in findings:
                    md += f"- **{f.get('issue', '问题')}**\n"
                    if f.get('legal_basis'):
                        md += f"  - 法条依据：{f['legal_basis']}\n"
                    if f.get('details'):
                        md += f"  - 详细说明：{f['details']}\n"
                md += "\n"

            cross_opinion = authenticity.get("cross_opinion", "")
            if cross_opinion:
                md += f"**质证意见**：{cross_opinion}\n\n"

            strategy = authenticity.get("strategy", [])
            if strategy:
                md += "**质证策略**：\n"
                for s in strategy:
                    md += f"- {s}\n"
                md += "\n"

            # 关联性审查
            relevance = rev.get("relevance", {})
            rel_score = relevance.get("score", 0)
            rel_conclusion = relevance.get("conclusion", "存疑")
            md += f"#### （三）关联性审查\n\n"
            md += f"**审查结论**：{rel_conclusion}（{rel_score}分）\n\n"

            findings = relevance.get("findings", [])
            if findings:
                md += "**发现问题**：\n\n"
                for f in findings:
                    md += f"- **{f.get('issue', '问题')}**\n"
                    if f.get('legal_basis'):
                        md += f"  - 法条依据：{f['legal_basis']}\n"
                    if f.get('details'):
                        md += f"  - 详细说明：{f['details']}\n"
                md += "\n"

            cross_opinion = relevance.get("cross_opinion", "")
            if cross_opinion:
                md += f"**质证意见**：{cross_opinion}\n\n"

            strategy = relevance.get("strategy", [])
            if strategy:
                md += "**质证策略**：\n"
                for s in strategy:
                    md += f"- {s}\n"
                md += "\n"

            # 综合结论
            final_conclusion = rev.get("final_conclusion", "存疑")
            summary = rev.get("cross_examination_summary", "")
            md += f"#### （四）综合结论\n\n"
            md += f"**结论**：{final_conclusion}\n\n"
            if summary:
                md += f"**综合质证意见**：{summary}\n\n"

            md += "---\n\n"

        return md

    # 保留旧方法名作为别名，保持向后兼容
    async def review_evidence_triple_property(self) -> Dict[str, Any]:
        """对全部证据进行三性审查（真实性、合法性、关联性）

        已重构为 generate_cross_examination_opinion()
        审查结果保存到 evidence/evidence_review.json
        """
        return await self.generate_cross_examination_opinion()

    # ========== 阅卷笔录 ==========

    async def generate_review_notes(self) -> Dict[str, Any]:
        """生成阅卷笔录

        阅卷笔录是律师阅卷工作的核心文档，汇总案件关键信息。
        """
        texts = self._load_evidence_texts()

        # 获取各阶段分析结果
        stage1_md = _read_stage_md(self.analysis_dir, 1)  # 指控要素
        stage2_md = _read_stage_md(self.analysis_dir, 2)  # 人物关系
        stage3_md = _read_stage_md(self.analysis_dir, 3)  # 事件时间线
        stage4_md = _read_stage_md(self.analysis_dir, 4)  # 法律法规

        # 检查是否有证据审查结果
        review_file = self.analysis_dir / "evidence_review.json"
        review_data = None
        if review_file.exists():
            try:
                review_data = json.loads(review_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        # 构建阅卷笔录内容
        notes_md = "# 阅卷笔录\n\n"
        notes_md += f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"

        # 一、案件基本信息（从 stage 1 提取）
        if stage1_md:
            notes_md += "## 一、案件基本信息\n\n"
            # 提取关键信息
            import re
            defendant_match = re.search(r"被告人[：:]\s*(.+?)(?:\n|$)", stage1_md)
            crime_match = re.search(r"涉嫌罪名[：:]\s*(.+?)(?:\n|$)", stage1_md)
            if defendant_match or crime_match:
                if defendant_match:
                    notes_md += f"**被告人**：{defendant_match.group(1).strip()}\n\n"
                if crime_match:
                    notes_md += f"**涉嫌罪名**：{crime_match.group(1).strip()}\n\n"
            notes_md += "### 指控要素摘要\n\n"
            # 截取前 2000 字
            notes_md += stage1_md[:2000] + "\n\n"

        # 二、证据目录
        notes_md += "## 二、证据目录\n\n"
        notes_md += "| 编号 | 证据名称 | 类型 |\n|------|---------|------|\n"
        for t in texts:
            ref = t.get("evidence_ref", "")
            if ref:
                notes_md += f"| {ref} | {t['filename']} | {t.get('type', '其他')} |\n"

        # 三、证据三性审查摘要
        if review_data and review_data.get("reviews"):
            notes_md += "\n## 三、证据三性审查摘要\n\n"
            for rev in review_data["reviews"][:10]:  # 最多显示前 10 个
                name = rev.get("evidence_name", "未知证据")
                notes_md += f"### {name}\n\n"
                if rev.get("authenticity"):
                    score = rev["authenticity"].get("score", 0)
                    notes_md += f"- **真实性**：{score}分 — {rev['authenticity'].get('conclusion', '')}\n"
                if rev.get("legality"):
                    score = rev["legality"].get("score", 0)
                    notes_md += f"- **合法性**：{score}分 — {rev['legality'].get('conclusion', '')}\n"
                if rev.get("relevance"):
                    score = rev["relevance"].get("score", 0)
                    notes_md += f"- **关联性**：{score}分 — {rev['relevance'].get('conclusion', '')}\n"
                notes_md += "\n"

        # 四、人物关系（从 stage 2 提取）
        if stage2_md:
            notes_md += "## 四、人物关系\n\n"
            notes_md += stage2_md[:1500] + "\n\n"

        # 五、事件时间线（从 stage 3 提取）
        if stage3_md:
            notes_md += "## 五、事件时间线\n\n"
            notes_md += stage3_md[:1500] + "\n\n"

        # 六、法律分析（从 stage 4 提取）
        if stage4_md:
            notes_md += "## 六、法律分析\n\n"
            notes_md += stage4_md[:1500] + "\n\n"

        # 七、辩护要点（待补充）
        notes_md += "## 七、辩护要点\n\n"
        notes_md += "> 请在完成三阶层分析后补充辩护要点\n"

        # 保存阅卷笔录
        notes_file = self.analysis_dir / "review_notes.md"
        notes_file.write_text(notes_md, encoding="utf-8")

        return {
            "case_id": self.case_id,
            "content": notes_md,
            "generated_at": datetime.now().isoformat(),
        }

    # ========== 质证意见（向后兼容） ==========

    async def generate_cross_examination(self) -> Dict[str, Any]:
        """生成质证意见（向后兼容方法）

        注意：此方法已合并到 generate_cross_examination_opinion() 中。
        如果未进行证据审查，会先执行审查。
        """
        # 检查是否已有审查结果
        review_file = self.analysis_dir / "evidence_review.json"
        if not review_file.exists():
            # 执行审查
            result = await self.generate_cross_examination_opinion()
        else:
            # 读取已有结果
            try:
                result = json.loads(review_file.read_text(encoding="utf-8"))
            except Exception:
                result = await self.generate_cross_examination_opinion()

        # 读取生成的质证意见 Markdown
        cross_file = self.analysis_dir / "cross_examination.md"
        cross_content = ""
        if cross_file.exists():
            cross_content = cross_file.read_text(encoding="utf-8")

        # 统计问题证据数量
        problematic_count = 0
        reviews = result.get("reviews", [])
        for rev in reviews:
            leg_score = rev.get("legality", {}).get("score", 100)
            auth_score = rev.get("authenticity", {}).get("score", 100)
            rel_score = rev.get("relevance", {}).get("score", 100)
            if leg_score < 70 or auth_score < 70 or rel_score < 70:
                problematic_count += 1

        return {
            "case_id": self.case_id,
            "content": cross_content,
            "total_evidence": len(reviews),
            "problematic_count": problematic_count,
            "generated_at": datetime.now().isoformat(),
        }


# ========== 工具函数 ==========

def generate_evidence_chain(case_path: Path) -> Dict[str, Any]:
    """生成证据链可视化数据

    结构：
    - 顶层：指控事实（从起诉书提取）
    - 中层：待证事实（构成要件事实：主体、主观、行为、结果、情节）
    - 底层：证据（按类型分组）
    - 边：证明关系、印证关系、矛盾关系

    核心逻辑：证据用于证明事实
    """
    analysis_dir = case_path / "analysis"
    evidence_dir = case_path / "evidence"

    # 1. 读取证据清单
    index_file = evidence_dir / "index.json"
    if not index_file.exists():
        return {"nodes": [], "edges": [], "groups": [], "total_evidence": 0, "total_relations": 0, "error": "证据清单不存在"}

    try:
        data = json.loads(index_file.read_text(encoding="utf-8"))
        evidence_list = data.get("evidence", []) if isinstance(data, dict) else data
    except Exception:
        return {"nodes": [], "edges": [], "groups": [], "total_evidence": 0, "total_relations": 0, "error": "证据清单读取失败"}

    if not evidence_list:
        return {"nodes": [], "edges": [], "groups": [], "total_evidence": 0, "total_relations": 0, "error": "无证据数据"}

    # 2. 提取指控事实（从起诉书/起诉意见书）
    accusation = _extract_accusation(evidence_list, analysis_dir, evidence_dir)
    defendant = accusation.get("defendant", "") if accusation else ""

    # 2.5 提取具体的待证事实内容（从分析结果）
    facts_content = _extract_facts_to_prove(analysis_dir, defendant)

    # 3. 定义待证事实（构成要件事实）
    facts_to_prove = [
        {
            "id": "fact_subject",
            "name": "主体事实",
            "description": facts_content.get("fact_subject", "被告人身份、刑事责任能力"),
            "required": True,
            "keywords": ["身份", "户籍", "年龄", "精神", "责任能力", "被告人", "犯罪嫌疑人", "姓名", "出生"],
        },
        {
            "id": "fact_subjective",
            "name": "主观事实",
            "description": facts_content.get("fact_subjective", "犯罪故意/过失、目的、动机"),
            "required": True,
            "keywords": ["故意", "明知", "目的", "动机", "应当知道", "应当预见", "过失", "放任", "希望", "牟利", "非法利益", "营利", "谋利"],
        },
        {
            "id": "fact_behavior",
            "name": "行为事实",
            "description": facts_content.get("fact_behavior", "具体犯罪行为、手段、方式"),
            "required": True,
            "keywords": ["实施", "行为", "手段", "方式", "参与", "组织", "策划", "实施", "操作", "进行"],
        },
        {
            "id": "fact_result",
            "name": "结果事实",
            "description": facts_content.get("fact_result", "危害后果、数额、损失"),
            "required": True,
            "keywords": ["数额", "金额", "损失", "后果", "获利", "渔利", "赌资", "抽头", "价值", "人民币"],
        },
        {
            "id": "fact_causation",
            "name": "因果关系",
            "description": facts_content.get("fact_causation", "行为与结果的因果链"),
            "required": True,
            "keywords": ["导致", "致使", "造成", "引起", "引发", "结果", "因果"],
        },
        {
            "id": "fact_circumstance",
            "name": "情节事实",
            "description": facts_content.get("fact_circumstance", "自首、坦白、认罪认罚、累犯等"),
            "required": False,
            "keywords": ["自首", "坦白", "认罪", "从轻", "从重", "累犯", "立功", "退赃", "赔偿"],
        },
    ]

    # 4. 证据类型定义（依据刑事诉讼法第50条规定的八种法定证据种类）
    #
    # 法定证据种类（8类）：
    # 1. 物证 - 以物质属性证明案件（形状、颜色、大小等）
    # 2. 书证 - 以记载内容、文字、符号证明案件
    # 3. 证人证言 - 证人对案件事实的陈述
    # 4. 被害人陈述 - 被害人对案件事实的陈述
    # 5. 犯罪嫌疑人、被告人供述和辩解 - 嫌疑人/被告人的陈述
    # 6. 鉴定意见 - 专业人员（鉴定人）对专门性问题的技术性结论，需要鉴定资质
    # 7. 勘验、检查、辨认、侦查实验等笔录 - 侦查活动的程序性记录
    # 8. 视听资料、电子数据 - 以科技手段存储的信息
    #
    # 关键区分：
    # - 电子数据检查笔录 = 勘验检查笔录（程序性记录），不是电子数据本身
    # - 手机检查笔录 = 勘验检查笔录，记录检查过程
    # - 提取的微信记录、转账记录等 = 电子数据（内容本身）
    # - 鉴定意见需要鉴定人资质，如法医鉴定、DNA鉴定、毒物鉴定等
    #
    evidence_types = {
        "indictment": {
            "name": "指控文书",
            "color": "#dc2626",
            "keywords": ["起诉书", "起诉意见书", "公诉书"],
            "desc": "起诉书、起诉意见书等指控材料"
        },
        "confession": {
            "name": "被告人供述",
            "color": "#2563eb",
            "keywords": ["供述", "辩解", "讯问笔录"],
            "desc": "犯罪嫌疑人、被告人的陈述"
        },
        "witness": {
            "name": "证人证言",
            "color": "#16a34a",
            "keywords": ["证人证言"],
            "desc": "证人对案件事实的陈述"
        },
        "victim": {
            "name": "被害人陈述",
            "color": "#f59e0b",
            "keywords": ["被害人陈述"],
            "desc": "被害人对案件事实的陈述"
        },
        "documentary": {
            "name": "书证",
            "color": "#9333ea",
            "keywords": ["书证"],
            "desc": "以记载内容证明案件（合同、协议、决定书等）"
        },
        "physical": {
            "name": "物证",
            "color": "#78716c",
            "keywords": ["物证"],
            "desc": "以物质属性证明案件"
        },
        "expert": {
            "name": "鉴定意见",
            "color": "#ea580c",
            "keywords": ["鉴定意见"],
            "desc": "专业人员的技术性结论（需要鉴定资质）"
        },
        "inspection": {
            "name": "勘验检查笔录",
            "color": "#0891b2",
            "keywords": ["勘验", "检查笔录", "辨认笔录", "侦查实验"],
            "desc": "侦查活动的程序性记录"
        },
        "electronic": {
            "name": "电子数据",
            "color": "#6366f1",
            "keywords": ["电子数据"],
            "desc": "以科技手段存储的信息（聊天记录、转账记录等）"
        },
        "audiovisual": {
            "name": "视听资料",
            "color": "#14b8a6",
            "keywords": ["视听资料"],
            "desc": "视频、音频等"
        },
        "procedural": {
            "name": "程序性文书",
            "color": "#6b7280",
            "keywords": ["程序性文书", "立案决定", "拘留证", "逮捕证", "受案登记"],
            "desc": "诉讼程序文书（立案、拘留、逮捕等）"
        },
        "other": {
            "name": "其他证据",
            "color": "#6b7280",
            "keywords": [],
            "desc": ""
        },
    }

    # 5. 分类证据并关联待证事实
    evidence_by_type = {t: [] for t in evidence_types}
    all_evidence = []

    for i, ev in enumerate(evidence_list):
        if not isinstance(ev, dict):
            continue

        ev_id = ev.get("id", f"ev_{i}")
        ev_name = ev.get("name", f"证据{i}")
        ev_type = ev.get("type", "")
        ev_summary = ev.get("summary_preview", "")
        ev_content = ev.get("content", "")

        # 分类证据：严格依据刑事诉讼法法定证据种类
        # 优先根据证据 type 字段精确匹配，再使用关键词 fallback
        cat = "other"

        # 1. 指控文书（起诉书、起诉意见书）- 单独分类便于识别
        if "起诉书" in ev_type or "起诉意见书" in ev_type:
            cat = "indictment"

        # 2. 犯罪嫌疑人供述和辩解（法定种类第5类）
        elif "犯罪嫌疑人供述" in ev_type or "供述和辩解" in ev_type:
            cat = "confession"

        # 3. 证人证言（法定种类第3类）
        elif "证人证言" in ev_type:
            cat = "witness"

        # 4. 被害人陈述（法定种类第4类）
        elif "被害人陈述" in ev_type:
            cat = "victim"

        # 5. 勘验检查辨认笔录（法定种类第7类）- 优先级要高！
        # 包括：现场勘验笔录、检查笔录、辨认笔录、电子数据检查笔录
        # 注意：电子数据检查笔录是笔录类，不是鉴定意见或电子数据！
        # 通过证据名称判断：如果名称包含"检查笔录"、"勘验"、"辨认"，归为笔录类
        elif "勘验检查辨认笔录" in ev_type:
            cat = "inspection"
        elif "勘验" in ev_type and "笔录" in ev_type:
            cat = "inspection"
        elif "检查笔录" in ev_name or "检查笔录" in ev_type:
            # 手机检查笔录、电子数据检查笔录都属于勘验检查笔录
            cat = "inspection"
        elif "辨认笔录" in ev_type:
            cat = "inspection"

        # 6. 鉴定意见（法定种类第6类）
        # 必须是专业鉴定人出具的技术性结论，如DNA鉴定、法医鉴定等
        # 注意：电子数据检查笔录不是鉴定意见！要通过上面的笔录类优先排除
        elif ev_type == "鉴定意见":
            cat = "expert"

        # 7. 视听资料（法定种类第8类的一部分）
        elif "视听资料" in ev_type:
            cat = "audiovisual"

        # 8. 电子数据（法定种类第8类）
        # 提取的聊天记录、转账记录、文件内容等
        # 注意：电子数据检查笔录已经在上面的笔录类排除了
        elif ev_type == "电子数据" or "电子证据" in ev_type:
            cat = "electronic"

        # 9. 书证（法定种类第2类）
        # 以记载内容证明案件：合同、协议、决定书、行政处罚决定书等
        elif ev_type == "书证":
            cat = "documentary"

        # 10. 物证（法定种类第1类）
        elif "物证" in ev_type:
            cat = "physical"

        # 11. 程序性文书（诉讼程序性材料）
        # 立案决定书、拘留证、逮捕证、受案登记表、提请批准逮捕书等
        # 注意：程序性文书/书证 优先归类为程序性文书
        elif "程序性文书" in ev_type:
            cat = "procedural"

        # 12. 书证（兜底，处理单独的书证标记）
        elif "书证" in ev_type:
            cat = "documentary"

        else:
            # 关键词匹配作为 fallback
            combined = ev_name + " " + ev_type + " " + ev_summary
            for type_key, type_info in evidence_types.items():
                if any(kw in combined for kw in type_info["keywords"]):
                    cat = type_key
                    break

        # 分析该证据能证明哪些待证事实
        proves_facts = []
        proves_strength = {}  # 记录每个事实的证明强度
        full_text = (ev_name + " " + ev_summary + " " + ev_content[:3000]).lower()

        # 如果 index.json 中没有 content，尝试读取 MD 文件
        if not ev_content and ev.get("md_file"):
            md_path = evidence_dir / ev["md_file"]
            if md_path.exists():
                try:
                    full_text = (ev_name + " " + ev_summary + " " + md_path.read_text(encoding="utf-8")[:5000]).lower()
                except Exception:
                    pass

        for fact in facts_to_prove:
            match_count = sum(1 for kw in fact["keywords"] if kw.lower() in full_text)
            if match_count > 0:
                proves_facts.append(fact["id"])
                # 根据匹配关键词数量估算证明力
                if match_count >= 3:
                    proves_strength[fact["id"]] = "high"
                elif match_count >= 1:
                    proves_strength[fact["id"]] = "medium"

        evidence_item = {
            "id": ev_id,
            "name": ev_name,
            "type": ev_type[:20] if ev_type else "其他",
            "category": cat,
            "color": evidence_types[cat]["color"],
            "proves": proves_facts,
            "proves_strength": proves_strength,
            "summary": ev_summary[:150] if ev_summary else "",  # 证据摘要（前150字）
        }

        evidence_by_type[cat].append(evidence_item)
        all_evidence.append(evidence_item)

    # 6. 构建节点和边
    nodes = []
    edges = []
    groups = []

    # 6.1 指控事实节点
    if accusation:
        nodes.append({
            "id": "accusation",
            "name": accusation.get("name", "指控事实"),
            "description": accusation.get("description", "")[:100],
            "type": "accusation",
            "color": "#1e3a5f",
        })

    # 6.2 待证事实节点
    for fact in facts_to_prove:
        # 统计关联证据数量
        ev_count = sum(1 for ev in all_evidence if fact["id"] in ev.get("proves", []))
        fact_node = {
            "id": fact["id"],
            "name": fact["name"],
            "description": fact["description"],
            "type": "fact",
            "required": fact["required"],
            "evidence_count": ev_count,
            # 根据证据数量判断强弱
            "strength": "strong" if ev_count >= 3 else ("medium" if ev_count >= 1 else "weak"),
        }
        # 颜色根据证据充分度
        if ev_count >= 3:
            fact_node["color"] = "#16a34a"  # 绿色-充分
        elif ev_count >= 1:
            fact_node["color"] = "#ca8a04"  # 黄色-一般
        else:
            fact_node["color"] = "#dc2626"  # 红色-薄弱
        nodes.append(fact_node)

    groups.append({"id": "facts", "name": "待证事实", "color": "#1f2937", "count": len(facts_to_prove)})

    # 6.3 证据节点（每类限制数量，优先显示证明关系多的）
    max_per_type = {
        "indictment": 5,
        "confession": 10,
        "witness": 10,
        "documentary": 10,
        "expert": 5,
        "inspection": 10,
        "electronic": 5,
        "audiovisual": 5,
        "procedural": 10,
        "physical": 5,
        "victim": 5,
        "other": 10
    }
    evidence_nodes = []

    for cat, items in evidence_by_type.items():
        # 按证明事实数量排序，优先显示证明关系多的
        sorted_items = sorted(items, key=lambda x: len(x.get("proves", [])), reverse=True)
        for item in sorted_items[:max_per_type.get(cat, 4)]:
            nodes.append(item)
            evidence_nodes.append(item)

        if items:
            groups.append({
                "id": cat,
                "name": evidence_types[cat]["name"],
                "color": evidence_types[cat]["color"],
                "count": len(items),
            })

    # 6.4 构建边：证据 → 待证事实（证明关系）
    for ev in evidence_nodes:
        for fact_id in ev.get("proves", []):
            strength = ev.get("proves_strength", {}).get(fact_id, "medium")
            edges.append({
                "source": ev["id"],
                "target": fact_id,
                "type": "prove",
                "label": "证明",
                "strength": strength,
            })

    # 6.5 待证事实 → 指控事实（支撑关系）
    if accusation:
        for fact in facts_to_prove:
            if fact["required"]:  # 只有必要的待证事实才关联指控
                edges.append({
                    "source": fact["id"],
                    "target": "accusation",
                    "type": "support",
                    "label": "支撑",
                })

    # 6.6 读取证据三性审查，提取矛盾关系
    review_file = analysis_dir / "evidence_review.json"
    contradictions = []
    evidence_issues = {}  # 记录每个证据的问题

    if review_file.exists():
        try:
            review_data = json.loads(review_file.read_text(encoding="utf-8"))
            for review in review_data.get("reviews", []):
                ev_ref = review.get("evidence_ref", "")
                ev_name = review.get("evidence_name", "")
                issues = []
                for prop in ["authenticity", "legality", "relevance"]:
                    prop_data = review.get(prop, {})
                    if prop_data.get("issues"):
                        issues.extend(prop_data.get("issues", []))
                if issues:
                    contradictions.append({
                        "evidence": ev_ref,
                        "name": ev_name,
                        "issues": issues[:2],
                    })
                    evidence_issues[ev_ref] = issues[:2]
        except Exception:
            pass

    # 6.7 构建证据间的印证/矛盾关系
    # 印证关系：证明同一待证事实的不同证据
    fact_evidence_map = {}  # fact_id -> [evidence_ids]
    for ev in evidence_nodes:
        for fact_id in ev.get("proves", []):
            if fact_id not in fact_evidence_map:
                fact_evidence_map[fact_id] = []
            fact_evidence_map[fact_id].append(ev["id"])

    # 添加印证边（同一事实的证据间）
    for fact_id, ev_ids in fact_evidence_map.items():
        if len(ev_ids) >= 2:
            # 取前两个证据建立印证关系
            edges.append({
                "source": ev_ids[0],
                "target": ev_ids[1],
                "type": "corroborate",
                "label": "印证",
            })

    # 7. 分析证据链薄弱环节
    weak_points = []
    for fact in facts_to_prove:
        ev_count = sum(1 for ev in all_evidence if fact["id"] in ev.get("proves", []))
        if fact["required"] and ev_count == 0:
            weak_points.append({
                "fact_id": fact["id"],
                "fact_name": fact["name"],
                "issue": f"无直接证据证明",
                "risk": "high",
            })
        elif fact["required"] and ev_count == 1:
            weak_points.append({
                "fact_id": fact["id"],
                "fact_name": fact["name"],
                "issue": "仅有单一证据，缺乏印证",
                "risk": "medium",
            })

    # 检查是否仅有言词证据
    for fact in facts_to_prove:
        related_ev = [ev for ev in all_evidence if fact["id"] in ev.get("proves", [])]
        verbal_only = all(ev["category"] in ["confession", "witness", "indictment"] for ev in related_ev)
        if verbal_only and len(related_ev) > 0 and fact["required"]:
            existing = next((wp for wp in weak_points if wp["fact_id"] == fact["id"]), None)
            if not existing:
                weak_points.append({
                    "fact_id": fact["id"],
                    "fact_name": fact["name"],
                    "issue": "仅有言词证据，缺乏客观证据印证",
                    "risk": "medium",
                })

    # 8. 更新 facts_to_prove 中的 evidence_count
    for fact in facts_to_prove:
        ev_count = sum(1 for ev in all_evidence if fact["id"] in ev.get("proves", []))
        fact["evidence_count"] = ev_count

    # 9. 统计摘要
    strong_chains = [f["name"] for f in facts_to_prove
                     if f.get("evidence_count", 0) >= 3]
    weak_chains = [wp["fact_name"] for wp in weak_points if wp["risk"] == "high"]

    return {
        "accusation": accusation,
        "nodes": nodes,
        "edges": edges,
        "groups": groups,
        "facts_to_prove": facts_to_prove,
        "evidence_groups": [{"id": k, "name": v["name"], "color": v["color"],
                            "count": len(evidence_by_type[k])} for k, v in evidence_types.items()
                           if evidence_by_type[k]],
        "weak_points": weak_points,
        "contradictions": contradictions[:5],
        "summary": {
            "total_evidence": len(all_evidence),
            "displayed_evidence": len(evidence_nodes),
            "total_relations": len(edges),
            "strong_chains": strong_chains,
            "weak_chains": weak_chains,
        },
    }


def _extract_accusation(evidence_list: list, analysis_dir: Path, evidence_dir: Path) -> Optional[Dict[str, Any]]:
    """从起诉书/起诉意见书提取指控事实

    优先级：stage_1/output.md > stage_1/output.json > 直接解析起诉书 MD
    """
    # 1. 尝试从 stage_1/output.md 提取（内容更丰富）
    stage_1_md = analysis_dir / "stage_1" / "output.md"
    if stage_1_md.exists():
        try:
            content = stage_1_md.read_text(encoding="utf-8")
            # 提取关键信息
            lines = content.split("\n")
            crime_type = ""
            accusation_summary = []
            defendant = ""

            for line in lines:
                # 提取罪名
                if "**开设赌场罪**" in line or "指控罪名" in line:
                    crime_type = "开设赌场罪"
                elif "**" in line and "罪**" in line:
                    # 提取其他罪名
                    import re
                    match = re.search(r'\*\*(.+?罪)\*\*', line)
                    if match:
                        crime_type = match.group(1)

                # 提取被告人
                if "被告人" in line and ("高为峰" in line or "主犯" in line):
                    import re
                    match = re.search(r'(高为峰|丁以建|方天兴)', line)
                    if match:
                        defendant = match.group(1)

                # 提取指控事实摘要
                if any(kw in line for kw in ["指控事实", "核心行为", "危害结果", "抽头渔利"]):
                    accusation_summary.append(line.strip())

            if crime_type and accusation_summary:
                return {
                    "name": f"指控：{crime_type}",
                    "description": " ".join(accusation_summary[:3])[:500],
                    "source": "阶段1分析",
                    "defendant": defendant,
                }
        except Exception:
            pass

    # 2. 尝试从 stage_1/output.json 读取
    stage_1_file = analysis_dir / "stage_1" / "output.json"
    if stage_1_file.exists():
        try:
            stage_1_data = json.loads(stage_1_file.read_text(encoding="utf-8"))
            if stage_1_data.get("accusation"):
                return stage_1_data["accusation"]
            # 从分析结果提取
            if stage_1_data.get("crime_type"):
                return {
                    "name": f"指控：{stage_1_data.get('crime_type', '罪名')}",
                    "description": stage_1_data.get("accusation_summary", ""),
                    "source": "阶段1分析",
                }
            # 提取被告人信息
            defendant = stage_1_data.get("defendant", "")
            indictment_source = stage_1_data.get("indictment_source", "")
            if defendant and indictment_source:
                # 从 stage_1 信息推断罪名
                return {
                    "name": "指控：开设赌场罪" if "赌场" in indictment_source else "指控事实",
                    "description": f"被告人：{defendant}；来源：{indictment_source}",
                    "source": indictment_source,
                    "defendant": defendant,
                }
        except Exception:
            pass

    # 2. 从起诉书/起诉意见书 MD 文件提取
    for ev in evidence_list:
        if not isinstance(ev, dict):
            continue
        ev_name = ev.get("name", "")
        if "起诉书" in ev_name or "起诉意见书" in ev_name:
            # 读取 MD 文件内容（拼接 evidence_dir）
            md_filename = ev.get("md_file", "")
            if md_filename:
                md_file = evidence_dir / md_filename
                if md_file.exists():
                    try:
                        content = md_file.read_text(encoding="utf-8")
                        # 提取指控相关内容
                        lines = content.split("\n")
                        accusation_lines = []
                        for line in lines[:50]:
                            if any(kw in line for kw in ["指控", "犯罪", "实施", "经查", "认定"]):
                                accusation_lines.append(line.strip())
                        description = " ".join(accusation_lines[:5])[:300] if accusation_lines else content[:300]
                        return {
                            "name": f"指控：{ev_name.replace('.md', '')}",
                            "description": description,
                            "source": ev_name,
                        }
                    except Exception:
                        pass

    # 3. 返回默认值
    return {
        "name": "指控事实",
        "description": "请完成阶段1分析以提取指控事实",
        "source": "待分析",
    }


def _extract_facts_to_prove(analysis_dir: Path, defendant: str = "") -> Dict[str, str]:
    """从分析结果中提取具体的待证事实内容

    从 stage_1/output.md 中提取具体的构成要件事实描述，
    用于在证据链可视化中展示具体案件内容。

    Returns:
        Dict[fact_id, 具体描述内容]
    """
    facts_content = {}

    # 1. 尝试从 stage_1/output.md 提取
    stage_1_md = analysis_dir / "stage_1" / "output.md"
    if not stage_1_md.exists():
        return facts_content

    try:
        content = stage_1_md.read_text(encoding="utf-8")
        lines = content.split("\n")

        # 提取主体事实
        subject_info = []
        for line in lines:
            if any(kw in line for kw in ["被告人", "犯罪嫌疑人", "身份", "户籍", "出生", "年龄", "刑事责任"]):
                # 过滤掉表头和分隔符
                if "|" not in line or "**" in line:
                    continue
                if "---" in line:
                    continue
                subject_info.append(line.strip())
        if subject_info:
            facts_content["fact_subject"] = "；".join(subject_info[:3])[:200]

        # 提取主观事实
        subjective_info = []
        for line in lines:
            if any(kw in line for kw in ["故意", "明知", "目的", "牟利", "营利", "非法利益", "主观"]):
                if "---" not in line:
                    subjective_info.append(line.strip().replace("*", ""))
        if subjective_info:
            facts_content["fact_subjective"] = "；".join(subjective_info[:3])[:200]

        # 提取行为事实
        behavior_info = []
        capture_behavior = False
        for line in lines:
            # 检测行为描述段落
            if "指控行为" in line or "核心行为" in line:
                capture_behavior = True
                continue
            if capture_behavior:
                if line.startswith("###") or line.startswith("##"):
                    capture_behavior = False
                    continue
                if line.strip() and not line.startswith("---"):
                    behavior_info.append(line.strip().replace("*", ""))
        if behavior_info:
            facts_content["fact_behavior"] = " ".join(behavior_info[:4])[:300]

        # 提取结果事实
        result_info = []
        for line in lines:
            if any(kw in line for kw in ["数额", "金额", "渔利", "获利", "损失", "人民币", "万元"]):
                if "---" not in line and len(line.strip()) > 5:
                    result_info.append(line.strip().replace("*", ""))
        if result_info:
            facts_content["fact_result"] = "；".join(result_info[:3])[:200]

        # 提取情节事实
        circumstance_info = []
        for line in lines:
            if any(kw in line for kw in ["自首", "坦白", "认罪", "从轻", "立功", "退赃", "赔偿", "累犯"]):
                if "---" not in line:
                    circumstance_info.append(line.strip().replace("*", ""))
        if circumstance_info:
            facts_content["fact_circumstance"] = "；".join(circumstance_info[:2])[:150]

    except Exception:
        pass

    return facts_content


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
