# 工作流重构 阶段 1 实现计划（改动 F 矛盾边界 + 改动 A 4c 前移+类案）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 矛盾分析边界厘清（纯 prompt）+ 步骤 4 顺序改为 4a→4c→4b→4d，4c 扩展为"法条+司法解释+类案裁判规则"，4b 注入法律框架

**Architecture:** 全部改动在 criminal-llm/backend/analysis_pipeline.py + 新建 backend/case_framework.py。类案检索走本地代理已有的云端配置（_service_config），失败静默降级。

**设计文档:** `docs/superpowers/specs/2026-07-31-analysis-workflow-redesign-design.md` 第 3、8 节

**分支:** `feat/analysis-workflow-redesign`（已创建）

**执行中发现的既有 bug（本计划修复）：**
- `analysis_pipeline.py:1123` `_search_legal_knowledge(charges)`：`charges` 在 step4 作用域未定义（NameError）
- `_search_legal_knowledge` 方法（:893）参数名 `charges` 但方法体用未定义的 `crime_type`（调用即 NameError）
- 两处叠加导致 4c 对新案件必然崩溃（旧案件因 `适用法条.md` 已存在而走 skipped 分支从未触发）

---

### Task 1: 矛盾分析边界厘清（改动 F，纯 prompt）

**Files:**
- Modify: `backend/analysis_pipeline.py`（step3 prompt + step4 的 4b prompt）
- Test: `tests/test_phase1_boundary.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_phase1_boundary.py
"""改动 F：矛盾分析边界——步骤 3 只做供述内矛盾，4b 只管证据间矛盾"""
import inspect
import analysis_pipeline


def test_step3_prompt_declares_internal_contradiction_only():
    src = inspect.getsource(analysis_pipeline.AnalysisPipeline.step3_internal_contradiction)
    assert "供述内矛盾" in src
    assert "不分析不同证据之间的矛盾" in src


def test_step4b_prompt_declares_inter_evidence_contradiction():
    src = inspect.getsource(analysis_pipeline.AnalysisPipeline.step4_build_case_wiki)
    assert "证据间矛盾" in src
    assert "同一人口供前后矛盾已在步骤 3 完成" in src
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/zhanghan/.openclaw/workspace/criminal-llm && python3 -m pytest tests/test_phase1_boundary.py -v`
Expected: FAIL（两个断言均不满足）

- [ ] **Step 3: 实现——step3 的 user prompt（约 :827 "请逐维度对比每次笔录的差异" 段）开头加一句**

在 step3 的 user prompt 中 `{person}共有{session_count}次{etype}...` 之后、"请逐维度对比"之前加：

```
注意：本步骤仅分析同一人口供/证言的前后矛盾（供述内矛盾），不分析不同证据之间的矛盾（证据间矛盾在步骤 4 处理）。
```

- [ ] **Step 4: 实现——4b 的 user prompt（约 :1082 "请分析：" 段）第 3 点修改**

4b user prompt 的分析要求第 3 点 `3. 与其他已分析证据的关系（印证/矛盾/补充）` 改为：

```
3. 与其他已分析证据的关系（印证/矛盾/补充）——此处只关注不同证据之间的矛盾（证据间矛盾）；同一人口供前后矛盾已在步骤 3 完成，不要重复分析
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python3 -m pytest tests/test_phase1_boundary.py -v`
Expected: 2 passed

- [ ] **Step 6: 提交**

```bash
cd /Users/zhanghan/.openclaw/workspace/criminal-llm
git add backend/analysis_pipeline.py tests/test_phase1_boundary.py
git commit -m "refactor: 矛盾分析边界厘清（步骤3供述内/步骤4证据间）"
```

---

### Task 2: 类案裁判规则检索模块（改动 A 之一）

