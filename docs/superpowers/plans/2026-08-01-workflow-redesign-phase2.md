# 工作流重构 阶段 2 实现计划（改动 D：统一上下文预算管理）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 统一上下文预算模块（窗口映射 + fit_texts 优先级截断），替换分析侧硬截断，提取分块加重叠与块级断点续传

**设计文档:** `docs/superpowers/specs/2026-07-31-analysis-workflow-redesign-design.md` 第 6 节

**分支:** `feat/analysis-workflow-redesign`（沿用）

**探查结论（范围修正依据）：**
- 提取分块已存在：`case_manager.py:_split_content_by_tokens`（按 ## 边界 + tiktoken 精确计数）+ `_merge_evidence_blocks` 按 (name, source) 去重；预算 `context_limit - 38000`（:1124）
- 证据预算已有模型感知版本：`analysis_engine.py:_get_content_budget_chars`（:55，(limit-38000)×1.35 字符），与 case_manager 的公式**重复**
- 提取断点续传是**文件级**（.done 标记），多块文件中途失败会整文件重提
- `_strip_cover_page`/`_strip_non_evidence_sections` 是死代码（定义未调用），不处理（阶段 3 用"标记"方案替代）
- 分析侧硬截断点：`analysis_pipeline.py` step3 `[:40000]`、4b `[:30000]`、4d `[:15000]`；`llm_client.py` `report_context[:50000]`、`original_report[:60000]`

---

### Task 1: 统一预算模块 context_budget.py

**Files:**
- Create: `backend/context_budget.py`
- Test: `tests/test_context_budget.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_context_budget.py
import context_budget
from context_budget import (
    get_model_window, content_budget_chars, truncate_with_marker, fit_texts,
)


def test_model_window_mapping():
    assert get_model_window("deepseek-v4-pro") == 1000000
    assert get_model_window("kimi-k3") == 262144
    assert get_model_window("qwen3.5-plus") == 131072
    assert get_model_window("unknown-model") is None


def test_content_budget_chars(monkeypatch):
    monkeypatch.setattr(context_budget, "get_context_limit", lambda: 250000)
    assert content_budget_chars() == int((250000 - 38000) * 1.35)


def test_truncate_with_marker():
    text = "x" * 1000
    out = truncate_with_marker(text, 100, "测试证据")
    assert out.startswith("x" * 100)
    assert "已截断" in out and "1000" in out and "测试证据" in out


def test_truncate_noop_when_fits():
    assert truncate_with_marker("短文本", 100) == "短文本"


def test_fit_texts_high_priority_never_truncated():
    texts = [
        {"label": "起诉书", "text": "高" * 800, "priority": 0},
        {"label": "次要材料", "text": "低" * 800, "priority": 2},
    ]
    out = fit_texts(texts, 1000)
    assert "高" * 800 in out           # 高优先级完整保留
    assert "已截断" in out              # 低优先级被截断
    assert "低" * 800 not in out


def test_fit_texts_mid_proportional():
    texts = [
        {"label": "证据A", "text": "A" * 600, "priority": 1},
        {"label": "证据B", "text": "B" * 600, "priority": 1},
    ]
    out = fit_texts(texts, 1000)
    assert "已截断" in out
    assert "证据A" in out and "证据B" in out  # 两个标签都在


def test_fit_texts_all_fits():
    texts = [{"label": "A", "text": "短", "priority": 1}]
    assert "已截断" not in fit_texts(texts, 1000)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/zhanghan/.openclaw/workspace/criminal-llm && python3 -m pytest tests/test_context_budget.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 `backend/context_budget.py`**

```python
"""统一上下文预算管理

职责：
- 模型上下文窗口识别（设置页展示/建议用）
- 内容预算换算（token → 字符，全项目统一公式，替代各处重复的 (limit-38000)×1.35）
- 优先级截断（fit_texts：高优先级完整保留，中比例分配，低截断标注）
"""
from config_manager import get_config_value

# 模型家族 → 上下文窗口（tokens），用于设置页展示与建议；实际预算以用户配置为准
MODEL_CONTEXT_WINDOWS = {
    "deepseek": 1000000,
    "kimi": 262144,
    "qwen": 131072,
    "glm": 131072,
    "gpt": 128000,
    "claude": 200000,
}

