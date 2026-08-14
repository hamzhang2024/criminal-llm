# 证据详细摘要层实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 证据提取完成后自动生成 8 栏目结构化详细摘要，供单发分析阶段（时间线/矛盾分析）消费并在前端与全文双通道展示。

**Architecture:** 新模块 `backend/evidence_summarizer.py`（与 `evidence_perdoc.py` 平级），提取流程末尾自动串联；摘要双写到 `index.json` 的 `digest` 字段和 `evidence/summaries/` 缓存；`analysis_engine._load_evidence_texts` 加 `prefer_summary` 显式切换；前端证据预览加「摘要 | 全文」切换。

**Tech Stack:** Python 3.13 / FastAPI / pytest（后端），React 18 + TypeScript（前端）

**设计文档：** `docs/superpowers/specs/2026-08-14-evidence-summary-design.md`

**⚠️ 对设计文档的一处修正**：设计文档说摘要写入 index.json 的 `summary` 字段，但 `summary` 已被占用（`EVIDENCE_STRUCTURED_FIELDS` 把**全文**摘要写入该字段）。本计划改用新字段 **`digest`**（浓缩摘要）和 **`digest_warning`**（保真校验警告）。

**关键背景知识（零上下文必读）：**
- 测试从仓库根目录运行：`python3 -m pytest tests/xxx.py -q`（`tests/conftest.py` 已把 `backend/` 加入 sys.path）
- 假 LLM client 模式（参照 `tests/test_evidence_perdoc.py` 的 `_fake_client`）：`client.chat(messages)` 是 async 方法
- 证据 index.json 路径：`{case_dir}/evidence/index.json`，结构 `{"evidence": [{"name","type","md_file","summary",...}]}`
- 证据全文 MD 路径：`{case_dir}/evidence/{md_file}`
- 提取主流程 `_do_extract_evidence` 在 `backend/case_manager.py:1483`，index.json 写入在约 2062 行
- `clear-evidence` 端点删除整个 `evidence/` 目录（`summaries/` 在其中，自动清空，**无需改动**）

---

### Task 1: 保真校验纯函数

**Files:**
- Create: `backend/evidence_summarizer.py`
- Test: `tests/test_evidence_summarizer.py`

- [ ] **Step 1: 写失败测试**

```python
"""证据详细摘要：保真校验与栏目齐全性测试"""
from evidence_perdoc import _norm_str  # noqa: F401  (确认测试环境可导入 backend 模块)
from evidence_summarizer import verify_summary_fidelity, SECTION_TITLES

FULL_TEXT = """
讯问时间：2026年3月12日14时。问：你和高蓉的借贷怎么回事？
答：2022年9月底，高蓉房子抵押，我分两笔转了20万给她，月息7分也就是14000元，
分给孙琴芳6000元，我和唐鑫一人4000元。问：一共放贷多少？答：400万元不到点，
获利30万元左右，一人15万元左右。
""".strip()

GOOD_SUMMARY = """## 概述
2022年9月底高蓉房产抵押借款20万，月息7分。
## 共谋与分工
唐鑫揽客收息，与供述人平分。
## 主观明知
明知月息7分。
## 获利与分账
获利30万元左右，一人15万元左右；孙琴芳分6000元，与唐鑫各分4000元。
## 辩解与否认
无
## 关键事实
- 2022年9月｜高蓉｜20万｜月息7分（14000元）｜2026年3月12日讯问确认
- 累计放贷400万元不到点
## 态度变化
无
## 矛盾提示
无
"""


def test_good_summary_passes():
    issues = verify_summary_fidelity(FULL_TEXT, GOOD_SUMMARY, persons="张某（嫌疑人）")
    assert issues == []


def test_missing_sections_detected():
    bad = GOOD_SUMMARY.replace("## 主观明知", "## 明知")  # 栏目名被改
    issues = verify_summary_fidelity(FULL_TEXT, bad, persons="")
    assert any("栏目缺失" in i and "主观明知" in i for i in issues)


def test_low_entity_coverage_detected():
    # 摘要丢掉全部金额和日期
    bad = """## 概述
供述人承认放贷。
## 共谋与分工
无
## 主观明知
无
## 获利与分账
无
## 辩解与否认
无
## 关键事实
无
## 态度变化
无
## 矛盾提示
无
"""
    issues = verify_summary_fidelity(FULL_TEXT, bad, persons="")
    assert any("覆盖率" in i for i in issues)


def test_missing_person_detected():
    issues = verify_summary_fidelity(FULL_TEXT, GOOD_SUMMARY, persons="李某（证人）、王五（同案犯）")
    assert any("李某" in i for i in issues)
    assert any("王五" in i for i in issues)


def test_section_titles_are_eight():
    assert SECTION_TITLES == ["概述", "共谋与分工", "主观明知", "获利与分账",
                              "辩解与否认", "关键事实", "态度变化", "矛盾提示"]
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_evidence_summarizer.py -q`
Expected: ImportError（`evidence_summarizer` 不存在）

