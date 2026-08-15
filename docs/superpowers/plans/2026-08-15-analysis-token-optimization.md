# 分析链路 Token 优化 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 统一分析链路的 prompt 为缓存友好结构（材料前置、指令后置），消除多罪名重复计算，修复研究发现的 3 个 bug。

**Architecture:** 新增 `build_cached_messages()` 工具函数统一"system 固定规则 + user 前段材料 + 末尾指令"的三层结构（DeepSeek prompt cache 按前缀命中）；逐阶段改造 prompt 组装；多罪名 5B 跳过已有产物。

**Tech Stack:** Python 3.13 / FastAPI / pytest

**研究报告依据**：`backend/analysis_pipeline.py`（5a-5f、控辩对抗）、`backend/analysis_engine.py`（质证、5C、阶段52）、`backend/stage_api.py`（多罪名循环）

**关键背景（零上下文必读）：**
- 测试从仓库根运行：`python3 -m pytest tests/xxx.py -q`（`tests/conftest.py` 已加 backend 到 sys.path）
- DeepSeek prompt cache 按**前缀**匹配：只有 system + user 开头完全一致的部分才命中，所以材料必须在指令之前
- LLM client 假桩模式：`type("C", (), {"chat": staticmethod(fake_chat)})()`，`monkeypatch.setattr("llm_client.get_llm_client", lambda: fake)`
- `_read_stage_md(analysis_dir, stage)` 只读共享路径 `stage_{N}/output.md`；罪名层产物在 `analysis/{charge}/stage_{N}/output.md`
- `_save_stage(stage, data, md, charge)`：charge 非空时写 `analysis/{charge}/stage_{N}/`

---

### Task 1: 缓存友好消息组装工具

**Files:**
- Create: `backend/prompt_cache.py`
- Test: `tests/test_prompt_cache.py`

- [ ] **Step 1: 写失败测试**

```python
"""缓存友好消息组装工具测试"""
from prompt_cache import build_cached_messages


def test_material_before_instruction():
    """材料在 user 前段、指令在末尾（缓存前缀共享的结构保证）"""
    msgs = build_cached_messages("系统规则", "案件材料", "本次任务指令")
    assert msgs[0] == {"role": "system", "content": "系统规则"}
    assert msgs[1]["role"] == "user"
    # 材料在前、指令在后
    assert msgs[1]["content"].index("案件材料") < msgs[1]["content"].index("本次任务指令")
    assert msgs[1]["content"].startswith("案件材料")
    assert msgs[1]["content"].endswith("本次任务指令")


def test_same_prefix_across_calls():
    """同一材料不同指令：system 和 user 前缀一致（缓存命中的条件）"""
    a = build_cached_messages("规则", "材料X", "指令1")
    b = build_cached_messages("规则", "材料X", "指令2")
    assert a[0] == b[0]
    assert a[1]["content"][:len("材料X")] == b[1]["content"][:len("材料X")]


def test_empty_material():
    """材料为空时退化为纯指令（不产出多余分隔符）"""
    msgs = build_cached_messages("规则", "", "指令")
    assert msgs[1]["content"] == "指令"
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_prompt_cache.py -q`
Expected: ImportError

- [ ] **Step 3: 实现 `backend/prompt_cache.py`**

```python
"""缓存友好的 LLM 消息组装

DeepSeek prompt cache 按前缀命中：system + user 开头完全一致的部分才走缓存价。
分析链路的多次调用若把案件材料放在 user 末尾（指令在前），每次都全价；
统一为「system 固定规则 → user 前段材料（共享前缀）→ 末尾任务指令」后，
同一案件材料的后续调用命中缓存（命中价约为全价 1/10）。
"""


def build_cached_messages(system: str, material: str, instruction: str) -> list:
    """组装缓存友好的 messages：材料在 user 前段，指令在末尾

    Args:
        system: 固定角色/规则（同一批调用应保持一致才能共享缓存）
        material: 案件材料/阶段产物（同一案件的多次调用共享此前缀）
        instruction: 本次任务指令（变化的放最后）
    """
    if not material:
        user = instruction
    else:
        user = f"{material}\n\n---\n\n{instruction}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
```