DEFAULT_CONTEXT_LIMIT = 250000
DEFAULT_RESERVE_TOKENS = 38000   # system prompt + 输出预留
CHARS_PER_TOKEN = 1.35           # 中文：1 token ≈ 1.35 字符


def get_context_limit() -> int:
    """内容预算的基准：用户配置（model_context_limit）为唯一事实源"""
    try:
        return int(get_config_value("model_context_limit", str(DEFAULT_CONTEXT_LIMIT)))
    except Exception:
        return DEFAULT_CONTEXT_LIMIT


def get_model_window(model: str) -> int | None:
    """按模型名识别上下文窗口（设置页展示/建议），未知返回 None"""
    model_lower = (model or "").lower()
    for family, window in MODEL_CONTEXT_WINDOWS.items():
        if family in model_lower:
            return window
    return None


def content_budget_chars(reserve_tokens: int = DEFAULT_RESERVE_TOKENS) -> int:
    """内容字符预算 = (context_limit - 预留) × 1.35"""
    return int((get_context_limit() - reserve_tokens) * CHARS_PER_TOKEN)


def truncate_with_marker(text: str, budget: int, label: str = "") -> str:
    """超预算截断并标注（不超原样返回）"""
    if len(text) <= budget:
        return text
    tag = f"\n\n[已截断：{label + '，' if label else ''}原文共 {len(text)} 字符，仅显示前 {budget} 字符]"
    return text[:budget] + tag


