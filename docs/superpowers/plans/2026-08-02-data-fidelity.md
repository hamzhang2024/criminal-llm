# 数据流修正实现计划（A' 提取保真 + B' 起诉书全文 + C 预算截断）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 消除分析链路的保真损失——笔录问答全录、起诉书全文原文切片、Wiki 小截断预算化

**分支:** `feat/data-fidelity`（从 main 新建）

**背景（冒烟/审计结论）：**
- 主分析链路吃"提取摘要的摘要"：step1/2 基于提取件（LLM 摘要+选择性摘录），原始笔录全文不进分析 prompt
- 提取件的"原文摘录"是选择性的 → A'：笔录类改为问答全录
- `_process_indictment_single`（case_manager.py:866）对起诉书是 LLM 转述 → B'：原文切片
- 4d 的 `content[:2000]`/step5 的 `[:3000]`/`[:2000]` 硬截断仍在 → C：预算比例
- 4a 数据源 `_find_indictment_in_md_files`（读 evidence/ 优先）

---

### Task 1: A' 笔录问答全录

**Files:**
- Modify: `backend/case_manager.py`（_EVIDENCE_EXTRACTION_RULES 笔录节 + 证据模板）
- Test: `tests/test_extraction_fidelity.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_extraction_fidelity.py
"""A'：笔录类证据原文摘录为问答全录"""
import inspect
import case_manager


def test_rules_require_full_qa_transcript():
    src = inspect.getsource(case_manager._EVIDENCE_EXTRACTION_RULES)
    assert "全部问答" in src
    assert "不得筛选" in src or "不得省略" in src
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /Users/zhanghan/.openclaw/workspace/criminal-llm && python3 -m pytest tests/test_extraction_fidelity.py -v`
Expected: FAIL

- [ ] **Step 3: 实现——RULES 的"讯问/询问笔录类提取要求"一节**

找到"笔录全文要点：保留关键问答原文摘录（问答形式），特别是："段落，改为：

```
- **笔录全文要点（原文摘录要求）：以问答形式完整保留全部问答原文**——从第一问第一答到最后一问最后一答，不得筛选、不得省略、不得概括。只有与案情完全无关的程序性问答（如告知权利义务的固定问答）可省略，并标注"[程序性问答略]"。特别注意：
```

（保留其后的"关于案发时间、地点"等着重提示不变。）

- [ ] **Step 4: 运行确认通过 + 无回归**

Run: `python3 -m pytest tests/test_extraction_fidelity.py -v && python3 -m pytest tests/ -q`

- [ ] **Step 5: 提交**

```bash
git add backend/case_manager.py tests/test_extraction_fidelity.py
git commit -m "feat: 笔录类证据原文摘录改为问答全录（保真增强）"
```

---

### Task 2: B' 起诉书/起诉意见书全文原文切片

**Files:**
- Modify: `backend/case_manager.py`（_process_indictment_single 加全文切片 + 模板加"原文全文"段）
- Modify: `backend/analysis_pipeline.py`（_find_indictment_in_md_files 优先读原文全文段）
- Test: `tests/test_indictment_fulltext.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_indictment_fulltext.py
"""B'：起诉书/意见书提取件带原文全文（LLM 只定位，代码切片，不经 LLM 转述）"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import case_manager
from case_manager import _slice_section_by_markers, _process_indictment_single


def test_slice_by_markers():
    raw = "## 拘留证\n内容A\n## 起诉意见书\n第一行正文\n中间内容\n最后一行\n## 逮捕证\n内容B"
    out = _slice_section_by_markers(raw, "第一行正文", "最后一行")
    assert out is not None
    assert out.startswith("第一行正文")
    assert out.endswith("最后一行")
    assert "中间内容" in out
    assert "内容A" not in out


def test_slice_marker_not_found():
    assert _slice_section_by_markers("内容", "不存在首行", "不存在末行") is None


def test_slice_reversed_markers():
    """末行先于首行出现（LLM 给错）返回 None"""
    raw = "最后一行\n中间\n第一行正文"
    assert _slice_section_by_markers(raw, "第一行正文", "最后一行") is None


def test_indictment_evidence_contains_fulltext(tmp_path, monkeypatch):
    """提取件包含原文全文段（非 LLM 转述）"""
    raw_md = "## 卷内目录\n目录内容\n## 起诉意见书\n澄公刑诉字（2025）697号\n被告人张三盗窃……\n此致\n某某检察院\n## 其他文书\n内容"

    chat_count = {"n": 0}

    class FakeClient:
        async def chat(self, messages, **kw):
            chat_count["n"] += 1
            user = messages[-1]["content"]
            if "首行" in user or "末行" in user:
                # 定位调用：返回原文首行/末行
                return "首行：澄公刑诉字（2025）697号\n末行：某某检察院"
            return "结构化提取结果"

    monkeypatch.setattr("llm_client.get_llm_client", lambda: FakeClient())
    md_file = tmp_path / "第1卷.md"
    md_file.write_text(raw_md, encoding="utf-8")
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()

    out_path = asyncio.run(_process_indictment_single(md_file, raw_md, evidence_dir, 31))
    content = out_path.read_text(encoding="utf-8")
    assert "## 原文全文" in content
    assert "澄公刑诉字（2025）697号" in content
    assert "此致" in content
    assert "目录内容" not in content.split("## 原文全文")[1]  # 切片外内容不混入
```