- [ ] **Step 3: 实现 `backend/evidence_summarizer.py` 的校验部分**

```python
"""证据详细摘要：8 栏目结构化浓缩摘要的生成与保真校验

背景：按份提取后证据全文保真（单份笔录 4-9K 字符），单发分析阶段
（时间线/矛盾分析）的 _truncate_all 装不下会截断。本模块生成浓缩摘要层：
- 事实透彻性由 8 个固定栏目承担（共谋分工/主观明知/获利分账/辩解否认必列）
- 保真度由确定性校验保障（金额/日期/人名实体覆盖率 ≥90%，不达标重试）
"""
import logging
import re

logger = logging.getLogger(__name__)

# 8 个固定栏目（顺序固定，无内容填"无"，不省略）
SECTION_TITLES = ["概述", "共谋与分工", "主观明知", "获利与分账",
                  "辩解与否认", "关键事实", "态度变化", "矛盾提示"]

# 短证据阈值：全文不足 800 字无需摘要，直接复制原文
SHORT_EVIDENCE_CHARS = 800

# 实体抽取正则
_AMOUNT_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:万余元|万元|万|元)")
_RATE_RE = re.compile(r"(?:月息|月利率|日息|利率)\s*\d+(?:\.\d+)?\s*(?:分|毛|厘|%|％)|(?:月息|月利率)\s*[一二三四五六七八九十]\s*(?:分|毛|厘)")
_DATE_RE = re.compile(r"20\d{2}\s*年\s*\d{1,2}\s*\d{1,2}\s*日")
# persons 字段中的人名："张某（嫌疑人）" → 张某
_PERSON_RE = re.compile(r"([一-龥]{2,4})\s*[（(]")

COVERAGE_THRESHOLD = 0.9


def extract_entities(text: str) -> set:
    """从全文抽取关键实体（金额/利率/日期的去重集合）"""
    entities = set()
    for rx in (_AMOUNT_RE, _RATE_RE, _DATE_RE):
        for m in rx.finditer(text or ""):
            entities.add(re.sub(r"\s+", "", m.group(0)))
    return entities


def extract_person_names(persons: str) -> list:
    """从 persons 字段提取人名清单"""
    return _PERSON_RE.findall(persons or "")


def verify_summary_fidelity(full_text: str, summary: str, persons: str = "") -> list:
    """确定性保真校验。返回问题列表（空 = 通过）

    - 8 栏目齐全
    - 金额/利率/日期实体覆盖率 ≥ 90%（全文实体过少时跳过）
    - persons 字段中的人名全部出现
    """
    issues = []

    # 栏目齐全性
    missing = [t for t in SECTION_TITLES if f"## {t}" not in summary]
    if missing:
        issues.append(f"栏目缺失：{'、'.join(missing)}")

    # 实体覆盖率
    entities = extract_entities(full_text)
    if len(entities) >= 3:  # 实体太少不校验（程序性文书等）
        covered = sum(1 for e in entities if e in re.sub(r"\s+", "", summary))
        ratio = covered / len(entities)
        if ratio < COVERAGE_THRESHOLD:
            issues.append(f"覆盖率 {ratio:.0%} 低于 {COVERAGE_THRESHOLD:.0%}（{covered}/{len(entities)}）")

    # 人名
    for name in extract_person_names(persons):
        if name not in summary:
            issues.append(f"人名未出现：{name}")

    return issues
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_evidence_summarizer.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/evidence_summarizer.py tests/test_evidence_summarizer.py
git commit -m "feat: 证据摘要保真校验（8栏目齐全性+实体覆盖率+人名检查）"
```

---

### Task 2: 单份摘要生成（prompt + 重试 + 短证据跳过）

**Files:**
- Modify: `backend/evidence_summarizer.py`
- Test: `tests/test_evidence_summarizer.py`

- [ ] **Step 1: 追加失败测试**