**Files:**
- Create: `backend/case_framework.py`
- Test: `tests/test_case_framework.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_case_framework.py
import pytest
import case_framework
from case_framework import fetch_case_rules


class FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


def _patch(monkeypatch, search_payload, cards):
    monkeypatch.setattr(case_framework, "_service_config", lambda: ("http://cloud", "cca_x"))

    class FakeRequests:
        calls = []

        @staticmethod
        def get(url, params=None, headers=None, timeout=None):
            FakeRequests.calls.append(params)
            return FakeResp(200, search_payload)

    monkeypatch.setattr(case_framework, "requests", FakeRequests)
    monkeypatch.setattr(case_framework, "fetch_case_cards", lambda nos: cards)
    return FakeRequests


def test_no_key_returns_empty(monkeypatch):
    monkeypatch.setattr(case_framework, "_service_config", lambda: ("http://cloud", ""))
    assert fetch_case_rules(["盗窃罪"]) == {}


def test_rules_formatted_with_disclaimer(monkeypatch):
    cards = [{
        "case_no": "第1000号", "title": "李某甲等寻衅滋事案",
        "issue": "未成年人多次强取财物如何处理",
        "holding_summary": "未成年人轻微暴力强索少量财物定寻衅滋事。",
        "reasoning_excerpt": "本案审理中存在两种意见……",
    }]
    _patch(monkeypatch, {"results": [{"case_no": "第1000号"}]}, cards)
    rules = fetch_case_rules(["寻衅滋事罪"])
    md = rules["寻衅滋事罪"]
    assert "自动检索，供分析参考" in md
    assert "【第1000号】李某甲等寻衅滋事案" in md
    assert "未成年人轻微暴力强索少量财物定寻衅滋事。" in md
    assert "本案审理中存在两种意见" in md


def test_charge_filter_and_size(monkeypatch):
    fr = _patch(monkeypatch, {"results": []}, [])
    fetch_case_rules(["盗窃罪"], size=3)
    assert fr.calls[0]["charge"] == "盗窃罪"
    assert fr.calls[0]["size"] == 3


def test_keywords_joined_into_query(monkeypatch):
    fr = _patch(monkeypatch, {"results": []}, [])
    fetch_case_rules(["寻衅滋事罪"], keywords=["未成年人", "轻微暴力"])
    assert fr.calls[0]["q"] == "未成年人 轻微暴力"
    assert fr.calls[0]["charge"] == "寻衅滋事罪"


def test_zero_results_skipped(monkeypatch):
    _patch(monkeypatch, {"results": []}, [])
    assert fetch_case_rules(["不存在罪"]) == {}


def test_connection_error_stops_remaining_charges(monkeypatch):
    monkeypatch.setattr(case_framework, "_service_config", lambda: ("http://cloud", "cca_x"))

    class BrokenRequests:
        @staticmethod
        def get(*a, **kw):
            raise case_framework.requests.RequestException("down")

    monkeypatch.setattr(case_framework, "requests", BrokenRequests)
    assert fetch_case_rules(["盗窃罪", "诈骗罪"]) == {}


def test_search_http_error_continues_next_charge(monkeypatch):
    monkeypatch.setattr(case_framework, "_service_config", lambda: ("http://cloud", "cca_x"))
    seen = []

    class MixedRequests:
        @staticmethod
        def get(url, params=None, headers=None, timeout=None):
            seen.append(params["charge"])
            if params["charge"] == "盗窃罪":
                return FakeResp(500, {})
            return FakeResp(200, {"results": [{"case_no": "第1号"}]})

    cards = [{"case_no": "第1号", "title": "甲案", "issue": "i", "holding_summary": "h", "reasoning_excerpt": "r"}]
    monkeypatch.setattr(case_framework, "requests", MixedRequests)
    monkeypatch.setattr(case_framework, "fetch_case_cards", lambda nos: cards)
    rules = fetch_case_rules(["盗窃罪", "诈骗罪"])
    assert "盗窃罪" not in rules
    assert "诈骗罪" in rules
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_case_framework.py -v`
Expected: FAIL（ModuleNotFoundError: case_framework）

- [ ] **Step 3: 实现 `backend/case_framework.py`**

