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
import os
import tempfile
import time
import logging
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List
from datetime import datetime

from prompt_cache import build_cached_messages

logger = logging.getLogger(__name__)

# 统一禁止对话式输出的指令，追加到每个分析阶段的 system_prompt 末尾
_NO_CHITCHAT = """
**输出规则：直接输出分析内容，不要输出任何对话式开场白、寒暄、"好的"、"作为XXX"等客套话。
不要称呼对方为"律师"或被告人姓名，直接输出结构化分析结果。**"""

# 模块级 tiktoken 编码器缓存（避免每次调用重新加载）
_tiktoken_enc = None

def _get_enc():
    global _tiktoken_enc
    if _tiktoken_enc is None:
        import tiktoken
        _tiktoken_enc = tiktoken.get_encoding("cl100k_base")
    return _tiktoken_enc

def _get_content_budget_chars() -> int:
    """获取内容字符预算（委托统一预算模块）"""
    import context_budget
    return context_budget.content_budget_chars()


def _get_report_budget_chars() -> int:
    """获取报告上下文字符预算（约占总预算50%）"""
    return int(_get_content_budget_chars() * 0.5)


def _get_indictment_budget_chars() -> int:
    """获取起诉书字符预算（约占总预算10%）"""
    return int(_get_content_budget_chars() * 0.1)


def _get_evidence_budget_chars() -> int:
    """获取证据上下文字符预算（约占总预算80%）"""
    return int(_get_content_budget_chars() * 0.8)


def _get_knowledge_budget_chars() -> int:
    """获取法律知识字符预算（约占总预算10%）"""
    return int(_get_content_budget_chars() * 0.1)


async def _batch_analyze_evidence(
    texts: List[Dict[str, str]],
    system_prompt: str,
    user_prompt_header: str,
    user_prompt_footer: str,
    progress_cb: Optional[Callable] = None,
    label: str = "分析",
) -> List[str]:
    """分批处理证据，每批独立调用 LLM，返回所有批次的原始响应列表。

    不截断任何证据内容，按 token 预算分批，起诉书每批都带。
    """
    enc = _get_enc()

    from llm_client import get_llm_client
    client = get_llm_client()

    # 计算 prompt 固定开销
    header_tokens = len(enc.encode(user_prompt_header))
    footer_tokens = len(enc.encode(user_prompt_footer))
    system_tokens = len(enc.encode(system_prompt))
    # 预留：system + 响应 + 安全余量
    fixed_overhead = system_tokens + header_tokens + footer_tokens + 5000

    try:
        from config_manager import get_config_value
        context_limit = int(get_config_value("model_context_limit", "250000"))
    except Exception:
        context_limit = 250000

    evidence_budget = context_limit - fixed_overhead
    if evidence_budget < 20000:
        evidence_budget = 20000

    # 分离起诉书（每批必带）和普通证据
    indictment_types = {"起诉书", "起诉意见书"}
    indictments = [t for t in texts if t.get("type", "") in indictment_types]
    evidence = [t for t in texts if t.get("type", "") not in indictment_types]

    indictment_text = "\n\n".join(
        f"### {t['filename']}（{t['type']}）\n{t['text']}" for t in indictments
    )
    indictment_tokens = len(enc.encode(indictment_text)) if indictment_text else 0

    # M6: 起诉书单独超预算时告警并截断，避免首批就溢出
    if indictment_tokens > evidence_budget:
        logger.warning(f"[{label}] 起诉书 {indictment_tokens:,} tokens 超预算 {evidence_budget:,}，截断")
        indictment_text = indictment_text[:int(len(indictment_text) * evidence_budget / indictment_tokens)]
        indictment_tokens = len(enc.encode(indictment_text))

    # 普通证据按 token 数分批
    evidence_chunks: List[List[Dict[str, str]]] = []
    current_chunk: List[Dict[str, str]] = []
    current_tokens = indictment_tokens

    for ev in evidence:
        ev_text = f"### {ev['filename']}（{ev['type']}）\n{ev['text']}"
        ev_tokens = len(enc.encode(ev_text))
        if current_tokens + ev_tokens > evidence_budget and current_chunk:
            evidence_chunks.append(current_chunk)
            current_chunk = [ev]
            current_tokens = indictment_tokens + ev_tokens
        else:
            current_chunk.append(ev)
            current_tokens += ev_tokens
    if current_chunk:
        evidence_chunks.append(current_chunk)

    total_chunks = len(evidence_chunks)
    if total_chunks == 0:
        # 只有起诉书，没有普通证据
        evidence_chunks = [[]]
        total_chunks = 1

    logger.info(f"[{label}] {len(evidence)} 份证据 + {len(indictments)} 份起诉书 → "
                f"{total_chunks} 批（预算 {evidence_budget:,} tokens）")

    results: List[str] = []
    for ci, chunk in enumerate(evidence_chunks):
        if progress_cb:
            progress_cb(f"{label}第 {ci+1}/{total_chunks} 批...")

        chunk_text = indictment_text
        if chunk:
            chunk_parts = [f"### {t['filename']}（{t['type']}）\n{t['text']}" for t in chunk]
            chunk_text = (indictment_text + "\n\n" if indictment_text else "") + "\n\n".join(chunk_parts)

        user_prompt = f"{user_prompt_header}\n\n{chunk_text}\n\n{user_prompt_footer}"

        response = await client.chat([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])
        results.append(response)
        logger.info(f"[{label}] 第 {ci+1}/{total_chunks} 批完成，{len(response)} 字符")

    return results

try:
    from legal_knowledge import (
        get_legal_knowledge, get_dynamic_legal_knowledge, THEORY_THREE_TIERS,
        CONSTITUTIVE_ELEMENT_ANALYSIS, EVIDENCE_REVIEW_TEMPLATES,
        LEGAL_BASIS_FOR_REVIEW, CROSS_EXAMINATION_STRATEGIES, CROSS_EXAMINATION_TEMPLATE
    )
except ImportError:
    def get_legal_knowledge(): return ""
    def get_dynamic_legal_knowledge(charges=None): return ""
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
    json_blocks = _re.findall(r'```json[ \t]*\r?\n(.*?)```', text, _re.DOTALL)
    for block in json_blocks:
        try:
            data = json.loads(block.strip())
            # mermaid_fn 要求 dict，LLM 可能返回 list 导致 .get 报错
            if not isinstance(data, dict):
                logger.warning(f"[JSON渲染] JSON 非对象，跳过: {str(data)[:80]}")
                continue
            mermaid = mermaid_fn(data)
            replacement = f"```mermaid\n{mermaid}\n```"
            text = text.replace(f"```json\n{block}```", replacement, 1)
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as e:
            logger.warning(f"[JSON渲染] 解析失败: {e}, block={block[:80]}")
    return text