```python
import asyncio
import json


def _fake_client(responses):
    """按调用次序返回响应的假 client"""
    state = {"i": 0}

    async def fake_chat(messages, **kw):
        r = responses[min(state["i"], len(responses) - 1)]
        state["i"] += 1
        return r

    return type("C", (), {"chat": staticmethod(fake_chat)})()


LONG_TEXT = "讯问笔录内容。" * 100  # 超过 800 字阈值


def test_short_evidence_copies_original():
    """短证据（<800字）不调 LLM，直接复制原文"""
    from evidence_summarizer import summarize_one
    ev = {"name": "询问通知书", "persons": "", "md_file": "001_x.md"}
    client = _fake_client(["不应被调用"])
    digest, warning = asyncio.run(summarize_one(client, ev, "短内容", "案件.md"))
    assert digest == "短内容"
    assert warning is False


def test_summary_generated_and_verified():
    """长证据调 LLM，输出通过校验"""
    from evidence_summarizer import summarize_one
    ev = {"name": "张某讯问笔录", "persons": "张某（嫌疑人）", "md_file": "002_x.md"}
    summary = """## 概述
内容。## 共谋与分工
无
## 主观明知
无
## 获利与分账
无
## 辩解与否认
无
## 关键事实
无
## 态度变化
无
## 矛盾提示
无
"""
    client = _fake_client([summary])
    digest, warning = asyncio.run(summarize_one(client, ev, LONG_TEXT, "案件.md"))
    assert "## 概述" in digest
    assert warning is False


def test_summary_retry_then_warning():
    """两轮都不达标：保留结果但标记警告"""
    from evidence_summarizer import summarize_one
    ev = {"name": "张某讯问笔录", "persons": "", "md_file": "002_x.md"}
    bad = "## 概述\n只有概述，缺栏目。"
    client = _fake_client([bad, bad])  # 两次都坏
    digest, warning = asyncio.run(summarize_one(client, ev, LONG_TEXT, "案件.md"))
    assert digest == bad
    assert warning is True
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_evidence_summarizer.py -q`
Expected: 3 failed（`summarize_one` 不存在）

- [ ] **Step 3: 在 `backend/evidence_summarizer.py` 追加**

```python
import asyncio
from typing import Optional, Tuple

_SUMMARY_SYSTEM = """你是刑事案卷阅卷助手。给定一份证据全文，输出结构化详细摘要。

输出 Markdown，必须包含且仅包含以下 8 个栏目（## 标题，顺序固定，无内容填"无"，不得省略栏目）：

## 概述
事件脉络叙述（长度随事实量自然伸缩）
## 共谋与分工
谁提议/谁出资/谁执行/如何约定，逐环节保留
## 主观明知
明知内容（如利率违法性、资金来源等）
## 获利与分账
总获利、每人分得数额、分配方式、分配时间
## 辩解与否认
无罪/罪轻辩解，逐条保留，绝不省略
## 关键事实
逐笔一行：时间｜主体｜行为｜金额｜（利率/资金来源/分成）
## 态度变化
供述稳定性、翻供、认罪认罚
## 矛盾提示
与其他证据或前后供述的矛盾点

硬性要求：全部金额、日期、人名必须出现在摘要中，不得用"等""若干"概括。"""


async def summarize_one(client, ev: dict, full_text: str, source_name: str,
                        timeout: int = 600) -> Tuple[str, bool]:
    """生成单份证据的详细摘要。

    Returns:
        (digest, warning)：digest 为摘要文本；warning=True 表示两轮校验未过（仍保留结果）
    """
    # 短证据无需摘要，直接复制原文
    if len(full_text) < SHORT_EVIDENCE_CHARS:
        return full_text, False

    persons = ev.get("persons", "")
    base_msg = f"证据名称：《{ev.get('name', '')}》（来源：{source_name}）\n\n证据全文：\n{full_text}"

    result = ""
    issues = ["调用失败"]
    for attempt in (1, 2):
        try:
            user_msg = base_msg if attempt == 1 else (
                base_msg + f"\n\n⚠️ 上次输出未通过校验：{'；'.join(issues)}。请务必修正后重新输出完整 8 栏目。")
            result = await asyncio.wait_for(client.chat([
                {"role": "system", "content": _SUMMARY_SYSTEM},
                {"role": "user", "content": user_msg},
            ]), timeout=timeout)
        except Exception as e:
            logger.warning(f"[证据摘要] 《{ev.get('name')}》第 {attempt} 次调用失败: {e}")
            issues = ["调用失败"]
            continue

        issues = verify_summary_fidelity(full_text, result, persons)
        if not issues:
            break
        logger.warning(f"[证据摘要] 《{ev.get('name')}》第 {attempt} 次校验未过: {issues}")

    if not result:
        # 两轮调用都失败：回退全文，保证分析端有内容可消费
        return full_text, True
    return result, bool(issues)
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_evidence_summarizer.py -q`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add backend/evidence_summarizer.py tests/test_evidence_summarizer.py
git commit -m "feat: 单份证据摘要生成（8栏目prompt+两轮校验重试+短证据复制原文+失败回退全文）"
```

---

### Task 3: summarize_evidence 主流程（缓存 + 并发 + index.json 双写）

**Files:**
- Modify: `backend/evidence_summarizer.py`
- Test: `tests/test_evidence_summarizer.py`

- [ ] **Step 1: 追加失败测试**

```python
from pathlib import Path