```python
"""类案裁判规则检索：按罪名从案例库拉取 top N 卡片

定位：自动检索，供分析参考；报告正式引用仍需人工确认（可核验性底线）。
降级：无 API Key / 检索失败 / 0 结果 → 静默跳过（返回空），法条路径照常。
"""
import requests

from case_search_api import _service_config, fetch_case_cards, TIMEOUT

DISCLAIMER = "# 类案裁判规则（自动检索，供分析参考，正式引用需人工确认）\n\n"


def _format_rules_md(cards: list[dict]) -> str:
    md = DISCLAIMER
    for c in cards:
        md += f"## 【{c.get('case_no', '')}】{c.get('title', '')}\n\n"
        md += f"- 主要问题：{c.get('issue', '')}\n"
        md += f"- 裁判要旨：{c.get('holding_summary', '')}\n"
        md += f"- 裁判理由摘录：{c.get('reasoning_excerpt', '')}\n\n"
    return md


def fetch_case_rules(charges: list[str], keywords: list[str] | None = None, size: int = 3) -> dict[str, str]:
    """按罪名检索类案卡片并格式化为 Markdown，返回 {罪名: md}。

    - keywords：用户确认/LLM 推荐的案件特征关键词（如"未成年人""轻微暴力"），
      与罪名过滤组合检索；空则仅罪名过滤
    - 单罪名 HTTP 错误：跳过该罪名继续下一个
    - 连接级失败（云端不可达）：终止剩余罪名
    - 无 API Key / 0 结果：静默降级
    """
    base, key = _service_config()
    if not key:
        return {}
    q = " ".join(keywords) if keywords else ""
    rules: dict[str, str] = {}
    for charge in charges:
        try:
            resp = requests.get(
                f"{base}/api/cases/search",
                params={"charge": charge, "q": q, "size": size},
                headers={"X-API-Key": key},
                timeout=TIMEOUT,
            )
        except requests.RequestException:
            break
        if resp.status_code != 200:
            continue
        case_nos = [r["case_no"] for r in resp.json().get("results", [])]
        if not case_nos:
            continue
        cards = fetch_case_cards(case_nos)
        if cards:
            rules[charge] = _format_rules_md(cards)
    return rules
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_case_framework.py -v`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add backend/case_framework.py tests/test_case_framework.py
git commit -m "feat: 类案裁判规则检索模块（自动检索+静默降级）"
```

---

### Task 3: 步骤 4 顺序调换 + 4c 扩展 + 4b 注入法律框架（改动 A 主体）

**Files:**
- Modify: `backend/analysis_pipeline.py`（step4_build_case_wiki 重构 + _search_legal_knowledge 签名修复）
- Test: `tests/test_step4_reorder.py`

**背景（实现者必读）：** 当前 step4_build_case_wiki（analysis_pipeline.py:952-1260 附近）顺序为 4a→4b→4c→4d。目标改为 4a→4c→4b→4d。4c 代码块（"===== 4c: 法律依据检索 =====" 到 4d 之前）整体移到 4b 代码块（"===== 4b: 逐人证据摄入（串行） ====="）之前。同时：

1. SUB_STEPS 改为 `["4a-指控要素", "4c-法律框架", "4b-证据摄入", "4d-综合结论"]`，sub_done 递增顺序同步调整，progress_cb 文案同步
2. 修复 NameError：`legal = self._search_legal_knowledge(charges)` 改为传 crime_type；`_search_legal_knowledge(self, charges: list = None)` 签名改为 `(self, crime_type: str | None = None)`
3. 4c 末尾（适用法条.md 保存后）新增类案裁判规则：按罪名逐篇存 `04-法律依据/类案裁判规则-<罪名>.md`
4. 4b user prompt 注入法律框架（见下方代码）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_step4_reorder.py
"""改动 A：步骤 4 顺序 4a→4c→4b→4d + 4c 类案扩展 + 4b 注入法律框架"""
import asyncio
import inspect
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import analysis_pipeline
from analysis_pipeline import AnalysisPipeline


def _make_pipeline(tmp_path: Path) -> AnalysisPipeline:
    """构造带最小 step1/step2 产物的 pipeline

    已确认的构造事实（analysis_pipeline.py:215-222）：
    - __init__(case_id, case_dir, indictment_file=None)
    - self.analysis_dir = case_dir / "analysis"（直接在 case_dir 下，无中间子目录）
    """
    case_path = tmp_path / "case_001"
    analysis_dir = case_path / "analysis"
    (analysis_dir / "summaries" / "讯问笔录").mkdir(parents=True)
    (analysis_dir / "preprocess").mkdir(parents=True)
    # step1 产物
    (analysis_dir / "step_1_result.json").write_text(json.dumps({
        "merged_files": [{"person": "张三", "type": "讯问笔录", "session_count": 1}]
    }), encoding="utf-8")
    # step2 产物
    (analysis_dir / "step_2_result.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    # 总结文件（4b 读取）
    (analysis_dir / "summaries" / "讯问笔录" / "张三_共1次_总结.md").write_text(
        "张三供述：盗窃财物若干。", encoding="utf-8")
    pipe = AnalysisPipeline("case_001", case_path)
    return pipe


def test_step4_order_4c_before_4b(tmp_path, monkeypatch):
    """4c 的 LLM 调用必须发生在 4b 之前；4b prompt 含法律框架"""
    pipe = _make_pipeline(tmp_path)
    calls = []

    async def fake_chat(messages, **kw):
        user = messages[-1]["content"]
        calls.append(user)
        return "分析结果"

    pipe.llm.chat = fake_chat
    monkeypatch.setattr(analysis_pipeline.AnalysisPipeline, "_find_indictment_in_md_files",
                        AsyncMock(return_value=("起诉书内容：指控张三盗窃。", "起诉书")))
    monkeypatch.setattr("case_framework.fetch_case_rules", lambda charges, keywords=None, size=3: {"盗窃罪": "# 类案裁判规则\n\n盗窃裁判规则内容"})

    asyncio.run(pipe.step4_build_case_wiki("张三", "盗窃罪"))

    # 找到 4c（法律依据）与 4b（待分析证据）的调用位置
    idx_4c = next(i for i, c in enumerate(calls) if "从刑法知识库检索到的法条" in c)
    idx_4b = next(i for i, c in enumerate(calls) if "待分析证据" in c)
    assert idx_4c < idx_4b, "4c 必须先于 4b"

    # 4b prompt 注入法律框架（含类案裁判规则）
    prompt_4b = calls[idx_4b]
    assert "法律框架" in prompt_4b
    assert "盗窃裁判规则内容" in prompt_4b

    # 类案裁判规则按罪名存盘
    wiki = tmp_path / "case_001" / "analysis" / "indictment_wiki" / "04-法律依据"
    assert (wiki / "类案裁判规则-盗窃罪.md").exists()


def test_suggested_keywords_saved_and_used(tmp_path, monkeypatch):
    """4a 后 LLM 推荐关键词存 case.json；4c 检索使用有效关键词（用户编辑优先）"""
    pipe = _make_pipeline(tmp_path)
    captured = {}

    async def fake_chat(messages, **kw):
        user = messages[-1]["content"]
        if "类案检索关键词" in messages[0]["content"]:
            return "未成年人\n轻微暴力\n多次作案"
        return "分析结果"

    pipe.llm.chat = fake_chat
    monkeypatch.setattr(analysis_pipeline.AnalysisPipeline, "_find_indictment_in_md_files",
                        AsyncMock(return_value=("起诉书内容：指控张三盗窃。", "起诉书")))

    def fake_rules(charges, keywords=None, size=3):
        captured["keywords"] = keywords
        return {}

    monkeypatch.setattr("case_framework.fetch_case_rules", fake_rules)
    asyncio.run(pipe.step4_build_case_wiki("张三", "盗窃罪"))

    meta = json.loads((tmp_path / "case_001" / "case.json").read_text(encoding="utf-8"))
    assert meta["suggested_keywords"] == ["未成年人", "轻微暴力", "多次作案"]
    assert captured["keywords"] == ["未成年人", "轻微暴力", "多次作案"]


def test_step4_case_rules_failure_degrades(tmp_path, monkeypatch):
    """类案检索失败：静默跳过，法条路径照常"""
    pipe = _make_pipeline(tmp_path)

    async def fake_chat(messages, **kw):
        return "分析结果"

    pipe.llm.chat = fake_chat
    monkeypatch.setattr(analysis_pipeline.AnalysisPipeline, "_find_indictment_in_md_files",
                        AsyncMock(return_value=("起诉书内容", "起诉书")))
    monkeypatch.setattr("case_framework.fetch_case_rules", lambda charges, keywords=None, size=3: {})

    asyncio.run(pipe.step4_build_case_wiki("张三", "盗窃罪"))
    wiki = tmp_path / "case_001" / "analysis" / "indictment_wiki" / "04-法律依据"
    assert (wiki / "适用法条.md").exists()
    assert not list(wiki.glob("类案裁判规则-*.md"))
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_step4_reorder.py -v`
Expected: FAIL（idx_4c > idx_4b / 无"法律框架" / 无类案文件）