- [ ] **Step 4: 运行确认通过** — 3 passed

- [ ] **Step 5: Commit**

```bash
git add backend/prompt_cache.py tests/test_prompt_cache.py
git commit -m "feat: 缓存友好消息组装工具（材料前置指令后置，DeepSeek前缀命中）"
```

---

### Task 2: 辩护意见 5a-5f prompt 重排（最大单笔收益）

**Files:**
- Modify: `backend/analysis_pipeline.py`（sub_steps 定义，约 2165-2225 行）
- Test: `tests/test_prompt_cache.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
def test_5x_substeps_share_prefix(monkeypatch):
    """5a-5f 六个子步骤：system 一致 + context 在 user 前段（跨调用共享缓存前缀）"""
    import inspect
    import analysis_pipeline
    src = inspect.getsource(analysis_pipeline.AnalysisPipeline._step5_defense_opinion)
    # 重排后：sub_steps 的 prompt 组装必须用 build_cached_messages
    assert "build_cached_messages" in src
    # context 不得再出现在指令文本之后拼接的旧结构（旧结构特征：指令 f-string 内嵌 {context[:20000]}）
    assert "{context[:20000]}" not in src and "{context[:25000]}" not in src
```

注意：先 Read `backend/analysis_pipeline.py` 找 `_step5` 相关函数名（可能是 `_step5_defense_opinion` 或类似），测试里的函数名以实际为准。

- [ ] **Step 2: 运行确认失败** — FAIL（当前无 build_cached_messages）

- [ ] **Step 3: 重排 sub_steps**

先 Read `backend/analysis_pipeline.py` 约 2160-2260 行（`sub_steps = [...]` 六元组定义及其后的 for 循环）。改造方式：

1. 文件顶部 import：`from prompt_cache import build_cached_messages`
2. 六个子步骤的 prompt 改为「context 在前、指令在后」。结构示例（以 5a 为例，其余五个同理）：

旧：
```python
("5a", "01-案件概述.md", "案件概述",
 f"""你是刑事律师。基于以下材料，为被告人 **{defendant}** 生成案件概述章节。
要求：...
{context[:20000]}
请输出 Markdown 格式，聚焦案件概述。"""),
```

新（sub_steps 改为存指令文本，prompt 在循环里用 build_cached_messages 组装）：
```python
sub_steps = [
    ("5a", "01-案件概述.md", "案件概述",
     f"""为被告人 **{defendant}** 生成案件概述章节。
要求：概括指控罪名、指控事实、涉案金额、涉案人员。
请输出 Markdown 格式，聚焦案件概述。"""),
    # 5b/5c/5d/5e/5f 同理：删除开头的「你是刑事律师。基于以下材料，」，
    # 删除中间的 {context[:20000]}，保留各节具体要求与"重要区分"提示
]
```

循环内（找到 `for stage_key, filename, stage_name, prompt in sub_steps:` 及其后的 chat 调用）改为：

```python
        shared_system = (
            "你是刑事辩护律师，为被告人撰写辩护报告章节。"
            "起诉书/起诉意见书是指控文书不是证据，引用时写\"据起诉书\"/\"据起诉意见书\"，"
            "不要用\"见证据XXX\"格式。只输出 Markdown。"
        )
        for stage_key, filename, stage_name, instruction in sub_steps:
            ...
            messages = build_cached_messages(shared_system, context[:20000], instruction)
            # 5d 三阶层用 context[:25000]，理论/构成要件文本追加在 context 之后（保持公共前缀完整）：
            # material_5d = f"{context[:25000]}\n\n{theory_text}\n\n{element_text}"
            resp = await self.llm.chat(messages)
```

注意：
- 5d 的 material 是 `context[:25000] + theory_text + element_text`（理论文本必须放在 context **之后**：它与 5a-5c/5e/5f 不构成公共前缀，若插在 strategy_prefix 与 context 之间会击穿已缓存的 context[:20000] 前缀；放在末尾则 5d 仍能命中 system + strategy_prefix + context[:20000] 的共享缓存，语义上理论文本仍在材料段、仍在指令之前）
- 各节「重要区分」提示已并入 shared_system，各指令里删除重复表述（保留各节特有要求）
- 不要改输出文件名、保存逻辑、进度回调