def _make_case(tmp_path: Path, evidences: list) -> Path:
    """构造测试案件目录：evidence/index.json + evid md 文件"""
    case_dir = tmp_path / "case_x"
    ev_dir = case_dir / "evidence"
    ev_dir.mkdir(parents=True)
    for ev in evidences:
        (ev_dir / ev["md_file"]).write_text(ev.pop("_full_text"), encoding="utf-8")
    (ev_dir / "index.json").write_text(json.dumps(
        {"evidence": evidences}, ensure_ascii=False), encoding="utf-8")
    return case_dir


def test_summarize_evidence_writes_digest(tmp_path):
    """主流程：摘要写入 index.json digest 字段 + summaries/ 落盘"""
    from evidence_summarizer import summarize_evidence
    long_text = "2026年3月12日讯问。" + "内容。" * 200
    case_dir = _make_case(tmp_path, [
        {"name": "张某讯问笔录", "type": "犯罪嫌疑人供述和辩解", "persons": "",
         "md_file": "001_张某.md", "_full_text": long_text},
        {"name": "通知书", "type": "程序性文书", "persons": "",
         "md_file": "002_通知.md", "_full_text": "短内容"},
    ])
    good_summary = """## 概述
x
## 共谋与分工
无
## 主观明知
无
## 获利与分账
无
## 辩解与否认
无
## 关键事实
无
## 态度变化
无
## 矛盾提示
无
"""
    client = _fake_client([good_summary])
    stats = asyncio.run(summarize_evidence(client, case_dir, concurrency=2))

    assert stats["total"] == 2 and stats["done"] == 1 and stats["skipped"] == 1
    index = json.loads((case_dir / "evidence" / "index.json").read_text(encoding="utf-8"))
    assert index["evidence"][0]["digest"] == good_summary
    assert index["evidence"][0]["digest_warning"] is False
    assert index["evidence"][1]["digest"] == "短内容"
    assert (case_dir / "evidence" / "summaries" / "001_张某.md").exists()
    assert not (case_dir / "evidence" / "summaries" / "002_通知.md").exists()


def test_summarize_evidence_resume_skips_cached(tmp_path):
    """断点续传：已有缓存且源文件未变 → 不重复调用 LLM"""
    from evidence_summarizer import summarize_evidence
    long_text = "内容。" * 300
    case_dir = _make_case(tmp_path, [
        {"name": "张某讯问笔录", "type": "x", "persons": "", "md_file": "001_张某.md",
         "_full_text": long_text},
    ])
    # 预置缓存
    summaries = case_dir / "evidence" / "summaries"
    summaries.mkdir()
    (summaries / "001_张某.md").write_text("已缓存摘要", encoding="utf-8")
    (summaries / "001_张某.meta.json").write_text(
        json.dumps({"src_len": len(long_text)}), encoding="utf-8")

    client = _fake_client(["不应被调用"])
    stats = asyncio.run(summarize_evidence(client, case_dir))
    assert stats["cached"] == 1 and stats["done"] == 0
    index = json.loads((case_dir / "evidence" / "index.json").read_text(encoding="utf-8"))
    assert index["evidence"][0]["digest"] == "已缓存摘要"


