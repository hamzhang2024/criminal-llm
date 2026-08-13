"""
案卷分析 5 步流水线（重构版）

以"单次笔录"为基本单位：
1. 合并笔录：按人名+类型合并，分隔出单次笔录
2. 逐次详细总结：每次笔录单独 LLM 总结
3. 内部矛盾分析：多次笔录者对比差异
4. 起诉意见书分析 + 证据事实映射（并发）
5. 辩护意见生成
"""
import json
import re
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

from llm_client import get_llm_client
import context_budget

# 默认分析状态结构
DEFAULT_STATE = {
    "version": 1,
    "updated_at": "",
    "steps": {
        "1": {"status": "idle", "completed_at": None},
        "2": {"status": "idle", "completed_at": None, "details": {}},
        "3": {"status": "idle", "completed_at": None, "details": {}},
        "4": {
            "status": "idle",
            "completed_at": None,
            "sub_steps": {
                "4a": "idle",
                "4b": {},
                "4c": "idle",
                "4d": "idle",
            },
        },
        "4.5": {
            "status": "idle",
            "completed_at": None,
            "sub_steps": {
                "45a": "idle",
                "45b": "idle",
                "45c": "idle",
                "45d": "idle",
                "45e": "idle",
            },
        },
        "4.75": {"status": "idle", "completed_at": None},
        "5": {
            "status": "idle",
            "completed_at": None,
            "sub_steps": {
                "5a": "idle",
                "5b": "idle",
                "5c": "idle",
                "5d": "idle",
                "5e": "idle",
                "5f": "idle",
            },
        },
    },
}

# 笔录时间分隔正则（支持两种格式：至15时32分 和 至2025年11月25日15时32分）
SESSION_TIME_PATTERN = re.compile(r'时间(\d{4}年\d{2}月\d{2}日\d{2}时\d{2}分.*?至(?:\d{4}年\d{2}月\d{2}日)?\d{2}时\d{2}分)')

# 笔录正文中提取人名的正则（被讯问人/被询问人后的姓名）
CONTENT_NAME_PATTERNS = [
    # 无冒号紧凑格式：被询问人项少甫性别 / 被讯问人张三年龄 / 被询问/讯问人李四出生日期
    re.compile(r'(?:被讯问|被询问)[/／]?(?:讯问|询问)?人\s*([\u4e00-\u9fff]{2,4})(?=性别|年龄|出生日期|出生[年月])'),
    # 冒号格式：被讯问人：XXX / 被询问人：XXX
    re.compile(r'(?:被讯问|被询问)[/／]?(?:讯问|询问)?人\s*[：:]\s*([\u4e00-\u9fff]{2,4})'),
    # 犯罪嫌疑人/被告人
    re.compile(r'(?:犯罪嫌疑人|被告人)\s*[：:]\s*([\u4e00-\u9fff]{2,4})'),
    re.compile(r'(?:犯罪嫌疑人|被告人)\s*([\u4e00-\u9fff]{2,4})(?=性别|年龄|出生日期|出生[年月])'),
    # 姓名标签
    re.compile(r'姓\s*名\s*[：:]\s*([\u4e00-\u9fff]{2,4})'),
    # 我叫 XXX（对话中提到）
    re.compile(r'(?:我叫|本人叫|名字是)\s*([\u4e00-\u9fff]{2,4})'),
]


def _extract_name_from_content(text: str, max_len: int = 5000) -> Optional[str]:
    """从笔录正文提取人名（匹配被讯问人/被询问人后的姓名）"""
    preview = text[:max_len]
    for pat in CONTENT_NAME_PATTERNS:
        m = pat.search(preview)
        if m:
            name = m.group(1).strip()
            # 过滤明显不是人名的词
            if name not in ('不知道', '不清楚', '没有', '以上', '以下', '是什么'):
                return name
    return None


def _extract_person_from_filename(filename: str) -> Optional[str]:
    """从文件名提取人名
    文件名格式示例：
    - 第2卷_处理_01_江涛讯问笔录.md  （_序号_人名+类型）
    - 张萍询问笔录.md                （人名直接+类型）
    - 王烁宇_询问笔录.md             （人名_类型）
    策略：找到"询问/讯问/辨认"后，取紧邻的 2-4 字中文作为人名
    """
    stem = Path(filename).stem if '.' in filename else filename
    for kw in ['讯问', '询问', '辨认']:
        idx = stem.find(kw)
        if idx == -1:
            continue
        prefix = stem[:idx]

        # 格式 1：_序号_人名（如 _01_江涛）
        regex_match = re.search(r'_(\d+)_([\u4e00-\u9fff]{2,4})$', prefix)
        if regex_match:
            return regex_match.group(2)

        # 格式 2：人名直接紧跟关键词（如 张萍询问 → 张萍）
        name_match = re.search(r'([\u4e00-\u9fff]{2,4})$', prefix)
        if name_match:
            return name_match.group(1)

        # 格式 3：人名_（如 张某_讯问笔录 → 张某）
        name_match2 = re.search(r'([\u4e00-\u9fff]{2,4})_+$', prefix)
        if name_match2:
            return name_match2.group(1)
    return None


def infer_evidence_type(filename: str) -> str:
    """从文件名推断证据类型"""
    if "起诉书" in filename and "意见" not in filename:
        return "起诉书"
    elif "起诉意见书" in filename or "指控" in filename:
        return "起诉意见书"
    elif "讯问" in filename or "供述" in filename:
        return "讯问笔录"
    elif "询问" in filename:
        return "询问笔录"
    elif "证言" in filename or "证人" in filename:
        return "证人证言"
    elif "鉴定" in filename:
        return "鉴定意见"
    elif "勘验" in filename or "检查" in filename:
        return "勘验笔录"
    elif "辨认" in filename:
        return "辨认笔录"
    elif "银行" in filename or "流水" in filename or "转账" in filename:
        return "书证-金融"
    elif "合同" in filename or "协议" in filename:
        return "书证-合同"
    elif "身份" in filename or "户籍" in filename:
        return "书证-身份"
    elif "拘留" in filename or "逮捕" in filename or "取保" in filename:
        return "程序性文书"
    else:
        return "其他证据"


async def _classify_document_type(llm, text: str) -> str:
    """用 LLM 判断一段文书文本是「起诉书」还是「起诉意见书」还是「其他」"""
    try:
        result = await llm.chat([
            {"role": "system", "content": "你是刑事律师助手。请判断以下文书的类型。只回答：起诉书 / 起诉意见书 / 其他。不要解释。"},
            {"role": "user", "content": f"请判断以下文书的类型（只回答：起诉书 / 起诉意见书 / 其他）：\n\n{text[:3000]}"},
        ])
        result = result.strip()
        if "起诉意见书" in result:
            return "起诉意见书"
        if "起诉书" in result:
            return "起诉书"
    except Exception:
        pass
    return "其他"


def _contains_indictment_title(text: str) -> bool:
    """判断文本是否包含起诉书标题，排除「起诉意见书」的干扰。

    "起诉意见书"中包含"起诉书"三个字，直接用 `in` 匹配会误判。
    本函数使用负向前后查找，确保匹配的是独立的"起诉书"而非"起诉意见书"的一部分。
    """
    # 匹配独立的"起诉书"（前面不是"意见"，后面不是"意见"）
    pattern = r"(?<!意见)起诉书(?!意见)"
    if re.search(pattern, text):
        return True
    if "公诉书" in text:
        return True
    return False


def _extract_fulltext_section(text: str) -> str:
    """提取件中的原文全文段（B'）；无则返回空"""
    marker = "## 原文全文"
    idx = text.find(marker)
    if idx < 0:
        return ""
    rest = text[idx + len(marker):]
    next_h = rest.find("\n## ")
    return rest[:next_h].strip() if next_h > 0 else rest.strip()


def _split_sessions(text: str) -> list[dict]:
    """
    按时间分隔出单次笔录
    Returns: list of {time_range: str, content: str}
    """
    matches = list(SESSION_TIME_PATTERN.finditer(text))
    if not matches:
        return [{"time_range": "未知", "content": text}]

    sessions = []
    for i, m in enumerate(matches):
        time_range = m.group(1)
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        sessions.append({
            "time_range": time_range,
            "content": content,
        })
    return sessions