- [ ] **Step 4: 运行测试 + 相关回归**

Run: `python3 -m pytest tests/test_prompt_cache.py tests/test_stage5_full_defense.py -q`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add backend/analysis_pipeline.py tests/test_prompt_cache.py
git commit -m "perf: 辩护意见5a-5f改共享前缀结构（6次调用公共2万字符命中缓存）"
```

---

### Task 3: 质证 prompt 重排 + 死截断清理

**Files:**
- Modify: `backend/analysis_engine.py`（`_build_review_prompt`，约 1670-1760 行）
- Test: `tests/test_prompt_cache.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
def test_cross_examination_prompt_cached_structure():
    """质证 prompt：固定模板/法律依据在 system（跨证据共享前缀），证据内容在 user 前段"""
    import inspect
    import analysis_engine
    src = inspect.getsource(analysis_engine.AnalysisEngine._build_review_prompt)
    assert "build_cached_messages" in src or "_build_review_messages" in src
    # 死截断清理：先截6000再截4000的双重截断应只剩 4000
    assert "[:6000]" not in src
```

- [ ] **Step 2: 运行确认失败** — FAIL

- [ ] **Step 3: 重排**

先 Read `backend/analysis_engine.py` 的 `_build_review_prompt`（约 1670-1760 行）和其调用点（约 1816 行 `llm.chat` 处）。改造：

1. `_build_review_prompt` 改为 `_build_review_messages(ev, template)`，返回 `build_cached_messages(system, material, instruction)`：
   - system = 固定审查规则（角色 + JSON schema + 评分标准，所有证据共享）
   - material = `审查模板 {template}\n\n法律依据 {LEGAL_BASIS_FOR_REVIEW[:3000]}\n\n证据内容 {ev_text[:4000]}`
     （template 按证据类型有少数几个变体，同类型证据间仍可共享前缀）
   - instruction = `请对上述证据（{ev_name}，{ev_type}）进行三性审查并输出质证意见（JSON）`
2. 删除 `ev_text[:6000]` 再 `[:4000]` 的双重截断，只保留 `[:4000]`
3. 调用点（`llm.chat([...])`）改为直接 `llm.chat(self._build_review_messages(ev, template))`

注意保持返回结构：若现有调用点对返回的 prompt 字符串有其他处理，对齐调整。先 Read 确认。

- [ ] **Step 4: 运行测试 + 回归**

Run: `python3 -m pytest tests/test_prompt_cache.py tests/test_stage5_full_defense.py -q && python3 -m pytest tests/ -q 2>&1 | tail -1`
Expected: passed

- [ ] **Step 5: Commit**

```bash
git add backend/analysis_engine.py tests/test_prompt_cache.py
git commit -m "perf: 质证prompt固定部分入system（逐份N次调用共享前缀）+ 清理6000/4000死截断"
```

---

### Task 4: 控辩对抗 45a/45b/45d prompt 重排

**Files:**
- Modify: `backend/analysis_pipeline.py`（45a 约 1534 行、45b 约 1593 行、45d 约 1736 行）
- Test: `tests/test_prompt_cache.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
def test_debate_prompts_share_context_prefix():
    """控辩对抗 45a/45b/45d：context 在 user 前段（公诉人/辩方两次调用共享材料前缀）"""
    import inspect
    import analysis_pipeline
    src = inspect.getsource(analysis_pipeline.AnalysisPipeline)
    # 找 45a 的红方构建段（含"你是本案公诉人"）
    idx = src.find("你是本案公诉人")
    assert idx > 0
    segment = src[idx:idx + 3000]
    # 重排后：context 不应出现在角色指令之后的 f-string 尾部拼接（旧结构特征）
    # 新结构使用 build_cached_messages
    assert "build_cached_messages" in src