- [ ] **Step 3: 实现顺序调换与 4c 扩展**

（a）把 "===== 4c: 法律依据检索 =====" 整段（含 `sub_done = 3` 及其 progress_cb）移到 "===== 4b: 逐人证据摄入（串行） =====" 之前，调整 sub_done 序列为 4a=1、4c=2、4b=3、4d=4，progress_cb 文案改为"法律框架构建"等对应新顺序的描述。

（b）修复 NameError：4c 内 `legal = self._search_legal_knowledge(charges)` 改为 `legal = self._search_legal_knowledge(crime_type)`；`_search_legal_knowledge` 签名改为 `def _search_legal_knowledge(self, crime_type: str | None = None) -> dict:`（方法体本来就用 crime_type，不需改）。

（c）4a 完成后（`01-指控要素.md` 保存后、且为新分析非 skipped）新增 LLM 推荐关键词：

```python
            # LLM 推荐类案检索关键词（罪名除外），存 case.json suggested_keywords
            try:
                kw_text = await self.llm.chat([
                    {"role": "system", "content": "你是刑事律师。请从指控要素分析中提取 3-5 个类案检索关键词（不要包含罪名本身），聚焦行为特征、情节要素、对象特征，每行一个，只输出关键词。"},
                    {"role": "user", "content": indictment_content[:5000]},
                ])
                suggested = [line.strip("- •　 ") for line in kw_text.strip().split("\n") if line.strip()][:5]
                if suggested:
                    self._save_suggested_keywords(suggested)
            except Exception as e:
                print(f"[步骤 4a] 关键词推荐失败（不影响主流程）: {e}")
```