注意：`_process_indictment_single` 当前签名是 `(md_file, md_text, evidence_dir, next_id)`，内部调一次 LLM 做结构化提取。改造后调两次（结构化 + 定位）；先读现有实现确认细节（如何取 md_file.name 判断类型等），测试的 fake 按真实 prompt 关键词区分两次调用。

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_indictment_fulltext.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**

（a）`case_manager.py` 新增：

```python
def _slice_section_by_markers(raw_text: str, first_line: str, last_line: str) -> str | None:
    """按原文首行/末行切片（LLM 只定位，文本不经转述）。找不到或顺序颠倒返回 None"""
    first_line = first_line.strip()
    last_line = last_line.strip()
    if not first_line or not last_line:
        return None
    start = raw_text.find(first_line)
    if start < 0:
        return None
    end = raw_text.find(last_line, start)
    if end < 0:
        return None
    end += len(last_line)
    return raw_text[start:end].strip()
```

（b）`_process_indictment_single` 改造：结构化提取调用之后，追加定位调用：

```python
    # 原文全文切片：LLM 只给首行/末行定位，代码从原文切（不经转述）
    fulltext_section = ""
    try:
        locate = await client.chat([
            {"role": "system", "content": f"你是案卷整理员。给定文件内容，找出其中《{doc_type}》正文的**第一行原文**和**最后一行原文**（逐字引用，不要改写）。只输出两行：\n首行：xxx\n末行：xxx"},
            {"role": "user", "content": md_text[:_FULLTEXT_LOCATE_BUDGET]},
        ])
        first_line = last_line = ""
        for line in locate.strip().split("\n"):
            if line.startswith("首行"):
                first_line = line.split("：", 1)[-1].strip()
            elif line.startswith("末行"):
                last_line = line.split("：", 1)[-1].strip()
        sliced = _slice_section_by_markers(md_text, first_line, last_line)
        if sliced:
            fulltext_section = f"\n\n## 原文全文\n\n{sliced}\n"
        else:
            logger.warning(f"[证据提取] {md_file.name}: 起诉书原文定位失败，仅保留结构化提取")
    except Exception as e:
        logger.warning(f"[证据提取] {md_file.name}: 起诉书原文切片失败（不影响结构化提取）: {e}")
```

`_FULLTEXT_LOCATE_BUDGET = 60000`（模块常量）。evidence 文件内容模板在"详细提取"段后追加 `{fulltext_section}`。

（c）`analysis_pipeline.py` 的 `_find_indictment_in_md_files`：返回提取件文本时，若含"## 原文全文"段则**只返回该段内容**（原文）：

```python
def _extract_fulltext_section(text: str) -> str:
    """提取件中的原文全文段（B'）；无则返回空"""
    marker = "## 原文全文"
    idx = text.find(marker)
    if idx < 0:
        return ""
    rest = text[idx + len(marker):]
    next_h = rest.find("\n## ")
    return rest[:next_h].strip() if next_h > 0 else rest.strip()
```

在 `_find_indictment_in_md_files` 三处 `return f["text"][:...], doc_type` 处改为：
```python
fulltext = _extract_fulltext_section(f["text"])
return (fulltext or f["text"])[:context_budget.content_budget_chars()], doc_type
```

- [ ] **Step 4: 运行确认通过 + 无回归**

Run: `python3 -m pytest tests/test_indictment_fulltext.py -v && python3 -m pytest tests/ -q`

- [ ] **Step 5: 提交**

```bash
git add backend/case_manager.py backend/analysis_pipeline.py tests/test_indictment_fulltext.py
git commit -m "feat: 起诉书/意见书提取件带原文全文（LLM定位+代码切片），4a 优先读原文"
```

---

### Task 3: C 小截断预算比例化

**Files:**
- Modify: `backend/analysis_pipeline.py`（4d 的 [:2000]、step5 的 [:3000]/[:2000]）
- Test: `tests/test_budget_integration.py`（追加断言）

- [ ] **Step 1: 写失败测试（追加）**

```python
def test_pipeline_step4d_no_perfile_hardcoded_slice():
    src = inspect.getsource(analysis_pipeline.AnalysisPipeline.step4_build_case_wiki)
    assert "content[:2000]" not in src


def test_pipeline_step5_no_perfile_hardcoded_slice():
    src = inspect.getsource(analysis_pipeline.AnalysisPipeline.step5_defense_opinion)
    assert "[:3000]" not in src
    assert "[:2000]" not in src
```

- [ ] **Step 2: 运行确认失败 → Step 3: 实现**

- 4d 证据汇总 `content[:2000]` → `content[:per_page_budget]`，其中：
```python
pages = self._list_wiki_pages("03-证据分析")
per_page_budget = max(2000, context_budget.content_budget_chars() // max(1, len(pages)))
```
- 4d 法律依据 `content[:2000]` 同理（按 04-法律依据 页数）
- step5 的 `wiki_legal` 每页 `[:3000]` 与 `wiki_evidence_summary` 每页 `[:2000]` 同理（按各自页数均摊 `content_budget_chars()`）

- [ ] **Step 4: 运行确认通过 + 无回归 → Step 5: 提交**

```bash
git add backend/analysis_pipeline.py tests/test_budget_integration.py
git commit -m "refactor: 4d/步骤5 的 Wiki 每页截断接入预算比例"
```

---

### Task 4: 冒烟（人工检查点）

- [ ] 冯叶飞案重提取起诉书所在文件（删对应 .done 标记单文件重提），验证提取件含"## 原文全文"且与原文一致；笔录类证据的原文摘录为全录
- [ ] 钱江案重跑步骤 4，确认 4a 读到原文全文