```

- [ ] **Step 2: 运行确认失败** — FAIL

- [ ] **Step 3: 重排三处**

先 Read `backend/analysis_pipeline.py` 约 1530-1560（45a）、1590-1630（45b）、1730-1760（45d）。

- **45a**：`build_cached_messages("你是刑事诉讼角色扮演引擎，严格按用户指定角色输出。", context, "你的角色：本案公诉人。被告人：**{defendant}**。独立构建最强指控逻辑……（原指令其余部分保留）")`
- **45b**：同 system，material 同为 `context`（与 45a 共享前缀），instruction 改为辩方角色指令
- **45d**：material = `## 控方指控（独立构建）\n{red_argument}\n\n## 辩方辩护（独立构建）\n{blue_defense}\n\n## 交叉对决结果\n{clash_analysis}`，instruction = 法官裁决指令。system = "你是中立法官……"（原样保留）

注意：45c（交叉对决）若也是"指令在前"结构，一并重排（先 Read 确认其结构）。

- [ ] **Step 4: 运行测试 + 回归**

Run: `python3 -m pytest tests/test_prompt_cache.py tests/test_debate_fixes.py -q`
Expected: passed

- [ ] **Step 5: Commit**

```bash
git add backend/analysis_pipeline.py tests/test_prompt_cache.py
git commit -m "perf: 控辩对抗45a/45b共享context前缀（第二次调用命中缓存）"
```

---

### Task 5: 多罪名 5B 去重 + stage4_md 罪名层 bug

**Files:**
- Modify: `backend/analysis_engine.py`（`stage_5_full_defense` 内 5B 调用点约 1475 行；stage4_md 读取约 1436 行）
- Test: `tests/test_debate_fixes.py` 或新建 `tests/test_multi_charge.py`

- [ ] **Step 1: 写失败测试**

```python
"""多罪名：5B 共享层去重 + stage_4 罪名层读取"""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

from analysis_engine import AnalysisEngine


def test_5b_skipped_when_output_exists(tmp_path, monkeypatch):
    """5B 产物已存在且非空时跳过重跑（多罪名第二个罪名不再重复矛盾分析）"""
    case_path = tmp_path / "case"
    ad = case_path / "analysis"
    (ad / "stage_52").mkdir(parents=True)
    (ad / "stage_52" / "output.md").write_text("已有矛盾分析", encoding="utf-8")
    engine = AnalysisEngine("c", case_path)

    called = []
    async def fake_5b(defendant, progress_cb=None):
        called.append(True)
        return "新矛盾分析"
    monkeypatch.setattr(engine, "stage_5b_contradiction_analysis", fake_5b)

    result = asyncio.run(engine._run_5b_if_needed("张三"))
    assert called == []  # 未重跑
    assert result == "已有矛盾分析"


def test_5b_runs_when_missing(tmp_path, monkeypatch):
    """5B 产物不存在时正常执行"""
    case_path = tmp_path / "case"
    (case_path / "analysis").mkdir(parents=True)
    engine = AnalysisEngine("c", case_path)

    called = []
    async def fake_5b(defendant, progress_cb=None):
        called.append(True)
        return "新矛盾分析"
    monkeypatch.setattr(engine, "stage_5b_contradiction_analysis", fake_5b)

    result = asyncio.run(engine._run_5b_if_needed("张三"))
    assert called == [True]
    assert result == "新矛盾分析"


def test_stage4_md_reads_charge_layer(tmp_path):
    """多罪名：stage4_md 读取罪名层产物 analysis/{charge}/stage_4/output.md"""
    case_path = tmp_path / "case"
    charge_dir = case_path / "analysis" / "诈骗罪" / "stage_4"
    charge_dir.mkdir(parents=True)
    (charge_dir / "output.md").write_text("诈骗罪法规", encoding="utf-8")
    engine = AnalysisEngine("c", case_path)
    md = engine._read_stage4_for_charge("诈骗罪")
    assert md == "诈骗罪法规"


def test_stage4_md_shared_when_no_charge(tmp_path):
    """单罪名/共享层：读 analysis/stage_4/output.md"""
    case_path = tmp_path / "case"
    shared = case_path / "analysis" / "stage_4"
    shared.mkdir(parents=True)
    (shared / "output.md").write_text("共享法规", encoding="utf-8")
    engine = AnalysisEngine("c", case_path)
    assert engine._read_stage4_for_charge(None) == "共享法规"
```

- [ ] **Step 2: 运行确认失败** — FAIL（两方法不存在）

