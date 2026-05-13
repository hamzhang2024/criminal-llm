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

        # 格式 3：人名_（如 顾君燕_讯问笔录 → 顾君燕）
        name_match2 = re.search(r'([\u4e00-\u9fff]{2,4})_+$', prefix)
        if name_match2:
            return name_match2.group(1)
    return None


def infer_evidence_type(filename: str) -> str:
    """从文件名推断证据类型"""
    if "起诉" in filename or "指控" in filename:
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

    def __init__(self, case_id: str, case_dir: Path):
        self.case_id = case_id
        self.case_dir = Path(case_dir) if isinstance(case_dir, str) else case_dir
        self.analysis_dir = self.case_dir / "analysis"
        self.analysis_dir.mkdir(exist_ok=True)
        self.llm = get_llm_client()

    # ========== 工具方法 ==========

    def _load_md_files(self) -> list[dict]:
        """读取 md/ 目录下所有 Markdown 文件"""
        md_dir = self.case_dir / "md"
        if not md_dir.exists():
            raise ValueError("md/ 目录不存在，请先完成案卷拆分和转MD")
        files = []
        for f in sorted(md_dir.iterdir(), key=lambda x: x.name):
            if f.suffix.lower() == ".md":
                files.append({
                    "filename": f.name,
                    "filepath": str(f),
                    "text": f.read_text(encoding="utf-8"),
                    "type": infer_evidence_type(f.name),
                })
        return files

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
                # 从文件名提取信息: "顾君燕_共11次_矛盾分析.md"
                name = f.stem  # "顾君燕_共11次_矛盾分析"
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
{session['content'][:30000]}"""},
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

{summary_text[:40000]}

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

    def _search_legal_knowledge(self, crime_type: Optional[str]) -> dict:
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
                            if lines[j].strip().startswith("#") or lines[j].strip().startswith("第") and "条" in lines[j] and line not in result["articles"]:
                                break
                            result["articles"] += lines[j].strip() + "\n"
            except Exception as e:
                print(f"[法律知识库] 加载内置刑法失败: {e}")

        return result

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
        SUB_STEPS = ["4a-指控要素", "4b-证据摄入", "4c-法律依据", "4d-综合结论"]
        sub_done = 0
        sub_total = len(SUB_STEPS)
        if progress_cb:
            progress_cb(sub_done, sub_total, "步骤 4：开始构建案件 Wiki（指控要素分析）")

        results_log = {"sub_steps": [], "wiki_dir": str(wiki_dir)}

        # ===== 4a: 起诉意见书分析 =====
        if not self._wiki_page_exists("", "01-指控要素.md"):
            indictment_text = ""
            for f in self._load_md_files():
                if f["type"] == "起诉意见书":
                    indictment_text = f["text"][:40000]
                    break

            if indictment_text:
                print("[步骤 4a] 分析起诉意见书...")
                try:
                    analysis = await self.llm.chat([
                        {"role": "system", "content": "你是刑事律师，详细分析起诉意见书的指控逻辑。"},
                        {"role": "user", "content": f"""请详细分析以下起诉意见书，尤其关注：

1. 指控罪名及法律依据
2. 犯罪事实概要（尽可能详细：时间、地点、人物、事件经过）
3. 共同犯罪中每个人的具体行为分解
4. 涉案金额及计算方式
5. 证据清单

起诉意见书内容：
{indictment_text}

请以 Markdown 格式输出分析结果。"""},
                    ])
                    self._save_wiki_page("", "01-指控要素.md", analysis)
                    results_log["sub_steps"].append({"step": "4a", "name": "指控要素分析", "status": "done"})
                    print("[步骤 4a] 完成指控要素分析")
                except Exception as e:
                    self._save_wiki_page("", "01-指控要素.md", f"分析失败：{e}")
                    results_log["sub_steps"].append({"step": "4a", "name": "指控要素分析", "status": "failed", "error": str(e)})
            else:
                self._save_wiki_page("", "01-指控要素.md", "本案无起诉意见书")
                results_log["sub_steps"].append({"step": "4a", "name": "指控要素分析", "status": "no_indictment"})
        else:
            results_log["sub_steps"].append({"step": "4a", "name": "指控要素分析", "status": "skipped"})
        sub_done = 1
        if progress_cb:
            progress_cb(sub_done, sub_total, "步骤 4：逐人证据摄入（证据分析）")

        # 读取指控要素（用于后续步骤）
        indictment_content = self._load_wiki_page("", "01-指控要素.md")

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
                    summary_text = other_index_path.read_text(encoding="utf-8")[:30000]

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
                analysis = await self.llm.chat([
                    {"role": "system", "content": "你是刑事律师，正在进行案件证据分析。请基于证据材料，逐项分析该证据的证明力和证明内容。"},
                    {"role": "user", "content": f"""## 指控要素
{indictment_content or '无起诉意见书'}

