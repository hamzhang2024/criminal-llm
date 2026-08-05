# 工作流重构 阶段 3 实现计划（改动 C 提取指引 + 改动 E 完整性校验与非证据标注）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 证据提取注入"要件+类案裁判规则"指引并标注关联要件；封面/目录/封底/备考表标注非证据（保留不删除）；提取完整性规则对账 + LLM 抽检 + 前端状态展示

**设计文档:** `docs/superpowers/specs/2026-07-31-analysis-workflow-redesign-design.md` 第 5、7 节

**分支:** `feat/analysis-workflow-redesign`（沿用）

**探查结论：**
- 提取 prompt 结构（case_manager.py:942+）：`_EVIDENCE_SYSTEM_PROMPT` + `_EVIDENCE_EXTRACTION_RULES`（assistant 固定前缀，缓存优化）+ user（charges_str + 文件内容）
- 现有 RULES 中"封面/目录必须提取为独立证据"一节**与新需求相反**，本阶段改写
- `_do_extract_evidence`（:1397+）：先解析 case_charges（case.json 或案件名推断）→ 断点续传 → 逐文件提取
- index.json：`{case_id, total_evidence, evidence: [{id, name, type, source, page_range, persons, related_entities, summary_preview, has_quotes, md_file}], generated_at}`，无文件级信息
- `_strip_cover_page`/`_strip_non_evidence_sections`（:2165-2215）是死代码
- `case_framework.fetch_case_rules(charges, keywords, size)` 已就绪；`_effective_keywords`（pipeline）可复用思路

---

### Task 1: 法律框架构建器 + 提取 prompt 注入 + 要件标注（改动 C）

**Files:**
- Create: `backend/extraction_framework.py`
- Modify: `backend/case_manager.py`（_do_extract_evidence 前置调用 + _extract_single_file 注入 + _parse_evidence_blocks/证据模板加 elements）
- Test: `tests/test_extraction_framework.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_extraction_framework.py
"""改动 C：提取指引法律框架（要件拆解 + 类案裁判规则）"""
import json
from pathlib import Path
from unittest.mock import AsyncMock

import extraction_framework
from extraction_framework import build_extraction_framework, framework_prompt_prefix


def test_framework_cached(tmp_path, monkeypatch):
    """同一案件只构建一次：缓存到 evidence/legal_framework.json"""
    calls = []

    async def fake_chat(messages, **kw):
        calls.append(messages)
        return "虚构交易\n资金支付结算\n信用卡套现"

    class FakeClient:
        chat = fake_chat

    monkeypatch.setattr(extraction_framework, "get_llm_client", lambda: FakeClient())
    monkeypatch.setattr("case_framework.fetch_case_rules",
                        lambda charges, keywords=None, size=2: {"非法经营罪": "# 类案裁判规则\n\n规则内容"})

    import asyncio
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    fw1 = asyncio.run(build_extraction_framework(evidence_dir, ["非法经营罪"], ["虚构交易"]))
    fw2 = asyncio.run(build_extraction_framework(evidence_dir, ["非法经营罪"], ["虚构交易"]))
    assert len(calls) == 1  # 第二次走缓存
    assert "虚构交易" in fw1["elements"]
    assert "非法经营罪" in fw1["case_rules"]
    assert (evidence_dir / "legal_framework.json").exists()


def test_degrades_without_llm(tmp_path, monkeypatch):
    """LLM 失败：elements 为空，流程不崩"""
    class BrokenClient:
        chat = AsyncMock(side_effect=RuntimeError("down"))

    monkeypatch.setattr(extraction_framework, "get_llm_client", lambda: BrokenClient())
    monkeypatch.setattr("case_framework.fetch_case_rules", lambda charges, keywords=None, size=2: {})

    import asyncio
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    fw = asyncio.run(build_extraction_framework(evidence_dir, ["诈骗罪"], []))
    assert fw["elements"] == []
    assert fw["case_rules"] == {}


def test_prompt_prefix_stable():
    """固定前缀包含要件与裁判规则，供 system 消息缓存"""
    fw = {
        "charges": ["非法经营罪"],
        "elements": ["虚构交易", "资金支付结算"],
        "case_rules": {"非法经营罪": "# 类案裁判规则\n\n规则A"},
    }
    prefix = framework_prompt_prefix(fw)
    assert "关联要件" in prefix
    assert "虚构交易" in prefix
    assert "规则A" in prefix
    assert "供分析参考" in prefix
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/zhanghan/.openclaw/workspace/criminal-llm && python3 -m pytest tests/test_extraction_framework.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 `backend/extraction_framework.py`**

```python
"""证据提取指引：罪名 → 构成要件 + 类案裁判规则（每案件一次，缓存复用）

设计：
- LLM 拆解要件（行为特征/情节要素），类案走 case_framework（无 Key 静默降级）
- 缓存到 evidence/legal_framework.json，重复提取/断点续传不重复调用
- 输出作为提取 prompt 的固定前缀（符合缓存优化：固定前缀在前）
"""
import json
from pathlib import Path