- [ ] **Step 3: 实现**

在 `AnalysisEngine` 加两个方法（放在 `stage_5_full_defense` 之前）：

```python
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
```

然后改 `stage_5_full_defense` 两处：
1. `stage4_md = _read_stage_md(self.analysis_dir, 4)` → `stage4_md = self._read_stage4_for_charge(crime_type)`
2. 找到 5B 调用点（先 Read stage_5_full_defense 约 1470-1490 行确认现状，形如 `await self.stage_5b_contradiction_analysis(...)` 或 `stage_52` 相关调用）→ 改为 `await self._run_5b_if_needed(defendant, progress_cb=progress_cb)`

- [ ] **Step 4: 运行测试 + 回归**

Run: `python3 -m pytest tests/test_multi_charge.py tests/test_strategy_confirm_rerun.py tests/test_stage5_full_defense.py -q && python3 -m pytest tests/ -q 2>&1 | tail -1`
Expected: passed

- [ ] **Step 5: Commit**

```bash
git add backend/analysis_engine.py tests/test_multi_charge.py
git commit -m "fix: 多罪名5B矛盾分析去重（共享层复用）+ 5C读取罪名层stage_4产物"
```

---

### Task 6: 死代码清理 + 5C 输入瘦身

**Files:**
- Modify: `backend/analysis_engine.py`（184-191 行死引用；5C 注入约 1494-1516 行）
- Test: `tests/test_multi_charge.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
def test_no_dead_zhang_defense_reference():
    """ZHANG_CRIMINAL_DEFENSE 死引用已清除（引用的文件不存在，恒为空串）"""
    import inspect
    import analysis_engine
    src = inspect.getsource(analysis_engine)
    assert "ZHANG_CRIMINAL_DEFENSE" not in src


def test_5c_prompt_no_duplicate_catalog():
    """5C prompt：证据目录不重复出现（evidence_catalog_text 与 5A 目录只保留一个）"""
    import inspect
    import analysis_engine
    src = inspect.getsource(analysis_engine.AnalysisEngine.stage_5_full_defense)
    # evidence_list_md 与 evidence_catalog_text 不得同时注入 prompt
    assert src.count("evidence_list_md") >= 1  # 5A 产物仍保存
    # prompt 组装段不重复引用两个目录变量
    prompt_section = src[src.find("stage35_md"):src.find("stage35_md") + 3000] if "stage35_md" in src else src
    assert not ("evidence_catalog_text" in prompt_section and "evidence_list_md" in prompt_section)
```

- [ ] **Step 2: 运行确认失败** — FAIL

- [ ] **Step 3: 实现**

1. **删死引用**：Read `backend/analysis_engine.py` 180-195 行，删除 `ZHANG_CRIMINAL_DEFENSE` 的 try/except 加载块，并在 5C prompt 组装处（搜 `ZHANG_CRIMINAL_DEFENSE` 引用点）删除该节（"刑事辩护提示词"一节恒为空，连标题一起删）。
2. **5C 目录去重**：Read 5C prompt 组装段（约 1490-1520 行）。`evidence_catalog_text`（_split_indictment_and_evidence 产出）与 5A 的 `evidence_list_md` 内容等价——prompt 中只保留 5A 目录（信息更全，含编号），删除 evidence_catalog_text 的注入段。
3. **5C 阶段产物截断**：stage1/2/3/35/4 的注入加单份上限 15000 字符（`stage1_md[:15000]` 等），防止单份超长产物打爆 prompt。

- [ ] **Step 4: 运行测试 + 全套件回归**