def test_summarize_evidence_regenerates_on_source_change(tmp_path):
    """源 MD 变化 → 缓存失效重新生成"""
    from evidence_summarizer import summarize_evidence
    long_text = "内容。" * 300
    case_dir = _make_case(tmp_path, [
        {"name": "张某讯问笔录", "type": "x", "persons": "", "md_file": "001_张某.md",
         "_full_text": long_text},
    ])
    summaries = case_dir / "evidence" / "summaries"
    summaries.mkdir()
    (summaries / "001_张某.md").write_text("旧摘要", encoding="utf-8")
    (summaries / "001_张某.meta.json").write_text(
        json.dumps({"src_len": 999}), encoding="utf-8")  # 长度不匹配 → 失效

    good = ("## 概述\nx\n" + "".join(f"## {t}\n无\n" for t in SECTION_TITLES[1:])).strip()
    client = _fake_client([good])
    stats = asyncio.run(summarize_evidence(client, case_dir))
    assert stats["done"] == 1
    assert (summaries / "001_张某.md").read_text(encoding="utf-8") == good
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_evidence_summarizer.py -q`
Expected: 3 failed（`summarize_evidence` 不存在）

- [ ] **Step 3: 在 `backend/evidence_summarizer.py` 追加主流程**

```python
async def summarize_evidence(client, case_dir: Path, concurrency: int = 3) -> dict:
    """证据详细摘要主流程：提取完成后自动串联调用。

    双写：index.json 每条证据的 digest/digest_warning 字段 + evidence/summaries/ 落盘缓存。
    断点续传：缓存 meta 的 src_len 与证据 MD 当前长度一致则复用。
    失败不抛异常（摘要步骤不阻塞分析，分析端对无 digest 的证据回退全文）。

    Returns:
        {"total", "done", "cached", "skipped", "failed"}
    """
    from pathlib import Path as _Path  # 局部导入避免循环
    import json as _json

    evidence_dir = _Path(case_dir) / "evidence"
    index_file = evidence_dir / "index.json"
    stats = {"total": 0, "done": 0, "cached": 0, "skipped": 0, "failed": 0}
    if not index_file.exists():
        logger.warning("[证据摘要] index.json 不存在，跳过")
        return stats

    index = _json.loads(index_file.read_text(encoding="utf-8"))
    evidences = index.get("evidence", [])
    stats["total"] = len(evidences)

    summaries_dir = evidence_dir / "summaries"
    summaries_dir.mkdir(exist_ok=True)

    sem = asyncio.Semaphore(concurrency)

    async def _one(ev: dict):
        md_name = ev.get("md_file", "")
        md_path = evidence_dir / md_name
        if not md_name or not md_path.exists():
            stats["failed"] += 1
            return
        full_text = md_path.read_text(encoding="utf-8")

        cache_md = summaries_dir / md_name
        cache_meta = summaries_dir / (md_name + ".meta.json")

        # 断点续传：缓存有效则复用
        if cache_md.exists() and cache_meta.exists():
            try:
                meta = _json.loads(cache_meta.read_text(encoding="utf-8"))
                if meta.get("src_len") == len(full_text):
                    ev["digest"] = cache_md.read_text(encoding="utf-8")
                    ev["digest_warning"] = bool(meta.get("warning", False))
                    stats["cached"] += 1
                    return
            except Exception:
                pass

        async with sem:
            digest, warning = await summarize_one(client, ev, full_text, ev.get("source", ""))

        ev["digest"] = digest
        ev["digest_warning"] = warning
        if len(full_text) < SHORT_EVIDENCE_CHARS:
            stats["skipped"] += 1  # 短证据复制原文，不落缓存
        else:
            stats["done"] += 1
            try:
                cache_md.write_text(digest, encoding="utf-8")
                cache_meta.write_text(_json.dumps(
                    {"src_len": len(full_text), "warning": warning},
                    ensure_ascii=False), encoding="utf-8")
            except Exception as e:
                logger.warning(f"[证据摘要] 缓存写入失败 {md_name}: {e}")
        if warning:
            stats["failed"] += 1

    await asyncio.gather(*(_one(ev) for ev in evidences))

    index_file.write_text(_json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"[证据摘要] 完成: {stats}")
    return stats
```

注意：`import json` 和 `from pathlib import Path` 在 Task 1 的文件顶部已有 `logging/re`，需要把 `import asyncio`、`import json`、`from pathlib import Path` 合并到文件顶部（Task 2/3 的局部 import 只是计划展示的聚焦写法，实现时统一放顶部）。

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_evidence_summarizer.py -q`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add backend/evidence_summarizer.py tests/test_evidence_summarizer.py
git commit -m "feat: 摘要主流程（并发+断点续传+index.json双写+源变化自动重生成）"
```

---

### Task 4: 提取流程串联摘要

**Files:**
- Modify: `backend/case_manager.py`（`_do_extract_evidence` 尾部，约 2086 行「清理临时文件」之后）
- Test: `tests/test_evidence_summarizer.py`

- [ ] **Step 1: 追加失败测试（串联点冒烟）**

```python
def test_chain_call_shape(monkeypatch, tmp_path):
    """串联调用：summarize_evidence 接收 (client, case_dir, concurrency)，异常被吞掉不阻塞"""
    import evidence_summarizer

    called = {}

    async def fake_summarize(client, case_dir, concurrency=3):
        called["case_dir"] = case_dir
        called["concurrency"] = concurrency
        return {"total": 0, "done": 0, "cached": 0, "skipped": 0, "failed": 0}

    monkeypatch.setattr(evidence_summarizer, "summarize_evidence", fake_summarize)
    stats = asyncio.run(evidence_summarizer.summarize_evidence(None, tmp_path, concurrency=5))
    assert called["concurrency"] == 5 and stats["total"] == 0
```

此测试只锁定函数签名（case_manager 的串联代码是 try/except 包裹的单次调用，集成验证靠 Task 8 真实案件）。

- [ ] **Step 2: 运行确认通过**

Run: `python3 -m pytest tests/test_evidence_summarizer.py -q`
Expected: 12 passed

- [ ] **Step 3: 修改 `backend/case_manager.py`**

在 `_do_extract_evidence` 中，找到这段（约 2083-2088 行）：

```python
        # 清理临时文件（无论走哪个分支都清理）
        old_temp = evidence_dir / "_temp_extract"
        if old_temp.exists():
            shutil.rmtree(old_temp)
            logger.info(f"[证据提取] 临时目录已清理")

        logger.info(f"[证据提取] 完成，共 {len(all_evidence)} 份证据")