from llm_client import get_llm_client

CACHE_FILE = "legal_framework.json"


async def build_extraction_framework(evidence_dir: Path, charges: list[str], keywords: list[str]) -> dict:
    """构建（或读缓存）提取指引框架。返回 {charges, elements, case_rules}"""
    cache_path = Path(evidence_dir) / CACHE_FILE
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # 要件拆解（LLM，失败降级为空）
    elements: list[str] = []
    try:
        client = get_llm_client()
        charges_str = "、".join(charges)
        text = await client.chat([
            {"role": "system", "content": "你是刑事律师。请列出以下罪名在司法实践中认定时的关键要件/事实要素（行为特征、情节要素、对象特征），每行一个，5-8 个，只输出要素名。例如非法经营罪（支付结算类）：虚构交易、资金支付结算、信用卡套现、POS 机。"},
            {"role": "user", "content": f"罪名：{charges_str}"},
        ])
        elements = [line.strip("- •　 ") for line in text.strip().split("\n") if line.strip()][:8]
    except Exception as e:
        print(f"[提取指引] 要件拆解失败（降级为仅罪名）: {e}")

    # 类案裁判规则（无 Key 静默降级）
    case_rules: dict = {}
    try:
        from case_framework import fetch_case_rules
        case_rules = fetch_case_rules(charges, keywords=keywords, size=2)
    except Exception as e:
        print(f"[提取指引] 类案检索降级: {e}")

    framework = {"charges": charges, "elements": elements, "case_rules": case_rules}
    try:
        cache_path.write_text(json.dumps(framework, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return framework


def framework_prompt_prefix(framework: dict) -> str:
    """提取 prompt 固定前缀（空框架返回空串，保持原行为）"""
    elements = framework.get("elements") or []
    case_rules = framework.get("case_rules") or {}
    if not elements and not case_rules:
        return ""
    parts = ["\n\n**本案法律框架（供提取时关联标注，供分析参考）：**\n"]
    if elements:
        parts.append("关键要件（提取时为每份证据标注关联要件）：" + "、".join(elements))
    for charge, rules_md in case_rules.items():
        parts.append(f"\n{rules_md[:3000]}")
    parts.append("\n**提取要求（要件关联）：** 每份证据除标注关联罪名外，还必须从上述要件中选择关联要件（elements 字段，0-3 个）；无关联则为空列表。\n")
    return "\n".join(parts)
```

- [ ] **Step 4: case_manager.py 接入**

（a）`_do_extract_evidence` 中 case_charges 解析完成后、提取循环前：
```python
    # 提取指引法律框架（要件 + 类案裁判规则，每案件一次缓存）
    from extraction_framework import build_extraction_framework, framework_prompt_prefix
    _fw_keywords = []
    try:
        meta_for_kw = json.loads(case_json.read_text(encoding="utf-8")) if case_json.exists() else {}
        _fw_keywords = meta_for_kw.get("search_keywords") or meta_for_kw.get("suggested_keywords") or []
    except Exception:
        pass
    extraction_fw = await build_extraction_framework(evidence_dir, case_charges, _fw_keywords)
    extraction_fw_prefix = framework_prompt_prefix(extraction_fw)
    if extraction_fw_prefix:
        logger.info(f"[证据提取] 法律框架已注入（要件 {len(extraction_fw.get('elements', []))} 个，类案 {len(extraction_fw.get('case_rules', {}))} 个罪名）")
```
把 `extraction_fw_prefix` 透传到 `_extract_single_file_with_tracking` → `_extract_single_file`（加参数 `framework_prefix: str = ""`）。

（b）`_extract_single_file` 签名加 `framework_prefix: str = ""`；两处 client.chat 的 system 消息改为：
```python
{"role": "system", "content": _EVIDENCE_SYSTEM_PROMPT + "\n\n" + _EVIDENCE_EXTRACTION_RULES + framework_prefix},
```
（固定前缀仍在文件内容之前，缓存友好。）

（c）要件标注：`_EVIDENCE_EXTRACTION_RULES` 的 JSON 输出 schema 说明处（找 fields 列表）加 `elements` 字段；`_parse_evidence_blocks` 解析处加 `block["elements"] = _extract_field(...)` 或从 JSON 直取（先读该函数确认解析方式，JSON 主路径加 `.get("elements", [])`，文本标记回退路径同样补上）；证据模板（`ev_content`）的关联信息区加一行 `| **关联要件** | {、.join(ev_block.get('elements', [])) or '无'} |`；`evidence_list.append({...})` 中加 `"elements": ev_block.get("elements", [])`。

- [ ] **Step 5: 写接入测试（追加到 tests/test_extraction_framework.py）**

```python
def test_extract_single_file_injects_framework(tmp_path, monkeypatch):
    """_extract_single_file：framework_prefix 出现在 system 消息中，且 elements 透传到证据项"""
    import asyncio
    import case_manager

    seen = {}

    class FakeClient:
        async def chat(self, messages, **kw):
            seen["system"] = messages[0]["content"]
            return """```json
[{"name": "测试证据", "type": "书证", "summary": "摘要", "elements": ["虚构交易"]}]
```"""

    monkeypatch.setattr("llm_client.get_llm_client", lambda: FakeClient())
    md_file = tmp_path / "测试.md"
    md_file.write_text("## 书证\n\n内容", encoding="utf-8")
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    name, evidence_list = asyncio.run(case_manager._extract_single_file(
        md_file, "内容", temp_dir, ["非法经营罪"], framework_prefix="\n\n**本案法律框架**\n虚构交易"))
    assert "本案法律框架" in seen["system"]
    assert evidence_list[0]["elements"] == ["虚构交易"]
```

（`_extract_single_file` 中 get_llm_client 是函数内局部 import，monkeypatch `llm_client.get_llm_client` 有效——阶段 2 已验证此模式。JSON 返回格式先读 `_parse_evidence_blocks` 确认，按其真实解析路径调整 fake 返回。）

- [ ] **Step 6: 运行测试确认通过**

Run: `python3 -m pytest tests/test_extraction_framework.py -v` → `python3 -m pytest tests/ -q`（无回归）

- [ ] **Step 7: 提交**

```bash
cd /Users/zhanghan/.openclaw/workspace/criminal-llm
git add backend/extraction_framework.py backend/case_manager.py tests/test_extraction_framework.py
git commit -m "feat: 证据提取注入法律框架指引（要件+类案）+ 证据标注关联要件"
```

---

### Task 2: 文书分类与非证据标注（改动 E1）

**Files:**
- Create: `backend/doc_classifier.py`
- Modify: `backend/case_manager.py`（_do_extract_evidence 分类跳过 + index.json 加 files 字段 + RULES 封面一节改写 + 死代码删除）
- Test: `tests/test_doc_classifier.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_doc_classifier.py
"""文书分类：封面/目录/封底/备考表标注非证据（保留不删除），拿不准归证据"""
from unittest.mock import AsyncMock

import asyncio

import doc_classifier
from doc_classifier import classify_document, NON_EVIDENCE_TYPES


def classify(name, head):
    return asyncio.run(classify_document(name, head))


def test_filename_rules_hit():
    assert classify("第1卷封面_去水印.md", "") == "non_evidence:封面"
    assert classify("卷内目录_去水印.md", "") == "non_evidence:目录"
    assert classify("封底_去水印.md", "") == "non_evidence:封底"
    assert classify("备考表_去水印.md", "") == "non_evidence:备考表"


def test_normal_file_is_evidence():
    assert classify("讯问笔录_去水印.md", "## 讯问笔录\n时间：...") == "evidence"


def test_uncertain_defaults_to_evidence():
    """内容不明时宁可误提取，不误标非证据"""
    assert classify("第2卷_去水印.md", "## 一些内容") == "evidence"


def test_cover_by_content(monkeypatch):
    """文件名无特征但内容是封面：LLM 兜底判定"""
    monkeypatch.setattr(doc_classifier, "_llm_classify",
                        AsyncMock(return_value="non_evidence:封面"))
    assert classify("第1卷_去水印.md", "# 刑事侦查卷宗\n某公安局") == "non_evidence:封面"


def test_llm_uncertain_defaults_evidence(monkeypatch):
    monkeypatch.setattr(doc_classifier, "_llm_classify", AsyncMock(return_value="evidence"))
    assert classify("第3卷_去水印.md", "## 不明文书") == "evidence"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_doc_classifier.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 `backend/doc_classifier.py`**

```python
"""文书分类：证据 / 非证据（封面/目录/封底/备考表）

原则：
- 非证据文件**保留在原目录与文件列表**，不入提取流程（案卷完整性，永不删除）
- 宁可误提取，不误标非证据：规则需高置信，拿不准归证据
- 文件名规则优先，LLM 兜底（只看开头 500 字，一次调用）
"""
import re

NON_EVIDENCE_TYPES = ("封面", "目录", "封底", "备考表")

_FILENAME_RULES = [
    (re.compile(r"封面"), "封面"),
    (re.compile(r"卷内.*目录|文书目录|(?<!卷内)目录"), "目录"),
    (re.compile(r"封底"), "封底"),
    (re.compile(r"备考表"), "备考表"),
]

_CONTENT_HINT = re.compile(r"刑事侦查卷宗|卷内文书目录|备\s*考\s*表|封\s*底")


async def classify_document(filename: str, first_500_chars: str) -> str:
    """返回 "evidence" 或 "non_evidence:<subtype>"

    文件名规则优先；内容高度吻合封面特征才走 LLM 兜底，否则直接归证据。
    """
    for pattern, subtype in _FILENAME_RULES:
        if pattern.search(filename):
            return f"non_evidence:{subtype}"
    if not _CONTENT_HINT.search(first_500_chars or ""):
        return "evidence"
    return await _llm_classify(filename, first_500_chars)


async def _llm_classify(filename: str, first_500_chars: str) -> str:
    """LLM 兜底判定（只认高置信非证据，其余归证据）"""
    from llm_client import get_llm_client
    client = get_llm_client()
    try:
        result = await client.chat([
            {"role": "system", "content": "你是案卷整理员。判断文书类型，只回答一个词：封面 / 目录 / 封底 / 备考表 / 证据。只有非常确定是程序性封面、卷内目录、封底或备考表时才回答前四项，否则一律回答"证据"。"},
            {"role": "user", "content": f"文件名：{filename}\n\n开头内容：\n{first_500_chars[:500]}"},
        ])
        result = result.strip().strip("。.")
        if result in NON_EVIDENCE_TYPES:
            return f"non_evidence:{result}"
    except Exception:
        pass
    return "evidence"
```

- [ ] **Step 4: case_manager.py 接入**

（a）`_do_extract_evidence` 的提取循环（`_sort_md_files` 之后）加分类与跳过：
```python
    # 文书分类：非证据（封面/目录/封底/备考表）标注后跳过提取（文件保留）
    from doc_classifier import classify_document
    file_classifications = {}  # filename -> doc_type
    files_to_extract = []
    for f in md_files_sorted:
        doc_type = await classify_document(f.name, f.read_text(encoding="utf-8")[:500])
        file_classifications[f.name] = doc_type
        if doc_type.startswith("non_evidence"):
            logger.info(f"[证据提取] {f.name} 标注为非证据（{doc_type.split(':')[1]}），保留文件不入提取")
            # 非证据也计入进度，避免进度条缺格
            task = EXTRACT_TASKS.get(case_id)
            if task:
                task["processed_files"] = task.get("processed_files", 0) + 1
        else:
            files_to_extract.append(f)
```
（变量名 `md_files_sorted` 按现场调整；后续提取循环改用 `files_to_extract`。）

（b）index.json 写入处（找 `index_data = {...}` 或 json.dump(index...)）顶层加 `"files": [{"name": n, "doc_type": t} for n, t in file_classifications.items()]`（保持既有 evidence 结构不变；重提时 files 需与当次分类合并更新）。

（c）`_EVIDENCE_EXTRACTION_RULES` 的"### 封面/目录/三面照"一节改写：非证据文件已在提取前分流，LLM 侧规则同步改为：
```
### 封面/目录/三面照

封面、卷内目录、封底、备考表已在提取前标注为非证据，不在你的输入中。
若输入中仍混入此类内容，跳过不提取。嫌疑人三面照/照片是证据，提取为"嫌疑人三面照"。
```

（d）删除死代码：`_strip_cover_page` 和 `_strip_non_evidence_sections`（:2165-2215）整段删除（grep 全库确认无调用）。

- [ ] **Step 5: 写接入测试（追加）**

```python
def test_rules_no_longer_require_cover_extraction():
    """提取规则不再要求把封面目录提取为证据"""
    import inspect
    src = inspect.getsource(case_manager)
    assert "必须提取为独立证据，不得遗漏。\n\n- 封面" not in src
    assert "已在提取前标注为非证据" in src
```

- [ ] **Step 6: 运行测试确认通过**

Run: `python3 -m pytest tests/test_doc_classifier.py -v` → `python3 -m pytest tests/ -q`（无回归）

- [ ] **Step 7: 提交**

```bash
git add backend/doc_classifier.py backend/case_manager.py tests/test_doc_classifier.py
git commit -m "feat: 文书分类与非证据标注（封面/目录/封底/备考表保留不入提取）"
```

---

### Task 3: 完整性校验（改动 E2）

**Files:**
- Create: `backend/completeness.py`
- Modify: `backend/case_manager.py`（提取完成后调用 + API 端点 GET /api/cases/{case_id}/evidence/completeness）
- Test: `tests/test_completeness.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_completeness.py
"""完整性校验：编号项规则对账 + LLM 抽检关键文书"""
from unittest.mock import AsyncMock

import completeness
from completeness import reconcile_numbered_items, check_completeness


def test_reconcile_numbered_items_full_coverage():
    source = "犯罪事实：\n一、2023年1月盗窃手机\n二、2023年2月盗窃电脑\n三、2023年3月盗窃现金"
    extracted = ["盗窃手机", "盗窃电脑", "盗窃现金"]
    result = reconcile_numbered_items(source, extracted)
    assert result["source_items"] == 3
    assert result["covered"] == 3
    assert result["missing"] == []


def test_reconcile_numbered_items_missing():
    source = "一、2023年1月盗窃手机\n二、2023年2月盗窃电脑"
    extracted = ["盗窃手机"]
    result = reconcile_numbered_items(source, extracted)
    assert result["covered"] == 1
    assert len(result["missing"]) == 1
    assert "电脑" in result["missing"][0]


def test_reconcile_bi_and_qi_patterns():
    source = "第一笔：诈骗甲公司50万元\n第二笔：诈骗乙公司30万元"
    extracted = ["诈骗甲公司", "诈骗乙公司"]
    result = reconcile_numbered_items(source, extracted)
    assert result["source_items"] == 2
    assert result["covered"] == 2


def test_check_completeness_report(tmp_path, monkeypatch):
    """生成完整性报告：每文件状态 + LLM 抽检关键文书"""
    monkeypatch.setattr(completeness, "_llm_spot_check",
                        AsyncMock(return_value={"covered": True, "missing_items": []}))
    files = {
        "起诉意见书.md": "一、盗窃手机\n二、盗窃电脑",
        "讯问笔录.md": "问：你干了什么？答：盗窃了手机和电脑。",
    }
    extracted_by_file = {
        "起诉意见书.md": ["盗窃手机", "盗窃电脑"],
        "讯问笔录.md": ["盗窃手机", "盗窃电脑"],
    }
    import asyncio
    report = asyncio.run(completeness.check_completeness(files, extracted_by_file))
    assert report["files"]["起诉意见书.md"]["status"] == "ok"
    assert report["files"]["起诉意见书.md"]["llm_checked"] is True
    assert report["files"]["讯问笔录.md"]["llm_checked"] is False
    assert report["summary"]["ok"] >= 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_completeness.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 `backend/completeness.py`**

```python
"""提取完整性校验：规则对账 + LLM 抽检关键文书

- 规则对账（全文件，零 LLM 成本）：编号项（第X笔/第X起/中文序号）与提取清单核对
- LLM 抽检（仅起诉书/起诉意见书/判决书）：一次调用确认逐笔覆盖
- 报告存 evidence/completeness_report.json；LLM 与规则冲突时以 LLM 为准并标注人工复核
"""
import re
from pathlib import Path

KEY_DOC_PATTERN = re.compile(r"起诉书|起诉意见书|判决书")

_CN_NUM = "一二三四五六七八九十"
_ITEM_PATTERNS = [
    re.compile(rf"^[\s>*-]*([{_CN_NUM}]+)、(.+)$", re.M),          # 一、xxx
    re.compile(r"第([一二三四五六七八九十\d]+)[笔起][：:](.+)$", re.M),  # 第一笔：xxx / 第3起：xxx
    re.compile(r"^[\s>*-]*(\d+)[.、]\s*(.+)$", re.M),               # 1. xxx
]


def _extract_numbered_items(source: str) -> list[str]:
    """从原文提取编号项（去重，保留顺序）"""
    items: list[str] = []
    for pattern in _ITEM_PATTERNS:
        for m in pattern.finditer(source):
            title = m.group(m.lastindex).strip()
            if len(title) >= 4 and title not in items:
                items.append(title)
    return items


def _covered(item: str, extracted: list[str]) -> bool:
    """条目关键词与提取清单重合度判断（取条目前 4-10 字关键词）"""
    keywords = [kw for kw in re.split(r"[，,、\s]", item) if len(kw) >= 2]
    for ev in extracted:
        for kw in keywords[:3]:
            if kw[:6] in ev or kw in ev:
                return True
    return False


def reconcile_numbered_items(source: str, extracted: list[str]) -> dict:
    """规则对账：返回 {source_items, covered, missing}"""
    items = _extract_numbered_items(source)
    missing = [it for it in items if not _covered(it, extracted)]
    return {
        "source_items": len(items),
        "covered": len(items) - len(missing),
        "missing": missing,
    }


async def _llm_spot_check(source: str, extracted: list[str]) -> dict:
    """LLM 抽检关键文书：返回 {covered, missing_items}"""
    from llm_client import get_llm_client
    client = get_llm_client()
    extracted_str = "\n".join(f"- {e}" for e in extracted[:50])
    result = await client.chat([
        {"role": "system", "content": "你是案卷审查员。对照原文与提取清单，判断原文列出的每笔事实是否都被覆盖。只输出 JSON：{\"covered\": true/false, \"missing_items\": [\"遗漏的笔数简述\"]}"},
        {"role": "user", "content": f"## 原文（编号事实）\n{source[:20000]}\n\n## 提取清单\n{extracted_str}"},
    ])
    import json as _json
    m = re.search(r"\{.*\}", result, re.S)
    if m:
        return _json.loads(m.group(0))
    return {"covered": True, "missing_items": []}


async def check_completeness(files: dict, extracted_by_file: dict) -> dict:
    """全量完整性校验。files: {文件名: 原文}；extracted_by_file: {文件名: [证据名]}"""
    report = {"files": {}, "summary": {"ok": 0, "suspect": 0, "failed": 0}}
    for fname, source in files.items():
        extracted = extracted_by_file.get(fname, [])
        rec = reconcile_numbered_items(source, extracted)
        is_key = bool(KEY_DOC_PATTERN.search(fname))
        entry = {
            "source_items": rec["source_items"],
            "covered": rec["covered"],
            "missing": rec["missing"],
            "llm_checked": False,
        }
        if is_key:
            try:
                spot = await _llm_spot_check(source, extracted)
                entry["llm_checked"] = True
                if not spot.get("covered", True):
                    entry["missing"] = spot.get("missing_items", rec["missing"])
                    entry["needs_review"] = True
            except Exception:
                pass
        # 状态判定：无编号项的文件不做遗漏判定
        if rec["source_items"] == 0:
            status = "ok"
        elif entry["missing"]:
            status = "suspect"
        else:
            status = "ok"
        entry["status"] = status
        report["files"][fname] = entry
        report["summary"][status if status != "suspect" else "suspect"] += 1
    return report
```

- [ ] **Step 4: case_manager.py 接入**

（a）提取全部完成、index.json 写盘后（`_do_extract_evidence` 末尾，找 index.json 写入点之后）：
```python
    # 完整性校验（规则对账 + LLM 抽检关键文书）
    try:
        from completeness import check_completeness, KEY_DOC_PATTERN
        source_texts = {}
        extracted_by_file = {}
        for f in files_to_extract:
            source_texts[f.name] = f.read_text(encoding="utf-8")
        for ev in index_data.get("evidence", []):
            extracted_by_file.setdefault(ev.get("source", ""), []).append(ev.get("name", ""))
        completeness_report = await check_completeness(source_texts, extracted_by_file)
        (evidence_dir / "completeness_report.json").write_text(
            json.dumps(completeness_report, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"[证据提取] 完整性校验: {completeness_report['summary']}")
    except Exception as e:
        logger.warning(f"[证据提取] 完整性校验失败（不影响提取结果）: {e}")
```

（b）新 API 端点（case_manager 的路由区，参照 get_evidence_index）：
```python
@router.get("/{case_id}/evidence/completeness")
async def get_evidence_completeness(case_id: str):
    """提取完整性报告"""
    case_path = find_case_path(case_id)
    if not case_path:
        raise HTTPException(status_code=404, detail="案件不存在")
    report_file = case_path / "evidence" / "completeness_report.json"
    if not report_file.exists():
        return {"files": {}, "summary": {}}
    return json.loads(report_file.read_text(encoding="utf-8"))
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python3 -m pytest tests/test_completeness.py -v` → `python3 -m pytest tests/ -q`（无回归）

- [ ] **Step 6: 提交**

```bash
git add backend/completeness.py backend/case_manager.py tests/test_completeness.py
git commit -m "feat: 提取完整性校验（规则对账+LLM抽检关键文书）"
```

---

### Task 4: 前端证据列表徽标与状态点

**Files:**
- Modify: `frontend/src/api/cases.ts`（或新建 evidence api 函数）
- Modify: `frontend/src/pages/CaseDetailPage`（证据列表：非证据徽标 + 完整性状态点）
- Modify: `frontend/src/pages/ReportPage.tsx`（证据列表 tab 同步徽标）

- [ ] **Step 1: API 封装**

在 `frontend/src/api/` 合适位置（参照 caseSearch.ts 模式）加：
```ts
export interface CompletenessEntry {
  source_items: number
  covered: number
  missing: string[]
  llm_checked: boolean
  needs_review?: boolean
  status: 'ok' | 'suspect' | 'failed'
}

export interface CompletenessReport {
  files: Record<string, CompletenessEntry>
  summary: Record<string, number>
}

export async function getEvidenceCompleteness(caseId: string): Promise<CompletenessReport> {
  const res = await fetch(`${API_BASE}/cases/${caseId}/evidence/completeness`)
  return res.json()
}
```
（getEvidenceIndex 现有函数若已返回 files 字段（index.json 顶层新增），doc_type 直接可用；确认 index.json 的 evidence 响应包含 files。）

- [ ] **Step 2: CaseDetailPage 证据列表改造**

先读证据列表渲染处（找证据/文件列表组件）：
- 文件名旁加徽标：doc_type 为 non_evidence:* 时显示灰色"非证据（封面）"小徽标，条目整体降透明度
- 文件名旁加完整性状态点：绿(ok) / 黄(suspect，title 显示 missing 数与"建议人工复核") / 灰(无报告)
- 列表顶部加完整性摘要行（如"54 份证据完整 · 1 份疑似遗漏 · 3 份非证据"）

- [ ] **Step 3: ReportPage 证据列表 tab 同步**

证据列表 tab 中文件来源（source）处同步非证据徽标（读 ReportPage 证据列表渲染处，复用同一徽标小组件——抽到 `components/report/EvidenceBadge.tsx`？CaseDetailPage 已有 EvidenceBadge 用于合法性评分，命名避让，用 `DocTypeBadge`）

- [ ] **Step 4: 验证**

```bash
cd frontend && npx tsc --noEmit && npm run build
```

- [ ] **Step 5: 提交**

```bash
git add frontend/src/api/ frontend/src/pages/ frontend/src/components/
git commit -m "feat: 证据列表非证据徽标与完整性状态点"
```

---

### Task 5: 真实案件冒烟（人工检查点）

- [ ] **Step 1:** 选一个含封面/目录文件的案件（如冯叶飞案），重跑证据提取（先备份 evidence 目录），验证：封面文件标注 non_evidence 且保留、提取 prompt 含法律框架、证据含 elements、completeness_report.json 生成
- [ ] **Step 2:** 前端两页面查看徽标与状态点显示正确

---

## 执行顺序

Task 1（改动 C）→ Task 2（文书分类）→ Task 3（完整性）→ Task 4（前端）→ Task 5（冒烟）