def fit_texts(texts: list[dict], budget_chars: int) -> str:
    """按优先级把多段文本装进预算。

    texts: [{"label": str, "text": str, "priority": 0|1|2}]（0=高，1=中，2=低）
    - 高优先级：完整保留（单段超预算才截断标注）
    - 中优先级：剩余预算的 80% 按比例分配
    - 低优先级：剩余预算的 20% 分配，不够则截断标注
    返回拼接后的 "## {label}\n{text}" 块
    """
    high = [t for t in texts if t.get("priority", 1) == 0]
    mid = [t for t in texts if t.get("priority", 1) == 1]
    low = [t for t in texts if t.get("priority", 1) == 2]

    parts: list[str] = []
    used = 0

    for t in high:
        block_text = truncate_with_marker(t["text"], budget_chars, t["label"])
        block = f"## {t['label']}\n{block_text}"
        parts.append(block)
        used += len(block)

    remaining = max(0, budget_chars - used)
    mid_pool = int(remaining * 0.8) if mid else 0
    low_pool = remaining - mid_pool

    for pool, group in ((mid_pool, mid), (low_pool, low)):
        if not group:
            continue
        per = max(200, pool // len(group))
        for t in group:
            block_text = truncate_with_marker(t["text"], per, t["label"])
            parts.append(f"## {t['label']}\n{block_text}")

    return "\n\n".join(parts)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_context_budget.py -v`
Expected: 7 passed

- [ ] **Step 5: 提交**

```bash
cd /Users/zhanghan/.openclaw/workspace/criminal-llm
git add backend/context_budget.py tests/test_context_budget.py
git commit -m "feat: 统一上下文预算模块（窗口映射+优先级截断）"
```

---

### Task 2: 分析侧硬截断统一接入

**Files:**
- Modify: `backend/case_manager.py`（:1124 预算计算）
- Modify: `backend/analysis_engine.py`（_get_content_budget_chars 改为委托）
- Modify: `backend/analysis_pipeline.py`（step3/4b/4d/起诉书截断点 + 预算日志）
- Modify: `backend/llm_client.py`（report_context/original_report 截断点）
- Test: `tests/test_budget_integration.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_budget_integration.py
"""预算接入：各处截断点统一走 context_budget，且与模型配置联动"""
import inspect
import analysis_engine
import analysis_pipeline
import case_manager
import llm_client


def test_analysis_engine_delegates_to_context_budget():
    src = inspect.getsource(analysis_engine._get_content_budget_chars)
    assert "context_budget" in src


def test_case_manager_uses_context_budget():
    src = inspect.getsource(case_manager._extract_single_file)
    assert "context_budget" in src
    assert "38000" not in src  # 旧公式移除


def test_pipeline_no_hardcoded_large_slices():
    src = inspect.getsource(analysis_pipeline.AnalysisPipeline.step3_internal_contradiction)
    assert "[:40000]" not in src
    src4 = inspect.getsource(analysis_pipeline.AnalysisPipeline.step4_build_case_wiki)
    assert "[:30000]" not in src4
    assert "[:15000]" not in src4


def test_llm_client_report_slice_budget_aware():
    src = inspect.getsource(llm_client)
    assert "report_context[:50000]" not in src
    assert "original_report[:60000]" not in src
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_budget_integration.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**

（a）`case_manager.py` `_extract_single_file` 中：
```python
    context_limit = int(get_config_value("model_context_limit", "250000"))
    content_budget = context_limit - 38000  # 预留 system prompt + 提取规则 + 响应
    if content_budget < 50000:
        content_budget = 50000  # 最少保证 50K tokens
```
改为：
```python
    import context_budget
    # 字符预算转 token 预算（分块按 tiktoken 计数）：与统一公式保持一致
    content_budget = int(context_budget.content_budget_chars() / context_budget.CHARS_PER_TOKEN)
    if content_budget < 50000:
        content_budget = 50000  # 最少保证 50K tokens
```
（`get_config_value` 的 import 若因此闲置则清理。）

（b）`analysis_engine.py` `_get_content_budget_chars` 函数体改为：
```python
def _get_content_budget_chars() -> int:
    """获取内容字符预算（委托统一预算模块）"""
    import context_budget
    return context_budget.content_budget_chars()
```
（保留原函数名与签名，上层 `_get_evidence_budget_chars` 等比例函数不动。）

（c）`analysis_pipeline.py`：
- 顶部 import 区加 `import context_budget`
- step3 `summary_text[:40000]` → `summary_text[:context_budget.content_budget_chars()]`
- 4b `summary_text[:30000]` → `summary_text[:context_budget.content_budget_chars()]`；同处 `other_index_path...[:30000]` 同样替换
- 4d `all_evidence_analysis[:15000]` → `all_evidence_analysis[:context_budget.content_budget_chars()]`
- `_find_indictment_in_md_files` 中三处 `f["text"][:40000]` → `f["text"][:context_budget.content_budget_chars()]`
- 4b 和 4d 的 LLM 调用前各加一行日志：`print(f"[预算] 4b 单证据 prompt 约 {len(user_prompt)} 字符 / 预算 {context_budget.content_budget_chars()}")`（4d 同理，user_prompt 为实际构建的 prompt 字符串变量名，按现场变量名调整）

（d）`llm_client.py`：
- `report_context[:50000]` → `report_context[:_get_report_budget_chars()]`
- `original_report[:60000]` → `original_report[:_get_report_budget_chars()]`
- `_get_report_budget_chars`：在 analysis_engine.py 既有 `_get_xxx_budget_chars` 家族旁新增 `def _get_report_budget_chars() -> int: return int(_get_content_budget_chars() * 0.5)`，llm_client 顶部既有 `from analysis_engine import (...)` 的 import 列表（:18 附近）中加入它

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_budget_integration.py -v` → `python3 -m pytest tests/ -q`（无回归）

- [ ] **Step 5: 提交**

```bash
git add backend/case_manager.py backend/analysis_engine.py backend/analysis_pipeline.py backend/llm_client.py tests/test_budget_integration.py
git commit -m "refactor: 分析侧截断点统一接入上下文预算模块"
```

---

### Task 3: 提取分块重叠 + 块级断点续传

**Files:**
- Modify: `backend/case_manager.py`（_split_content_by_tokens/_split_by_token_count 重叠 + _extract_single_file 多块分支块级续传）
- Test: `tests/test_chunk_overlap_resume.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_chunk_overlap_resume.py
"""提取分块：块间重叠防跨块事实切断 + 块级断点续传"""
from pathlib import Path
from case_manager import _split_content_by_tokens


def test_chunks_have_overlap():
    """多块时，后一块开头包含前一块尾部（重叠区）"""
    # 构造两个 ## 段，每段约 600 字（用中文字符保证 tiktoken 计数超预算）
    sec1 = "## 第一份笔录\n\n" + "甲说。" * 300
    sec2 = "## 第二份笔录\n\n" + "乙说。" * 300
    chunks = _split_content_by_tokens(sec1 + "\n" + sec2, 400, "测试.md")
    assert len(chunks) == 2
    # 第二块应包含第一块尾部的重叠内容
    assert "甲说" in chunks[1]["text"][:600]


def test_single_chunk_no_overlap():
    chunks = _split_content_by_tokens("## 短\n\n内容", 100000, "测试.md")
    assert len(chunks) == 1
    assert chunks[0]["label"] == "测试.md"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_chunk_overlap_resume.py -v`
Expected: FAIL（当前无重叠）

- [ ] **Step 3: 实现块间重叠**

`_split_content_by_tokens`：每次发出一个 chunk 并开始新的 `current_parts` 时，把刚发出 chunk 的**尾部 ≤500 字符**（尽量在段落边界 `\n\n` 处截断）作为重叠前缀加入新 chunk：

```python
OVERLAP_CHARS = 500

def _overlap_tail(text: str) -> str:
    """取文本尾部 ≤OVERLAP_CHARS 字符作为下一块的重叠前缀（尽量段落边界）"""
    if len(text) <= OVERLAP_CHARS:
        return ""
    tail = text[-OVERLAP_CHARS:]
    para = tail.find("\n\n")
    if para > 0:
        tail = tail[para + 2:]
    return tail.strip()
```

实现方式：在 `chunks.append({"label": source_name, "text": "\n".join(current_parts)})` 之后、开始新积累时，记住刚发出 chunk 的文本 `emitted = "\n".join(current_parts)`，新 `current_parts` 的第一项改为 `[_overlap_tail(emitted) + "\n\n" + section]`（overlap 非空时），即新块为"重叠前缀 + 新段"。`_split_by_token_count` 硬切路径：每块从上一块回退 250 tokens 开始（`max(0, i - 250)`，第一块除外）。

注意：重叠导致块 token 数略超 budget 是可接受的（500 字符 ≈ 370 tokens）；`_merge_evidence_blocks` 已按 (name, source) 去重，重叠区重复提取的证据会被合并。

- [ ] **Step 4: 写块级续传测试（追加到同文件）**

```python
import asyncio
import case_manager


def test_chunk_level_resume(tmp_path, monkeypatch):
    """多块文件：已完成块跳过，只处理缺失块"""
    md_text = "## 段一\n\n" + "甲。" * 500 + "\n## 段二\n\n" + "乙。" * 500 + "\n## 段三\n\n" + "丙。" * 500
    md_file = tmp_path / "大文件.md"
    md_file.write_text(md_text, encoding="utf-8")
    file_temp = tmp_path / "temp" / "大文件"
    file_temp.mkdir(parents=True)

    calls = []

    class FakeClient:
        async def chat(self, messages, **kw):
            label = messages[-1]["content"].split("## 案卷文件：")[1].split("\n")[0]
            calls.append(label)
            return "[]"

    monkeypatch.setattr(case_manager, "get_llm_client", lambda: FakeClient())
    # 预置第 1 块已完成
    (file_temp / "_chunk_0_blocks.json").write_text("[]", encoding="utf-8")
    (file_temp / ".chunk_0.done").write_text("", encoding="utf-8")

    monkeypatch.setattr(case_manager, "_split_content_by_tokens", lambda text, budget, name: [
        {"label": "大文件.md - 分块 1/3", "text": "块1"},
        {"label": "大文件.md - 分块 2/3", "text": "块2"},
        {"label": "大文件.md - 分块 3/3", "text": "块3"},
    ])

    asyncio.run(case_manager._extract_single_file(md_file, md_text, file_temp, []))
    assert len(calls) == 2  # 只处理块 2、3
    assert "分块 2/3" in calls[0] and "分块 3/3" in calls[1]
```

注意：读 `_extract_single_file` 确认 `get_llm_client` 的引用方式（它是函数内 `from llm_client import get_llm_client` 局部导入——monkeypatch 目标应为 `llm_client.get_llm_client` 或在 case_manager 模块属性上打桩，按实际代码调整测试）。

- [ ] **Step 5: 实现块级续传（多块分支改造）**

```python
    else:
        # 多块，逐块提取后合并（块级断点续传：已完成块跳过）
        all_evidence_blocks = []
        for ci, chunk in enumerate(chunks):
            chunk_label = chunk["label"]
            done_marker = temp_dir / f".chunk_{ci}.done"
            blocks_file = temp_dir / f"_chunk_{ci}_blocks.json"
            if done_marker.exists() and blocks_file.exists():
                try:
                    cached = json.loads(blocks_file.read_text(encoding="utf-8"))
                    all_evidence_blocks.extend(cached)
                    logger.info(f"[证据提取] {chunk_label}: 已完成，跳过（缓存 {len(cached)} 份）")
                    continue
                except Exception:
                    pass  # 缓存损坏则重提该块
            chunk_text = chunk["text"]
            logger.info(f"[证据提取] {chunk_label}: 发送 {_count_tokens(chunk_text)} tokens")
            result = await asyncio.wait_for(
                client.chat([
                    {"role": "system", "content": _EVIDENCE_SYSTEM_PROMPT + "\n\n" + _EVIDENCE_EXTRACTION_RULES},
                    {"role": "user", "content": f"## 案卷文件：{chunk_label}\n\n{charges_str}\n\n{chunk_text}"},
                ]),
                timeout=timeout_seconds,
            )
            blocks = _parse_evidence_blocks(result, chunk_label)
            logger.info(f"[证据提取] {chunk_label}: 提取 {len(blocks)} 份证据")
            try:
                blocks_file.write_text(json.dumps(blocks, ensure_ascii=False), encoding="utf-8")
                done_marker.write_text("", encoding="utf-8")
            except Exception:
                pass
            all_evidence_blocks.extend(blocks)
        evidence_blocks = _merge_evidence_blocks(all_evidence_blocks)
        logger.info(f"[证据提取] {md_file.name}: {len(chunks)} 块合并后 {len(evidence_blocks)} 份证据")
```

（`json` 在 case_manager 顶部已导入。`_parse_evidence_blocks` 返回的 blocks 必须可 JSON 序列化——先读它确认字段都是 str/list/dict；temp_dir 传入的是该文件专属子目录（extract_and_save_temp 里 `file_temp_dir = temp_dir / md_file.stem`），标记互不误伤。）

- [ ] **Step 6: 运行测试确认通过**

Run: `python3 -m pytest tests/test_chunk_overlap_resume.py -v` → `python3 -m pytest tests/ -q`（无回归）

- [ ] **Step 7: 提交**

```bash
git add backend/case_manager.py tests/test_chunk_overlap_resume.py
git commit -m "feat: 提取分块块间重叠 + 块级断点续传"
```

---

### Task 4: 设置页模型窗口显示 + 人工验证

**Files:**
- Modify: `backend/config_manager.py`（get_config_status 加 model_window_detected）
- Modify: `frontend/src/pages/SettingsPage.tsx`（模型字段旁显示窗口）

- [ ] **Step 1: 后端——get_config_status 返回字典加**

```python
        "model_window_detected": _detect_model_window(config.get("llm_model", "")),
```
文件顶部或函数附近加：
```python
def _detect_model_window(model: str):
    """按模型名识别上下文窗口（未知返回 None）"""
    try:
        import context_budget
        return context_budget.get_model_window(model)
    except Exception:
        return None
```

- [ ] **Step 2: 前端——SettingsPage 模型字段下方加一行说明**

读 SettingsPage 找到 `llm_model` 输入框与 `model_context_limit` 输入框，在 context_limit 输入框旁/下方加：
```tsx
{config.model_window_detected && (
  <span style={{ fontSize: '11px', color: 'var(--macos-text-secondary)' }}>
    检测到 {config.llm_model} 窗口为 {config.model_window_detected.toLocaleString()} tokens（可在下方覆盖）
  </span>
)}
```
（按页面实际变量名与样式惯例接入。）

- [ ] **Step 3: 验证**

```bash
python3 -m pytest tests/ -q   # 无回归
cd frontend && npx tsc --noEmit && npm run build
```

- [ ] **Step 4: 提交**

```bash
git add backend/config_manager.py frontend/src/pages/SettingsPage.tsx
git commit -m "feat: 设置页显示模型上下文窗口识别结果"
```

- [ ] **Step 5: 人工验证**：设置页切换模型，确认窗口识别显示正确；大模型/小模型各跑一次提取与分析，日志观察预算打印