配套辅助方法（与 _case_charges 放一起）：

```python
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
```

（d）4c 扩展（适用法条.md 的 try/except 块之后、else 分支前）：

```python
            # 类案裁判规则（自动检索，供分析参考；失败静默降级）
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
```

（e）新增辅助方法（放在 _search_legal_knowledge 附近）：

```python
    def _case_charges(self, crime_type: Optional[str] = None) -> list[str]:
        """案件罪名列表：优先 case.json 的 charges，回退 crime_type 单罪名"""
        meta_file = self.case_dir / "case.json"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                charges = meta.get("charges") or []
                if charges:
                    return charges
            except Exception:
                pass
        return [crime_type] if crime_type else []
```

（f）4b prompt 注入法律框架：4b 循环开始前（`analyzed_evidence = []` 附近）加载：

```python
        # 法律框架（4c 产物：法条 + 司法解释 + 类案裁判规则）
        legal_framework = ""
        for lf in self._list_wiki_pages("04-法律依据"):
            legal_framework += f"\n### {lf}\n{self._load_wiki_page('04-法律依据', lf)[:2000]}\n"
```

4b 的 user prompt 在 `## 指控要素` 段之后、`## 待分析证据` 段之前插入：

```
## 法律框架（法条 + 司法解释 + 类案裁判规则）
{legal_framework if legal_framework else '无'}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_step4_reorder.py tests/test_phase1_boundary.py tests/test_case_framework.py -v`
Expected: 全部通过；`python3 -m pytest tests/ -v` 无回归

- [ ] **Step 5: 提交**

```bash
git add backend/analysis_pipeline.py tests/test_step4_reorder.py
git commit -m "feat: 步骤4顺序改为4a→4c→4b→4d，4c扩展类案裁判规则，4b注入法律框架"
```

---

### Task 4: 真实案件冒烟（人工检查点）

- [ ] **Step 1: 本地启动后端，对一个测试案件只重跑步骤 4（先备份该案件 analysis 目录），确认：Wiki 中 04-法律依据 含类案裁判规则文件、4b 证据分析引用了法律框架、断点续传正常（重跑全部 skipped）**
- [ ] **Step 2: 若无 API Key 的环境，确认降级路径（无法条类案但流程不崩）**

---

## 执行顺序

Task 1（独立）→ Task 2（独立）→ Task 3（依赖 Task 2 的模块）→ Task 4（人工）