## 待分析证据：{person}（{etype}）
{summary_text[:30000]}

## 该人的矛盾分析（如有）
{contradiction_text if contradiction_text else '无'}

## 已分析的其他证据（供交叉参考）
{analyzed_summary if analyzed_summary else '暂无'}

请分析：
1. 该证据证明了指控中的哪些事实？
2. 证明力（强/中/弱）及理由
3. 与其他已分析证据的关系（印证/矛盾/补充）
4. 对辩方有利的内容
5. 是否存在需要其他证据验证的点

请输出 Markdown 格式的详细分析。"""},
                ])
                self._save_wiki_page("03-证据分析", wiki_filename, analysis)
                analyzed_evidence.append(f"{person}（{etype}）")
                results_log["sub_steps"].append({"step": "4b", "name": f"{person}（{etype}）", "status": "done"})
                print(f"[步骤 4b] 完成 {person} 证据摄入")
            except Exception as e:
                self._save_wiki_page("03-证据分析", wiki_filename, f"分析失败：{e}")
                results_log["sub_steps"].append({"step": "4b", "name": f"{person}（{etype}）", "status": "failed", "error": str(e)})
                print(f"[步骤 4b] {person} 分析失败: {e}")

        sub_done = 2
        if progress_cb:
            progress_cb(sub_done, sub_total, "步骤 4：法律依据检索")

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

        sub_done = 3
        if progress_cb:
            progress_cb(sub_done, sub_total, "步骤 4：生成综合结论")

        # ===== 4d: 综合结论 =====
        if not self._wiki_page_exists("", "06-综合结论.md"):
            print("[步骤 4d] 生成综合结论...")
            # 收集所有证据分析
            all_evidence_analysis = ""
            for f in self._list_wiki_pages("03-证据分析"):
                content = self._load_wiki_page("03-证据分析", f)
                all_evidence_analysis += f"\n### {f}\n{content[:2000]}\n"

            legal_content = ""
            for f in self._list_wiki_pages("04-法律依据"):
                content = self._load_wiki_page("04-法律依据", f)
                legal_content += f"\n### {f}\n{content[:2000]}\n"

            try:
                conclusion = await self.llm.chat([
                    {"role": "system", "content": "你是刑事律师，请基于案件 Wiki 的所有分析结果，生成综合结论。"},
                    {"role": "user", "content": f"""以下是本案的 Wiki 分析结果：

## 指控要素
{indictment_content[:3000]}

## 证据分析汇总
{all_evidence_analysis[:15000]}

## 法律依据
{legal_content[:3000]}

请综合分析：
1. 指控事实的证据支撑程度
2. 证据链条的完整性
3. 核心矛盾点及其影响
4. 法律适用的关键问题
5. 对辩方有利的要点
6. 对控方不利的要点