class AnalysisPipeline:
    """案卷分析 5 步流水线（重构版）"""

    MAX_CONCURRENCY = 5

    def __init__(self, case_id: str, case_dir: Path, indictment_file: Optional[str] = None):
        self.case_id = case_id
        self.case_dir = Path(case_dir) if isinstance(case_dir, str) else case_dir
        self.analysis_dir = self.case_dir / "analysis"
        self.analysis_dir.mkdir(exist_ok=True)
        self.llm = get_llm_client()
        # 用户手动指定的起诉书文件名（优先级高于自动检测）
        self.selected_indictment_file = indictment_file

    # ========== 工具方法 ==========

    def _load_md_files(self) -> list[dict]:
        """读取证据文件，优先从 evidence/ 目录，回退到 md/ 目录"""
        files = []

        # 优先从 evidence/ 目录加载
        evidence_dir = self.case_dir / "evidence"
        if evidence_dir.exists():
            index_file = evidence_dir / "index.json"
            if index_file.exists():
                try:
                    import json
                    index = json.loads(index_file.read_text(encoding="utf-8"))
                    for ev in index.get("evidence", []):
                        # 跳过条目级标注的非证据（封面/目录等，旧案件无 doc_type 字段不受影响）
                        if (ev.get("doc_type") or "evidence").startswith("non_evidence"):
                            continue
                        md_file = evidence_dir / ev.get("md_file", "")
                        if md_file.exists():
                            text = md_file.read_text(encoding="utf-8")
                            if text.strip():
                                files.append({
                                    "filename": ev.get("name", md_file.name),
                                    "filepath": str(md_file),
                                    "text": text,
                                    "type": ev.get("type", infer_evidence_type(md_file.name)),
                                })
                except Exception:
                    pass

            # 如果 index.json 不存在或为空，直接扫描 .md 文件
            if not files:
                for f in sorted(evidence_dir.glob("*.md"), key=lambda x: x.name):
                    try:
                        text = f.read_text(encoding="utf-8")
                        if text.strip():
                            files.append({
                                "filename": f.name,
                                "filepath": str(f),
                                "text": text,
                                "type": infer_evidence_type(f.name),
                            })
                    except Exception:
                        pass

        # 回退到 md/ 目录
        if not files:
            md_dir = self.case_dir / "md"
            if md_dir.exists():
                for f in sorted(md_dir.iterdir(), key=lambda x: x.name):
                    if f.suffix.lower() == ".md":
                        try:
                            files.append({
                                "filename": f.name,
                                "filepath": str(f),
                                "text": f.read_text(encoding="utf-8"),
                                "type": infer_evidence_type(f.name),
                            })
                        except Exception:
                            pass

        if not files:
            raise ValueError("案件中无证据文件，请先完成证据提取")

        return files

    def _collect_fund_evidence(self, max_chars: int) -> str:
        """双源抽取资金相关段落，控制 token 预算

        证据摘要（evidence/）可能漏掉流水细节，必须同时扫转换后的原始 MD
        全文（md/）——截图经 OCR 识别后的文字也在其中。按文件名去重。
        """
        from fund_flow import collect_fund_paragraphs

        texts = []
        seen = set()
        try:
            for f in self._load_md_files():
                texts.append(f)
                seen.add(f["filename"])
        except ValueError:
            pass

        # 补充原始 md/ 全文（证据提取可能遗漏资金内容）
        md_dir = self.case_dir / "md"
        if md_dir.exists():
            for f in sorted(md_dir.glob("*.md"), key=lambda x: x.name):
                if f.name in seen:
                    continue
                try:
                    text = f.read_text(encoding="utf-8")
                except Exception:
                    continue
                if text.strip():
                    texts.append({"filename": f.name, "text": text})

        return collect_fund_paragraphs(texts, max_chars)

    async def _find_indictment_in_md_files(self) -> tuple[str, str]:
        """在所有 MD 文件中找到起诉书或起诉意见书。
        返回 (文书内容, 文书类型)，都没找到返回 ("", "")。
        如果用户手动指定了起诉书文件，优先使用该文件。
        """
        # 如果用户手动指定了起诉书文件，直接使用
        if self.selected_indictment_file:
            md_files = self._load_md_files()
            for f in md_files:
                if f["filename"] == self.selected_indictment_file:
                    # 用 LLM 确认类型
                    doc_type = await _classify_document_type(self.llm, f["text"][:5000])
                    if doc_type in ("起诉书", "起诉意见书"):
                        fulltext = _extract_fulltext_section(f["text"])
                        return (fulltext or f["text"])[:context_budget.content_budget_chars()], doc_type
                    # LLM 无法确认类型时，回退到自动检测

        md_files = self._load_md_files()

        # 起诉意见书常见标题
        OPINION_PATTERNS = ["起诉意见书", "呈请起诉", "起诉报告"]

        indictment_candidates = []
        opinion_candidates = []

        for f in md_files:
            head = f["text"][:3000]
            if _contains_indictment_title(head):
                indictment_candidates.append(f)
            if any(p in head for p in OPINION_PATTERNS):
                opinion_candidates.append(f)

        # 优先确认起诉书
        for f in indictment_candidates:
            doc_type = await _classify_document_type(self.llm, f["text"][:5000])
            if doc_type == "起诉书":
                fulltext = _extract_fulltext_section(f["text"])
                return (fulltext or f["text"])[:context_budget.content_budget_chars()], "起诉书"

        # 再确认起诉意见书
        for f in opinion_candidates:
            doc_type = await _classify_document_type(self.llm, f["text"][:5000])
            if doc_type == "起诉意见书":
                fulltext = _extract_fulltext_section(f["text"])
                return (fulltext or f["text"])[:context_budget.content_budget_chars()], "起诉意见书"

        return "", ""

    def _save_step_result(self, step: int, data: dict):
        """保存步骤结果到 JSON"""
        path = self.analysis_dir / f"step_{step}_result.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load_step_result(self, step: int) -> dict | None:
        """读取前序步骤结果"""
        path = self.analysis_dir / f"step_{step}_result.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    # ========== 分析状态管理（断点续传） ==========

    def _state_path(self) -> Path:
        return self.analysis_dir / "analysis_state.json"

    def _load_analysis_state(self) -> dict:
        """加载分析状态，不存在则从磁盘证据重建"""
        path = self._state_path()
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return self._reconstruct_state_from_disk()

    def _reconstruct_state_from_disk(self) -> dict:
        """从磁盘已有的分析结果重建状态（用于旧案件无 analysis_state.json 的情况）"""
        state = json.loads(json.dumps(DEFAULT_STATE))
        now = datetime.now().isoformat()

        # 优先检查 pipeline 格式（step_X_result.json）
        for step_num in range(1, 6):
            result_file = self.analysis_dir / f"step_{step_num}_result.json"
            if result_file.exists():
                step_key = str(step_num)
                state["steps"][step_key]["status"] = "completed"
                state["steps"][step_key]["completed_at"] = now
                if step_num == 4:
                    try:
                        with open(result_file, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        if "sub_steps" in data:
                            for sk, sv in data["sub_steps"].items():
                                if isinstance(sv, dict) and "status" in sv:
                                    state["steps"][step_key]["sub_steps"][sk] = sv["status"]
                                elif isinstance(sv, str):
                                    state["steps"][step_key]["sub_steps"][sk] = sv
                    except Exception:
                        for sk in state["steps"][step_key].get("sub_steps", {}):
                            state["steps"][step_key]["sub_steps"][sk] = "done"

        # 如果没有 pipeline 格式，回退检查 stage_api 格式（analysis/stage_X/output.json）
        if not any(state["steps"][str(s)]["status"] == "completed" for s in range(1, 6)):
            stage_to_step = {1: "1", 2: "2", 3: "3", 4: "4", 5: "5"}
            for stage_num, step_key in stage_to_step.items():
                stage_output = self.analysis_dir / f"stage_{stage_num}" / "output.json"
                if stage_output.exists():
                    state["steps"][step_key]["status"] = "completed"
                    state["steps"][step_key]["completed_at"] = now

            # 检查 stage_api 的子阶段
            for sub_stage, sub_key in [(51, "5a"), (52, "5b"), (53, "5f")]:
                if (self.analysis_dir / f"stage_{sub_stage}" / "output.json").exists():
                    state["steps"]["5"]["sub_steps"][sub_key] = "done"

        # 检查步骤 4.5（控辩对抗）— 两种格式都检查
        debate_result = self.analysis_dir / "step_4.5_result.json"
        debate_dir = self.analysis_dir / "04.5-控辩对抗"
        if debate_result.exists() or (debate_dir.exists() and (debate_dir / "对抗分析.md").exists()):
            state["steps"]["4.5"]["status"] = "completed"
            state["steps"]["4.5"]["completed_at"] = now
            for sk in state["steps"]["4.5"]["sub_steps"]:
                state["steps"]["4.5"]["sub_steps"][sk] = "done"

        return state

    def _save_analysis_state(self, state: Optional[dict] = None):
        """保存分析状态到 analysis_state.json

        Args:
            state: 要保存的状态，如果为 None 则从磁盘重新加载
        """
        if state is None:
            state = self._load_analysis_state()
        state["updated_at"] = datetime.now().isoformat()
        path = self._state_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def _mark_step_done(self, step: int):
        """标记整个步骤完成"""
        state = self._load_analysis_state()
        step_key = str(step)
        if step_key in state["steps"]:
            state["steps"][step_key]["status"] = "completed"
            state["steps"][step_key]["completed_at"] = datetime.now().isoformat()
        self._save_analysis_state(state)  # 传递修改后的 state

    def _mark_step_running(self, step: int):
        """标记步骤开始运行"""
        state = self._load_analysis_state()
        step_key = str(step)
        if step_key in state["steps"]:
            state["steps"][step_key]["status"] = "running"
        self._save_analysis_state(state)  # 传递修改后的 state

    def _mark_substep_done(self, step: str, substep_key: str, status: str = "done"):
        """标记子步骤完成并持久化"""
        state = self._load_analysis_state()
        step_data = state["steps"].get(step, {})

        if "details" in step_data:
            step_data["details"][substep_key] = status
        elif "sub_steps" in step_data:
            # 检查是否是嵌套的子步骤（如 4b 中的人名）
            sub = step_data["sub_steps"].get(substep_key)
            if isinstance(sub, dict):
                pass  # 由调用方自行写入嵌套 dict
            else:
                step_data["sub_steps"][substep_key] = status
        else:
            # 初始化 details
            step_data["details"] = {substep_key: status}

        state["steps"][step] = step_data
        self._save_analysis_state(state)  # 传递修改后的 state

    def _get_next_unfinished_step(self) -> Optional[float]:
        """找到下一个未完成的步骤编号，全部完成返回 None。
        步骤 4 完成后优先检查 4.5 是否完成，再进入步骤 5。
        步骤 5 已完但 4.5 不存在时，返回 4.5 用于补充控辩对抗。
        """
        state = self._load_analysis_state()
        for step_num in range(1, 6):
            step_key = str(step_num)
            step_data = state["steps"].get(step_key, {})
            if step_data.get("status") != "completed":
                # 步骤 5 待完成，但先检查 4.5 和 4.75
                if step_num == 5:
                    step4_data = state["steps"].get("4", {})
                    if step4_data.get("status") == "completed":
                        step45 = state["steps"].get("4.5", {})
                        if step45.get("status") != "completed":
                            return 4.5
                        step475 = state["steps"].get("4.75", {})
                        if step475.get("status") not in ("completed", "awaiting_confirmation"):
                            return 4.75
                        if step475.get("status") == "awaiting_confirmation":
                            return 4.75  # 返回以便 API 层提示待确认（step 方法内部直接返回已有建议）
                return step_num

        # 全部 5 步都完成了，检查是否需要补充步骤 4.5
        step45 = state["steps"].get("4.5", {})
        if step45.get("status") != "completed":
            debate_dir = self.analysis_dir / "04.5-控辩对抗"
            if not debate_dir.exists() or not (debate_dir / "对抗分析.md").exists():
                return 4.5
        return None

    def _get_resume_summary(self) -> dict:
        """返回断点恢复摘要：各步骤完成状态"""
        state = self._load_analysis_state()
        result = {}
        for step_num in range(1, 6):
            step_key = str(step_num)
            step_data = state["steps"].get(step_key, {})
            status = step_data.get("status", "idle")
            detail = {}

            if step_num == 2 and "details" in step_data:
                detail = step_data["details"]
            elif step_num == 3 and "details" in step_data:
                detail = step_data["details"]
            elif step_num == 4 and "sub_steps" in step_data:
                detail = step_data["sub_steps"]
            elif step_num == 5 and "sub_steps" in step_data:
                detail = step_data["sub_steps"]

            result[step_key] = {"status": status, "detail": detail}

        # 步骤 4.5 单独处理
        step45_key = "4.5"
        step45_data = state["steps"].get(step45_key, {})
        if step45_data:
            result[step45_key] = {
                "status": step45_data.get("status", "idle"),
                "detail": step45_data.get("sub_steps", {}),
            }

        # 步骤 4.75 单独处理（辩护思路确认，可能处于 awaiting_confirmation）
        step475_key = "4.75"
        step475_data = state["steps"].get(step475_key, {})
        if step475_data:
            result[step475_key] = {
                "status": step475_data.get("status", "idle"),
                "detail": {},
            }

        return result

    def _save_preprocess_file(self, subdir: str, filename: str, content: str):
        """保存预处理文件到 analysis/preprocess/{subdir}/"""
        target_dir = self.analysis_dir / "preprocess" / subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / filename
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def _save_summary_file(self, subdir: str, filename: str, content: str):
        """保存总结文件到 analysis/summaries/{subdir}/"""
        target_dir = self.analysis_dir / "summaries" / subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / filename
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def _save_contradiction_file(self, filename: str, content: str):
        """保存矛盾分析文件到 analysis/contradictions/（Markdown 格式）"""
        target_dir = self.analysis_dir / "contradictions"
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / filename
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def _load_summary_file(self, subdir: str, filename: str) -> Optional[str]:
        """读取已存在的总结文件"""
        path = self.analysis_dir / "summaries" / subdir / filename
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def _load_contradiction_file(self, filename: str) -> Optional[str]:
        """读取已存在的矛盾分析文件（返回 Markdown 文本）"""
        path = self.analysis_dir / "contradictions" / filename
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def _list_contradiction_files(self) -> list[dict]:
        """列出所有矛盾分析文件"""
        target_dir = self.analysis_dir / "contradictions"
        if not target_dir.exists():
            return []
        files = []
        for f in sorted(target_dir.iterdir()):
            if f.is_file() and f.suffix == ".md":
                # 从文件名提取信息: "张某_共11次_矛盾分析.md"
                name = f.stem  # "张某_共11次_矛盾分析"
                files.append({
                    "filename": f.name,
                    "displayName": name.replace("_矛盾分析", ""),
                })
        return files

    # ========== 步骤 1: 合并笔录 ==========

    async def step1_merge_statements(self, defendant: str, crime_type: Optional[str] = None) -> dict:
        """合并笔录：扫描 md/ 目录，按人名+类型合并笔录文件（纯代码，无 LLM）

        人名提取策略（三源交叉验证）：
        1. 从文件名提取（_序号_人名{讯问/询问}笔录）
        2. 从文件内容提取（被讯问人/被询问人/姓名: XXX）
        3. 两者一致 → 确认；仅一个有效 → 采用；都无 → 归为"未知"
        """
        existing = self._load_step_result(1)
        if existing and existing.get("merged_files"):
            return existing

        md_files = self._load_md_files()

        person_groups: dict[tuple[str, str], list[dict]] = {}
        other_evidence = []

        for f in md_files:
            if f["type"] in ("讯问笔录", "询问笔录"):
                # 策略 1：文件名提取
                name_from_file = _extract_person_from_filename(f["filename"])
                # 策略 2：正文提取
                name_from_content = _extract_name_from_content(f["text"])

                person = None
                if name_from_file and name_from_content:
                    # 两者一致，确认
                    if name_from_file == name_from_content:
                        person = name_from_file
                    else:
                        # 不一致：优先用文件名的（更可靠），但记录正文提取结果
                        person = name_from_file
                        print(f"[步骤 1] 人名不一致: {f['filename']} -> 文件名={name_from_file}, 正文={name_from_content}，采用文件名结果")
                elif name_from_file:
                    person = name_from_file
                elif name_from_content:
                    person = name_from_content

                if person:
                    key = (person, f["type"])
                    person_groups.setdefault(key, []).append(f)
                else:
                    key = ("未知", f["type"])
                    person_groups.setdefault(key, []).append(f)
            else:
                other_evidence.append(f)

        merged_files = []
        for (person, etype), files in sorted(person_groups.items()):
            files.sort(key=lambda x: x["filename"])

            all_sessions = []
            for f in files:
                sessions = _split_sessions(f["text"])
                all_sessions.extend(sessions)

            merged_content = f"# {person}{etype}\n\n"
            merged_content += f"共 {len(all_sessions)} 次笔录\n\n"
            merged_content += "=" * 60 + "\n\n"
            for i, session in enumerate(all_sessions, 1):
                merged_content += f"## 第 {i} 次笔录（{session['time_range']}）\n\n"
                merged_content += session["content"] + "\n\n"
                merged_content += "-" * 40 + "\n\n"

            self._save_preprocess_file(etype, f"{person}_{etype}.md", merged_content)

            merged_files.append({
                "person": person,
                "type": etype,
                "filename": f"{person}_{etype}.md",
                "session_count": len(all_sessions),
                "source_files": [f["filename"] for f in files],
            })

        # 其他证据索引
        other_index = "# 其他证据索引\n\n"
        for f in other_evidence:
            etype = f["type"]
            text_preview = f["text"][:500].strip()
            other_index += f"### {f['filename']} ({etype})\n\n{text_preview}\n\n"
            if "流水" in f["filename"] or "银行" in f["filename"]:
                time_matches = re.findall(r'(\d{4}年\d{2}月\d{2}日.*?至\d{4}年\d{2}月\d{2}日)', f["text"])
                if time_matches:
                    other_index += f"**时间范围**: {time_matches[0]}\n\n"
                account_matches = re.findall(r'(账号[：:]\s*\S+)', f["text"][:2000])
                if account_matches:
                    other_index += f"**账户**: {', '.join(account_matches[:3])}\n\n"
            other_index += "\n" + "-" * 40 + "\n\n"

        self._save_preprocess_file("", "其他证据索引.md", other_index)

        other_evidence_list = [{"filename": f["filename"], "type": f["type"]} for f in other_evidence]

        result = {
            "merged_files": merged_files,
            "other_evidence": other_evidence_list,
            "total_persons": len(set(p for p, _ in person_groups.keys())),
            "total_sessions": sum(m["session_count"] for m in merged_files),
        }
        self._save_step_result(1, result)
        self._mark_step_done(1)
        return result

    # ========== 步骤 2: 逐次详细总结 ==========

    async def step2_detailed_summaries(self, defendant: str, crime_type: Optional[str] = None, progress_cb=None) -> dict:
        """对合并后的笔录文件，逐人做详细总结（串行，每次一个）"""
        step1 = self._load_step_result(1)
        if not step1:
            raise ValueError("请先完成步骤 1（合并笔录）")

        summary_results = []
        failed = []

        # 计算总笔录数
        total_sessions = 0
        for mf in step1["merged_files"]:
            session_count = mf["session_count"]
            existing_summary = self._load_summary_file(mf["type"], f"{mf['person']}_共{session_count}次_总结.md")
            if not existing_summary:
                preprocess_path = self.analysis_dir / "preprocess" / mf["type"] / mf["filename"]
                if preprocess_path.exists():
                    total_sessions += session_count

        completed = 0
        if progress_cb:
            progress_cb(0, total_sessions, f"共 {len(step1['merged_files'])} 人，{total_sessions} 次笔录需要总结")

        for mf in step1["merged_files"]:
            person = mf["person"]
            etype = mf["type"]
            filename = mf["filename"]
            session_count = mf["session_count"]

            existing_summary = self._load_summary_file(etype, f"{person}_共{session_count}次_总结.md")
            if existing_summary:
                summary_results.append({"person": person, "type": etype, "session_count": session_count, "status": "skipped"})
                print(f"[步骤 2] 跳过 {person}（已存在总结）")
                continue

            preprocess_path = self.analysis_dir / "preprocess" / etype / filename
            if not preprocess_path.exists():
                failed.append({"person": person, "type": etype, "status": "failed", "error": f"预处理文件不存在: {preprocess_path}"})
                print(f"[步骤 2] 失败 {person}（预处理文件不存在）")
                continue

            content = preprocess_path.read_text(encoding="utf-8")
            sessions = _split_sessions(content)
            session_summaries = []

            for i, session in enumerate(sessions, 1):
                try:
                    print(f"[步骤 2] 总结 {person} 第{i}/{len(sessions)} 次笔录...")
                    if progress_cb:
                        progress_cb(completed, total_sessions, f"正在总结：{person} 第{i}/{len(sessions)} 次笔录")
                    summary = await self.llm.chat([
                        {"role": "system", "content": "你是一个专业的笔录整理员。你的任务是忠实转录笔录内容，并指出 PDF 转 Markdown 过程中的识别错误。不要加入律师观点或分析判断。"},
                        {"role": "user", "content": f"""以下是{person}的第{i}次{etype.replace('笔录', '')}（时间：{session['time_range']}）。

请完成以下任务：

## 一、忠实转录
将笔录内容逐要点整理，要求：
1. 完整保留所有陈述内容，不做分析判断
2. 人名、时间、金额、事件等关键信息要准确，与原文一致
3. 区分"承认"、"否认"、"不清楚"等不同表述
4. 不要遗漏任何重要信息
5. **不要加入律师观点、法律分析、定性判断**
6. 保持原始陈述的语气和逻辑

## 二、转换错误标记
指出该笔录中明显的 PDF → Markdown 识别错误，例如：
- 乱码、错字、漏字（OCR 识别错误）
- 段落断裂或语句不通顺
- 表格内容错位
- 页眉页脚混入正文
- 其他明显的格式异常

笔录内容：
{session['content'][:context_budget.content_budget_chars()]}"""},
                    ])
                    session_summaries.append({"session_number": i, "time_range": session["time_range"], "summary": summary})
                    completed += 1
                    if progress_cb:
                        progress_cb(completed, total_sessions, f"正在总结：{person} 第{i}/{len(sessions)} 次笔录")
                except Exception as e:
                    session_summaries.append({"session_number": i, "time_range": session["time_range"], "summary": f"（总结失败：{e}）"})
                    completed += 1

            summary_md = f"# {person}{etype}总结\n\n共 {len(session_summaries)} 次笔录\n\n"
            for ss in session_summaries:
                summary_md += f"## 第{ss['session_number']}次（{ss['time_range']}）\n\n{ss['summary']}\n\n{'-' * 40}\n\n"

            self._save_summary_file(etype, f"{person}_共{session_count}次_总结.md", summary_md)
            summary_results.append({"person": person, "type": etype, "session_count": len(session_summaries), "status": "done"})
            print(f"[步骤 2] 完成 {person} 总结")

        if progress_cb:
            progress_cb(total_sessions, total_sessions, f"完成！共总结 {total_sessions} 次笔录")

        result = {"summaries": summary_results, "failed": failed, "total_persons": len(summary_results)}
        self._save_step_result(2, result)
        self._mark_step_done(2)
        return result

    # ========== 步骤 3: 内部矛盾分析 ==========

    async def step3_internal_contradiction(self, defendant: str, crime_type: Optional[str] = None, progress_cb=None) -> dict:
        """对有多次笔录的人，做内部矛盾分析（串行）"""
        step1 = self._load_step_result(1)
        step2 = self._load_step_result(2)
        if not step1 or not step2:
            raise ValueError("请先完成步骤 1 和步骤 2")

        multi_session_persons = [mf for mf in step1["merged_files"] if mf["session_count"] >= 2]
        contradiction_results = []
        total = len(multi_session_persons)

        if progress_cb:
            progress_cb(0, total, f"共 {total} 人需要矛盾分析")

        for idx, pd in enumerate(multi_session_persons, 1):
            person = pd["person"]
            etype = pd["type"]
            session_count = pd["session_count"]

            existing = self._load_contradiction_file(f"{person}_矛盾分析.md")
            if existing:
                contradiction_results.append({"person": person, "type": etype, "session_count": session_count, "status": "skipped"})
                print(f"[步骤 3] 跳过 {person}（已存在矛盾分析）")
                if progress_cb:
                    progress_cb(idx, total, f"正在分析：{person}（{idx}/{total}）")
                continue

            summary_path = self.analysis_dir / "summaries" / etype / f"{person}_共{session_count}次_总结.md"
            if not summary_path.exists():
                contradiction_results.append({"person": person, "type": etype, "status": "failed", "error": f"总结文件不存在: {summary_path}"})
                print(f"[步骤 3] 失败 {person}（总结文件不存在）")
                if progress_cb:
                    progress_cb(idx, total, f"正在分析：{person}（{idx}/{total}）")
                continue

            summary_text = summary_path.read_text(encoding="utf-8")

            try:
                print(f"[步骤 3] 分析 {person} 的矛盾...")
                if progress_cb:
                    progress_cb(idx - 1, total, f"正在分析：{person}（{idx}/{total}）")
                analysis = await self.llm.chat([
                    {"role": "system", "content": f"你是刑事律师，请对比{person}的多份{etype}，找出前后矛盾。"},
                    {"role": "user", "content": f"""{person}共有{session_count}次{etype}，以下是每次笔录的详细总结：

{summary_text[:context_budget.content_budget_chars()]}

注意：本步骤仅分析同一人口供/证言的前后矛盾（供述内矛盾），不分析不同证据之间的矛盾（证据间矛盾在步骤 4 处理）。

请逐维度对比每次笔录的差异：
1. 关键事实描述的变化（时间、金额、参与人、行为方式）
2. 供述/证言的变化趋势（从否认到承认、从模糊到具体等）
3. 前后矛盾的具体点
4. 对每个矛盾做合理性分析（记忆变化、压力、诱导、时间间隔等）

请列出具体矛盾点，并分析可能原因。"""},
                ])
            except Exception as e:
                contradiction_results.append({"person": person, "type": etype, "status": "failed", "error": str(e)})
                print(f"[步骤 3] {person} 分析失败: {e}")
                if progress_cb:
                    progress_cb(idx, total, f"正在分析：{person}（{idx}/{total}）")
                continue

            md_content = f"# {person} {etype}矛盾分析\n\n"
            md_content += f"共 {session_count} 次笔录\n\n"
            md_content += analysis
            self._save_contradiction_file(f"{person}_共{session_count}次_矛盾分析.md", md_content)
            contradiction_results.append({"person": person, "type": etype, "session_count": session_count, "status": "done"})
            print(f"[步骤 3] 完成 {person} 矛盾分析")
            if progress_cb:
                progress_cb(idx, total, f"正在分析：{person}（{idx}/{total}）")

        if progress_cb:
            progress_cb(total, total, f"完成！共分析 {total} 人矛盾")

        result = {
            "contradictions": contradiction_results,
            "total_analyzed": len([r for r in contradiction_results if r.get("status") in ("done", "skipped")]),
        }
        self._save_step_result(3, result)
        self._mark_step_done(3)
        return result

    # ========== 步骤 4: 案件 Wiki 构建（LLM Wiki 模式） ==========

    def _wiki_dir(self) -> Path:
        return self.analysis_dir / "indictment_wiki"

    def _save_wiki_page(self, subdir: str, filename: str, content: str):
        d = self._wiki_dir() / subdir
        d.mkdir(parents=True, exist_ok=True)
        (d / filename).write_text(content, encoding="utf-8")

    def _load_wiki_page(self, subdir: str, filename: str) -> str:
        p = self._wiki_dir() / subdir / filename
        if p.exists():
            return p.read_text(encoding="utf-8")
        return ""

    def _wiki_page_exists(self, subdir: str, filename: str) -> bool:
        return (self._wiki_dir() / subdir / filename).exists()

    def _list_wiki_pages(self, subdir: str) -> list[str]:
        d = self._wiki_dir() / subdir
        if not d.exists():
            return []
        return sorted([f.name for f in d.iterdir() if f.is_file()])

    def _search_legal_knowledge(self, crime_type: str | None = None) -> dict:
        """从内置刑法中搜索相关法条"""
        from legal_knowledge import CRIME_ARTICLE_MAP, load_criminal_law, load_criminal_procedure_law

        result = {"articles": "", "interpretations": "", "cases": ""}

        if not crime_type:
            return result

        # 1. 从内置刑法中查找对应条文（提取该罪名相关的所有条文）
        article_num = CRIME_ARTICLE_MAP.get(crime_type)
        if article_num:
            try:
                law_text = load_criminal_law()
                # 提取包含该法条号的段落及其内容
                lines = law_text.split("\n")
                for i, line in enumerate(lines):
                    if f"第{article_num}条" in line:
                        result["articles"] += line.strip() + "\n"
                        # 提取后续若干行作为条文内容
                        for j in range(i+1, min(i+10, len(lines))):
                            next_line = lines[j].strip()
                            if next_line.startswith("#") or (next_line.startswith("第") and "条" in next_line):
                                break
                            result["articles"] += next_line + "\n"
            except Exception as e:
                print(f"[法律知识库] 加载内置刑法失败: {e}")

        return result

    def _case_charges(self, crime_type: Optional[str] = None) -> list[str]:
        """案件罪名列表：优先 case.json 的 charges，回退 crime_type 单罪名"""
        meta_file = self.case_dir / "case.json"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                charges = meta.get("charges") or []
                if isinstance(charges, list) and charges:
                    return charges
            except Exception:
                pass
        return [crime_type] if crime_type else []

    def _save_suggested_keywords(self, keywords: list[str]):
        """LLM 推荐关键词写入 case.json（不覆盖用户已编辑的 search_keywords）"""
        meta_file = self.case_dir / "case.json"
        meta = {}
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        meta["suggested_keywords"] = keywords
        meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def _effective_keywords(self) -> list[str]:
        """有效检索关键词：用户编辑 > LLM 推荐 > 空"""
        meta_file = self.case_dir / "case.json"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                return meta.get("search_keywords") or meta.get("suggested_keywords") or []
            except Exception:
                pass
        return []

    def _build_wiki_index(self) -> str:
        """生成 Wiki 索引"""
        idx = "# 案件证据 Wiki 索引\n\n"
        idx += "## 01-指控要素\n"
        if self._wiki_page_exists("01-指控要素", ""):
            idx += "- [指控要素](01-指控要素.md)\n"

        idx += "\n## 02-事实要素\n"
        for f in self._list_wiki_pages("02-事实要素"):
            idx += f"- [{f.replace('.md', '')}](02-事实要素/{f})\n"

        idx += "\n## 03-证据分析\n"
        for f in self._list_wiki_pages("03-证据分析"):
            idx += f"- [{f.replace('.md', '')}](03-证据分析/{f})\n"

        idx += "\n## 04-法律依据\n"
        for f in self._list_wiki_pages("04-法律依据"):
            idx += f"- [{f.replace('.md', '')}](04-法律依据/{f})\n"

        idx += "\n## 05-矛盾记录\n"
        if self._wiki_page_exists("", "05-矛盾记录.md"):
            idx += "- [矛盾记录](05-矛盾记录.md)\n"

        idx += "\n## 06-综合结论\n"
        if self._wiki_page_exists("", "06-综合结论.md"):
            idx += "- [综合结论](06-综合结论.md)\n"

        return idx

    async def step4_build_case_wiki(self, defendant: str, crime_type: Optional[str] = None, progress_cb=None) -> dict:
        """步骤 4：用 LLM Wiki 模式构建案件证据知识库（全部串行）"""
        step1 = self._load_step_result(1)
        step2 = self._load_step_result(2)
        if not step1 or not step2:
            raise ValueError("请先完成步骤 1 和步骤 2")

        wiki_dir = self._wiki_dir()
        wiki_dir.mkdir(parents=True, exist_ok=True)

        # 创建子目录
        for subdir in ["02-事实要素", "03-证据分析", "04-法律依据"]:
            (wiki_dir / subdir).mkdir(exist_ok=True)

        # 定义所有子步骤总数，用于进度报告
        SUB_STEPS = ["4a-指控要素", "4c-法律框架", "4b-证据摄入", "4e-资金流", "4d-综合结论"]
        sub_done = 0
        sub_total = len(SUB_STEPS)
        if progress_cb:
            progress_cb(sub_done, sub_total, "步骤 4：开始构建案件 Wiki（指控要素分析）")

        results_log = {"sub_steps": [], "wiki_dir": str(wiki_dir)}

        # ===== 4a: 指控要素分析（起诉书 > 起诉意见书） =====
        indictment_analyzed = False
        if not self._wiki_page_exists("", "01-指控要素.md"):
            indictment_text, indictment_type = await self._find_indictment_in_md_files()

            if indictment_text:
                print(f"[步骤 4a] 分析{indictment_type}...")
                try:
                    analysis = await self.llm.chat([
                        {"role": "system", "content": f"你是刑事律师，详细分析{indictment_type}的指控逻辑。"},
                        {"role": "user", "content": f"""请详细分析以下{indictment_type}，尤其关注：

1. 指控罪名及法律依据
2. 犯罪事实概要（尽可能详细：时间、地点、人物、事件经过）
3. 共同犯罪中每个人的具体行为分解
4. 涉案金额及计算方式
5. 证据清单

{indictment_type}内容：
{indictment_text}

请以 Markdown 格式输出分析结果。"""},
                    ])
                    self._save_wiki_page("", "01-指控要素.md", analysis)
                    indictment_analyzed = True
                    results_log["sub_steps"].append({"step": "4a", "name": "指控要素分析", "status": "done"})
                    print("[步骤 4a] 完成指控要素分析")
                except Exception as e:
                    self._save_wiki_page("", "01-指控要素.md", f"分析失败：{e}")
                    results_log["sub_steps"].append({"step": "4a", "name": "指控要素分析", "status": "failed", "error": str(e)})
            else:
                self._save_wiki_page("", "01-指控要素.md", "本案未发现起诉书或起诉意见书")
                results_log["sub_steps"].append({"step": "4a", "name": "指控要素分析", "status": "no_indictment"})
        else:
            results_log["sub_steps"].append({"step": "4a", "name": "指控要素分析", "status": "skipped"})
        sub_done = 1
        if progress_cb:
            progress_cb(sub_done, sub_total, "步骤 4：构建法律框架（法条+类案）")

        # 读取指控要素（用于后续步骤）
        indictment_content = self._load_wiki_page("", "01-指控要素.md")

        # LLM 推荐类案检索关键词（罪名除外），存 case.json suggested_keywords
        if indictment_analyzed:
            try:
                kw_text = await self.llm.chat([
                    {"role": "system", "content": "你是刑事律师。请从指控要素分析中提取 3-5 个类案检索关键词（不要包含罪名本身），聚焦行为特征、情节要素、对象特征，每行一个，只输出关键词。"},
                    {"role": "user", "content": indictment_content[:5000]},
                ])
                suggested = []
                for line in kw_text.strip().split("\n"):
                    # 剥离编号前缀（1. / 2、/ 3)）与列表符号，得到干净关键词
                    line = re.sub(r"^\d+[.、\)]\s*", "", line.strip()).strip("- •　 ")
                    if line:
                        suggested.append(line)
                suggested = suggested[:5]
                if suggested:
                    self._save_suggested_keywords(suggested)
            except Exception as e:
                print(f"[步骤 4a] 关键词推荐失败（不影响主流程）: {e}")

        # ===== 4c: 法律依据检索 =====
        if not self._wiki_page_exists("04-法律依据", "适用法条.md"):
            print("[步骤 4c] 检索法律依据...")
            legal = self._search_legal_knowledge(crime_type)

            # 读取用户提供的参考材料
            user_ref_text = ""
            user_ref_dir = self.analysis_dir / "user_reference"
            if user_ref_dir.exists():
                for md in user_ref_dir.rglob("*.md"):
                    user_ref_text += md.read_text(encoding="utf-8")[:5000] + "\n\n"

            try:
                legal_analysis = await self.llm.chat([
                    {"role": "system", "content": "你是刑事律师，请根据案件情况检索并分析相关法律依据。"},
                    {"role": "user", "content": f"""## 指控要素
{indictment_content}

## 罪名类型：{crime_type or '未知'}

## 从刑法知识库检索到的法条
{legal['articles'][:8000] if legal['articles'] else '未找到相关法条'}

## 从刑法知识库检索到的司法解释
{legal['interpretations'][:8000] if legal['interpretations'] else '未找到相关司法解释'}

## 从刑法知识库检索到的案例
{legal['cases'][:5000] if legal['cases'] else '未找到相关案例'}

## 用户提供的参考材料
{user_ref_text[:5000] if user_ref_text else '无'}

请综合分析：
1. 适用的主要刑法条文及内容
2. 相关司法解释的适用要点
3. 类似案例的裁判规则（如有）
4. 对本案的法律适用建议

请输出 Markdown 格式，分别保存到适用法条、司法解释、参考案例三个部分。"""},
                ])
                self._save_wiki_page("04-法律依据", "适用法条.md", legal_analysis)
                results_log["sub_steps"].append({"step": "4c", "name": "法律依据检索", "status": "done"})
                print("[步骤 4c] 完成法律依据分析")
            except Exception as e:
                self._save_wiki_page("04-法律依据", "适用法条.md", f"分析失败：{e}")
                results_log["sub_steps"].append({"step": "4c", "name": "法律依据检索", "status": "failed", "error": str(e)})

        else:
            results_log["sub_steps"].append({"step": "4c", "name": "法律依据检索", "status": "skipped"})

        # 类案裁判规则（自动检索，供分析参考；失败静默降级）
        # 独立于适用法条.md 的存在性：存量案件重跑时也可补检
        if not self._list_wiki_pages("04-法律依据") or not any(
            f.startswith("类案裁判规则-") for f in self._list_wiki_pages("04-法律依据")
        ):
            try:
                from case_framework import fetch_case_rules
                charge_list = self._case_charges(crime_type)
                case_rules = fetch_case_rules(charge_list, keywords=self._effective_keywords())
                for charge_name, rules_md in case_rules.items():
                    safe_name = charge_name.replace("/", "_")
                    self._save_wiki_page("04-法律依据", f"类案裁判规则-{safe_name}.md", rules_md)
                if case_rules:
                    print(f"[步骤 4c] 已检索类案 {len(case_rules)} 个罪名的裁判规则")
            except Exception as e:
                print(f"[步骤 4c] 类案检索降级（不影响主流程）: {e}")

        sub_done = 2
        if progress_cb:
            progress_cb(sub_done, sub_total, "步骤 4：逐人证据摄入（证据分析）")

        # ===== 4b: 逐人证据摄入（串行） =====
        evidence_list = []
        for mf in step1["merged_files"]:
            person = mf["person"]
            etype = mf["type"]
            session_count = mf["session_count"]
            summary_file = f"{person}_共{session_count}次_总结.md"
            summary_path = self.analysis_dir / "summaries" / etype / summary_file
            if summary_path.exists():
                evidence_list.append({"person": person, "type": etype, "summary_file": summary_file})

        # 其他证据
        other_index_path = self.analysis_dir / "preprocess" / "其他证据索引.md"
        if other_index_path.exists():
            evidence_list.append({"person": "其他证据", "type": "其他证据", "summary_file": None})

        # 读取已分析的证据列表（用于交叉引用）
        analyzed_evidence = []

        # 法律框架（4c 产物：法条 + 司法解释 + 类案裁判规则）
        # 成本控制：单文件截断 2000 字，合计超 6000 字停止追加，避免 4b prompt 膨胀
        legal_framework = ""
        for lf in self._list_wiki_pages("04-法律依据"):
            if len(legal_framework) > 6000:
                break
            legal_framework += f"\n### {lf}\n{self._load_wiki_page('04-法律依据', lf)[:2000]}\n"

        print(f"[步骤 4b] 开始逐人证据摄入（{len(evidence_list)} 份证据，串行）...")
        for ev in evidence_list:
            person = ev["person"]
            etype = ev["type"]
            wiki_filename = f"{person}_{etype.replace('笔录', '')}.md".replace("/", "_")
            # 简化文件名
            wiki_filename = f"{person}_{etype}.md"

            if self._wiki_page_exists("03-证据分析", wiki_filename):
                analyzed_evidence.append(f"{person}（{etype}）")
                continue

            # 读取总结
            summary_text = ""
            if ev["summary_file"]:
                sp = self.analysis_dir / "summaries" / etype / ev["summary_file"]
                if sp.exists():
                    summary_text = sp.read_text(encoding="utf-8")

            if not summary_text and etype == "其他证据":
                if other_index_path.exists():
                    summary_text = other_index_path.read_text(encoding="utf-8")[:context_budget.content_budget_chars()]

            if not summary_text:
                print(f"[步骤 4b] 跳过 {person}（无总结文件）")
                continue

            # 读取矛盾分析
            contradiction_text = ""
            if person != "其他证据":
                # 查找该人的矛盾分析文件（可能有多个，取第一个）
                contradiction_files = self._list_contradiction_files()
                for cf in contradiction_files:
                    if cf["displayName"].startswith(person):
                        contradiction_text = self._load_contradiction_file(cf["filename"]) or ""
                        break
                if contradiction_text:
                    contradiction_text = contradiction_text[:3000]

            # 构建已分析证据摘要（用于交叉引用）
            analyzed_summary = ""
            if analyzed_evidence:
                for ae in analyzed_evidence[-5:]:  # 最近 5 份
                    ae_filename = f"{ae.split('（')[0]}_{ae.split('（')[1].replace('）', '')}.md" if "（" in ae else ""
                    if ae_filename and self._wiki_page_exists("03-证据分析", ae_filename):
                        ae_content = self._load_wiki_page("03-证据分析", ae_filename)
                        analyzed_summary += f"\n### {ae}\n{ae_content[:1500]}\n"

            print(f"[步骤 4b] 摄入 {person}（{etype}）...")
            try:
                user_prompt = f"""## 指控要素
{indictment_content or '无起诉意见书'}

## 法律框架（法条 + 司法解释 + 类案裁判规则）
{legal_framework if legal_framework else '无'}

## 待分析证据：{person}（{etype}）
{summary_text[:context_budget.content_budget_chars()]}

## 该人的矛盾分析（如有）
{contradiction_text if contradiction_text else '无'}

## 已分析的其他证据（供交叉参考）
{analyzed_summary if analyzed_summary else '暂无'}

请分析：
1. 该证据证明了指控中的哪些事实？
2. 证明力（强/中/弱）及理由
3. 与其他已分析证据的关系（印证/矛盾/补充）——此处只关注不同证据之间的矛盾（证据间矛盾）；同一人口供前后矛盾已在步骤 3 完成，不要重复分析
4. 对辩方有利的内容
5. 是否存在需要其他证据验证的点

请输出 Markdown 格式的详细分析。"""
                print(f"[预算] 步骤4b 单证据 prompt: {len(user_prompt)} 字符 / 预算 {context_budget.content_budget_chars()}")
                analysis = await self.llm.chat([
                    {"role": "system", "content": "你是刑事律师，正在进行案件证据分析。请基于证据材料，逐项分析该证据的证明力和证明内容。"},
                    {"role": "user", "content": user_prompt},
                ])
                self._save_wiki_page("03-证据分析", wiki_filename, analysis)
                analyzed_evidence.append(f"{person}（{etype}）")
                results_log["sub_steps"].append({"step": "4b", "name": f"{person}（{etype}）", "status": "done"})
                print(f"[步骤 4b] 完成 {person} 证据摄入")
            except Exception as e:
                self._save_wiki_page("03-证据分析", wiki_filename, f"分析失败：{e}")
                results_log["sub_steps"].append({"step": "4b", "name": f"{person}（{etype}）", "status": "failed", "error": str(e)})
                print(f"[步骤 4b] {person} 分析失败: {e}")

        sub_done = 3
        if progress_cb:
            progress_cb(sub_done, sub_total, "步骤 4：资金流梳理")

        # ===== 4e: 资金流梳理（4b 之后、4d 之前） =====
        if not self._wiki_page_exists("02-事实要素", "资金流梳理.md"):
            print("[步骤 4e] 梳理资金流...")
            fund_evidence = self._collect_fund_evidence(
                max_chars=int(context_budget.content_budget_chars() * 0.6)
            )
            if not fund_evidence.strip():
                self._save_wiki_page("02-事实要素", "资金流梳理.md",
                                     "本案证据中未检测到资金类内容，无需进行资金流梳理。")
                results_log["sub_steps"].append({"step": "4e", "name": "资金流梳理", "status": "no_fund_evidence"})
                print("[步骤 4e] 无资金类证据，跳过")
            else:
                try:
                    # prompt 由共享模块构建（含起诉书有效性判断与四档对照结论）
                    from fund_flow import build_fund_prompt, FUND_SYSTEM_PROMPT
                    fund_prompt = build_fund_prompt(indictment_content, fund_evidence)
                    print(f"[预算] 步骤4e 资金流 prompt: {len(fund_prompt)} 字符 / 预算 {context_budget.content_budget_chars()}")
                    fund_analysis = await self.llm.chat([
                        {"role": "system", "content": FUND_SYSTEM_PROMPT},
                        {"role": "user", "content": fund_prompt},
                    ])
                    self._save_wiki_page("02-事实要素", "资金流梳理.md", fund_analysis)
                    results_log["sub_steps"].append({"step": "4e", "name": "资金流梳理", "status": "done"})
                    print("[步骤 4e] 完成资金流梳理")
                except Exception as e:
                    self._save_wiki_page("02-事实要素", "资金流梳理.md", f"分析失败：{e}")
                    results_log["sub_steps"].append({"step": "4e", "name": "资金流梳理", "status": "failed", "error": str(e)})
        else:
            results_log["sub_steps"].append({"step": "4e", "name": "资金流梳理", "status": "skipped"})

        sub_done = 4
        if progress_cb:
            progress_cb(sub_done, sub_total, "步骤 4：生成综合结论")

        # ===== 4d: 综合结论 =====
        if not self._wiki_page_exists("", "06-综合结论.md"):
            print("[步骤 4d] 生成综合结论...")
            # 收集所有证据分析（按页数均摊内容预算）
            evidence_pages = self._list_wiki_pages("03-证据分析")
            per_page = max(2000, context_budget.content_budget_chars() // max(1, len(evidence_pages)))
            all_evidence_analysis = ""
            for f in evidence_pages:
                content = self._load_wiki_page("03-证据分析", f)
                all_evidence_analysis += f"\n### {f}\n{content[:per_page]}\n"

            legal_pages = self._list_wiki_pages("04-法律依据")
            per_legal_page = max(2000, context_budget.content_budget_chars() // max(1, len(legal_pages)))
            legal_content = ""
            for f in legal_pages:
                content = self._load_wiki_page("04-法律依据", f)
                legal_content += f"\n### {f}\n{content[:per_legal_page]}\n"

            # 资金流梳理（4e 产物，存在则注入）
            fund_flow = self._load_wiki_page("02-事实要素", "资金流梳理.md")
            fund_flow_section = f"\n## 资金流梳理\n{fund_flow[:5000]}\n" if fund_flow.strip() else ""

            try:
                user_prompt = f"""以下是本案的 Wiki 分析结果：

## 指控要素
{indictment_content[:3000]}

## 证据分析汇总
{all_evidence_analysis[:int(context_budget.content_budget_chars() * 0.7)]}

## 法律依据
{legal_content[:int(context_budget.content_budget_chars() * 0.25)]}
{fund_flow_section}
请综合分析：
1. 指控事实的证据支撑程度
2. 证据链条的完整性
3. 核心矛盾点及其影响
4. 法律适用的关键问题
5. 对辩方有利的要点
6. 对控方不利的要点
7. 资金流与指控金额的印证情况（如提供了资金流梳理）

请输出 Markdown 格式的综合结论。"""
                print(f"[预算] 步骤4d 综合结论 prompt: {len(user_prompt)} 字符 / 预算 {context_budget.content_budget_chars()}")
                conclusion = await self.llm.chat([
                    {"role": "system", "content": "你是刑事律师，请基于案件 Wiki 的所有分析结果，生成综合结论。"},
                    {"role": "user", "content": user_prompt},
                ])
                self._save_wiki_page("", "06-综合结论.md", conclusion)
                results_log["sub_steps"].append({"step": "4d", "name": "综合结论", "status": "done"})
                print("[步骤 4d] 完成综合结论")
            except Exception as e:
                self._save_wiki_page("", "06-综合结论.md", f"分析失败：{e}")
                results_log["sub_steps"].append({"step": "4d", "name": "综合结论", "status": "failed", "error": str(e)})
        else:
            results_log["sub_steps"].append({"step": "4d", "name": "综合结论", "status": "skipped"})

        sub_done = 5
        if progress_cb:
            progress_cb(sub_done, sub_total, "完成！案件 Wiki 构建完成")

        # 更新索引
        self._save_wiki_page("", "00-index.md", self._build_wiki_index())

        # 更新矛盾记录（从 contradiction 目录直接读取所有 MD 文件，按文件数均摊预算）
        contradiction_summary = ""
        contradiction_files = self._list_contradiction_files()
        per_contra = max(2000, context_budget.content_budget_chars() // max(1, len(contradiction_files)))
        for cf in contradiction_files:
            ccontent = self._load_contradiction_file(cf["filename"])
            if ccontent:
                contradiction_summary += f"\n### {cf['displayName']}\n{ccontent[:per_contra]}\n"
        if contradiction_summary:
            self._save_wiki_page("", "05-矛盾记录.md", f"# 矛盾记录\n\n{contradiction_summary}")

        self._save_step_result(4, results_log)
        self._mark_step_done(4)

        return results_log

    # ========== 步骤 4.5: 控辩对抗模拟 ==========

    def _debate_dir(self) -> Path:
        return self.analysis_dir / "04.5-控辩对抗"

    def _save_debate_file(self, filename: str, content: str):
        d = self._debate_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / filename).write_text(content, encoding="utf-8")

    def _load_debate_file(self, filename: str) -> str:
        p = self._debate_dir() / filename
        if p.exists():
            return p.read_text(encoding="utf-8")
        return ""

    def _debate_file_exists(self, filename: str) -> bool:
        # 文件存在且内容非空才视为已完成——0 字节空文件（LLM 返回空串的残留）应触发重跑自愈
        p = self._debate_dir() / filename
        return p.exists() and p.stat().st_size > 0

    async def step45_debate_simulation(self, defendant: str, crime_type: Optional[str] = None, progress_cb=None) -> dict:
        """步骤 4.5：控辩对抗模拟（沙箱模式 + 交叉对决 + 法官裁决）

        沙箱模式：控辩双方各自独立组织论点，互不可见。
        完成后由交叉对决阶段正面碰撞，最终由法官 Agent 独立评价。
        """
        # 读取前序步骤材料 — 优先 pipeline Wiki 格式，回退 stage_api 格式
        wiki_indictment = self._load_wiki_page("", "01-指控要素.md")
        wiki_conclusion = self._load_wiki_page("", "06-综合结论.md")
        wiki_contradictions = self._load_wiki_page("", "05-矛盾记录.md")
        wiki_evidence_summary = ""
        for f in self._list_wiki_pages("03-证据分析"):
            content = self._load_wiki_page("03-证据分析", f)
            wiki_evidence_summary += f"\n### {f}\n{content[:2000]}\n"
        wiki_legal = ""
        for f in self._list_wiki_pages("04-法律依据"):
            wiki_legal += self._load_wiki_page("04-法律依据", f)[:2000] + "\n\n"

        # 资金流梳理（4e 产物，存在则注入）
        wiki_fund_flow = self._load_wiki_page("02-事实要素", "资金流梳理.md")[:5000]

        # 回退：从 stage_api 格式（stage_N/output.md）读取旧案件数据
        if not wiki_indictment:
            stage1 = self.analysis_dir / "stage_1" / "output.md"
            if stage1.exists():
                wiki_indictment = stage1.read_text(encoding="utf-8")
        if not wiki_conclusion:
            stage53 = self.analysis_dir / "stage_53" / "output.md"
            if stage53.exists():
                wiki_conclusion = stage53.read_text(encoding="utf-8")
        if not wiki_contradictions:
            stage52 = self.analysis_dir / "stage_52" / "output.md"
            if stage52.exists():
                wiki_contradictions = stage52.read_text(encoding="utf-8")
        if not wiki_evidence_summary:
            stage51 = self.analysis_dir / "stage_51" / "output.md"
            if stage51.exists():
                wiki_evidence_summary = stage51.read_text(encoding="utf-8")[:4000]
        if not wiki_legal:
            stage4 = self.analysis_dir / "stage_4" / "output.md"
            if stage4.exists():
                wiki_legal = stage4.read_text(encoding="utf-8")[:4000]

        context_parts = [p for p in [
            f"## 指控要素\n{wiki_indictment}" if wiki_indictment else None,
            f"## 综合结论\n{wiki_conclusion}" if wiki_conclusion else None,
            f"## 矛盾记录\n{wiki_contradictions}" if wiki_contradictions else None,
            f"## 证据分析\n{wiki_evidence_summary}" if wiki_evidence_summary else None,
            f"## 法律依据\n{wiki_legal}" if wiki_legal else None,
            f"## 资金流梳理\n{wiki_fund_flow}" if wiki_fund_flow.strip() else None,
        ] if p]
        context = "\n\n".join(context_parts)[:context_budget.content_budget_chars()]

        step_name_map = {
            "45a": "控方沙箱",
            "45b": "辩方沙箱",
            "45c": "交叉对决",
            "45d": "法官裁决",
        }

        results_log = {"sub_steps": [], "debate_dir": str(self._debate_dir())}
        sub_done = 0
        sub_total = 4

        # ===== 45a: 控方沙箱（独立构建最强指控） =====
        if not self._debate_file_exists("01-控方指控.md"):
            print("[步骤 4.5a] 控方沙箱：构建最强指控...")
            if progress_cb:
                progress_cb(sub_done, sub_total, "步骤 4.5：控方构建指控")
            try:
                red_argument = await self.llm.chat([
                    {"role": "system", "content": "你是公诉人（控方律师）。你的职责是构建最强有力的指控逻辑。"},
                    {"role": "user", "content": f"""你是本案公诉人。被告人：**{defendant}**。

基于以下案卷材料，独立构建最强指控逻辑：

{context}

请输出以下内容（Markdown 格式）：

## 一、争议焦点

列出 3-5 个本案核心争议焦点，每个焦点一句话概括。

## 二、控方指控论点

针对每个争议焦点，详细阐述：
1. 事实依据（引用具体证据）
2. 法律依据（适用法条）
3. 逻辑推理链条

## 三、交叉询问策略

针对关键证人/被告人，设计交叉询问问题：
1. 对每个争议焦点，你会向谁提问？
2. 具体问什么问题？（逐条列出）
3. 期望通过这些问题证明什么？
4. 预判证人可能怎么回答？
"""},
                ])
                if not red_argument.strip():
                    # LLM 返回空内容：不保存成功产物、不标记 done，保留重跑自愈机会
                    print("[步骤 4.5a] 控方沙箱失败：LLM 返回空内容")
                    results_log["sub_steps"].append({
                        "step": "45a", "name": "控方沙箱",
                        "status": "failed", "error": "LLM 返回空内容",
                    })
                else:
                    self._save_debate_file("01-控方指控.md", red_argument)
                    results_log["sub_steps"].append({"step": "45a", "name": "控方沙箱", "status": "done"})
                    self._mark_substep_done("4.5", "45a", "done")
                    print("[步骤 4.5a] 完成控方指控")
            except Exception as e:
                self._save_debate_file("01-控方指控.md", f"分析失败：{e}")
                results_log["sub_steps"].append({"step": "45a", "name": "控方沙箱", "status": "failed", "error": str(e)})
        else:
            results_log["sub_steps"].append({"step": "45a", "name": "控方沙箱", "status": "skipped"})
        sub_done = 1
        if progress_cb:
            progress_cb(sub_done, sub_total, "步骤 4.5：辩方构建辩护")

        red_argument = self._load_debate_file("01-控方指控.md")

        # ===== 45b: 辩方沙箱（独立构建多路径辩护，不读取控方论点） =====
        if not self._debate_file_exists("02-辩方辩护.md"):
            print("[步骤 4.5b] 辩方沙箱：独立构建多路径辩护...")
            if progress_cb:
                progress_cb(sub_done, sub_total, "步骤 4.5：辩方构建辩护")
            try:
                blue_defense = await self.llm.chat([
                    {"role": "system", "content": "你是刑事辩护律师。你的职责是为被告人构建最强辩护逻辑。"},
                    {"role": "user", "content": f"""你是被告人 **{defendant}** 的辩护律师。

你是独立工作的——**不要参考或回应任何控方论点**。仅基于以下案卷材料，独立构建三条辩护路径：

{context}

请分别展开以下三条辩护路径，每条路径独立、完整：

## 一、无罪辩护（核心策略）

从以下角度构建无罪辩护：
1. **证据不足**：指控证据是否达到"确实、充分"标准？是否存在合理怀疑？
2. **程序违法**：侦查、取证程序是否合法？非法证据排除后还剩什么？
3. **主体不适格**：被告人是否具备犯罪构成要件的主体资格？
4. **因果关系断裂**：被告行为与危害结果之间是否存在直接因果关系？

## 二、改变定性辩护（备选策略）

从以下角度论证指控罪名不成立，应定性为较轻的罪名或民事纠纷：
1. 指控罪名的构成要件是否全部满足？哪个要件存在疑问？
2. 是否可以定性为更轻的罪名？（例如：诈骗→民事违约、开设赌场→聚众赌博）
3. 民事/行政违法与刑事犯罪的边界在哪里？本案是否停留在前一层级？

## 三、罪轻辩护（兜底策略）

即使法庭认定有罪，从以下角度争取从轻/减轻处罚：
1. **从犯地位**：是否是从犯、胁从犯？
2. **自首/坦白**：是否有自首、坦白情节？
3. **认罪认罚**：是否自愿认罪认罚？
4. **退赃退赔**：是否有退赃、退赔、赔偿行为？
5. **初犯/偶犯**：是否有前科？社会危害性大小？

## 四、辩护核心立场

用一段话总结三条路径的综合辩护策略。
"""},
                ])
                if not blue_defense.strip():
                    # LLM 返回空内容：不保存成功产物、不标记 done，保留重跑自愈机会
                    print("[步骤 4.5b] 辩方沙箱失败：LLM 返回空内容")
                    results_log["sub_steps"].append({
                        "step": "45b", "name": "辩方沙箱",
                        "status": "failed", "error": "LLM 返回空内容",
                    })
                else:
                    self._save_debate_file("02-辩方辩护.md", blue_defense)
                    results_log["sub_steps"].append({"step": "45b", "name": "辩方沙箱", "status": "done"})
                    self._mark_substep_done("4.5", "45b", "done")
                    print("[步骤 4.5b] 完成辩方辩护")
            except Exception as e:
                self._save_debate_file("02-辩方辩护.md", f"分析失败：{e}")
                results_log["sub_steps"].append({"step": "45b", "name": "辩方沙箱", "status": "failed", "error": str(e)})
        else:
            results_log["sub_steps"].append({"step": "45b", "name": "辩方沙箱", "status": "skipped"})
        sub_done = 2
        if progress_cb:
            progress_cb(sub_done, sub_total, "步骤 4.5：交叉对决")

        blue_defense = self._load_debate_file("02-辩方辩护.md")

        # ===== 45c: 交叉对决（双方论点正面碰撞） =====
        if not self._debate_file_exists("03-交叉对决.md"):
            print("[步骤 4.5c] 交叉对决：双方论点正面碰撞...")
            if progress_cb:
                progress_cb(sub_done, sub_total, "步骤 4.5：交叉对决")
            try:
                clash_analysis = await self.llm.chat([
                    {"role": "system", "content": "你是庭审对抗模拟系统。你的职责是让控辩双方观点正面交锋。"},
                    {"role": "user", "content": f"""以下是控辩双方各自独立构建的论点：

## 控方指控
{red_argument}

## 辩方辩护
{blue_defense}

请以"交叉对决"的形式，让双方观点正面碰撞：

## 一、逐焦点攻防

用表格形式展示每个争议焦点的攻防过程：

| 争议焦点 | 控方论点摘要 | 辩方反驳 | 控方再反驳 | 本焦点倾向 |
|---------|-------------|---------|-----------|-----------|
| ... | ... | ... | ... | (控方占优/辩方占优/势均力敌) |

注意：
- "辩方反驳"基于辩方的无罪辩护、改变定性辩护、罪轻辩护三条路径分别回应
- "控方再反驳"模拟控方对辩方各路径的反击
- "本焦点倾向"客观评估哪方更有说服力

## 二、无罪辩护 vs 控方指控

针对辩方无罪辩护的每个论点，逐一评估：
1. 该无罪论点是否有证据支撑？
2. 控方能否有效反驳？
3. 在法庭上被采纳的可能性（高/中/低）

## 三、改变定性辩护 vs 控方指控

针对辩方改变定性的每个论点，逐一评估：
1. 改变定性的法律依据是否成立？
2. 控方能否维持原指控定性？
3. 在法庭上被采纳的可能性（高/中/低）

## 四、罪轻辩护 vs 控方量刑建议

针对辩方罪轻辩护的每个情节，逐一评估：
1. 从轻情节是否成立？
2. 控方量刑建议是否适当？
3. 在法庭上被采纳的可能性（高/中/低）
"""},
                ])
                if not clash_analysis.strip():
                    # LLM 返回空内容：不保存成功产物、不标记 done，保留重跑自愈机会
                    print("[步骤 4.5c] 交叉对决失败：LLM 返回空内容")
                    results_log["sub_steps"].append({
                        "step": "45c", "name": "交叉对决",
                        "status": "failed", "error": "LLM 返回空内容",
                    })
                else:
                    self._save_debate_file("03-交叉对决.md", clash_analysis)
                    results_log["sub_steps"].append({"step": "45c", "name": "交叉对决", "status": "done"})
                    self._mark_substep_done("4.5", "45c", "done")
                    print("[步骤 4.5c] 完成交叉对决")
            except Exception as e:
                self._save_debate_file("03-交叉对决.md", f"分析失败：{e}")
                results_log["sub_steps"].append({"step": "45c", "name": "交叉对决", "status": "failed", "error": str(e)})
        else:
            results_log["sub_steps"].append({"step": "45c", "name": "交叉对决", "status": "skipped"})
        sub_done = 3
        if progress_cb:
            progress_cb(sub_done, sub_total, "步骤 4.5：法官裁决")

        # ===== 45d: 法官裁决（独立评价） =====
        clash_analysis = self._load_debate_file("03-交叉对决.md")
        if not self._debate_file_exists("对抗分析.md"):
            print("[步骤 4.5d] 法官裁决...")
            if progress_cb:
                progress_cb(sub_done, sub_total, "步骤 4.5：法官裁决")
            try:
                judge_verdict = await self.llm.chat([
                    {"role": "system", "content": "你是中立法官。你的职责是客观评估控辩双方的论点，给出公正裁决。"},
                    {"role": "user", "content": f"""你是本案主审法官。以下是控辩双方的独立论点及交叉对决结果：

## 控方指控（独立构建）
{red_argument}

## 辩方辩护（独立构建）
{blue_defense}

## 交叉对决结果
{clash_analysis}

请以法官视角，输出以下裁决报告（Markdown 格式）：

## 一、攻防总览

用表格汇总每个争议焦点的最终态势：

| 争议焦点 | 控方立场 | 辩方立场 | 法官评估 | 倾向 |
|---------|---------|---------|---------|------|
| ... | ... | ... | ... | (控方/辩方/存疑) |

## 二、辩护路径可行性评估

### 无罪辩护可行性：[高/中/低]
- 核心论点是否成立？
- 证据支撑是否充分？
- 法律依据是否可靠？
- 法官综合评价：

### 改变定性辩护可行性：[高/中/低]
- 改变定性的法律依据是否成立？
- 证据是否支持？
- 法官综合评价：

### 罪轻辩护可行性：[高/中/低]
- 从轻情节是否成立？
- 证据是否支持？
- 法官综合评价：

## 三、控方最可能攻击的弱点

列出控方在真实庭审中最可能重点攻击的 3-5 个辩方弱点：
1. 弱点描述
2. 攻击方式（证据/逻辑/法律）
3. 风险等级（高/中/低）

## 四、辩方需要加强的领域

列出辩方在真实庭审中需要重点准备的 3-5 个领域：
1. 需要补充什么证据或论证
2. 需要预判什么风险
3. 建议的应对策略

## 五、交叉询问预演指南

### 控方交叉询问预演
- 控方会向谁提问？
- 会问什么核心问题？
- 证人/被告人应如何回答？

### 辩方交叉询问预演
- 辩方是否有机会对控方证人提问？
- 应该问什么来削弱控方证据？

## 六、综合评估

用一段话给出法官的综合评估：哪一方的论点更有说服力，本案的核心争议是什么，最可能影响判决的因素是什么。
"""},
                ])
                if not judge_verdict.strip():
                    # LLM 返回空内容：不保存成功产物、不标记 done，保留重跑自愈机会
                    print("[步骤 4.5d] 法官裁决失败：LLM 返回空内容")
                    results_log["sub_steps"].append({
                        "step": "45d", "name": "法官裁决",
                        "status": "failed", "error": "LLM 返回空内容",
                    })
                else:
                    self._save_debate_file("对抗分析.md", judge_verdict)
                    results_log["sub_steps"].append({"step": "45d", "name": "法官裁决", "status": "done"})
                    self._mark_substep_done("4.5", "45d", "done")
                    print("[步骤 4.5d] 完成法官裁决")
            except Exception as e:
                self._save_debate_file("对抗分析.md", f"分析失败：{e}")
                results_log["sub_steps"].append({"step": "45d", "name": "法官裁决", "status": "failed", "error": str(e)})
        else:
            results_log["sub_steps"].append({"step": "45d", "name": "法官裁决", "status": "skipped"})

        sub_done = 5
        if progress_cb:
            progress_cb(sub_done, sub_total, "完成！控辩对抗模拟完成")

        # 合并所有子文件为完整对抗报告
        full_report = f"# 控辩对抗分析\n\n被告人：{defendant}\n生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        for filename in ["01-控方指控.md", "02-辩方辩护.md", "03-交叉对决.md", "对抗分析.md"]:
            content = self._load_debate_file(filename)
            if content:
                full_report += f"\n---\n\n{content}\n\n"

        result = {
            "debate_opinion": full_report,
            "full_report": full_report,
            "sub_steps": results_log["sub_steps"],
            "generated_at": datetime.now().isoformat(),
        }
        self._save_step_result(4.5, result)
        self._mark_step_done(4.5)

        return result

    # ========== 步骤 4.75: 辩护思路确认 ==========

    def _strategy_dir(self) -> Path:
        return self.analysis_dir / "04.75-辩护思路"

    async def step475_defense_strategy(self, defendant: str, crime_type: Optional[str] = None, progress_cb=None) -> dict:
        """生成辩护思路建议并进入待确认状态。

        待确认状态下重跑直接返回已有建议（不重复调 LLM）。
        """
        strategy_dir = self._strategy_dir()
        strategy_dir.mkdir(parents=True, exist_ok=True)
        suggestion_json = strategy_dir / "系统建议.json"

        if suggestion_json.exists():
            try:
                suggestion = json.loads(suggestion_json.read_text(encoding="utf-8"))
                # 已确认后重跑：直接早退，不重复进入待确认状态
                status = self._load_analysis_state()["steps"].get("4.75", {}).get("status", "idle")
                if status == "completed":
                    return {"awaiting_confirmation": False, "already_completed": True, "suggestion": suggestion}
                return {"awaiting_confirmation": True, "suggestion": suggestion}
            except Exception:
                pass  # 损坏则重新生成

        conclusion = self._load_wiki_page("", "06-综合结论.md")
        contradictions = self._load_wiki_page("", "05-矛盾记录.md")
        debate_file = self.analysis_dir / "04.5-控辩对抗" / "对抗分析.md"
        debate = debate_file.read_text(encoding="utf-8") if debate_file.exists() else ""

        # 回退：案件走的是 5 阶段引擎时，pipeline wiki 不存在，改读 stage 产物
        if not contradictions:
            stage52 = self.analysis_dir / "stage_52" / "output.md"
            if stage52.exists():
                contradictions = stage52.read_text(encoding="utf-8")
        if not conclusion:
            stage53 = self.analysis_dir / "stage_53" / "output.md"
            if stage53.exists():
                conclusion = stage53.read_text(encoding="utf-8")

        # 类案裁判规则（案例库检索的真实案例，思路依据可援引案号）
        case_rules_parts = []
        for page in self._list_wiki_pages("04-法律依据"):
            if page.startswith("类案裁判规则-"):
                content = self._load_wiki_page("04-法律依据", page)
                if content:
                    case_rules_parts.append(content)
        case_rules = "\n\n".join(case_rules_parts)

        case_rules_section = (
            f"\n## 类案裁判规则（真实案例，依据中可援引案号）\n{case_rules[:4000]}\n"
            if case_rules else ""
        )

        raw = await self.llm.chat([
            {"role": "system", "content": """你是资深刑事辩护律师。基于案件分析结果提出辩护思路建议。
只输出严格 JSON：{"directions": [{"type": "主攻"|"备选", "direction": "方向简述", "basis": "依据（引用具体证据/矛盾点/裁判规则）", "risk": "风险点"}]}
主攻方向 1-2 个，备选方向 1-3 个。"""},
            {"role": "user", "content": f"""## 综合结论
{conclusion[:8000]}

## 矛盾记录
{contradictions[:8000]}

## 控辩对抗（法官裁决倾向）
{debate[:5000]}
{case_rules_section}
被告人：{defendant}；罪名：{crime_type or '未知'}"""},
        ])

        m = re.search(r"\{.*\}", raw, re.S)
        try:
            suggestion = json.loads(m.group(0)) if m else {"directions": []}
        except (json.JSONDecodeError, ValueError):
            # LLM 输出不是合法 JSON：降级为空建议，不抛 500
            print(f"[步骤4.75] 警告：辩护思路建议 JSON 解析失败，使用空建议。原始输出前 200 字：{raw[:200]}")
            suggestion = {"directions": []}
        suggestion.setdefault("directions", [])

        suggestion_json.write_text(json.dumps(suggestion, ensure_ascii=False, indent=2), encoding="utf-8")
        (strategy_dir / "系统建议.md").write_text(self._render_suggestion_md(suggestion), encoding="utf-8")

        # 状态：待确认（不是 completed）
        state = self._load_analysis_state()
        state["steps"].setdefault("4.75", {})["status"] = "awaiting_confirmation"
        self._save_analysis_state(state)

        return {"awaiting_confirmation": True, "suggestion": suggestion}

    def _render_suggestion_md(self, suggestion: dict) -> str:
        lines = ["# 辩护思路建议（系统生成）\n"]
        for i, d in enumerate(suggestion.get("directions", [])):
            lines.append(f"## {i + 1}. [{d.get('type', '备选')}] {d.get('direction', '')}\n")
            lines.append(f"- 依据：{d.get('basis', '')}")
            lines.append(f"- 风险：{d.get('risk', '')}\n")
        return "\n".join(lines)

    async def confirm_defense_strategy(
        self,
        selected: list[int] | None = None,
        edited: dict | None = None,
        user_additions: list[str] | None = None,
        use_system_default: bool = False,
    ) -> dict:
        """确认辩护思路：写思路确认.md（含修改痕迹），状态置 completed。

        - selected: 选中的建议下标（从 0 开始）；None 且非 default 视为空选择
        - edited: {下标: 修改后的方向文本}
        - user_additions: 律师补充的思路列表
        - use_system_default: 一键采纳全部建议
        """
        suggestion_json = self._strategy_dir() / "系统建议.json"
        if not suggestion_json.exists():
            raise ValueError("尚未生成辩护思路建议，请先执行步骤 4.75")
        suggestion = json.loads(suggestion_json.read_text(encoding="utf-8"))
        directions = suggestion.get("directions", [])

        # 应用律师修改（先改后选）
        edited = edited or {}
        for idx, new_text in edited.items():
            i = int(idx)
            if 0 <= i < len(directions):
                directions[i] = {**directions[i], "direction": new_text, "_edited": True}

        if use_system_default:
            chosen = [(i, d) for i, d in enumerate(directions)]
        else:
            chosen = [(i, d) for i, d in enumerate(directions) if i in (selected or [])]

        additions = user_additions or []
        if not chosen and not additions:
            # 空确认视为采纳系统建议
            chosen = [(i, d) for i, d in enumerate(directions)]

        lines = ["# 辩护思路（律师已确认）\n", "## 采纳的方向\n"]
        for i, d in chosen:
            edited_mark = "（律师已修改）" if d.get("_edited") else ""
            lines.append(f"- **[{d.get('type', '备选')}] {d.get('direction', '')}**{edited_mark}")
            lines.append(f"  依据：{d.get('basis', '')}；风险：{d.get('risk', '')}")
        if additions:
            lines.append("\n## 律师补充\n")
            for a in additions:
                lines.append(f"- {a}")

        (self._strategy_dir() / "思路确认.md").write_text("\n".join(lines), encoding="utf-8")

        state = self._load_analysis_state()
        state["steps"].setdefault("4.75", {})["status"] = "completed"
        state["steps"]["4.75"]["completed_at"] = datetime.now().isoformat()

        # 确认/重新确认后，步骤 5 既有产物失效：删除已生成章节与步骤结果，
        # 否则重跑步骤 5 时子阶段文件已存在会被全部跳过，思路变更不生效
        defense_dir = self._defense_dir()
        if defense_dir.exists():
            for f in defense_dir.glob("*.md"):
                f.unlink()
        step5_result = self.analysis_dir / "step_5_result.json"
        step5_result.unlink(missing_ok=True)
        # 汇总报告也一并清除，避免重跑完成前用户看到旧报告
        for f in self.analysis_dir.glob("辩护分析报告_*.md"):
            f.unlink()
        # 步骤 5 状态重置，_get_next_unfinished_step 才能正确返回 5
        state["steps"]["5"] = {"status": "idle", "sub_steps": {}}

        self._save_analysis_state(state)
        return {"success": True, "chosen_count": len(chosen), "additions_count": len(additions)}

    def get_defense_strategy(self) -> dict:
        """供 API 读取：系统建议 + 确认稿 + 状态"""
        suggestion = {}
        suggestion_json = self._strategy_dir() / "系统建议.json"
        if suggestion_json.exists():
            try:
                suggestion = json.loads(suggestion_json.read_text(encoding="utf-8"))
            except Exception:
                pass
        confirmation_file = self._strategy_dir() / "思路确认.md"
        confirmation = confirmation_file.read_text(encoding="utf-8") if confirmation_file.exists() else None
        status = self._load_analysis_state()["steps"].get("4.75", {}).get("status", "idle")
        return {"suggestion": suggestion, "confirmation": confirmation, "status": status}

    # ========== 步骤 5: 辩护意见生成（分阶段渐进式） ==========

    def _defense_dir(self) -> Path:
        return self.analysis_dir / "05-辩护意见"

    def _save_defense_section(self, filename: str, content: str):
        d = self._defense_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / filename).write_text(content, encoding="utf-8")

    def _load_defense_section(self, filename: str) -> str:
        p = self._defense_dir() / filename
        if p.exists():
            return p.read_text(encoding="utf-8")
        return ""

    def _defense_section_exists(self, filename: str) -> bool:
        return (self._defense_dir() / filename).exists()

    def _assemble_defense_report(self, defendant: str) -> str:
        """合并所有子章节为完整辩护报告"""
        sections = [
            ("01-案件概述.md", "一、案件概述"),
            ("02-证据评估.md", "二、证据支撑程度评估"),
            ("03-矛盾利用.md", "三、核心矛盾点及其法律影响"),
            ("04-三阶层辩护.md", "四、三阶层犯罪论审查与辩护"),
            ("05-量刑情节.md", "五、量刑情节分析"),
            ("06-结论建议.md", "六、结论与建议"),
        ]
        report = f"# 辩护分析报告\n\n被告人：{defendant}\n生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        for filename, title in sections:
            content = self._load_defense_section(filename)
            if content:
                report += f"\n## {title}\n\n{content}\n\n---\n\n"
        return report

    async def step5_defense_opinion(self, defendant: str, crime_type: Optional[str] = None, progress_cb=None) -> dict:
        """辩护意见生成（分阶段渐进式，每阶段独立保存）"""
        step1 = self._load_step_result(1)

        # 从 Wiki 读取分析结果
        wiki_indictment = self._load_wiki_page("", "01-指控要素.md")
        wiki_conclusion = self._load_wiki_page("", "06-综合结论.md")
        wiki_contradictions = self._load_wiki_page("", "05-矛盾记录.md")

        # 资金流梳理（4e 产物，存在则注入）
        wiki_fund_flow = self._load_wiki_page("02-事实要素", "资金流梳理.md")[:5000]

        # 回退：案件走的是 5 阶段引擎时，pipeline 步骤/wiki 产物不存在，改读 stage 输出
        if not step1 or not wiki_indictment:
            stage1_file = self.analysis_dir / "stage_1" / "output.md"
            if stage1_file.exists():
                if not step1:
                    step1 = {"stage_fallback": True}
                if not wiki_indictment:
                    wiki_indictment = stage1_file.read_text(encoding="utf-8")
        if not step1:
            raise ValueError("请先完成步骤 1（合并笔录）")
        if not wiki_conclusion:
            stage53_file = self.analysis_dir / "stage_53" / "output.md"
            if stage53_file.exists():
                wiki_conclusion = stage53_file.read_text(encoding="utf-8")
        if not wiki_contradictions:
            stage52_file = self.analysis_dir / "stage_52" / "output.md"
            if stage52_file.exists():
                wiki_contradictions = stage52_file.read_text(encoding="utf-8")

        legal_pages = self._list_wiki_pages("04-法律依据")
        per_legal_page = max(2000, context_budget.content_budget_chars() // max(1, len(legal_pages)))
        wiki_legal = ""
        for f in legal_pages:
            wiki_legal += self._load_wiki_page("04-法律依据", f)[:per_legal_page] + "\n\n"
        if not wiki_legal.strip():
            stage4_file = self.analysis_dir / "stage_4" / "output.md"
            if stage4_file.exists():
                wiki_legal = stage4_file.read_text(encoding="utf-8")[:context_budget.content_budget_chars()]

        evidence_pages = self._list_wiki_pages("03-证据分析")
        per_evidence_page = max(2000, context_budget.content_budget_chars() // max(1, len(evidence_pages)))
        wiki_evidence_summary = ""
        for f in evidence_pages:
            content = self._load_wiki_page("03-证据分析", f)
            wiki_evidence_summary += f"\n### {f}\n{content[:per_evidence_page]}\n"
        if not wiki_evidence_summary.strip():
            stage51_file = self.analysis_dir / "stage_51" / "output.md"
            if stage51_file.exists():
                wiki_evidence_summary = stage51_file.read_text(encoding="utf-8")[:context_budget.content_budget_chars()]

        # 读取控辩对抗结果（如有）
        debate_file = self.analysis_dir / "04.5-控辩对抗" / "对抗分析.md"
        debate_context = ""
        if debate_file.exists():
            debate_context = debate_file.read_text(encoding="utf-8")[:10000]

        # 辩护思路（4.75 律师确认稿，存在则注入每节 prompt 最前面）
        strategy_file = self.analysis_dir / "04.75-辩护思路" / "思路确认.md"
        strategy_prefix = ""
        if strategy_file.exists():
            strategy_prefix = (
                "辩护思路（律师已确认，必须遵循；律师补充的思路优先级最高，与系统建议冲突时以律师为准）：\n"
                + strategy_file.read_text(encoding="utf-8")[:context_budget.content_budget_chars()]
                + "\n\n"
            )

        if not wiki_indictment and not wiki_conclusion:
            raise ValueError("请先完成步骤 4（案件 Wiki 构建）")

        try:
            from legal_knowledge import THEORY_THREE_TIERS, CONSTITUTIVE_ELEMENT_ANALYSIS
            theory_text = THEORY_THREE_TIERS[:context_budget.content_budget_chars()]
            element_text = CONSTITUTIVE_ELEMENT_ANALYSIS[:context_budget.content_budget_chars()]
        except ImportError:
            theory_text = "三阶层理论：构成要件符合性 → 违法性 → 有责性"
            element_text = "法条构成要件拆解分析法：提出问题 → 套入法条 → 是否符合 → 本罪/无罪/他罪"

        # 前三项截断 8000 字 + 资金流前移，防止 context[:20000] 把资金流段整体切掉（审查发现）
        context = "\n\n".join([
            part for part in [
                f"## 指控要素\n{wiki_indictment[:8000]}" if wiki_indictment else None,
                f"## 案件综合结论\n{wiki_conclusion[:8000]}" if wiki_conclusion else None,
                f"## 矛盾记录\n{wiki_contradictions[:8000]}" if wiki_contradictions else None,
                f"## 资金流梳理\n{wiki_fund_flow}" if wiki_fund_flow.strip() else None,
                f"## 法律依据\n{wiki_legal}" if wiki_legal else None,
                f"## 证据分析汇总\n{wiki_evidence_summary}" if wiki_evidence_summary else None,
                f"## 控辩对抗结果\n{debate_context}" if debate_context else None,
            ] if part
        ])

        sub_steps = [
            ("5a", "01-案件概述.md", "案件概述",
             f"""你是刑事律师。基于以下材料，为被告人 **{defendant}** 生成案件概述章节。
要求：概括指控罪名、指控事实、涉案金额、涉案人员。

**重要区分**：起诉书/起诉意见书是指控文书不是证据，引用时写"据起诉书"/"据起诉意见书"，不要用"见证据XXX"格式。

{context[:20000]}

请输出 Markdown 格式，聚焦案件概述。"""),
            ("5b", "02-证据评估.md", "证据评估",
             f"""你是刑事律师。基于以下材料，为被告人 **{defendant}** 生成证据支撑程度评估章节。
要求：对每个指控维度的证据强度评级（强/中/弱），分析证据链条完整性。

**重要区分**：起诉书/起诉意见书是指控文书不是证据，不能将其视为支撑指控的"证据"。评估证据强度时只看正式证据（笔录、证言、鉴定意见、书证等），指控文书本身不算证据支撑。

{context[:20000]}

请输出 Markdown 格式，聚焦证据评估。"""),
            ("5c", "03-矛盾利用.md", "矛盾利用",
             f"""你是刑事律师。基于以下材料，为被告人 **{defendant}** 生成核心矛盾点及其法律影响章节。
要求：找出核心矛盾点，分析对辩方有利和对控方不利的要点。

**重要区分**：起诉书/起诉意见书是指控文书不是证据，引用时写"据起诉书"/"据起诉意见书"。矛盾分析应聚焦证据之间的矛盾，不是指控与证据的矛盾。

{context[:20000]}

请输出 Markdown 格式，聚焦矛盾分析。"""),
            ("5d", "04-三阶层辩护.md", "三阶层辩护",
             f"""你是资深刑事辩护律师，精通三阶层犯罪论体系。

{theory_text}

{element_text}

基于以下材料，为被告人 **{defendant}** 生成三阶层辩护章节。
要求：逐项分析构成要件符合性、违法性、有责性，提出辩护意见。

**重要区分**：起诉书/起诉意见书是指控文书不是证据。引用时写"据起诉书"/"据起诉意见书"，绝不能用"见证据XXX（起诉意见书）"格式。对指控的每一项事实，要指出是否有独立证据支撑——起诉书的指控不等于有证据支撑。

{context[:25000]}

请输出 Markdown 格式，聚焦三阶层辩护。"""),
            ("5e", "05-量刑情节.md", "量刑情节",
             f"""你是刑事律师。基于以下材料，为被告人 **{defendant}** 生成量刑情节分析章节。
要求：分析法定/酌定量刑情节（自首、立功、从犯、未遂、中止、认罪认罚等）。引用证据时用"见证据XXX"格式，起诉书/起诉意见书引用时写"据起诉书"/"据起诉意见书"。

{context[:20000]}

请输出 Markdown 格式，聚焦量刑情节。"""),
            ("5f", "06-结论建议.md", "结论建议",
             f"""你是刑事律师。基于以下材料，为被告人 **{defendant}** 生成结论与建议章节。
要求：给出辩护策略建议（无罪/罪轻/程序辩护方向）、预期结果评估、下一步工作建议。

**重要区分**：起诉书/起诉意见书是指控文书不是证据，引用时写"据起诉书"/"据起诉意见书"，绝不能用"见证据XXX（起诉意见书）"格式。

{context[:20000]}

请输出 Markdown 格式，聚焦结论建议。"""),
        ]

        results_log = {"sub_steps": [], "defense_dir": str(self._defense_dir())}
        sub_done = 0
        sub_total = len(sub_steps)

        for stage_key, filename, stage_name, prompt in sub_steps:
            if self._defense_section_exists(filename):
                results_log["sub_steps"].append({"step": stage_key, "name": stage_name, "status": "skipped"})
                sub_done += 1
                if progress_cb:
                    progress_cb(sub_done, sub_total, f"步骤 5：{stage_name}（已存在）")
                continue

            print(f"[步骤 5-{stage_key}] 生成 {stage_name}...")
            if progress_cb:
                progress_cb(sub_done, sub_total, f"步骤 5：正在生成 {stage_name}")

            try:
                section_content = await self.llm.chat([
                    {"role": "system", "content": "你是一位资深的刑事辩护律师，综合前 4 步分析结果，形成全面、深入的辩护意见。\n\n重要：起诉书/起诉意见书是指控文书不是证据。引用时写'据起诉书'/'据起诉意见书'，不要用'见证据XXX'格式。只有正式证据（笔录、证言、鉴定意见、书证等）才用'见证据XXX'格式。"},
                    {"role": "user", "content": strategy_prefix + prompt},
                ])
                self._save_defense_section(filename, section_content)
                results_log["sub_steps"].append({"step": stage_key, "name": stage_name, "status": "done"})
                print(f"[步骤 5-{stage_key}] 完成 {stage_name}")
            except Exception as e:
                self._save_defense_section(filename, f"分析失败：{e}")
                results_log["sub_steps"].append({"step": stage_key, "name": stage_name, "status": "failed", "error": str(e)})
                print(f"[步骤 5-{stage_key}] {stage_name} 生成失败: {e}")

            sub_done += 1
            self._mark_substep_done("5", stage_key, results_log["sub_steps"][-1]["status"])
            if progress_cb:
                progress_cb(sub_done, sub_total, f"步骤 5：{stage_name} 完成")

        # 合并所有子章节为完整报告
        full_report = self._assemble_defense_report(defendant)
        result = {
            "defense_opinion": full_report,
            "full_report": full_report,
            "sub_steps": results_log["sub_steps"],
            "generated_at": datetime.now().isoformat(),
        }
        self._save_step_result(5, result)
        self._mark_step_done(5)

        report_file = self.analysis_dir / f"辩护分析报告_{defendant}.md"
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(full_report)
        result["report_file"] = str(report_file)

        return result