def build_reference_block(cards: List[Dict[str, Any]]) -> str:
    """把选中的真实案例卡片格式化为提示词注入块（畸形卡片缺字段时按空串渲染，不抛异常）"""
    blocks = []
    for c in cards:
        charges = "、".join(c.get("charges", []))
        blocks.append(
            f"【{c.get('case_no', '')}】{c.get('title', '')}\n"
            f"涉及罪名：{charges}\n"
            f"主要问题：{c.get('issue', '')}\n"
            f"裁判要旨：{c.get('holding_summary', '')}\n"
            f"裁判理由摘录：{c.get('reasoning_excerpt', '')}"
        )
    return "\n\n".join(blocks)


def _atomic_write(path, content: str):
    """先写同目录临时文件，成功后原子替换，避免中途失败损坏旧产物"""
    path = str(path)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


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

    def _load_evidence_texts(self, prefer_summary: bool = False) -> List[Dict[str, str]]:
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
                            # 优先从 index.json 的结构化字段构建 text
                            has_structured = any([
                                ev.get('key_facts', '').strip(),
                                ev.get('summary', '').strip(),
                            ])

                            if has_structured:
                                # 从结构化字段构建 text
                                fields_map = {
                                    'persons': '**涉案人员**：{}\n',
                                    'related_entities': '## 关联信息\n{}\n',
                                    'key_facts': '## 关键事实\n{}\n',
                                    'summary': '## 详细摘要\n{}\n',
                                    'original_quotes': '## 原文摘录\n{}\n',
                                    'contradiction_hints': '## 矛盾提示\n{}\n',
                                }

                                text_parts = [f"# {ev.get('name', '')}\n"]
                                for field, template in fields_map.items():
                                    if ev.get(field, '').strip():
                                        text_parts.append(template.format(ev[field]))

                                text = "\n".join(text_parts)
                            else:
                                # 向后兼容：回退到读取 .md 文件
                                text = md_file.read_text(encoding="utf-8")

                            # 判断是否为起诉书/起诉意见书（类型或名称包含关键词）
                            ev_type = ev.get("type", "其他证据")
                            ev_name = ev.get("name", "")
                            is_indictment = (
                                "起诉书" in ev_type or "起诉意见书" in ev_type or
                                "起诉书" in ev_name or "起诉意见书" in ev_name
                            )

                            # 单发阶段用浓缩摘要（digest）；起诉书保留原文全文，无摘要回退全文
                            text = _apply_digest(ev, text, prefer_summary and not is_indictment)

                            if text.strip():
                                ev_id = ev.get("id", 0)
                                texts.append({
                                    "filename": ev["name"],
                                    "type": ev_type,
                                    "text": text,
                                    "source": ev.get("source", ""),
                                    "page_range": ev.get("page_range", ""),
                                    "evidence_ref": f"证据{ev_id:03d}" if not is_indictment else "",
                                    "md_file": ev["md_file"],
                                    "doc_type": ev.get("doc_type", ""),
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

    def _save_stage(self, stage: int, data: Dict[str, Any], markdown: str, charge: Optional[str] = None):
        """保存阶段结果（多罪名时存到 analysis/{charge}/stage_N/）"""
        if charge:
            stage_dir = self.analysis_dir / charge / f"stage_{stage}"
        else:
            stage_dir = self.analysis_dir / f"stage_{stage}"
        stage_dir.mkdir(parents=True, exist_ok=True)

        # 保存 Markdown
        md_file = stage_dir / "output.md"
        _atomic_write(md_file, markdown)

        # 保存结构化 JSON
        json_file = stage_dir / "output.json"
        _atomic_write(json_file, json.dumps(data, ensure_ascii=False, indent=2))

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
- 用 Markdown 格式输出，结构清晰，便于后续分析""" + _NO_CHITCHAT

        user_prompt = f"""## 辩护对象
被告人：**{defendant}**

## 罪名线索
{f"涉嫌罪名可能是：{crime_type}" if crime_type else "（未指定罪名，请从材料中推断）"}

## 指控文书
{indictment["filename"]}（{indictment["type"]}）

> 注：优先以起诉书为准，无起诉书时以起诉意见书为准。如有多份，取形成时间最后的。

{indictment["text"][:_get_indictment_budget_chars()]}

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

    async def _ensure_digests(self):
        """digest 缺失的旧证据自动补摘要（防单发阶段回退全文消耗大量 token）

        起诉书/起诉意见书不算缺失（消费端对指控文书不用摘要）。
        summarize_evidence 有缓存断点续传，重复调用成本低；失败回退全文，不阻塞。
        """
        try:
            index_file = self.case_dir / "evidence" / "index.json"
            if not index_file.exists():
                return
            index = json.loads(index_file.read_text(encoding="utf-8"))
            missing = [
                ev for ev in index.get("evidence", [])
                if not ev.get("digest")
                and not ("起诉书" in ev.get("type", "") or "起诉意见书" in ev.get("type", "")
                         or "起诉书" in ev.get("name", "") or "起诉意见书" in ev.get("name", ""))
            ]
            if not missing:
                return
            from evidence_summarizer import summarize_evidence
            from llm_client import get_llm_client
            from config_manager import load_config
            conc = int(load_config().get("evidence_concurrency", 3) or 3)
            logger.info(f"[摘要] {len(missing)} 份证据缺 digest，分析前自动补生成")
            await summarize_evidence(get_llm_client(), self.case_dir, concurrency=conc)
        except Exception as e:
            logger.warning(f"[摘要] 自动补生成失败（分析回退全文）: {e}")

    async def stage_2_character_relations(
        self,
        defendant: str,
        crime_type: Optional[str] = None,
        progress_cb: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """
        阶段 2：从全部证据中提取人物关系
        输出：人物关系图（表格形式）
        人名/角色/关系在 8 栏目摘要中已结构化保留，用 digest 即可（全文分批的 token 消耗大）
        """
        await self._ensure_digests()
        texts = self._load_evidence_texts(prefer_summary=True)
        if progress_cb:
            progress_cb("正在分析人物关系...")

        indictment_catalog, indictment_text, evidence_catalog_text, evidence_only = _split_indictment_and_evidence(texts)

        system_prompt = """梳理案中人物关系。从全部证据中识别涉案人员及相互关系。

**重要区分**：
- 起诉书/起诉意见书是指控文书，引用时写"据起诉书"/"据起诉意见书"
- 正式证据用"见证据XXX"格式引用

输出要求：Markdown格式 + 必须输出关系图JSON（nodes/edges/subgraphs）""" + _NO_CHITCHAT

        prompt_header = f"""## 辩护对象
被告人：**{defendant}**

## 指控文书（非证据）
{indictment_catalog}

## 证据目录
{evidence_catalog_text}

## 全部案卷材料
"""

        prompt_footer = f"""---

## 请完成以下分析

### 一、涉案人员一览表

| 姓名 | 角色 | 与{defendant}的关系 | 涉案程度 | 证据来源 | 备注 |
|------|------|---------------------|----------|----------|------|

角色：被告人/嫌疑人、同案犯、被害人、证人、鉴定人、办案人员、其他。
涉案程度：核心、重要、次要、边缘。

### 二、人物关系图（JSON格式）

```json
{{"nodes":[{{"id":"A","label":"姓名","group":"core"}}],"edges":[{{"from":"A","to":"B","label":"关系"}}],"subgraphs":[{{"name":"组名","nodes":["A","B"]}}]}}
```

### 三、关系详细分析
梳理嫌疑人之间、嫌疑人与被害人、证人与各方的关系，说明性质、来源、对辩护的影响。"""

        batch_results = await _batch_analyze_evidence(
            texts, system_prompt, prompt_header, prompt_footer,
            progress_cb=progress_cb, label="人物关系",
        )

        # 合并多批结果：提取所有批次的 JSON 数据合并后渲染
        all_nodes = []
        all_edges = []
        all_subgraphs = []
        markdown_parts = []

        import re as _re
        for batch_md in batch_results:
            # 兼容 \r\n 和行尾空格
            json_blocks = _re.findall(r'```json[ \t]*\r?\n(.*?)```', batch_md, _re.DOTALL)
            for block in json_blocks:
                try:
                    data = json.loads(block.strip())
                    if not isinstance(data, dict):
                        logger.warning(f"[人物关系] JSON 非对象，跳过: {str(data)[:80]}")
                        continue
                    all_nodes.extend(data.get("nodes", []) or [])
                    all_edges.extend(data.get("edges", []) or [])
                    all_subgraphs.extend(data.get("subgraphs", []) or [])
                except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as e:
                    logger.warning(f"[人物关系] JSON 解析失败: {e}, block={block[:80]}")
            # 保留非 JSON 的 markdown 内容
            cleaned = _re.sub(r'```json[ \t]*\r?\n.*?```', '', batch_md, flags=_re.DOTALL).strip()
            if cleaned:
                markdown_parts.append(cleaned)

        # 去重节点（按 id 去重，保留第一个出现的；跳过缺 id 的）
        seen_ids = set()
        unique_nodes = []
        for n in all_nodes:
            if not isinstance(n, dict):
                continue
            nid = n.get("id")
            if not nid or nid in seen_ids:
                continue
            seen_ids.add(nid)
            unique_nodes.append(n)

        # 去重边（按 from+to+label 去重；跳过缺 from/to 的）
        seen_edges = set()
        unique_edges = []
        for e in all_edges:
            if not isinstance(e, dict):
                continue
            frm, to = e.get("from"), e.get("to")
            if not frm or not to:
                continue
            key = (frm, to, e.get("label", ""))
            if key not in seen_edges:
                seen_edges.add(key)
                unique_edges.append(e)

        # subgraphs 按 name 去重，合并 nodes
        seen_sg = set()
        unique_subgraphs = []
        for sg in all_subgraphs:
            if not isinstance(sg, dict):
                continue
            name = sg.get("name", "")
            if name in seen_sg:
                for existing in unique_subgraphs:
                    if existing.get("name") == name:
                        existing_nodes = set(existing.get("nodes", []))
                        existing["nodes"] = list(existing_nodes | set(sg.get("nodes", [])))
                        break
            else:
                seen_sg.add(name)
                unique_subgraphs.append(dict(sg))

        if unique_nodes:
            mermaid = _json_to_mermaid_graph({"nodes": unique_nodes, "edges": unique_edges, "subgraphs": unique_subgraphs})
            markdown_parts.insert(0, f"```mermaid\n{mermaid}\n```")

        md_output = "\n\n---\n\n".join(markdown_parts) if markdown_parts else "\n\n---\n\n".join(batch_results)

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
        await self._ensure_digests()
        texts = self._load_evidence_texts(prefer_summary=True)
        if progress_cb:
            progress_cb("正在分析事件时间线和证据归组...")

        from llm_client import get_llm_client
        client = get_llm_client()

        indictment_catalog, indictment_text, evidence_catalog_text, evidence_only = _split_indictment_and_evidence(texts)

        # 时间线需要全局时序，不适合分批，用优先级截断（起诉书完整、口供优先）
        all_text = _truncate_all(texts, max_total=_get_content_budget_chars())

        system_prompt = """梳理案卷中的事件脉络。按时间顺序识别关键事件，将证据归组到对应事件下。

**重要区分**：
- 起诉书/起诉意见书是指控文书，引用时写"据起诉书"/"据起诉意见书"
- 正式证据用"见证据XXX"格式

原则：以事件为单位，同事件挂接多份证据，必须输出时间线JSON。""" + _NO_CHITCHAT

        user_prompt = f"""## 辩护对象
被告人：**{defendant}**

## 指控文书（非证据）
{indictment_catalog}

## 证据目录
{evidence_catalog_text}

## 全部案卷材料
{all_text}

---

## 请完成以下分析

### 一、事件时间线（JSON格式）

```json
{{"title":"案件时间线","events":[{{"date":"2025-12-22","title":"事件简述","evidence":["见证据009"]}}]}}
```

规则：date用原始格式，title不超30字，evidence仅正式证据。

### 二、事件拆解与证据归组

每个事件列出：时间、地点、简述、相关证据（编号+名称+该证据说法1-2句）、初步观察（各证据是否一致、有无矛盾）。

确保不遗漏重要事件，每个事件挂接全部相关证据。"""

        md_output = await client.chat([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])

        # 优先尝试从 JSON 生成 Mermaid 时间线
        md_output = _extract_json_and_render(md_output, _json_to_mermaid_timeline)

        # 如果 LLM 直接输出了 mermaid（未走 JSON 路径），用旧逻辑修复
        if '```mermaid' in md_output:
            md_output = _legacy_fix_mermaid_timeline(md_output)

        # 叙述完整性校验：剥离代码块后文字过少（LLM 只输出了时间线没写事件拆解）→ 补全一次
        import re as _re
        narrative_text = _re.sub(r"```[\s\S]*?```", "", md_output).strip()
        if len(narrative_text) < 500:
            print("[阶段3] 事件拆解叙述缺失，发起补全调用...")
            supplement = await client.chat([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"""## 辩护对象
被告人：**{defendant}**

## 全部案卷材料
{all_text}

---

你的上一次输出只完成了事件时间线，缺少第二部分。请现在**只输出第二部分**：事件拆解与证据归组。

每个事件列出：时间、地点、简述、相关证据（编号+名称+该证据说法1-2句）、初步观察（各证据是否一致、有无矛盾）。
确保不遗漏重要事件，每个事件挂接全部相关证据。直接输出 Markdown 正文，不要重复时间线。"""},
            ])
            supplement_narrative = _re.sub(r"```[\s\S]*?```", "", supplement).strip()
            if len(supplement_narrative) >= 200:
                md_output = md_output.rstrip() + "\n\n" + supplement.strip()
            else:
                print("[阶段3] 补全后仍无有效叙述，保留时间线产物")

        data = {
            "stage": 3,
            "name": "事件拆解",
            "defendant": defendant,
            "generated_at": datetime.now().isoformat(),
        }

        self._save_stage(3, data, md_output)
        return data

    # ========== 阶段 3.5：资金流梳理 ==========

    def _load_fund_source_texts(self) -> List[Dict[str, str]]:
        """资金流抽取的数据源：结构化 fund_flows + 证据摘要 + 原始 MD 全文

        证据提取时 LLM 已把 OCR 流水文字结构化为 fund_flows（谁→谁｜金额｜时间｜
        账号｜用途），这里优先注入，避免资金流分析重复全文重扫 OCR 原始文字；
        md/ 全文兜底（fund_flows 缺失时仍能扫到 OCR 回填的文字）。
        """
        texts = []
        seen = set()
        for t in self._load_evidence_texts():
            texts.append(t)
            seen.add(t.get("filename", ""))
        # 注入结构化 fund_flows（读取 index.json 原文，不经 _load_evidence_texts）
        try:
            index_file = self.case_dir / "evidence" / "index.json"
            if index_file.exists():
                index = json.loads(index_file.read_text(encoding="utf-8"))
                for ev in index.get("evidence", []):
                    flows = ev.get("fund_flows") or []
                    if flows:
                        texts.append({"filename": ev.get("name", ev.get("md_file", "")),
                                      "text": _fund_flows_to_text(ev.get("name", ""), flows)})
        except Exception:
            pass
        md_dir = self.case_dir / "md"
        if md_dir.exists():
            for md_file in sorted(md_dir.glob("*.md")):
                if md_file.name in seen:
                    continue
                try:
                    text = md_file.read_text(encoding="utf-8")
                except Exception:
                    continue
                if text.strip():
                    texts.append({"filename": md_file.name, "text": text})
        return texts

    async def stage_35_fund_flow(
        self,
        defendant: str,
        crime_type: Optional[str] = None,
        progress_cb: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """阶段 3.5：资金流梳理与指控金额对照验证

        从证据中重建资金链条（标注客观/言词来源），与起诉书指控金额逐笔对照。
        无资金类证据时输出说明页；无起诉书时只做重建不对照。
        """
        if progress_cb:
            progress_cb("正在梳理资金流并验证指控金额...")

        from llm_client import get_llm_client
        client = get_llm_client()

        from fund_flow import (
            collect_fund_paragraphs, build_fund_prompt, FUND_SYSTEM_PROMPT,
            aggregate_fund_flows, build_master_table, build_fund_prompt_v2,
            verify_fund_output, extract_amounts,
        )

        # 三层分离：有结构化 fund_flows 时走「确定性主表 + LLM 只分析 + 机器对账」，
        # 无 fund_flows（旧案件）回退「关键词抽段 + LLM 转录」旧路径
        fund_rows = aggregate_fund_flows(self.case_dir)

        if fund_rows:
            master_table = build_master_table(fund_rows)
            indictment_md = _read_stage_md(self.analysis_dir, 1)
            fund_prompt = build_fund_prompt_v2(indictment_md, master_table)
            print(f"[预算] 阶段3.5 资金流 prompt: {len(fund_prompt)} 字符（结构化主表路径）")
            md_output = await client.chat([
                {"role": "system", "content": FUND_SYSTEM_PROMPT},
                {"role": "user", "content": fund_prompt},
            ])
            if not md_output.strip():
                md_output = "资金流梳理分析失败：LLM 返回空内容，请重跑本阶段。"
                print("[阶段3.5] LLM 返回空内容")
            else:
                # 校验层：LLM 引用金额必须可溯源（主表 ∪ 起诉书），否则标记待人工核对
                master_amounts = {r["amount"] for r in fund_rows}
                indictment_amounts = extract_amounts(indictment_md or "")
                issues = verify_fund_output(md_output, master_amounts, indictment_amounts)
                if issues:
                    md_output += "\n\n---\n\n> ⚠️ **金额溯源校验提示**（机器校验）：\n" + "\n".join(
                        f"> - {i}" for i in issues[:20])
                    print(f"[阶段3.5] 金额溯源校验: {len(issues)} 处待核对")

            data = {
                "stage": 35,
                "name": "资金流梳理",
                "defendant": defendant,
                "generated_at": datetime.now().isoformat(),
            }
            self._save_stage(35, data, md_output)
            return data

        texts = self._load_fund_source_texts()
        fund_evidence = collect_fund_paragraphs(
            texts, max_chars=int(_get_content_budget_chars() * 0.6)
        )

        if not fund_evidence.strip():
            md_output = "本案证据中未检测到资金类内容，无需进行资金流梳理。"
            print("[阶段3.5] 无资金类证据，输出说明页")
        else:
            # 指控要素来自阶段 1 产物
            indictment_md = _read_stage_md(self.analysis_dir, 1)
            fund_prompt = build_fund_prompt(indictment_md, fund_evidence)
            print(f"[预算] 阶段3.5 资金流 prompt: {len(fund_prompt)} 字符")
            md_output = await client.chat([
                {"role": "system", "content": FUND_SYSTEM_PROMPT},
                {"role": "user", "content": fund_prompt},
            ])
            # 空结果不落盘为成功产物（与控辩对抗空校验同一原则）
            if not md_output.strip():
                md_output = "资金流梳理分析失败：LLM 返回空内容，请重跑本阶段。"
                print("[阶段3.5] LLM 返回空内容")

        data = {
            "stage": 35,
            "name": "资金流梳理",
            "defendant": defendant,
            "generated_at": datetime.now().isoformat(),
        }
        self._save_stage(35, data, md_output)
        return data

    # ========== 阶段 4：法律法规梳理 ==========

    def _read_case_meta(self) -> Dict[str, Any]:
        """读取案件元数据（case.json），失败返回空 dict"""
        meta_path = self.case_dir / "case.json"
        if meta_path.exists():
            try:
                return json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _case_charges_for_rules(self, crime_type: Optional[str]) -> List[str]:
        """类案检索用罪名列表：case.json charges 优先，回退 crime_type"""
        charges = self._read_case_meta().get("charges")
        if isinstance(charges, list) and charges:
            return [str(c) for c in charges if c]
        return [crime_type] if crime_type else []

    def _case_keywords_for_rules(self) -> Optional[List[str]]:
        """类案检索关键词：用户确认的 search_keywords 优先，回退 LLM 推荐 suggested_keywords"""
        meta = self._read_case_meta()
        for key in ("search_keywords", "suggested_keywords"):
            kw = meta.get(key)
            if isinstance(kw, list) and kw:
                return [str(k) for k in kw if k]
        return None

    async def stage_4_legal_regulations(
        self,
        defendant: str,
        crime_type: Optional[str] = None,
        progress_cb: Optional[Callable] = None,
        reference_cases: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        阶段 4：梳理涉案罪名的法律法规
        输出：刑法法条 + 司法解释 + 类案裁判要旨 + 量刑指导意见
        reference_cases：用户勾选的真实案例卡片（《刑事审判参考》），注入提示词供引用
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
请根据案件涉及的罪名，梳理相关法律法规、司法解释和类案裁判规则。

输出要求：
1. 引用的法条要准确（包含条文号）
2. 司法解释要引用现行有效的
3. 类案只描述裁判规则和要旨，**不要编造案号、法院名称或当事人姓名**
4. 量刑部分要列明基准刑和调节因素""" + _NO_CHITCHAT

        # 无手动勾选案例时，自动从案例库检索真实类案（带案号，供律师援引）
        auto_rules_md = ""
        if not reference_cases:
            try:
                rule_charges = self._case_charges_for_rules(crime_type)
                if rule_charges:
                    from case_framework import fetch_case_rules
                    rules = fetch_case_rules(rule_charges, keywords=self._case_keywords_for_rules())
                    auto_rules_md = "\n\n".join(rules.values())
            except Exception as e:
                print(f"[阶段4] 自动检索类案降级（不影响主流程）: {e}")

        if reference_cases:
            system_prompt += f"""

参考案例（以下来自《刑事审判参考》的真实案例，案号与内容均真实可查）：
{build_reference_block(reference_cases)}

引用要求：引用类案时仅可引用以上提供的案例，格式为「【案号】案例名 + 裁判要旨」；
除上述案例外，仍不得引用或编造任何其他案号、法院名称或当事人姓名。"""
        elif auto_rules_md:
            system_prompt += f"""

参考案例（以下来自案例库自动检索的真实案例，案号与内容均真实可查）：
{auto_rules_md}

引用要求：引用类案时仅可引用以上提供的案例，格式为「【案号】案例名 + 裁判要旨」；
除上述案例外，仍不得引用或编造任何其他案号、法院名称或当事人姓名。"""

        # 先从阶段 1 结果中获取指控罪名
        stage1_md = ""
        stage1_file = self.analysis_dir / "stage_1" / "output.md"
        if stage1_file.exists():
            stage1_md = stage1_file.read_text(encoding="utf-8")

        crime_specific_section = ""
        if crime_specific:
            crime_specific_section = f"## 罪名特定知识\n{crime_specific}"

        # 类案裁判规则小节：有真实参考案例（手动勾选或自动检索）时要求引用之，否则严禁虚构
        if reference_cases or auto_rules_md:
            case_rules_section = """### 三、类案裁判规则
- 引用系统提示中提供的真实参考案例，格式为「【案号】案例名 + 裁判要旨」
- 除提供的案例外，严禁虚构任何案号、法院名称、裁判日期或当事人姓名
- 说明与本案的关联"""
        else:
            case_rules_section = """### 三、类案裁判规则
- **严禁虚构案例**：不得编造任何案号、法院名称、裁判日期或当事人姓名
- 仅描述相关裁判规则和法律要旨，不引用具体案例
- 如果引用指导性案例，必须是确信真实存在的（如最高人民法院正式发布的指导性案例）
- 说明与本案的关联"""

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

{case_rules_section}

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

        self._save_stage(4, data, md_output, charge=crime_type)
        return data

    # ========== 5B：矛盾分析 + 口供对比（阶段 5 与独立子阶段 52 共用）==========

    async def stage_5b_contradiction_analysis(
        self,
        defendant: str,
        progress_cb: Optional[Callable] = None,
    ) -> str:
        """矛盾分析：口供纵向对比 + 横向矛盾 + 证据链薄弱，返回完整 Markdown 并落盘 stage_52

        分步精简原则：输入为提取的证据摘要（evidence/），不回读案卷原文；
        拆成 3 次单任务聚焦调用——单次调用让模型一口气输出多板块时，
        长输入下模型容易只交第一块就收尾（2026-08-06 生产环境实测翻车）
        """
        await self._ensure_digests()
        texts = self._load_evidence_texts(prefer_summary=True)
        indictment_catalog, _, evidence_catalog_text, _ = _split_indictment_and_evidence(texts)

        from llm_client import get_llm_client
        client = get_llm_client()
        budget = _get_content_budget_chars()

        # 第 1 步：口供稳定性（只喂供述类证据摘要，输入精准）
        if progress_cb:
            progress_cb("正在进行口供稳定性对比...")
        confession_texts = _select_confession_texts(texts)
        if confession_texts:
            confession_blob = _truncate_all(confession_texts, max_total=budget)
            stability_md = await client.chat([
                {"role": "system", "content": "你是刑事辩护律师，本次只做口供纵向对比，不做其他分析。\n\n" + _NO_CHITCHAT + "\n\n引用格式：正式证据用'见证据XXX'。"},
                {"role": "user", "content": f"""## 辩护对象
被告人：**{defendant}**

## 供述类证据摘要（讯问笔录）
{confession_blob}

---

## 任务（仅此一项）：口供稳定性分析
对同一人的多次讯问笔录做纵向对比，输出表格：

| 时间 | 关键陈述 | 变化 | 可能原因 |
|------|---------|------|---------|

重点：首次供述与后续供述的差异、翻供与回避、趋利避害的角色变化。同案犯供述之间的推诿也要列出。"""},
            ])
        else:
            stability_md = "（本案无供述类证据）"

        # 第 2 步：横向矛盾（全部证据摘要在 1M 预算内可完整装入，单任务聚焦）
        if progress_cb:
            progress_cb("正在进行横向矛盾对比...")
        all_text = _truncate_all(texts, max_total=budget)
        horizontal_md = await client.chat([
            {"role": "system", "content": "你是刑事辩护律师，本次只做证据间横向矛盾比对，不做其他分析。\n\n" + _NO_CHITCHAT + "\n\n重要：起诉书/起诉意见书引用时写'据起诉书'/'据起诉意见书'，正式证据用'见证据XXX'格式。"},
            {"role": "user", "content": f"""## 辩护对象
被告人：**{defendant}**

## 指控文书（非证据）
{indictment_catalog}

## 全部证据摘要（提取精简版）
{all_text}

---

## 任务（仅此一项）：横向矛盾分析
对不同证据就同一事实的记载逐一比对，输出表格：

| 比对维度 | 被告人供述 | 证人证言 | 书证/物证 | 是否矛盾 |
|----------|-----------|---------|----------|----------|

矛盾类型：直接矛盾、间接矛盾、隐性矛盾。
硬性要求：
1. 必须覆盖 供述 vs 证言、证言 vs 书证、同案犯供述之间 三个方向，不得只做口供内部对比
2. 按犯罪事实逐笔比对（每笔交易/每个事件一行起）
3. 无矛盾的方向也要说明"已比对，无矛盾"和比对依据"""},
        ])

        # 第 3 步：证据链薄弱（基于前两步结论综合，再次精简后的产物）
        if progress_cb:
            progress_cb("正在梳理证据链薄弱环节...")
        chain_md = await client.chat([
            {"role": "system", "content": "你是刑事辩护律师，本次只做证据链薄弱环节评估。\n\n" + _NO_CHITCHAT},
            {"role": "user", "content": f"""## 辩护对象
被告人：**{defendant}**

## 证据目录
{evidence_catalog_text}

## 口供对比结论
{stability_md}

## 横向矛盾结论
{horizontal_md}

---

## 任务（仅此一项）：证据链条薄弱环节
基于以上结论，指出：
- 仅靠言词证据支撑、无客观证据印证的环节
- 证据链断裂处（有指控无证据、或证据无法到达指控事实）
- 孤证不能定案的位置
每处注明涉及的证据编号。"""},
        ])

        contradiction_md = (
            f"# 一、口供稳定性分析（同一人多次笔录对比）\n\n{stability_md}\n\n"
            f"# 二、横向矛盾分析\n\n{horizontal_md}\n\n"
            f"# 三、证据链条薄弱环节\n\n{chain_md}"
        )

        self._save_stage(52, {"name": "矛盾分析"}, contradiction_md)
        return contradiction_md

    # ========== 阶段 5：证据分析 + 矛盾分析 + 口供对比 + 三阶层辩护 ==========

    async def _run_5b_if_needed(self, defendant: str, progress_cb=None) -> str:
        """5B 矛盾分析：共享层产物已存在且非空则复用（多罪名不重复跑）"""
        existing = _read_stage_md(self.analysis_dir, 52)
        if existing.strip():
            logger.info("[阶段5B] 矛盾分析产物已存在，跳过重跑（多罪名共享层复用）")
            return existing
        return await self.stage_5b_contradiction_analysis(defendant, progress_cb=progress_cb)

    def _read_stage4_for_charge(self, charge: str | None) -> str:
        """读取阶段4法规产物：多罪名读罪名层 analysis/{charge}/stage_4/，单罪名读共享层"""
        if charge:
            charge_file = self.analysis_dir / charge / "stage_4" / "output.md"
            if charge_file.exists():
                return charge_file.read_text(encoding="utf-8")
        return _read_stage_md(self.analysis_dir, 4)

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

        from llm_client import get_llm_client
        client = get_llm_client()

        # 获取之前阶段的结果
        stage1_md = _read_stage_md(self.analysis_dir, 1)
        stage2_md = _read_stage_md(self.analysis_dir, 2)
        stage3_md = _read_stage_md(self.analysis_dir, 3)
        stage4_md = self._read_stage4_for_charge(crime_type)
        stage35_md = _read_stage_md(self.analysis_dir, 35)

        # 辩护思路（4.75 律师确认稿，存在则注入 prompt 最前面，最高优先级）
        strategy_file = self.analysis_dir / "04.75-辩护思路" / "思路确认.md"
        strategy_prefix = ""
        if strategy_file.exists():
            strategy_prefix = (
                "辩护思路（律师已确认，必须遵循；律师补充的思路优先级最高，与系统建议冲突时以律师为准）：\n"
                + strategy_file.read_text(encoding="utf-8")[:_get_content_budget_chars()]
                + "\n\n"
            )

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

        # ----- 5B：矛盾分析 + 口供对比（共享层，多罪名已存在则复用不重跑）-----
        contradiction_md = await self._run_5b_if_needed(defendant, progress_cb=progress_cb)

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

## 阶段 3.5：资金流梳理

{stage35_md if stage35_md.strip() else "（未生成或无资金类证据）"}

## 阶段 4：法律法规

{stage4_md}

## 阶段 5A：证据目录

{evidence_list_md}

## 阶段 5B：矛盾分析

{contradiction_md[:_get_knowledge_budget_chars()]}

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
7. 对指控的每一项事实，都要指出是否有独立证据支撑、证据是否充分（起诉书的指控不等于有证据支撑）""" + _NO_CHITCHAT},
            {"role": "user", "content": strategy_prefix + defense_prompt},
        ])

        self._save_stage(53, {"name": "三阶层辩护"}, defense_md, charge=crime_type)

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

        self._save_stage(5, data, full_report, charge=crime_type)
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

    def _build_review_messages(self, evidence: Dict[str, Any], template: str) -> list:
        """构建差异化的证据审查消息（缓存友好结构）

        system 放固定审查规则（角色 + JSON schema + 评分标准，所有证据共享前缀），
        user 前段放审查模板/法律依据/证据内容（同类型证据间可共享模板前缀），
        user 末尾放本次任务指令。逐份证据调用（N 次）命中 DeepSeek prompt cache。
        """
        ev_name = evidence.get("filename", "未知证据")
        ev_type = evidence.get("type", "其他证据")
        ev_ref = evidence.get("evidence_ref", "")
        ev_text = evidence.get("text", "")[:4000]  # 截断长文本

        system = """你是一名资深刑事辩护律师，精通证据法和庭审质证技巧。你的任务是严格审查证据的三性（合法性、真实性、关联性），并生成可直接用于庭审的质证意见。审查要具体、有针对性，法律依据要准确。

# 输出要求
请严格按照以下 JSON 格式输出审查结果，每个维度都要有具体的审查发现和法律依据：
注：evidence_name/evidence_ref/evidence_type 三字段按示例原文输出，由系统回填真实值

{
  "evidence_name": "证据名称",
  "evidence_ref": "证据编号",
  "evidence_type": "证据类型",
  "legality": {
    "conclusion": "采信/不采信/存疑",
    "score": 0-100,
    "findings": [
      {
        "issue": "发现的具体问题",
        "legal_basis": "对应法条（如：刑诉法第117条）",
        "details": "问题详细说明"
      }
    ],
    "cross_opinion": "可当庭陈述的质证意见（一句话）",
    "strategy": ["质证策略1", "质证策略2"]
  },
  "authenticity": {
    "conclusion": "采信/不采信/存疑",
    "score": 0-100,
    "findings": [
      {
        "issue": "发现的具体问题",
        "legal_basis": "对应法条",
        "details": "问题详细说明"
      }
    ],
    "cross_opinion": "可当庭陈述的质证意见",
    "strategy": ["质证策略1"]
  },
  "relevance": {
    "conclusion": "采信/不采信/存疑",
    "score": 0-100,
    "findings": [
      {
        "issue": "发现的具体问题",
        "legal_basis": "对应法条",
        "details": "问题详细说明"
      }
    ],
    "cross_opinion": "可当庭陈述的质证意见",
    "strategy": ["质证策略1"]
  },
  "final_conclusion": "综合结论：采信/不采信/存疑",
  "cross_examination_summary": "综合质证意见（可当庭陈述，200字以内）"
}

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

只输出 JSON，不要其他内容。""" + _NO_CHITCHAT

        material = f"""# 审查模板（请按此模板逐项审查）
{template}

# 法律依据参考
{LEGAL_BASIS_FOR_REVIEW[:3000]}

# 证据内容
{ev_text}"""

        ref_seg = f"，编号{ev_ref}" if ev_ref else ""
        instruction = f"请对上述证据（{ev_name}{ref_seg}，{ev_type}）进行三性审查并输出质证意见（JSON）"
        return build_cached_messages(system, material, instruction)

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
                result["evidence_type"] = evidence.get("type", "其他证据")
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

        # 过滤掉起诉书/起诉意见书 + 非证据（封面/目录等），只审查正式证据
        evidence_texts = [t for t in texts if not t.get("is_indictment") and not _is_non_evidence(t)]

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

            try:
                # 缓存友好消息：固定规则入 system，证据内容在 user 前段
                response = await llm.chat(self._build_review_messages(ev, template))

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

def generate_evidence_chain(case_path: Path, charge: Optional[str] = None) -> Dict[str, Any]:
    """生成证据链可视化数据

    结构：
    - 顶层：指控事实（从起诉书提取）
    - 中层：待证事实（构成要件事实：主体、主观、行为、结果、情节）
    - 底层：证据（按类型分组）
    - 边：证明关系、印证关系、矛盾关系

    核心逻辑：证据用于证明事实

    Args:
        case_path: 案件路径
        charge: 可选，按罪名筛选证据（仅返回关联该罪名的证据）
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

    # 按罪名过滤证据（多罪名案件支持）
    if charge:
        evidence_list = [
            ev for ev in evidence_list
            if isinstance(ev, dict) and charge in ev.get("charges", [])
        ]
        if not evidence_list:
            return {"nodes": [], "edges": [], "groups": [], "total_evidence": 0, "total_relations": 0, "error": f"罪名「{charge}」下无关联证据"}

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
        proves_details = {}   # 记录每个事实的相关内容片段
        full_text = (ev_name + " " + ev_summary + " " + ev_content[:3000]).lower()

        # 如果 index.json 中没有 content，尝试读取 MD 文件
        if not ev_content and ev.get("md_file"):
            md_path = evidence_dir / ev["md_file"]
            if md_path.exists():
                try:
                    full_text = (ev_name + " " + ev_summary + " " + md_path.read_text(encoding="utf-8")[:5000]).lower()
                except Exception:
                    pass

        # ========== 证据与待证事实的关联逻辑 =============
        # 三层防丢：LLM分析 → 规则推断 → 未关联保底

        # ── 第1层：LLM 分析结果优先（来自证据提取阶段的 proves_facts）──
        llm_proves = ev.get("proves_facts", [])
        llm_details = ev.get("proves_details", {})

        if llm_proves:
            # LLM 已分析：高置信度关联
            for fact_id in llm_proves:
                proves_facts.append(fact_id)
                proves_strength[fact_id] = "high"
                detail_text = llm_details.get(fact_id, "LLM 分析确认")
                proves_details[fact_id] = [detail_text]

        # ── 第2层：规则推断兜底（仅当 LLM 无分析结果时）──
        if not proves_facts:
            type_fact_mapping = {
                "indictment": ["fact_subject", "fact_subjective", "fact_behavior", "fact_result"],
                "confession": ["fact_subject", "fact_subjective", "fact_behavior"],
                "witness": ["fact_behavior", "fact_result"],
                "victim": ["fact_behavior", "fact_result"],
                "documentary": ["fact_result"],
                "physical": ["fact_behavior"],
                "expert": ["fact_result"],
                "inspection": ["fact_behavior"],
                "electronic": ["fact_result", "fact_behavior"],
                "audiovisual": ["fact_behavior"],
                "procedural": [],
                "other": [],
            }

            name_exact_mapping = {
                "户籍": ["fact_subject"], "身份证": ["fact_subject"],
                "出生": ["fact_subject"], "年龄": ["fact_subject"],
                "自首": ["fact_circumstance"], "坦白": ["fact_circumstance"],
                "立功": ["fact_circumstance"], "退赃": ["fact_circumstance"],
                "赔偿": ["fact_circumstance"], "抓获": ["fact_subject"],
                "到案": ["fact_circumstance"],
            }

            inferred_facts = type_fact_mapping.get(cat, [])
            for fact in facts_to_prove:
                if fact["id"] in inferred_facts:
                    proves_facts.append(fact["id"])
                    proves_strength[fact["id"]] = "low"
                    proves_details[fact["id"]] = [f"依据证据类型推断"]

            # 3. 用证据名称关键词补充（精确匹配）
            ev_name_lower = ev_name.lower()
            for kw, target_facts in name_exact_mapping.items():
                if kw in ev_name_lower:
                    for tf in target_facts:
                        if tf not in proves_facts:
                            proves_facts.append(tf)
                            proves_strength[tf] = "medium"
                            proves_details[tf] = [f"证据名称包含「{kw}」"]

            # 4. 精确关键词补充（仅当类型推断为空时使用）
            # 只有证据名称明确包含特定词汇才触发
            if not proves_facts:
                strict_keyword_mapping = {
                    "fact_subject": ["户籍证明", "身份证", "出生证", "抓获经过", "到案经过", "在逃", "网上追逃"],
                    "fact_subjective": ["故意", "明知", "目的", "动机", "非法占有", "营利目的"],
                    "fact_circumstance": ["自首", "坦白", "认罪", "立功", "退赃", "赔偿", "从轻", "从重", "累犯"],
                }

                for fact in facts_to_prove:
                    fact_id = fact["id"]
                    strict_kws = strict_keyword_mapping.get(fact_id, [])
                    if any(kw in ev_name_lower for kw in strict_kws):
                        proves_facts.append(fact_id)
                        proves_strength[fact_id] = "high"
                        proves_details[fact_id] = [f"证据名称精确匹配"]

        evidence_item = {
            "id": ev_id,
            "name": ev_name,
            "type": ev_type[:20] if ev_type else "其他",
            "category": cat,
            "color": evidence_types[cat]["color"],
            "proves": proves_facts,
            "proves_strength": proves_strength,
            "proves_details": proves_details,  # 新增：针对每个事实的相关内容
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
        if charge:
            accusation_name = f"指控：{charge}"
            accusation_desc = f"以{charge}定罪的事实依据"
        else:
            accusation_name = accusation.get("name", "指控事实")
            accusation_desc = accusation.get("description", "")[:100]
        nodes.append({
            "id": "accusation",
            "name": accusation_name,
            "description": accusation_desc,
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


def _select_confession_texts(texts: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """筛选供述类证据（讯问笔录/供述辩解），供口供纵向对比专用

    匹配依据：类型含"供述"（如"犯罪嫌疑人供述和辩解"）或名称含"讯问"
    """
    return [
        t for t in texts
        if "供述" in t.get("type", "") or "讯问" in t.get("filename", "")
    ]


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


def _fund_flows_to_text(ev_name: str, flows: list) -> str:
    """把结构化 fund_flows 组装成资金流分析可消费的文本

    证据提取时 LLM 把 OCR 流水文字结构化为 fund_flows（转出人→转入人｜金额｜
    时间｜账号｜用途），资金流分析直接消费，避免全文重扫 OCR 原始文字。
    """
    if not flows:
        return ""
    lines = "\n".join(f"- {f}" for f in flows)
    return f"## 资金往来（{ev_name}）\n{lines}\n"


def _apply_digest(ev: dict, text: str, prefer_summary: bool) -> str:
    """单发分析阶段（时间线/矛盾分析）用浓缩摘要替代全文；无摘要回退全文"""
    if prefer_summary and ev.get("digest"):
        return f"# {ev.get('name', '')}\n\n{ev['digest']}"
    return text


def _is_non_evidence(t: dict) -> bool:
    """非证据（封面/目录等程序性文件）判定：质证等逐份审查跳过"""
    return str(t.get("doc_type", "")).startswith("non_evidence")


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
    """按优先级分层截断：起诉书完整保留，口供优先，其余用剩余预算"""
    total = sum(len(t["text"]) for t in texts)
    if total <= max_total:
        return "\n\n".join([
            f"### {t['filename']}（{t['type']}）\n{t['text']}"
            for t in texts
        ])

    # 分三层：起诉书 > 口供/证言 > 其余
    indictment_types = {"起诉书", "起诉意见书"}
    key_types = {"犯罪嫌疑人供述和辩解", "证人证言", "被害人陈述"}

    indictment = [t for t in texts if t.get("type", "") in indictment_types]
    key_evidence = [t for t in texts if t.get("type", "") in key_types and t not in indictment]
    other = [t for t in texts if t not in indictment and t not in key_evidence]

    parts = []
    used = 0

    # 第一层：起诉书完整保留
    for t in indictment:
        text = f"### {t['filename']}（{t['type']}）\n{t['text']}"
        parts.append(text)
        used += len(t["text"])

    # 第二层：口供/证言优先保留（最多占剩余预算的60%）
    remaining = max_total - used
    key_budget = int(remaining * 0.6)
    key_total = sum(len(t["text"]) for t in key_evidence)
    if key_total <= key_budget:
        for t in key_evidence:
            text = f"### {t['filename']}（{t['type']}）\n{t['text']}"
            parts.append(text)
            used += len(t["text"])
    else:
        ratio = key_budget / key_total
        for t in key_evidence:
            truncated = t["text"][:int(len(t["text"]) * ratio)]
            text = f"### {t['filename']}（{t['type']}）\n{truncated}"
            parts.append(text)
            used += int(len(t["text"]) * ratio)

    # 第三层：其余证据用剩余预算按比例分配
    remaining = max_total - used
    other_total = sum(len(t["text"]) for t in other)
    if other_total > 0 and remaining > 0:
        ratio = min(1.0, remaining / other_total)
        for t in other:
            truncated = t["text"][:int(len(t["text"]) * ratio)]
            text = f"### {t['filename']}（{t['type']}）\n{truncated}"
            parts.append(text)

    logger.info(f"[截断] 总{total:,}字符 → 预算{max_total:,}: 起诉书{len(indictment)}份完整, "
                f"口供{len(key_evidence)}份, 其余{len(other)}份按比例")
    return "\n\n".join(parts)


def _read_stage_md(analysis_dir: Path, stage: int) -> str:
    """读取指定阶段的 Markdown 输出"""
    stage_file = analysis_dir / f"stage_{stage}" / "output.md"
    if stage_file.exists():
        return stage_file.read_text(encoding="utf-8")
    return ""