Run: `python3 -m pytest tests/ -q 2>&1 | tail -1`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add backend/analysis_engine.py tests/test_multi_charge.py
git commit -m "refactor: 删除ZHANG_CRIMINAL_DEFENSE死引用 + 5C证据目录去重+阶段产物截断15000"
```

---

### Task 7: 阶段2 起诉书仅首批注入

**Files:**
- Modify: `backend/analysis_engine.py`（`_batch_analyze_evidence`，约 64-160 行）
- Test: `tests/test_prompt_cache.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
def test_batch_analyze_indictment_only_first_batch():
    """分批分析：起诉书全文只在第一批注入，后续批次不带（省重复输入）"""
    import asyncio
    import analysis_engine

    calls = []

    async def fake_chat(messages, **kw):
        calls.append(messages)
        return "批结果 ```json\n{\"nodes\": [], \"edges\": []}\n```"

    # 构造 3 份证据 + 1 份起诉书，预算设小迫使分 2 批
    texts = [
        {"filename": "起诉书", "type": "起诉书", "text": "起诉书全文" * 500, "is_indictment": True},
        {"filename": "证据A", "type": "书证", "text": "内容A" * 3000},
        {"filename": "证据B", "type": "书证", "text": "内容B" * 3000},
    ]
    monkeypatch_client = type("C", (), {"chat": staticmethod(fake_chat)})()
    monkeypatch.setattr("llm_client.get_llm_client", lambda: monkeypatch_client)
    # 用小 context_limit 迫使分批
    monkeypatch.setattr("config_manager.get_config_value", lambda k, d="": "20000" if k == "model_context_limit" else d)

    results = asyncio.run(analysis_engine._batch_analyze_evidence(
        texts, "系统", "头部", "尾部", label="测试"))
    assert len(results) >= 2  # 确实分了多批
    # 起诉书全文只在第一批的 user prompt 中
    first_user = calls[0][-1]["content"]
    later_users = [c[-1]["content"] for c in calls[1:]]
    assert "起诉书全文" in first_user
    assert all("起诉书全文" not in u for u in later_users)
```

（monkeypatch 是 pytest fixture，测试函数签名加 `monkeypatch` 参数。）

- [ ] **Step 2: 运行确认失败** — FAIL（当前每批都带起诉书）

- [ ] **Step 3: 实现**

Read `_batch_analyze_evidence`（约 64-160 行），找到批次循环中 `chunk_text = indictment_text` 的拼接逻辑，改为：

```python
    for ci, chunk in enumerate(evidence_chunks):
        ...
        # 起诉书全文只在第一批注入（后续批次不再重复发送，省 token）
        if ci == 0:
            chunk_text = indictment_text
            if chunk:
                chunk_parts = [f"### {t['filename']}（{t['type']}）\n{t['text']}" for t in chunk]
                chunk_text = (indictment_text + "\n\n" if indictment_text else "") + "\n\n".join(chunk_parts)
        else:
            chunk_parts = [f"### {t['filename']}（{t['type']}）\n{t['text']}" for t in chunk]
            chunk_text = "\n\n".join(chunk_parts)
        user_prompt = f"{user_prompt_header}\n\n{chunk_text}\n\n{user_prompt_footer}"
```

注意：分配合并逻辑不变（起诉书仍计入第一批的 token 预算），只是后续批次不再拼接。

- [ ] **Step 4: 运行测试 + 回归**

Run: `python3 -m pytest tests/test_prompt_cache.py -q && python3 -m pytest tests/ -q 2>&1 | tail -1`
Expected: passed

- [ ] **Step 5: Commit**

```bash
git add backend/analysis_engine.py tests/test_prompt_cache.py
git commit -m "perf: 分批分析起诉书仅首批注入（多批案件省重复起诉书输入）"
```

---

## Self-Review 记录

- **范围覆盖**：批次1（prompt 缓存结构：T2 5a-5f / T3 质证 / T4 控辩对抗 + T1 工具函数）✓ 批次3 bug（T5 stage4 罪名层 + T6 死引用/目录去重）✓ 批次2（T5 多罪名5B去重 + T7 起诉书首批）✓
- **明确不做**（已在研究报告标注）：阶段52 供述去重（有质量风险需单独验证）、下线子阶段51/旧流水线入口（产品决策）、chat/update 增量更新（中改动量，下轮）
- **类型一致**：`build_cached_messages(system, material, instruction)` 在 T1 定义、T2/T3/T4 一致使用；`_run_5b_if_needed`/`_read_stage4_for_charge` 在 T5 定义并测试
- **风险点**：T2/T4 改动 prompt 文本结构，输出质量需真实案件验证（计划执行完后用冯叶飞案跑一次 5a-5f 对比章节完整性）