```

在 `临时目录已清理` 之后、`完成，共` 之前插入：

```python
        # 详细摘要层：提取完成后自动生成（失败不阻塞分析，分析端对无 digest 的证据回退全文）
        try:
            from evidence_summarizer import summarize_evidence
            from llm_client import get_llm_client
            conc = int(cfg.get("evidence_concurrency", 3) or 3)
            sum_stats = await summarize_evidence(get_llm_client(), case_path, concurrency=conc)
            logger.info(f"[证据摘要] 完成: {sum_stats}")
        except Exception as e:
            logger.warning(f"[证据摘要] 生成失败（不影响提取与分析，将回退全文）: {e}")
```

（`cfg` 在函数开头已 `load_config()`，直接复用。）

- [ ] **Step 4: 全套件回归**

Run: `python3 -m pytest tests/ -q 2>&1 | tail -1`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add backend/case_manager.py tests/test_evidence_summarizer.py
git commit -m "feat: 提取完成后自动串联摘要生成（失败回退全文不阻塞分析）"
```

---

### Task 5: 分析端 prefer_summary 切换

**Files:**
- Modify: `backend/analysis_engine.py`（`_load_evidence_texts` 约 365 行；阶段3 约 858 行；阶段52 约 1224 行）
- Test: `tests/test_prefer_summary.py`

- [ ] **Step 1: 写失败测试**

```python
"""分析端双层消费：prefer_summary=True 用 digest，False 用全文"""
from analysis_engine import _apply_digest


def test_digest_used_when_preferred():
    ev = {"name": "张某笔录", "digest": "浓缩摘要内容"}
    assert _apply_digest(ev, "全文内容", prefer_summary=True) == "# 张某笔录\n\n浓缩摘要内容"


def test_fulltext_when_not_preferred():
    ev = {"name": "张某笔录", "digest": "浓缩摘要内容"}
    assert _apply_digest(ev, "全文内容", prefer_summary=False) == "全文内容"


def test_fallback_fulltext_when_no_digest():
    ev = {"name": "张某笔录", "digest": ""}
    assert _apply_digest(ev, "全文内容", prefer_summary=True) == "全文内容"
    ev2 = {"name": "张某笔录"}
    assert _apply_digest(ev2, "全文内容", prefer_summary=True) == "全文内容"
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_prefer_summary.py -q`
Expected: ImportError（`_apply_digest` 不存在）

- [ ] **Step 3: 修改 `backend/analysis_engine.py`**

3a. 在 `_split_indictment_and_evidence` 函数前加：

```python
def _apply_digest(ev: dict, text: str, prefer_summary: bool) -> str:
    """单发分析阶段（时间线/矛盾分析）用浓缩摘要替代全文；无摘要回退全文"""
    if prefer_summary and str(ev.get("digest", "")).strip():
        return f"# {ev.get('name', '')}\n\n{ev['digest']}"
    return text
```

3b. `_load_evidence_texts` 签名改为：

```python
    def _load_evidence_texts(self, prefer_summary: bool = False) -> List[Dict[str, str]]:
```

3c. 在该方法中找到（约 419 行）：

```python
                            if text.strip():
```

在其前面插入：

```python
                            # 单发阶段用浓缩摘要（digest）；无摘要回退已组装的全文
                            text = _apply_digest(ev, text, prefer_summary)
```

3d. 阶段3（约 858 行）：

```python
        texts = self._load_evidence_texts()
```
改为：
```python
        texts = self._load_evidence_texts(prefer_summary=True)
```

3e. 阶段52（约 1236 行）同样改为 `prefer_summary=True`。

⚠️ 只改这两处；阶段2（709 行）、阶段5（1338 行）、质证（1729 行）等其余调用点**不改**（默认 False 读全文）。

- [ ] **Step 4: 运行测试 + 全套件回归**

Run: `python3 -m pytest tests/test_prefer_summary.py -q && python3 -m pytest tests/ -q 2>&1 | tail -1`
Expected: 3 passed；全套件 passed

- [ ] **Step 5: Commit**

```bash
git add backend/analysis_engine.py tests/test_prefer_summary.py
git commit -m "feat: 分析端双层消费（时间线/矛盾分析用浓缩摘要，分批阶段保持全文）"
```

---

### Task 6: 前端 ReportPage 摘要/全文切换

**Files:**
- Modify: `frontend/src/api/evidence.ts`
- Modify: `frontend/src/pages/ReportPage.tsx`（证据下拉区域，约 3315-3345 行；`loadEvidenceContent` 约 360 行）

- [ ] **Step 1: API 类型加字段**

`frontend/src/api/evidence.ts` 的 `EvidenceIndexResponse` 里 `evidence: any[]` 不变（any 已涵盖新字段），无需改动。跳过。

- [ ] **Step 2: 找到 ReportPage 证据项的来源**

Run: `grep -n "evidenceItems\|setEvidenceItems" frontend/src/pages/ReportPage.tsx | head -5`
确认 evidenceItems 的元素含 `mdFile`、`displayName`，找到其构建处，把 `digest` 和 `digestWarning` 从 index 数据带进来（如 `digest: ev.digest || ''`）。