请输出 Markdown 格式的综合结论。"""},
                ])
                self._save_wiki_page("", "06-综合结论.md", conclusion)
                results_log["sub_steps"].append({"step": "4d", "name": "综合结论", "status": "done"})
                print("[步骤 4d] 完成综合结论")
            except Exception as e:
                self._save_wiki_page("", "06-综合结论.md", f"分析失败：{e}")
                results_log["sub_steps"].append({"step": "4d", "name": "综合结论", "status": "failed", "error": str(e)})
        else:
            results_log["sub_steps"].append({"step": "4d", "name": "综合结论", "status": "skipped"})

        sub_done = 4
        if progress_cb:
            progress_cb(sub_done, sub_total, "完成！案件 Wiki 构建完成")

        # 更新索引
        self._save_wiki_page("", "00-index.md", self._build_wiki_index())

        # 更新矛盾记录（从 contradiction 目录直接读取所有 MD 文件）
        contradiction_summary = ""
        contradiction_files = self._list_contradiction_files()
        for cf in contradiction_files:
            ccontent = self._load_contradiction_file(cf["filename"])
            if ccontent:
                contradiction_summary += f"\n### {cf['displayName']}\n{ccontent[:2000]}\n"
        if contradiction_summary:
            self._save_wiki_page("", "05-矛盾记录.md", f"# 矛盾记录\n\n{contradiction_summary}")

        self._save_step_result(4, results_log)

        return results_log

    # ========== 步骤 5: 辩护意见生成 ==========

    async def step5_defense_opinion(self, defendant: str, crime_type: Optional[str] = None) -> dict:
        """辩护意见生成（综合前 4 步 Wiki 结果）"""
        step1 = self._load_step_result(1)

        if not step1:
            raise ValueError("请先完成步骤 1（合并笔录）")

        # 从 Wiki 读取分析结果
        wiki_indictment = self._load_wiki_page("", "01-指控要素.md")
        wiki_conclusion = self._load_wiki_page("", "06-综合结论.md")
        wiki_contradictions = self._load_wiki_page("", "05-矛盾记录.md")
        wiki_legal = ""
        for f in self._list_wiki_pages("04-法律依据"):
            wiki_legal += self._load_wiki_page("04-法律依据", f)[:3000] + "\n\n"

        # 证据分析汇总
        wiki_evidence_summary = ""
        for f in self._list_wiki_pages("03-证据分析"):
            content = self._load_wiki_page("03-证据分析", f)
            wiki_evidence_summary += f"\n### {f}\n{content[:2000]}\n"

        if not wiki_indictment and not wiki_conclusion:
            raise ValueError("请先完成步骤 4（案件 Wiki 构建）")

        try:
            from legal_knowledge import THEORY_THREE_TIERS, CONSTITUTIVE_ELEMENT_ANALYSIS
            theory_text = THEORY_THREE_TIERS[:3000]
            element_text = CONSTITUTIVE_ELEMENT_ANALYSIS[:3000]
        except ImportError:
            theory_text = "三阶层理论：构成要件符合性 → 违法性 → 有责性"
            element_text = "法条构成要件拆解分析法：提出问题 → 套入法条 → 是否符合 → 本罪/无罪/他罪"

        system_prompt = f"""你是一位资深的刑事辩护律师，精通刑法学界通行的三阶层犯罪论体系。

{theory_text}

你的职责是为被告人 {defendant} 进行有效辩护。
综合前 4 步分析结果，形成全面、深入的辩护意见。
核心方法：提出问题 → 套入法条 → 是否符合构成要件 → 本罪/无罪/他罪
输出 Markdown 格式，内容要充分展开，不要省略分析细节。"""

        context_parts = []

        if wiki_indictment:
            context_parts.append(f"## 指控要素\n{wiki_indictment}")

        if wiki_conclusion:
            context_parts.append(f"## 案件综合结论\n{wiki_conclusion}")

        if wiki_contradictions:
            context_parts.append(f"## 矛盾记录\n{wiki_contradictions}")

        if wiki_legal:
            context_parts.append(f"## 法律依据\n{wiki_legal}")

        if wiki_evidence_summary:
            context_parts.append(f"## 证据分析汇总\n{wiki_evidence_summary}")

        context = "\n\n".join(context_parts)

        defense = await self.llm.chat([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"""基于以下前 4 步分析结果，为被告人 **{defendant}** 形成全面辩护意见：

{context}

请根据上述分析内容，生成一份结构完整、论证充分的辩护意见。报告应全面展现分析成果，**不要使用固定模板**，而是根据案件实际情况调整结构。要求：

1. **全面利用上述分析材料**——将指控要素分析、证据审查、矛盾点、法律依据、逐人证据分析等融入报告的相应章节
2. **报告结构应由内容决定**——可以包含（但不限于）以下章节：
   - 案件概述（指控罪名、指控事实、涉案金额）
   - 证据支撑程度评估（对每个指控维度的证据强度评级）
   - 证据链条完整性分析（形式与实质审查）
   - 核心矛盾点及其法律影响
   - 法律适用关键问题
   - 三阶层犯罪论审查（构成要件符合性、违法性、有责性）
   - 对辩方有利要点汇总
   - 对控方不利要点汇总
   - 核心辩护意见
   - 量刑情节分析
   - 建议的下一步工作
3. **每个章节都要充分展开**，不要只写一两句话
4. **引用具体的证据名称和证人**，不要泛泛而谈
5. **引用法律依据**（法条、司法解释、指导案例）
6. **结论要明确具体**，有可操作性"""},
        ])

        result = {
            "defense_opinion": defense,
            "full_report": defense,
            "generated_at": datetime.now().isoformat(),
        }
        self._save_step_result(5, result)

        report_file = self.analysis_dir / f"辩护分析报告_{defendant}.md"
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(defense)
        result["report_file"] = str(report_file)

        return result