- [ ] **Step 3: 加视图切换状态与 UI**

在 `selectedEvidenceContent` state 附近加：

```tsx
const [evidenceViewMode, setEvidenceViewMode] = useState<'digest' | 'full'>('digest')
```

在证据下拉列表的 `</select>` 之后（约 3331 行），插入切换按钮组（仅当前证据有 digest 时显示）：

```tsx
{(() => {
  const cur = evidenceItems.find(i => i.id === selectedEvidenceId)
  if (!cur || !(cur as any).digest) return null
  return (
    <div style={{ display: 'flex', gap: '4px', padding: '6px 10px', borderBottom: `1px solid ${colors.border}` }}>
      {(['digest', 'full'] as const).map(mode => (
        <button key={mode}
          onClick={() => setEvidenceViewMode(mode)}
          style={{
            flex: 1, padding: '4px 0', fontSize: '11px', border: 'none', borderRadius: '4px', cursor: 'pointer',
            background: evidenceViewMode === mode ? colors.accent : 'transparent',
            color: evidenceViewMode === mode ? '#fff' : colors.textSecondary,
          }}>
          {mode === 'digest' ? '摘要' : '全文'}
        </button>
      ))}
      {(cur as any).digestWarning && (
        <span title="保真校验未完全通过，建议核对全文" style={{ fontSize: '11px', color: '#b7791f', alignSelf: 'center' }}>⚠️</span>
      )}
    </div>
  )
})()}
```

- [ ] **Step 4: 内容区按模式渲染**

把 MD 内容区（约 3335 行）的渲染源改为按模式选择：

```tsx
{(() => {
  const cur = evidenceItems.find(i => i.id === selectedEvidenceId)
  const digest = (cur as any)?.digest || ''
  const content = (evidenceViewMode === 'digest' && digest) ? digest : selectedEvidenceContent
  return content ? (
    <div
      className="report-content"
      style={{ fontSize: '12px', lineHeight: '1.75' }}
      dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(marked.parse(content, { async: false }) as string) }}
    />
  ) : (
    <div style={{ padding: '20px 0', fontSize: '12px', color: colors.textTertiary, textAlign: 'center' }}>选择证据查看详情</div>
  )
})()}
```

- [ ] **Step 5: 类型检查 + 构建**

Run: `cd frontend && npx tsc --noEmit && npm run build 2>&1 | tail -3`
Expected: 无错误，构建成功

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/ReportPage.tsx
git commit -m "feat: 报告页证据面板摘要/全文切换（默认摘要+保真警告标记）"
```

---

### Task 7: 前端 CaseDetailPage 预览切换

**Files:**
- Modify: `frontend/src/pages/CaseDetailPage/components/Preview.tsx`
- Modify: `frontend/src/pages/CaseDetailPage.tsx`（`onPreviewEvidence` 回调，约 530 行）
- Modify: `frontend/src/pages/CaseDetailPage/components/Step2Analyze.tsx`（预览回调签名透传 digest，可选）

- [ ] **Step 1: Preview 组件加可选 digest props**

`Preview.tsx` 的 props 类型加：

```tsx
digest?: string
digestWarning?: boolean
```

组件内加 state 与切换（仅 digest 存在时显示）：

```tsx
const [viewMode, setViewMode] = useState<'digest' | 'full'>(digest ? 'digest' : 'full')
```

MD 渲染处（`file.name.endsWith('.md')` 分支，约 61 行）：viewMode 为 digest 时用 `marked.parse(digest)`，否则用现有 serve-file 加载的 html。切换 UI 复用 Task 6 的按钮组样式（两按钮：摘要/全文 + ⚠️ 警告标记）。

- [ ] **Step 2: CaseDetailPage 传入 digest**

`onPreviewEvidence`（约 530 行）改为：

```tsx
onPreviewEvidence={(mdFile, evId) => {
  const mdPath = `${API_BASE}/cases/${caseId}/serve-file?file_path=${encodeURIComponent(mdFile)}&dir=evidence`
  const ev = evidenceList.find((e: any) => e.id === evId)
  handleOpenFile({ id: String(evId), name: mdFile, size: 0, status: 'done', path: mdPath } as unknown as CaseFile)
  setPreviewDigest(ev?.digest || '')
  setPreviewDigestWarning(!!ev?.digest_warning)
}}
```

并在该组件加 `previewDigest`/`previewDigestWarning` state，渲染处：

```tsx
{previewFile && <Preview file={previewFile as unknown as PreviewFile} onClose={closePreview} digest={previewDigest} digestWarning={previewDigestWarning} />}
```

（若 `evidenceList` 元素上没有 digest 字段，需确认 `loadEvidence` hook 直接把 index.json 的 evidence 原样存入——`evidence: any[]` 原生带过，无需转换。）

- [ ] **Step 3: 类型检查 + 构建**

Run: `cd frontend && npx tsc --noEmit && npm run build 2>&1 | tail -3`
Expected: 无错误

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/CaseDetailPage.tsx frontend/src/pages/CaseDetailPage/components/Preview.tsx
git commit -m "feat: 案件详情页证据预览摘要/全文切换"
```

---

### Task 8: 真实案件验证 + 文档

**Files:**
- Modify: `CLAUDE.md`（目录结构加 evidence_summarizer.py 一行）
- 验证用脚本：`/tmp/perdoc_validation/run_summarize.py`（临时，不进仓库）

- [ ] **Step 1: 写验证脚本**

```python
"""真实案件摘要验证：冯叶飞案第2卷提取产物"""
import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, "/Users/zhanghan/.openclaw/workspace/criminal-llm/backend")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from evidence_summarizer import summarize_evidence  # noqa: E402
from llm_client import get_llm_client  # noqa: E402

# 用 Task 3 真实提取的第2卷产物搭一个独立案件目录
SRC = Path("/tmp/perdoc_validation/第2卷")
CASE = Path("/tmp/perdoc_validation/summary_case")

async def main():
    import json, shutil
    ev_dir = CASE / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)
    # 从提取产物重建 index.json（模拟生产结构）
    evidences = []
    for i, f in enumerate(sorted(SRC.glob("evid_*.md")), 1):
        evidences.append({"id": i, "name": f.stem.split("_", 2)[-1], "type": "",
                          "persons": "", "md_file": f.name, "source": "第2卷_去水印.md"})
        shutil.copy2(f, ev_dir / f.name)
    (ev_dir / "index.json").write_text(json.dumps({"evidence": evidences}, ensure_ascii=False), encoding="utf-8")

    t0 = time.time()
    stats = await summarize_evidence(get_llm_client(), CASE, concurrency=3)
    print(f"耗时 {time.time()-t0:.0f}s, stats={stats}")

    index = json.loads((ev_dir / "index.json").read_text(encoding="utf-8"))
    total_chars = sum(len(e.get("digest", "")) for e in index["evidence"])
    warned = [e["name"] for e in index["evidence"] if e.get("digest_warning")]
    print(f"摘要总字符: {total_chars}（单发预算 125 万字符）")
    print(f"警告证据: {warned or '无'}")

asyncio.run(main())
```

- [ ] **Step 2: 运行真实验证（后台，约 5-10 分钟）**

Run: `cd backend && python3 /tmp/perdoc_validation/run_summarize.py`
验证点：
- 29 份证据全部有 digest
- 抽查《赵志强第十次讯问笔录》（44 对问答那份）的 digest：共谋与分工、获利与分账栏目有实质内容，金额齐全
- 摘要总字符 << 125 万（预期 3-6 万）

- [ ] **Step 3: 抽查保真（人工）**

```bash
cat "/tmp/perdoc_validation/summary_case/evidence/summaries/evid_023_赵志强第十次讯问笔录.md"
```
确认：共谋与分工/获利与分账/辩解与否认栏目非"无"且有细节。

- [ ] **Step 4: 更新 CLAUDE.md**

在目录结构 `analyzer_api.py` 行后加：

```
│   ├── evidence_summarizer.py  # 证据详细摘要（8栏目浓缩，供单发分析阶段消费）
```

并在「证据提取关键点」一节末尾加一条：

```markdown
- **详细摘要层**：提取完成后自动生成 8 栏目浓缩摘要（`evidence_summarizer.py`），写入 index.json 的 `digest` 字段 + `evidence/summaries/` 缓存；阶段3时间线/阶段52矛盾分析通过 `_load_evidence_texts(prefer_summary=True)` 消费摘要，分批阶段保持全文
```

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: 证据摘要层真实验证通过 + CLAUDE.md 更新"
```

---

## Self-Review 记录

- **spec 覆盖**：8 栏目（T1/2）✓ 保真校验（T1）✓ 双写存储（T3）✓ 断点续传/失效（T3）✓ 提取串联（T4）✓ prefer_summary（T5）✓ UI 双通道（T6/7）✓ 真实验证（T8）✓ clear-evidence 无需改（整个 evidence/ 删除）✓ 旧案件回退全文（T5 `_apply_digest` 空 digest 回退 + T6/7 无 digest 不显示切换）✓
- **字段命名一致性**：全计划统一 `digest` / `digest_warning`（index.json 后端）→ `digest` / `digestWarning`（前端 camelCase 映射处在 T6 Step2 显式转换）
- **已知偏差**：spec 说写 `summary` 字段，实际 `summary` 已被全文摘要占用（`EVIDENCE_STRUCTURED_FIELDS`），改用 `digest`——已在计划开头声明
