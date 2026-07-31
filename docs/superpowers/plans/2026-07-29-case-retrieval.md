# 案例检索服务实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为刑事案卷分析系统构建案例检索服务：《刑事审判参考》1750 篇离线提炼为卡片库 → 云端独立微服务提供 FTS 检索（API Key 鉴权）→ 桌面端报告页手动检索、勾选案例注入阶段 4 重生成。

**Architecture:** 三个代码库协同——`criminal-llm`（提炼脚本 scripts/ + 桌面后端代理 backend/ + 前端面板 frontend/）、`criminal-llm-auth`（云端 8000，扩展 API Key 管理）、`criminal-llm-cases`（新建，云端 8001，案例检索微服务）。LLM 调用全部在桌面端/离线侧。

**Tech Stack:** Python 3.13 / FastAPI / SQLite FTS5 / pytest / React 18 + TypeScript / 百炼 API（OpenAI 兼容）

**设计文档:** `docs/superpowers/specs/2026-07-29-case-retrieval-design.md`

**与 spec 的两处细化（实现层面更合理）：**
1. 用量计数表存于案例服务自己的 `usage.db`（`cases.db` 会被 scp 覆盖部署、`auth.db` 对案例服务只读挂载，都不适合写计数）
2. 案例服务不启用 CORS 中间件——只被桌面本地后端服务端到服务端调用，浏览器不直连
3. 【实施修正】桌面本地后端代理前缀由 `/api/cases` 改为 `/api/case-search`：case_manager 的 case_router 已占用 `/api/cases/{case_id}`，形状冲突不可调和。云端 8001 服务路径保持 `/api/cases/*` 不变

---

## 代码库路径约定

| 库 | 本地路径 |
|---|---|
| 桌面端 | `/Users/zhanghan/.openclaw/workspace/criminal-llm` |
| 认证服务 | `/Users/zhanghan/.openclaw/workspace/criminal-llm-auth` |
| 案例服务（新建） | `/Users/zhanghan/.openclaw/workspace/criminal-llm-cases` |
| 案例 MD 源 | `/Users/zhanghan/Desktop/刑事审判参考_MD` |

各库内提交遵循 `<type>: <描述>` 格式，使用中文。

---

# 阶段 A：离线提炼脚本（criminal-llm）

### Task 1: 文本工具函数（bigram / 文件名解析 / 章节截取）

**Files:**
- Create: `criminal-llm/scripts/__init__.py`（空文件）
- Create: `criminal-llm/scripts/case_text_utils.py`
- Create: `criminal-llm/tests/conftest.py`
- Test: `criminal-llm/tests/test_case_text_utils.py`

- [ ] **Step 1: 写 conftest.py（让测试能 import scripts 和 backend）**

```python
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend"))
```

- [ ] **Step 2: 写失败测试 `tests/test_case_text_utils.py`**

```python
from case_text_utils import to_bigrams, parse_case_filename, extract_sections

SAMPLE_MD = """【第1000号】李某甲等寻衅滋事案——未成年人多次强取财物的案件如何处理

## 一、基本案情

被告人李某甲，男。

## 二、主要问题

未成年人多次强取其他未成年人少量财物的案件如何处理?

## 三、裁判理由

本案在审理过程中存在两种意见。我们赞成后一种意见。
""" + "理由正文。" * 200


def test_to_bigrams_chinese():
    assert to_bigrams("寻衅滋事") == "寻衅 衅滋 滋事"


def test_to_bigrams_mixed():
    # token 规则：连续中文 -> bigram；连续英文/数字 -> 整词转小写；单字中文保留
    # "刑法第293条" -> ["刑法第","293","条"] -> "刑法 法第" + "293" + "条"
    assert to_bigrams("刑法第293条") == "刑法 法第 293 条"


def test_to_bigrams_single_char():
    assert to_bigrams("罪") == "罪"


def test_parse_case_filename():
    case_no, title = parse_case_filename(
        "【第1000号】李某甲等寻衅滋事案——未成年人多次强取财物的案件如何处理.md"
    )
    assert case_no == "第1000号"
    assert title.startswith("李某甲等寻衅滋事案")


def test_parse_case_filename_invalid():
    assert parse_case_filename("随便一个文件.md") is None


def test_extract_sections():
    sections = extract_sections(SAMPLE_MD)
    assert "未成年人多次强取" in sections["issue"]
    assert sections["reasoning_excerpt"].startswith("本案在审理过程中")
    assert len(sections["reasoning_excerpt"]) <= 500


def test_extract_sections_missing_issue():
    assert extract_sections("## 一、基本案情\n没有章节")["issue"] is None
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd /Users/zhanghan/.openclaw/workspace/criminal-llm && python -m pytest tests/test_case_text_utils.py -v`
Expected: FAIL（ModuleNotFoundError: case_text_utils）

- [ ] **Step 4: 实现 `scripts/case_text_utils.py`**

```python
"""案例文本工具：中文 bigram 切分、文件名解析、MD 章节截取"""
import re

_TOKEN_RE = re.compile(r"[0-9A-Za-z]+|[一-鿿]+")
_FILENAME_RE = re.compile(r"^【(第\d+号)】(.+)\.md$")
_ISSUE_RE = re.compile(r"##\s*二、主要问题\s*\n(.*?)(?=\n##\s|\Z)", re.DOTALL)
_REASON_RE = re.compile(r"##\s*三、裁判理由\s*\n(.*?)(?=\n##\s|\Z)", re.DOTALL)

REASONING_EXCERPT_MAX = 500


def to_bigrams(text: str) -> str:
    """把文本切为 FTS 可索引的 token 串：中文 bigram，英文/数字整词（小写）"""
    out = []
    for tok in _TOKEN_RE.findall(text):
        if tok.isascii():
            out.append(tok.lower())
        elif len(tok) == 1:
            out.append(tok)
        else:
            out.extend(a + b for a, b in zip(tok, tok[1:]))
    return " ".join(out)


def parse_case_filename(filename: str) -> tuple[str, str] | None:
    """'【第1000号】李某甲等寻衅滋事案——….md' -> ('第1000号', '李某甲等寻衅滋事案——…')；不规范返回 None"""
    m = _FILENAME_RE.match(filename)
    if not m:
        return None
    return m.group(1), m.group(2).strip()


def extract_sections(md_text: str) -> dict:
    """按 MD 标题结构截取「二、主要问题」与「三、裁判理由」（原文不改写）"""
    issue_m = _ISSUE_RE.search(md_text)
    reason_m = _REASON_RE.search(md_text)
    return {
        "issue": issue_m.group(1).strip() if issue_m else None,
        "reasoning_excerpt": reason_m.group(1).strip()[:REASONING_EXCERPT_MAX] if reason_m else "",
    }
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_case_text_utils.py -v`
Expected: 7 passed（如 mixed 用例预期值与实现有出入，按规则修正断言）

- [ ] **Step 6: 提交**

```bash
cd /Users/zhanghan/.openclaw/workspace/criminal-llm
git add scripts/ tests/conftest.py tests/test_case_text_utils.py
git commit -m "feat: 案例提炼文本工具（bigram/文件名解析/章节截取）"
```

---

### Task 2: 卡片校验 + LLM 提炼（带重试）

**Files:**
- Create: `criminal-llm/scripts/case_distiller.py`
- Test: `criminal-llm/tests/test_case_distiller.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_case_distiller.py
import json
import pytest
from case_distiller import validate_card, distill_case


def good_card():
    return {
        "charges": ["寻衅滋事罪"],
        "holding_summary": "未成年人以轻微暴力强索少量财物的，" + "应认定为寻衅滋事罪。" * 20,
        "keywords": ["寻衅滋事", "未成年人", "强拿硬要", "轻微暴力"],
    }


def test_validate_card_ok():
    assert validate_card(good_card()) == []


def test_validate_card_empty_charges():
    card = good_card()
    card["charges"] = []
    assert any("charges" in e for e in validate_card(card))


def test_validate_card_summary_too_short():
    card = good_card()
    card["holding_summary"] = "太短"
    assert any("holding_summary" in e for e in validate_card(card))


def test_validate_card_keywords_count():
    card = good_card()
    card["keywords"] = ["只有一个"]
    assert any("keywords" in e for e in validate_card(card))


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    """模拟百炼 API：第一次返回非法 JSON，第二次返回合法卡片"""

    def __init__(self):
        self.calls = 0

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls += 1
        if self.calls == 1:
            content = "这不是JSON"
        else:
            content = json.dumps(good_card(), ensure_ascii=False)
        return FakeResponse({"choices": [{"message": {"content": content}}]})


def test_distill_case_retries_once_then_succeeds():
    session = FakeSession()
    card = distill_case(session, "http://fake/v1", "key", "model", "标题", "正文" * 100)
    assert session.calls == 2
    assert card["charges"] == ["寻衅滋事罪"]


class BrokenSession:
    def post(self, url, headers=None, json=None, timeout=None):
        return FakeResponse({"choices": [{"message": {"content": "非JSON"}}]})


def test_distill_case_fails_after_retry():
    with pytest.raises(RuntimeError, match="提炼失败"):
        distill_case(BrokenSession(), "http://fake/v1", "key", "model", "标题", "正文" * 100)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_case_distiller.py -v`
Expected: FAIL（ModuleNotFoundError: case_distiller）

- [ ] **Step 3: 实现 `scripts/case_distiller.py`**

```python
"""案例卡片 LLM 提炼：百炼 OpenAI 兼容接口，严格 JSON 输出 + 校验 + 重试一次"""
import json

PROMPT_TEMPLATE = """你是法律知识工程师。请从以下《刑事审判参考》案例中提炼结构化信息。
只输出严格 JSON（不要输出任何其他内容），格式：
{{
  "charges": ["涉及罪名1", "罪名2"],
  "holding_summary": "裁判要旨，200-400字，概括本案确立的裁判规则，不得编造原文没有的内容",
  "keywords": ["5-10个检索关键词，含罪名、行为特征、法律概念"]
}}

案例标题：{title}

案例原文（节选）：
{excerpt}"""

EXCERPT_MAX = 6000


def validate_card(data: dict) -> list[str]:
    """校验卡片字段，返回错误列表（空列表 = 通过）"""
    errors = []
    charges = data.get("charges")
    if not isinstance(charges, list) or not charges or not all(isinstance(c, str) and c for c in charges):
        errors.append("charges 必须是非空字符串列表")
    summary = data.get("holding_summary")
    if not isinstance(summary, str) or not (100 <= len(summary) <= 600):
        errors.append("holding_summary 长度须在 100-600 字")
    keywords = data.get("keywords")
    if not isinstance(keywords, list) or not (3 <= len(keywords) <= 15):
        errors.append("keywords 须为 3-15 个元素的列表")
    return errors


def distill_case(session, base_url: str, api_key: str, model: str, title: str, md_text: str) -> dict:
    """调 LLM 提炼单篇案例。session 为 requests.Session（测试注入假 session）。失败重试一次后抛 RuntimeError"""
    prompt = PROMPT_TEMPLATE.format(title=title, excerpt=md_text[:EXCERPT_MAX])
    last_err: Exception | None = None
    for _attempt in range(2):
        try:
            resp = session.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                },
                timeout=300,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            data = json.loads(content)
            errors = validate_card(data)
            if errors:
                raise ValueError("卡片校验失败: " + "; ".join(errors))
            return data
        except Exception as e:
            last_err = e
    raise RuntimeError(f"提炼失败（已重试）: {last_err}")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_case_distiller.py -v`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add scripts/case_distiller.py tests/test_case_distiller.py
git commit -m "feat: 案例卡片 LLM 提炼（校验+重试）"
```

---

### Task 3: cases.db 构建 + 增量主流程

**Files:**
- Create: `criminal-llm/scripts/distill_cases.py`
- Test: `criminal-llm/tests/test_distill_cases_main.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_distill_cases_main.py
import json
import sqlite3
import distill_cases


def make_md(case_no_num: int, title: str = "测试案") -> str:
    return (
        f"【第{case_no_num}号】{title}——测试问题\n\n"
        "## 一、基本案情\n\n案情。\n\n"
        "## 二、主要问题\n\n如何处理?\n\n"
        "## 三、裁判理由\n\n理由如下。"
    )


class OkSession:
    def post(self, url, headers=None, json=None, timeout=None):
        card = {
            "charges": ["测试罪"],
            "holding_summary": "测试要旨。" * 30,
            "keywords": ["测试", "案例", "罪名"],
        }
        return type("R", (), {
            "raise_for_status": lambda self: None,
            "json": lambda self: {"choices": [{"message": {"content": json.dumps(card, ensure_ascii=False)}}]},
        })()


def test_run_incremental(tmp_path):
    md_dir = tmp_path / "md"
    md_dir.mkdir()
    (md_dir / "【第1号】甲测试案——问题一.md").write_text(make_md(1), encoding="utf-8")
    (md_dir / "【第2号】乙测试案——问题二.md").write_text(make_md(2), encoding="utf-8")
    (md_dir / "命名不规范.md").write_text("内容", encoding="utf-8")

    db_path = tmp_path / "cases.db"
    stats = distill_cases.run(md_dir, db_path, OkSession(), "http://fake/v1", "key", "model")
    assert stats["distilled"] == 2
    assert stats["skipped_invalid_name"] == 1

    # 第二遍：全部跳过（增量），不重复提炼
    stats2 = distill_cases.run(md_dir, db_path, OkSession(), "http://fake/v1", "key", "model")
    assert stats2["distilled"] == 0
    assert stats2["skipped_existing"] == 2

    # 新增一篇 -> 只提炼新篇
    (md_dir / "【第3号】丙测试案——问题三.md").write_text(make_md(3), encoding="utf-8")
    stats3 = distill_cases.run(md_dir, db_path, OkSession(), "http://fake/v1", "key", "model")
    assert stats3["distilled"] == 1

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("SELECT case_no, title, charges FROM cases ORDER BY case_no").fetchall()
    assert [r[0] for r in rows] == ["第1号", "第2号", "第3号"]
    assert json.loads(rows[0][2]) == ["测试罪"]

    # FTS 可检索
    fts = conn.execute(
        "SELECT case_no FROM cases_fts WHERE cases_fts MATCH ?",
        (distill_cases.build_match_query("测试"),),
    ).fetchall()
    assert len(fts) == 3
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_distill_cases_main.py -v`
Expected: FAIL（ModuleNotFoundError: distill_cases）

- [ ] **Step 3: 实现 `scripts/distill_cases.py`**

```python
"""《刑事审判参考》离线提炼主脚本：MD 目录 -> cases.db（卡片 + 原文 + FTS5）

用法：
    python scripts/distill_cases.py [--md-dir ~/Desktop/刑事审判参考_MD] [--db cases.db] [--limit N]

LLM 配置来源（优先级：环境变量 > 桌面端 criminal-llm-config.json）：
    LLM_BASE_URL / LLM_API_KEY / LLM_MODEL

增量：已存在于库中的案号自动跳过；失败记入 <db所在目录>/failed_cases.json。
"""
import argparse
import json
import os
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests

from case_text_utils import to_bigrams, parse_case_filename, extract_sections
from case_distiller import distill_case

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    case_no TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    charges TEXT NOT NULL,
    issue TEXT NOT NULL,
    holding_summary TEXT NOT NULL,
    reasoning_excerpt TEXT NOT NULL,
    keywords TEXT NOT NULL,
    full_text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS cases_fts USING fts5(
    case_no UNINDEXED,
    content,
    tokenize='unicode61'
);
"""

WORKERS = 5


def build_match_query(q: str) -> str:
    """与检索服务共用的 MATCH 查询构造（bigram 短语）"""
    bg = to_bigrams(q)
    return f'"{bg}"' if bg else '""'


def init_schema(conn: sqlite3.Connection):
    conn.executescript(SCHEMA)


def _fts_content(title: str, charges: list, issue: str, summary: str, keywords: list) -> str:
    return to_bigrams(" ".join([title, " ".join(charges), issue, summary, " ".join(keywords)]))


def insert_case(conn, case_no, title, sections, card, md_text):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO cases (case_no, title, charges, issue, holding_summary, reasoning_excerpt, keywords, full_text, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            case_no, title, json.dumps(card["charges"], ensure_ascii=False),
            sections["issue"], card["holding_summary"], sections["reasoning_excerpt"],
            json.dumps(card["keywords"], ensure_ascii=False), md_text, now,
        ),
    )
    conn.execute(
        "INSERT INTO cases_fts (case_no, content) VALUES (?, ?)",
        (case_no, _fts_content(title, card["charges"], sections["issue"], card["holding_summary"], card["keywords"])),
    )


def run(md_dir: Path, db_path: Path, session, base_url: str, api_key: str, model: str,
        limit: int | None = None, progress_cb=print) -> dict:
    conn = sqlite3.connect(str(db_path))
    init_schema(conn)
    existing = {r[0] for r in conn.execute("SELECT case_no FROM cases")}
    stats = {"distilled": 0, "skipped_existing": 0, "skipped_invalid_name": 0, "failed": 0}
    failed = []
    db_lock = threading.Lock()

    # 收集待处理文件
    todo = []
    for path in sorted(Path(md_dir).glob("*.md")):
        parsed = parse_case_filename(path.name)
        if not parsed:
            stats["skipped_invalid_name"] += 1
            progress_cb(f"[跳过] 命名不规范: {path.name}")
            continue
        case_no, title = parsed
        if case_no in existing:
            stats["skipped_existing"] += 1
            continue
        todo.append((path, case_no, title))
        if limit and len(todo) >= limit:
            break

    def process(path, case_no, title):
        md_text = path.read_text(encoding="utf-8")
        sections = extract_sections(md_text)
        if sections["issue"] is None:
            return (case_no, None, "缺少「二、主要问题」章节", None, None)
        try:
            card = distill_case(session, base_url, api_key, model, title, md_text)
        except Exception as e:
            return (case_no, None, str(e), None, None)
        return (case_no, title, None, sections, card)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(process, p, no, t): (p, no) for p, no, t in todo}
        for fut in as_completed(futures):
            md_text_holder = futures[fut]
            case_no, title, err, sections, card = fut.result()
            if err:
                stats["failed"] += 1
                failed.append({"case_no": case_no, "reason": err})
                progress_cb(f"[失败] {case_no}: {err}")
                continue
            md_text = md_text_holder[0].read_text(encoding="utf-8")
            with db_lock:
                insert_case(conn, case_no, title, sections, card, md_text)
                conn.commit()
            stats["distilled"] += 1
            progress_cb(f"[完成] {case_no} {title}（累计 {stats['distilled']}）")

    if failed:
        fail_path = Path(db_path).parent / "failed_cases.json"
        fail_path.write_text(json.dumps(failed, ensure_ascii=False, indent=2), encoding="utf-8")
        progress_cb(f"失败清单已写入: {fail_path}（{len(failed)} 篇）")
    conn.close()
    return stats


def _load_llm_config():
    base_url = os.getenv("LLM_BASE_URL")
    api_key = os.getenv("LLM_API_KEY")
    model = os.getenv("LLM_MODEL")
    cfg_path = Path.home() / "Documents" / ".criminal-llm-data" / "criminal-llm-config.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            base_url = base_url or cfg.get("llm_base_url")
            api_key = api_key or cfg.get("llm_api_key")
            model = model or cfg.get("llm_model")
        except Exception:
            pass
    if not api_key:
        sys.exit("缺少 LLM_API_KEY（环境变量或桌面端配置）")
    return base_url, api_key, model


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="刑事审判参考案例离线提炼")
    parser.add_argument("--md-dir", default=str(Path.home() / "Desktop" / "刑事审判参考_MD"))
    parser.add_argument("--db", default=str(Path(__file__).parent / "cases.db"))
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    base_url, api_key, model = _load_llm_config()
    stats = run(Path(args.md_dir), Path(args.db), requests.Session(), base_url, api_key, model)
    print(f"完成: {stats}")
```

注意：`run()` 里通过 futures 字典取回 path 再读一次文件（为避免在线程间传递大文本）。实现时如改为 process 返回 md_text 亦可，保持测试断言不变即可。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_distill_cases_main.py -v`
Expected: 1 passed

- [ ] **Step 5: 小规模真跑验证（3 篇，需 LLM_API_KEY）**

```bash
source ~/.zshrc 2>/dev/null || true
cd /Users/zhanghan/.openclaw/workspace/criminal-llm
python scripts/distill_cases.py --limit 3
```
Expected: 输出 3 篇完成，`scripts/cases.db` 生成；`sqlite3 scripts/cases.db "SELECT case_no, charges FROM cases"` 有 3 行

- [ ] **Step 6: 提交**

```bash
git add scripts/distill_cases.py tests/test_distill_cases_main.py
git commit -m "feat: 案例离线提炼主流程（增量+断点+FTS 建库）"
```

---

### Task 4: 全量提炼 + 质量门（人工检查点）

**Files:**
- 产出: `criminal-llm/scripts/cases.db`

- [ ] **Step 1: 全量运行（约 1-2 小时，可后台）**

```bash
source ~/.zshrc 2>/dev/null || true
cd /Users/zhanghan/.openclaw/workspace/criminal-llm
python scripts/distill_cases.py
```
Expected: distilled ≈ 1750，failed 清单少量；`.gitignore` 添加 `scripts/cases.db` 与 `scripts/failed_cases.json` 并提交

- [ ] **Step 2: 质量门——抽 30 篇人工校对**

```bash
sqlite3 scripts/cases.db "SELECT case_no, title, holding_summary FROM cases ORDER BY RANDOM() LIMIT 30"
```
逐篇对照原文 MD 检查 `holding_summary` 是否忠实、`issue` 是否与原文一致（脚本已保证 issue 是原文截取，重点看要旨质量）。失败案例记入 `scripts/failed_cases.json` 后可删除对应行重跑：
```bash
sqlite3 scripts/cases.db "DELETE FROM cases WHERE case_no='第X号'; DELETE FROM cases_fts WHERE case_no='第X号';"
python scripts/distill_cases.py   # 增量补跑
```
Expected: 30 篇中明显失真 ≤ 2 篇，否则调整 prompt 重跑

- [ ] **Step 3: 提交 .gitignore**

```bash
git add .gitignore
git commit -m "chore: 忽略提炼产物 cases.db 与失败清单"
```

---

# 阶段 B：云端案例微服务（criminal-llm-cases，新建库）

### Task 5: 服务骨架 + API Key 校验 + 用量配额

**Files:**
- Create: `criminal-llm-cases/main.py`
- Create: `criminal-llm-cases/requirements.txt`
- Create: `criminal-llm-cases/tests/__init__.py`（空文件）
- Test: `criminal-llm-cases/tests/test_auth_keys.py`

- [ ] **Step 1: 初始化库与依赖**

```bash
mkdir -p /Users/zhanghan/.openclaw/workspace/criminal-llm-cases/tests
cd /Users/zhanghan/.openclaw/workspace/criminal-llm-cases
git init
```

`requirements.txt`:
```
fastapi>=0.115.0
uvicorn>=0.34.0
pytest>=8.0
httpx>=0.27
```

`.gitignore`:
```
__pycache__/
data/
.pytest_cache/
```

- [ ] **Step 2: 写失败测试**

```python
# tests/test_auth_keys.py
import hashlib
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main


def make_auth_db(path: Path):
    conn = sqlite3.connect(str(path))
    conn.execute("""CREATE TABLE api_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        key_hash TEXT UNIQUE,
        key_prefix TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        quota_per_day INTEGER NOT NULL DEFAULT 200,
        created_at TEXT NOT NULL,
        approved_at TEXT
    )""")
    for raw, status in [("cca_active111", "active"), ("cca_revoked1", "revoked"), ("cca_quota222", "active")]:
        quota = 2 if raw == "cca_quota222" else 200
        conn.execute(
            "INSERT INTO api_keys (user_id, key_hash, key_prefix, status, quota_per_day, created_at) VALUES (1, ?, ?, ?, ?, '2026-01-01')",
            (hashlib.sha256(raw.encode()).hexdigest(), raw[:11], status, quota),
        )
    conn.commit()
    conn.close()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    auth_db = tmp_path / "auth.db"
    make_auth_db(auth_db)
    usage_db = tmp_path / "usage.db"
    cases_db = tmp_path / "cases.db"
    monkeypatch.setenv("AUTH_DB_PATH", str(auth_db))
    monkeypatch.setenv("USAGE_DB_PATH", str(usage_db))
    monkeypatch.setenv("CASES_DB_PATH", str(cases_db))
    main.init_usage_db(usage_db)
    return TestClient(main.app)


def test_health_no_auth(client):
    assert client.get("/health").status_code == 200


def test_missing_key_rejected(client):
    resp = client.get("/api/charges")
    assert resp.status_code == 401


def test_revoked_key_rejected(client):
    resp = client.get("/api/charges", headers={"X-API-Key": "cca_revoked1"})
    assert resp.status_code == 401


def test_unknown_key_rejected(client):
    resp = client.get("/api/charges", headers={"X-API-Key": "cca_nope"})
    assert resp.status_code == 401


def test_quota_exceeded_returns_429(client):
    headers = {"X-API-Key": "cca_quota222"}  # 配额 2
    assert client.get("/api/charges", headers=headers).status_code == 200
    assert client.get("/api/charges", headers=headers).status_code == 200
    resp = client.get("/api/charges", headers=headers)
    assert resp.status_code == 429
    assert "配额" in resp.json()["detail"]


def test_validate_endpoint(client):
    resp = client.post("/api/keys/validate", json={"api_key": "cca_active111"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["quota_per_day"] == 200
    resp2 = client.post("/api/keys/validate", json={"api_key": "cca_revoked1"})
    assert resp2.json()["valid"] is False
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd /Users/zhanghan/.openclaw/workspace/criminal-llm-cases && python -m pytest tests/test_auth_keys.py -v`
Expected: FAIL（import main 失败或 404/500）

- [ ] **Step 4: 实现 `main.py`（鉴权部分；检索端点 Task 6 补齐）**

```python
"""Criminal Case Analyzer — 案例检索微服务（端口 8001）

数据源：cases.db（离线提炼产物，scp 部署）
鉴权：X-API-Key，只读查询 auth.db 的 api_keys 表
用量：usage.db（本服务自有，按 key_id+日期计数）
"""
import hashlib
import os
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _env_path(name: str, default: Path) -> Path:
    return Path(os.getenv(name, str(default)))


def cases_db_path() -> Path:
    return _env_path("CASES_DB_PATH", DATA_DIR / "cases.db")


def auth_db_path() -> Path:
    return _env_path("AUTH_DB_PATH", Path("/opt/criminal-llm-auth/data/auth.db"))


def usage_db_path() -> Path:
    return _env_path("USAGE_DB_PATH", DATA_DIR / "usage.db")


app = FastAPI(title="Criminal Case Analyzer — Case Search Service")


# ===== 用量库 =====

def init_usage_db(path: Path | None = None):
    path = path or usage_db_path()
    conn = sqlite3.connect(str(path))
    conn.execute("""CREATE TABLE IF NOT EXISTS usage (
        key_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        count INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (key_id, day)
    )""")
    conn.commit()
    conn.close()


@contextmanager
def usage_conn():
    conn = sqlite3.connect(str(usage_db_path()))
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def usage_today(key_id: int) -> int:
    today = date.today().isoformat()
    with usage_conn() as conn:
        row = conn.execute("SELECT count FROM usage WHERE key_id = ? AND day = ?", (key_id, today)).fetchone()
    return row[0] if row else 0


def check_and_increment(key_id: int, quota: int) -> tuple[bool, int]:
    """返回 (是否放行, 本次之后的已用数)"""
    today = date.today().isoformat()
    with usage_conn() as conn:
        row = conn.execute("SELECT count FROM usage WHERE key_id = ? AND day = ?", (key_id, today)).fetchone()
        count = row[0] if row else 0
        if count >= quota:
            return False, count
        conn.execute(
            "INSERT INTO usage (key_id, day, count) VALUES (?, ?, 1)"
            " ON CONFLICT(key_id, day) DO UPDATE SET count = count + 1",
            (key_id, today),
        )
        return True, count + 1


# ===== Key 校验 =====

def lookup_key(api_key: str) -> sqlite3.Row | None:
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    uri = f"file:{auth_db_path()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT id, user_id, key_prefix, status, quota_per_day FROM api_keys WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
    finally:
        conn.close()


def require_key(x_api_key: str = Header(default="")) -> sqlite3.Row:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="缺少 X-API-Key 请求头")
    info = lookup_key(x_api_key)
    if not info or info["status"] != "active":
        raise HTTPException(status_code=401, detail="API Key 无效或已吊销")
    allowed, used = check_and_increment(info["id"], info["quota_per_day"])
    if not allowed:
        raise HTTPException(status_code=429, detail=f"超出每日配额（{info['quota_per_day']} 次），明日重置")
    return info


class ValidateRequest(BaseModel):
    api_key: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/keys/validate")
def validate_key(req: ValidateRequest):
    info = lookup_key(req.api_key)
    if not info or info["status"] != "active":
        return {"valid": False}
    return {
        "valid": True,
        "prefix": info["key_prefix"],
        "used_today": usage_today(info["id"]),
        "quota_per_day": info["quota_per_day"],
    }


# 检索端点在 Task 6 实现：/api/cases/search、/api/cases/{case_no}、/api/cases/{case_no}/full、/api/charges
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python3 -m pytest tests/test_auth_keys.py -v`
Expected: 6 passed（计划原文笔误为 7，实际 6 个用例）

另需在 main.py 末尾加启动初始化（计划遗漏，审查发现）：`init_usage_db()` 模块级调用一次，避免生产冷启动时 usage 表不存在导致 500。

占位实现（加在 Step 4 的 main.py 末尾）：
```python
@app.get("/api/charges")
def list_charges(_key=Depends(require_key)):
    return {"charges": []}
```

- [ ] **Step 6: 提交**

```bash
cd /Users/zhanghan/.openclaw/workspace/criminal-llm-cases
git add -A
git commit -m "feat: 案例服务骨架（API Key 校验 + 每日配额 + 健康检查）"
```

---

### Task 6: FTS 检索端点

**Files:**
- Modify: `criminal-llm-cases/main.py`
- Create: `criminal-llm-cases/case_text_utils.py`（从 criminal-llm/scripts 复制，两库有意各自持有 5 行工具，避免跨库依赖）
- Test: `criminal-llm-cases/tests/test_search.py`

- [ ] **Step 1: 复制工具函数**

```bash
cp /Users/zhanghan/.openclaw/workspace/criminal-llm/scripts/case_text_utils.py \
   /Users/zhanghan/.openclaw/workspace/criminal-llm-cases/case_text_utils.py
```

- [ ] **Step 2: 写失败测试**

```python
# tests/test_search.py
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from case_text_utils import to_bigrams

KEY = "cca_testkey1"


def make_cases_db(path: Path):
    conn = sqlite3.connect(str(path))
    conn.executescript("""
    CREATE TABLE cases (
        case_no TEXT PRIMARY KEY, title TEXT NOT NULL, charges TEXT NOT NULL,
        issue TEXT NOT NULL, holding_summary TEXT NOT NULL, reasoning_excerpt TEXT NOT NULL,
        keywords TEXT NOT NULL, full_text TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE VIRTUAL TABLE cases_fts USING fts5(case_no UNINDEXED, content, tokenize='unicode61');
    """)
    rows = [
        ("第1000号", "李某甲等寻衅滋事案", ["寻衅滋事罪"], "未成年人多次强取财物如何处理", "未成年人轻微暴力强索少量财物定寻衅滋事。", "理由摘录A", ["寻衅滋事", "未成年人"], "全文A"),
        ("第1001号", "谭永艮非法持有枪支案", ["非法持有枪支罪"], "情节严重与犯罪情节较轻是否矛盾", "二者评价对象不同不矛盾。", "理由摘录B", ["枪支", "缓刑"], "全文B"),
        ("第1002号", "张联新生产有毒食品案", ["生产、销售有毒、有害食品罪"], "新型地沟油司法认定", "地沟油属于有毒有害食品。", "理由摘录C", ["地沟油", "食品"], "全文C"),
    ]
    for case_no, title, charges, issue, summary, excerpt, keywords, full in rows:
        conn.execute(
            "INSERT INTO cases VALUES (?, ?, ?, ?, ?, ?, ?, ?, '2026-01-01')",
            (case_no, title, json.dumps(charges, ensure_ascii=False), issue, summary, excerpt,
             json.dumps(keywords, ensure_ascii=False), full),
        )
        content = to_bigrams(" ".join([title, " ".join(charges), issue, summary, " ".join(keywords)]))
        conn.execute("INSERT INTO cases_fts (case_no, content) VALUES (?, ?)", (case_no, content))
    conn.commit()
    conn.close()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    auth_db = tmp_path / "auth.db"
    conn = sqlite3.connect(str(auth_db))
    conn.execute("""CREATE TABLE api_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        key_hash TEXT UNIQUE, key_prefix TEXT, status TEXT NOT NULL,
        quota_per_day INTEGER NOT NULL, created_at TEXT NOT NULL, approved_at TEXT)""")
    conn.execute(
        "INSERT INTO api_keys (user_id, key_hash, key_prefix, status, quota_per_day, created_at) VALUES (1, ?, ?, 'active', 200, '2026-01-01')",
        (hashlib.sha256(KEY.encode()).hexdigest(), KEY[:11]),
    )
    conn.commit()
    conn.close()
    cases_db = tmp_path / "cases.db"
    make_cases_db(cases_db)
    monkeypatch.setenv("AUTH_DB_PATH", str(auth_db))
    monkeypatch.setenv("USAGE_DB_PATH", str(tmp_path / "usage.db"))
    monkeypatch.setenv("CASES_DB_PATH", str(cases_db))
    main.init_usage_db(tmp_path / "usage.db")
    return TestClient(main.app)


def auth():
    return {"X-API-Key": KEY}


def test_search_by_keyword(client):
    resp = client.get("/api/cases/search", params={"q": "寻衅滋事"}, headers=auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["results"][0]["case_no"] == "第1000号"
    assert body["results"][0]["charges"] == ["寻衅滋事罪"]


def test_search_by_charge_filter(client):
    resp = client.get("/api/cases/search", params={"charge": "枪支"}, headers=auth())
    body = resp.json()
    assert body["total"] == 1
    assert body["results"][0]["case_no"] == "第1001号"


def test_search_zero_result_falls_back_like(client):
    # 单字查询：bigram 索引无对应 token，FTS 零结果，触发 LIKE 降级
    resp = client.get("/api/cases/search", params={"q": "罪"}, headers=auth())
    body = resp.json()
    assert body["total"] >= 1


def test_search_pagination(client):
    resp = client.get("/api/cases/search", params={"page": 1, "size": 2}, headers=auth())
    body = resp.json()
    assert len(body["results"]) == 2
    assert body["total"] == 3
    resp2 = client.get("/api/cases/search", params={"page": 2, "size": 2}, headers=auth())
    assert len(resp2.json()["results"]) == 1


def test_get_card_and_full(client):
    card = client.get("/api/cases/第1000号", headers=auth())
    assert card.status_code == 200
    assert card.json()["reasoning_excerpt"] == "理由摘录A"
    assert "full_text" not in card.json()

    full = client.get("/api/cases/第1000号/full", headers=auth())
    assert full.json()["full_text"] == "全文A"

    missing = client.get("/api/cases/第9999号", headers=auth())
    assert missing.status_code == 404


def test_charges_list(client):
    resp = client.get("/api/charges", headers=auth())
    charges = resp.json()["charges"]
    assert "寻衅滋事罪" in charges
    assert len(charges) == 3
```

- [ ] **Step 3: 运行测试确认失败**

Run: `python -m pytest tests/test_search.py -v`
Expected: FAIL（404 on /api/cases/search）

- [ ] **Step 4: 实现检索端点（追加到 main.py，替换 Task 5 的 /api/charges 占位）**

```python
from fastapi import Query
from case_text_utils import to_bigrams


def build_match_query(q: str) -> str:
    bg = to_bigrams(q)
    return f'"{bg}"' if bg else '""'


@contextmanager
def cases_conn():
    conn = sqlite3.connect(str(cases_db_path()))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _row_to_summary(row: sqlite3.Row) -> dict:
    return {
        "case_no": row["case_no"],
        "title": row["title"],
        "charges": json.loads(row["charges"]),
        "issue": row["issue"],
        "holding_summary": row["holding_summary"],
    }


@app.get("/api/cases/search")
def search_cases(
    q: str = Query(default=""),
    charge: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=50),
    _key=Depends(require_key),
):
    where, params = [], []
    use_fts = bool(q.strip())
    if use_fts:
        where.append("cases_fts MATCH ?")
        params.append(build_match_query(q))
    if charge.strip():
        where.append("c.charges LIKE ?")
        params.append(f"%{charge.strip()}%")

    with cases_conn() as conn:
        if use_fts:
            base_from = "FROM cases_fts JOIN cases c ON c.case_no = cases_fts.case_no"
            where_sql = ("WHERE " + " AND ".join(where)) if where else ""
            total = conn.execute(f"SELECT COUNT(*) {base_from} {where_sql}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT c.case_no, c.title, c.charges, c.issue, c.holding_summary, bm25(cases_fts) AS rank"
                f" {base_from} {where_sql} ORDER BY rank LIMIT ? OFFSET ?",
                params + [size, (page - 1) * size],
            ).fetchall()
        else:
            base_from = "FROM cases c"
            where_sql = ("WHERE " + " AND ".join(where)) if where else ""
            total = conn.execute(f"SELECT COUNT(*) {base_from} {where_sql}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT c.case_no, c.title, c.charges, c.issue, c.holding_summary, 0 AS rank"
                f" {base_from} {where_sql} ORDER BY c.case_no LIMIT ? OFFSET ?",
                params + [size, (page - 1) * size],
            ).fetchall()

        # 降级：FTS 零结果时 LIKE 模糊匹配标题/罪名/主要问题
        if use_fts and total == 0 and not charge.strip():
            like = f"%{q.strip()}%"
            total = conn.execute(
                "SELECT COUNT(*) FROM cases c WHERE c.title LIKE ? OR c.charges LIKE ? OR c.issue LIKE ?",
                (like, like, like),
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT c.case_no, c.title, c.charges, c.issue, c.holding_summary, 0 AS rank FROM cases c"
                " WHERE c.title LIKE ? OR c.charges LIKE ? OR c.issue LIKE ?"
                " ORDER BY c.case_no LIMIT ? OFFSET ?",
                (like, like, like, size, (page - 1) * size),
            ).fetchall()

    return {"total": total, "page": page, "size": size, "results": [_row_to_summary(r) for r in rows]}


@app.get("/api/charges")
def list_charges(_key=Depends(require_key)):
    with cases_conn() as conn:
        rows = conn.execute("SELECT charges FROM cases").fetchall()
    seen: set[str] = set()
    for row in rows:
        seen.update(json.loads(row["charges"]))
    return {"charges": sorted(seen)}


@app.get("/api/cases/{case_no}")
def get_case_card(case_no: str, _key=Depends(require_key)):
    with cases_conn() as conn:
        row = conn.execute(
            "SELECT case_no, title, charges, issue, holding_summary, reasoning_excerpt, keywords"
            " FROM cases WHERE case_no = ?",
            (case_no,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="案例不存在")
    result = _row_to_summary(row)
    result["reasoning_excerpt"] = row["reasoning_excerpt"]
    result["keywords"] = json.loads(row["keywords"])
    return result


@app.get("/api/cases/{case_no}/full")
def get_case_full(case_no: str, _key=Depends(require_key)):
    with cases_conn() as conn:
        row = conn.execute("SELECT case_no, title, full_text FROM cases WHERE case_no = ?", (case_no,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="案例不存在")
    return {"case_no": row["case_no"], "title": row["title"], "full_text": row["full_text"]}
```

同时在 main.py 顶部 import 区加 `import json`。

- [ ] **Step 4b: IP 限流中间件（防 Key 爆破，60 次/分钟）**

main.py 追加：

```python
import time
from collections import defaultdict, deque
from fastapi import Request
from fastapi.responses import JSONResponse

RATE_LIMIT_PER_MIN = 60
_rate_buckets: dict[str, deque] = defaultdict(deque)


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    if request.url.path == "/health":
        return await call_next(request)
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    bucket = _rate_buckets[ip]
    while bucket and now - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_PER_MIN:
        return JSONResponse(status_code=429, content={"detail": "请求过于频繁，请稍后再试"})
    bucket.append(now)
    return await call_next(request)
```

tests/test_search.py 追加（注意限流是进程级全局状态，测试前必须清空）：

```python
def test_ip_rate_limit(client):
    main._rate_buckets.clear()
    for _ in range(60):
        client.get("/api/charges", headers=auth())
    resp = client.get("/api/charges", headers=auth())
    assert resp.status_code == 429
    assert "频繁" in resp.json()["detail"]
```

并在 test_search.py 的 `client` fixture 中 `main.init_usage_db(...)` 之后加一行 `main._rate_buckets.clear()`，避免用例间互相污染。

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/ -v`
Expected: 全部通过（test_auth_keys 7 + test_search 6）

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "feat: FTS5 案例检索端点（bigram + 降级 LIKE + 分页）"
```

---

# 阶段 C：认证服务扩展（criminal-llm-auth）

### Task 7: api_keys 表 + 用户申请/查询端点

**Files:**
- Modify: `criminal-llm-auth/main.py`（init_db 加表、文件尾部加端点）
- Create: `criminal-llm-auth/test_api_keys.py`

- [ ] **Step 1: 写失败测试**

```python
# test_api_keys.py
import importlib
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin123")
    import main
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "auth.db")
    importlib.reload(main)  # 注意：reload 后需重新打 DB_PATH
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "auth.db")
    main.init_db()
    yield TestClient(main.app)


def register_and_login(client, email="user@example.com", password="secret123"):
    assert client.post("/api/register", json={"email": email, "password": password}).status_code == 200
    resp = client.post("/api/login", json={"email": email, "password": password})
    return resp.json()["token"]


def test_apply_and_query_my_keys(client):
    token = register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.post("/api/api-keys/apply", headers=headers)
    assert resp.status_code == 200

    # 重复申请被拒
    resp2 = client.post("/api/api-keys/apply", headers=headers)
    assert resp2.status_code == 409

    mine = client.get("/api/api-keys/my", headers=headers)
    assert mine.status_code == 200
    keys = mine.json()["keys"]
    assert len(keys) == 1
    assert keys[0]["status"] == "pending"
    assert keys[0]["quota_per_day"] == 200
    assert "key_hash" not in keys[0]  # 永不外泄哈希


def test_apply_requires_login(client):
    resp = client.post("/api/api-keys/apply")
    assert resp.status_code == 401
```

注意：`main.py` 顶层在 import 时执行 `DATA_DIR.mkdir` 等，reload 策略若导致状态异常，可改为只 `import main` 一次 + monkeypatch DB_PATH + 调用 init_db()（get_db 每次调用时读全局 DB_PATH，monkeypatch 即生效）。以测试实际通过为准。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/zhanghan/.openclaw/workspace/criminal-llm-auth && python -m pytest test_api_keys.py -v`
Expected: FAIL（404 on /api/api-keys/apply）

- [ ] **Step 3: 实现——init_db 加表（在 main.py 的 init_db() 内追加）**

```python
    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            key_hash TEXT UNIQUE,
            key_prefix TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            quota_per_day INTEGER NOT NULL DEFAULT 200,
            created_at TEXT NOT NULL,
            approved_at TEXT
        )
    """)
```

- [ ] **Step 4: 实现端点（追加到 main.py "===== 网站页面 =====" 之前）**

```python
# ===== API Key 管理 =====

from fastapi import Request, Header

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
DEFAULT_QUOTA_PER_DAY = 200


def current_user(req: Request) -> dict:
    """从 Authorization: Bearer <jwt> 解析当前用户，失败 401"""
    auth = req.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    try:
        payload = verify_token(auth[7:])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token 已过期")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token 无效")
    return payload


def require_admin(x_admin_password: str = Header(default="")):
    if not ADMIN_PASSWORD or x_admin_password != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="管理密码错误")


@app.post("/api/api-keys/apply")
def api_key_apply(req: Request):
    payload = current_user(req)
    user_id = int(payload["sub"])
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM api_keys WHERE user_id = ? AND status IN ('pending', 'active')",
            (user_id,),
        ).fetchone()
        if row:
            raise HTTPException(status_code=409, detail="已有申请中或生效中的 API Key")
        conn.execute(
            "INSERT INTO api_keys (user_id, status, quota_per_day, created_at) VALUES (?, 'pending', ?, ?)",
            (user_id, DEFAULT_QUOTA_PER_DAY, now),
        )
    return {"success": True, "message": "申请已提交，审核通过后将通过邮件发送 API Key"}


@app.get("/api/api-keys/my")
def api_key_my(req: Request):
    payload = current_user(req)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, key_prefix, status, quota_per_day, created_at, approved_at"
            " FROM api_keys WHERE user_id = ? ORDER BY id DESC",
            (int(payload["sub"]),),
        ).fetchall()
    return {"keys": [dict(r) for r in rows]}
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest test_api_keys.py -v`
Expected: 2 passed

- [ ] **Step 6: 提交**

```bash
cd /Users/zhanghan/.openclaw/workspace/criminal-llm-auth
git add main.py test_api_keys.py
git commit -m "feat: API Key 申请与查询端点"
```

---

### Task 8: 管理端审批/吊销 + 邮件发放 Key

**Files:**
- Modify: `criminal-llm-auth/main.py`
- Test: `criminal-llm-auth/test_api_keys_admin.py`

- [ ] **Step 1: 写失败测试**

```python
# test_api_keys_admin.py
import pytest
from fastapi.testclient import TestClient

import main
from test_api_keys import register_and_login, client  # 复用 fixture


def apply_one(client, email="admin-target@example.com"):
    token = register_and_login(client, email=email)
    client.post("/api/api-keys/apply", headers={"Authorization": f"Bearer {token}"})


def admin_headers(pw="admin123"):
    return {"X-Admin-Password": pw}


def test_admin_requires_password(client):
    assert client.get("/api/admin/api-keys").status_code == 403
    assert client.get("/api/admin/api-keys", headers=admin_headers("wrong")).status_code == 403


def test_approve_issues_key_and_emails(client, monkeypatch):
    sent = []
    monkeypatch.setattr(main, "send_email", lambda to, subject, html: sent.append((to, subject, html)))

    apply_one(client)
    listing = client.get("/api/admin/api-keys", headers=admin_headers())
    assert listing.status_code == 200
    keys = listing.json()["keys"]
    assert len(keys) == 1
    assert keys[0]["email"] == "admin-target@example.com"
    assert keys[0]["status"] == "pending"

    resp = client.post(f"/api/admin/api-keys/{keys[0]['id']}/approve", headers=admin_headers())
    assert resp.status_code == 200
    body = resp.json()
    raw_key = body["api_key"]
    assert raw_key.startswith("cca_") and len(raw_key) == 36
    assert body["email_sent"] is True
    assert len(sent) == 1 and "admin-target@example.com" == sent[0][0]
    assert raw_key in sent[0][2]  # 邮件含明文 Key

    # 库里不存明文，存哈希
    import hashlib, sqlite3
    conn = sqlite3.connect(str(main.DB_PATH))
    row = conn.execute("SELECT key_hash, key_prefix, status FROM api_keys WHERE id = ?", (keys[0]["id"],)).fetchone()
    assert row[0] == hashlib.sha256(raw_key.encode()).hexdigest()
    assert row[1] == raw_key[:11]
    assert row[2] == "active"

    # 重复审批被拒
    resp2 = client.post(f"/api/admin/api-keys/{keys[0]['id']}/approve", headers=admin_headers())
    assert resp2.status_code == 400


def test_revoke(client, monkeypatch):
    monkeypatch.setattr(main, "send_email", lambda *a: None)
    apply_one(client, email="revoke@example.com")
    keys = client.get("/api/admin/api-keys", headers=admin_headers()).json()["keys"]
    client.post(f"/api/admin/api-keys/{keys[0]['id']}/approve", headers=admin_headers())

    resp = client.post(f"/api/admin/api-keys/{keys[0]['id']}/revoke", headers=admin_headers())
    assert resp.status_code == 200
    mine = client.get("/api/admin/api-keys", headers=admin_headers()).json()["keys"]
    assert mine[0]["status"] == "revoked"


def test_approve_email_failure_still_returns_key(client, monkeypatch):
    def boom(*a):
        raise RuntimeError("SMTP down")
    monkeypatch.setattr(main, "send_email", boom)
    apply_one(client, email="emailfail@example.com")
    keys = client.get("/api/admin/api-keys", headers=admin_headers()).json()["keys"]
    resp = client.post(f"/api/admin/api-keys/{keys[0]['id']}/approve", headers=admin_headers())
    body = resp.json()
    assert body["email_sent"] is False
    assert body["api_key"].startswith("cca_")  # Key 仍生效，管理页展示一次
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest test_api_keys_admin.py -v`
Expected: FAIL（404 on /api/admin/api-keys）

- [ ] **Step 3: 实现管理端点（追加到 main.py API Key 管理区）**

```python
@app.get("/api/admin/api-keys")
def admin_list_keys(_=Depends(require_admin)):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT ak.id, ak.key_prefix, ak.status, ak.quota_per_day, ak.created_at, ak.approved_at, u.email"
            " FROM api_keys ak JOIN users u ON u.id = ak.user_id ORDER BY ak.id DESC",
        ).fetchall()
    return {"keys": [dict(r) for r in rows]}


@app.post("/api/admin/api-keys/{key_id}/approve")
def admin_approve_key(key_id: int, _=Depends(require_admin)):
    with get_db() as conn:
        row = conn.execute(
            "SELECT ak.id, ak.status, u.email FROM api_keys ak JOIN users u ON u.id = ak.user_id WHERE ak.id = ?",
            (key_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="申请不存在")
        if row["status"] != "pending":
            raise HTTPException(status_code=400, detail=f"当前状态为 {row['status']}，无法审批")

        raw_key = "cca_" + secrets.token_hex(16)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE api_keys SET key_hash = ?, key_prefix = ?, status = 'active', approved_at = ? WHERE id = ?",
            (key_hash, raw_key[:11], now, key_id),
        )

    html = f"""
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
        <h2 style="color: #007aff;">{SYSTEM_NAME} 案例检索 API</h2>
        <p>您申请的案例检索 API Key 已审核通过：</p>
        <div style="font-family: monospace; font-size: 15px; background: #f5f5f7; padding: 14px; border-radius: 8px; word-break: break-all;">
            {raw_key}
        </div>
        <p style="color: #888;">请在桌面应用「设置 → 案例检索 API」中填入。此 Key 仅显示一次，请妥善保管；每日检索配额 200 次。</p>
    </div>
    """
    try:
        send_email(row["email"], f"{SYSTEM_NAME} 案例检索 API Key 已开通", html)
        return {"success": True, "api_key": raw_key, "email_sent": True}
    except Exception as e:
        return {"success": True, "api_key": raw_key, "email_sent": False, "warning": f"邮件发送失败: {e}"}


@app.post("/api/admin/api-keys/{key_id}/revoke")
def admin_revoke_key(key_id: int, _=Depends(require_admin)):
    with get_db() as conn:
        row = conn.execute("SELECT status FROM api_keys WHERE id = ?", (key_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Key 不存在")
        conn.execute("UPDATE api_keys SET status = 'revoked' WHERE id = ?", (key_id,))
    return {"success": True, "message": "已吊销"}
```

同时在 main.py 顶部 import 区加 `from fastapi import Depends`（与已有 import 合并）。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest test_api_keys.py test_api_keys_admin.py -v`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add main.py test_api_keys_admin.py
git commit -m "feat: API Key 管理端审批/吊销（哈希存储 + 邮件发放）"
```

---

### Task 9: 官网申请页 + 管理页（HTML）

**Files:**
- Create: `criminal-llm-auth/templates/api-access.html`
- Create: `criminal-llm-auth/templates/admin.html`
- Modify: `criminal-llm-auth/main.py`（页面路由）

- [ ] **Step 1: 页面路由（追加到 main.py 网站页面区）**

```python
@app.get("/api-access", response_class=HTMLResponse)
def api_access_page():
    return (BASE_DIR / "templates" / "api-access.html").read_text(encoding="utf-8")


@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return (BASE_DIR / "templates" / "admin.html").read_text(encoding="utf-8")
```

- [ ] **Step 2: `templates/api-access.html`（完整实现，风格参照现有 login.html：卡片居中、#007aff 主色）**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>案例检索 API — Criminal Case Analyzer</title>
<style>
  body { font-family: -apple-system, sans-serif; background: #f5f5f7; display: flex; justify-content: center; padding: 40px 16px; margin: 0; }
  .card { background: #fff; border-radius: 16px; padding: 32px; max-width: 520px; width: 100%; box-shadow: 0 4px 24px rgba(0,0,0,0.06); }
  h1 { font-size: 20px; color: #1d1d1f; margin: 0 0 8px; }
  .desc { font-size: 13px; color: #86868b; margin-bottom: 24px; line-height: 1.6; }
  input { width: 100%; box-sizing: border-box; padding: 10px 12px; border: 1px solid #d2d2d7; border-radius: 8px; font-size: 14px; margin-bottom: 12px; }
  button { background: #007aff; color: #fff; border: none; border-radius: 8px; padding: 10px 20px; font-size: 14px; cursor: pointer; }
  button:disabled { background: #d2d2d7; cursor: not-allowed; }
  .msg { font-size: 13px; margin: 12px 0; color: #007aff; }
  .error { color: #ff3b30; }
  .key-row { display: flex; justify-content: space-between; align-items: center; padding: 12px; background: #f5f5f7; border-radius: 8px; margin-top: 8px; font-size: 13px; }
  .badge { padding: 2px 10px; border-radius: 10px; font-size: 12px; }
  .pending { background: #fff8e1; color: #b58900; }
  .active { background: #e8f5e9; color: #2e7d32; }
  .revoked { background: #ffebee; color: #c62828; }
  .hidden { display: none; }
  a { color: #007aff; text-decoration: none; font-size: 13px; }
</style>
</head>
<body>
<div class="card">
  <h1>案例检索 API</h1>
  <p class="desc">数据来源：《刑事审判参考》1750 篇精品案例（含裁判要旨）。<br>每日检索配额 200 次。审核通过后 API Key 将发送至您的注册邮箱。</p>

  <div id="loginBox">
    <input id="email" type="email" placeholder="注册邮箱">
    <input id="password" type="password" placeholder="密码">
    <button onclick="login()">登录</button>
    <div id="loginMsg" class="msg"></div>
  </div>

  <div id="applyBox" class="hidden">
    <button id="applyBtn" onclick="apply()">申请案例检索 API</button>
    <div id="applyMsg" class="msg"></div>
    <h1 style="margin-top:24px; font-size:16px;">我的 Key</h1>
    <div id="keyList"></div>
  </div>
</div>
<script>
let token = localStorage.getItem('cca_token') || '';
if (token) showApply();

async function login() {
  const resp = await fetch('/api/login', { method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ email: email.value, password: password.value }) });
  const data = await resp.json();
  if (resp.ok) { token = data.token; localStorage.setItem('cca_token', token); showApply(); }
  else { loginMsg.textContent = data.detail || '登录失败'; loginMsg.className = 'msg error'; }
}

function showApply() {
  loginBox.classList.add('hidden'); applyBox.classList.remove('hidden'); loadKeys();
}

async function apply() {
  applyBtn.disabled = true;
  const resp = await fetch('/api/api-keys/apply', { method: 'POST', headers: { Authorization: 'Bearer ' + token } });
  const data = await resp.json();
  applyMsg.textContent = data.message || data.detail;
  applyMsg.className = 'msg' + (resp.ok ? '' : ' error');
  applyBtn.disabled = false;
  loadKeys();
}

async function loadKeys() {
  const resp = await fetch('/api/api-keys/my', { headers: { Authorization: 'Bearer ' + token } });
  if (!resp.ok) return;
  const { keys } = await resp.json();
  const label = { pending: '审核中', active: '已开通', revoked: '已吊销' };
  keyList.innerHTML = keys.length ? '' : '<p class="desc">暂无申请记录</p>';
  keys.forEach(k => {
    keyList.innerHTML += `<div class="key-row">
      <span>${k.key_prefix || '（待签发）'} · 每日 ${k.quota_per_day} 次</span>
      <span class="badge ${k.status}">${label[k.status]}</span></div>`;
  });
}
</script>
</body>
</html>
```

- [ ] **Step 3: `templates/admin.html`（完整实现）**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>API Key 管理 — Criminal Case Analyzer</title>
<style>
  body { font-family: -apple-system, sans-serif; background: #f5f5f7; display: flex; justify-content: center; padding: 40px 16px; margin: 0; }
  .card { background: #fff; border-radius: 16px; padding: 32px; max-width: 760px; width: 100%; box-shadow: 0 4px 24px rgba(0,0,0,0.06); }
  h1 { font-size: 20px; margin: 0 0 16px; }
  input { padding: 10px 12px; border: 1px solid #d2d2d7; border-radius: 8px; font-size: 14px; margin-right: 8px; }
  button { background: #007aff; color: #fff; border: none; border-radius: 8px; padding: 8px 16px; font-size: 13px; cursor: pointer; }
  button.revoke { background: #ff3b30; }
  table { width: 100%; border-collapse: collapse; margin-top: 16px; font-size: 13px; }
  th, td { text-align: left; padding: 10px 8px; border-bottom: 1px solid #f0f0f0; }
  .issued { background: #fff8e1; padding: 16px; border-radius: 8px; margin-top: 16px; font-family: monospace; word-break: break-all; }
  .error { color: #ff3b30; font-size: 13px; }
</style>
</head>
<body>
<div class="card">
  <h1>API Key 管理</h1>
  <div>
    <input id="pw" type="password" placeholder="管理密码">
    <button onclick="load()">进入</button>
    <span id="err" class="error"></span>
  </div>
  <div id="issued" class="issued hidden"></div>
  <table id="tbl" class="hidden">
    <thead><tr><th>ID</th><th>邮箱</th><th>Key 前缀</th><th>状态</th><th>配额/日</th><th>申请时间</th><th>操作</th></tr></thead>
    <tbody id="rows"></tbody>
  </table>
</div>
<script>
let password = '';
async function load() {
  password = pw.value;
  const resp = await fetch('/api/admin/api-keys', { headers: { 'X-Admin-Password': password } });
  if (!resp.ok) { err.textContent = '密码错误'; return; }
  err.textContent = ''; tbl.classList.remove('hidden');
  const { keys } = await resp.json();
  const label = { pending: '审核中', active: '已开通', revoked: '已吊销' };
  rows.innerHTML = keys.map(k => `<tr>
    <td>${k.id}</td><td>${k.email}</td><td>${k.key_prefix || '—'}</td>
    <td>${label[k.status]}</td><td>${k.quota_per_day}</td>
    <td>${(k.created_at || '').slice(0, 10)}</td>
    <td>${k.status === 'pending' ? `<button onclick="approve(${k.id})">批准</button>` : ''}
        ${k.status === 'active' ? `<button class="revoke" onclick="revoke(${k.id})">吊销</button>` : ''}</td>
  </tr>`).join('');
}

async function approve(id) {
  const resp = await fetch(`/api/admin/api-keys/${id}/approve`, { method: 'POST', headers: { 'X-Admin-Password': password } });
  const data = await resp.json();
  if (data.api_key) {
    issued.classList.remove('hidden');
    issued.innerHTML = `<b>Key 已签发（仅显示这一次，邮件${data.email_sent ? '已发送' : '发送失败，请手动转告用户'}）：</b><br>${data.api_key}`;
  }
  load();
}

async function revoke(id) {
  if (!confirm('确认吊销？用户将立即无法检索。')) return;
  await fetch(`/api/admin/api-keys/${id}/revoke`, { method: 'POST', headers: { 'X-Admin-Password': password } });
  load();
}
</script>
</body>
</html>
```

注意：admin.html 用了 `hidden` class 但未在 style 定义 `.hidden`，补上：`.hidden { display: none; }`（加入 style 块）。

- [ ] **Step 4: 手工验证页面**

```bash
cd /Users/zhanghan/.openclaw/workspace/criminal-llm-auth
ADMIN_PASSWORD=admin123 JWT_SECRET=test python main.py
# 浏览器打开 http://127.0.0.1:8000/api-access 与 /admin 走一遍：注册→登录→申请→审批→看到 Key
```
Expected: 全流程可走通

- [ ] **Step 5: 提交**

```bash
git add templates/api-access.html templates/admin.html main.py
git commit -m "feat: 案例检索 API 申请页与管理页"
```

---

# 阶段 D：桌面本地后端（criminal-llm/backend）

### Task 10: 配置项 + 云端代理路由

> 【实施说明】本任务已按 `/api/case-search` 前缀完成实现（见顶部修正 3）。下文测试中的本地路径以 `/api/case-search/*` 为准；`_get()` 转发的云端路径仍为 `/api/cases/*`。

**Files:**
- Modify: `criminal-llm/backend/config_manager.py`（DEFAULTS + get_config_status）
- Create: `criminal-llm/backend/case_search_api.py`
- Modify: `criminal-llm/backend/main.py`（注册路由）
- Test: `criminal-llm/tests/test_case_search_api.py`

- [ ] **Step 1: config_manager.py 增加配置项**

DEFAULTS 字典追加两个键：
```python
    "case_service_url": "",   # 案例检索服务地址，空则用小写默认 http://118.196.83.43:8001
    "case_api_key": "",        # 案例检索 API Key（设置页填写）
```
get_config_status() 返回字典追加：
```python
        "case_service_url": config.get("case_service_url", ""),
        "case_api_key": bool(config.get("case_api_key")),
        "case_api_key_value": config.get("case_api_key", ""),
```
（保存走现有 `PUT /api/config`，无需新增端点。）

- [ ] **Step 2: 写失败测试**

```python
# tests/test_case_search_api.py
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import case_search_api
from case_search_api import router, fetch_case_cards


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_search_requires_api_key(client, monkeypatch):
    monkeypatch.setattr(case_search_api, "_service_config", lambda: ("http://cloud", ""))
    resp = client.get("/api/cases/search", params={"q": "盗窃"})
    assert resp.status_code == 400
    assert "API Key" in resp.json()["detail"]


def test_search_forwards_params_and_key(client, monkeypatch):
    seen = {}

    class FakeRequests:
        @staticmethod
        def get(url, params=None, headers=None, timeout=None):
            seen.update(url=url, params=params, headers=headers, timeout=timeout)
            return FakeResp(200, {"total": 1, "results": []})

    monkeypatch.setattr(case_search_api, "_service_config", lambda: ("http://cloud", "cca_x"))
    monkeypatch.setattr(case_search_api, "requests", FakeRequests)
    resp = client.get("/api/cases/search", params={"q": "盗窃", "page": 2})
    assert resp.status_code == 200
    assert seen["url"] == "http://cloud/api/cases/search"
    assert seen["params"]["q"] == "盗窃" and seen["params"]["page"] == 2
    assert seen["headers"]["X-API-Key"] == "cca_x"


def test_upstream_down_returns_503(client, monkeypatch):
    class BrokenRequests:
        @staticmethod
        def get(*a, **kw):
            raise case_search_api.requests.RequestException("down")

    monkeypatch.setattr(case_search_api, "_service_config", lambda: ("http://cloud", "cca_x"))
    monkeypatch.setattr(case_search_api, "requests", BrokenRequests)
    resp = client.get("/api/charges")
    assert resp.status_code == 503


def test_upstream_429_passthrough(client, monkeypatch):
    class QuotaRequests:
        @staticmethod
        def get(*a, **kw):
            return FakeResp(429, {"detail": "超出每日配额（200 次），明日重置"})

    monkeypatch.setattr(case_search_api, "_service_config", lambda: ("http://cloud", "cca_x"))
    monkeypatch.setattr(case_search_api, "requests", QuotaRequests)
    resp = client.get("/api/cases/search")
    assert resp.status_code == 429
    assert "配额" in resp.json()["detail"]


def test_fetch_case_cards_skips_failures(monkeypatch):
    class MixedRequests:
        @staticmethod
        def get(url, headers=None, timeout=None):
            if "第1号" in url:
                return FakeResp(200, {"case_no": "第1号", "title": "甲案"})
            return FakeResp(404, {"detail": "不存在"})

    monkeypatch.setattr(case_search_api, "_service_config", lambda: ("http://cloud", "cca_x"))
    monkeypatch.setattr(case_search_api, "requests", MixedRequests)
    cards = fetch_case_cards(["第1号", "第9999号"])
    assert [c["case_no"] for c in cards] == ["第1号"]


def test_case_no_url_encoded(monkeypatch):
    """案号含中文必须 URL 编码"""
    seen = {}

    class EncRequests:
        @staticmethod
        def get(url, headers=None, timeout=None):
            seen["url"] = url
            return FakeResp(200, {"case_no": "第1000号"})

    monkeypatch.setattr(case_search_api, "_service_config", lambda: ("http://cloud", "cca_x"))
    monkeypatch.setattr(case_search_api, "requests", EncRequests)
    fetch_case_cards(["第1000号"])
    assert "第1000号" not in seen["url"]  # 已被编码
    assert "%" in seen["url"]
```

- [ ] **Step 3: 运行测试确认失败**

Run: `python -m pytest tests/test_case_search_api.py -v`
Expected: FAIL（ModuleNotFoundError: case_search_api）

- [ ] **Step 4: 实现 `backend/case_search_api.py`**

```python
"""案例检索云端代理：本地后端 -> 云端案例微服务

- API Key 存于本地配置（设置页填写），本模块纯转发不存储
- 云端不可达返回 503，Key 未配置返回 400，云端其他错误透传状态码与 detail
"""
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from config_manager import get_config_value

router = APIRouter(prefix="/api/cases", tags=["case-search"])

DEFAULT_CASE_SERVICE_URL = "http://118.196.83.43:8001"
TIMEOUT = 10


class ValidateKeyRequest(BaseModel):
    api_key: str


def _service_config() -> tuple[str, str]:
    base = (get_config_value("case_service_url", "") or DEFAULT_CASE_SERVICE_URL).rstrip("/")
    key = get_config_value("case_api_key", "")
    return base, key


def _unwrap(resp) -> Any:
    if resp.status_code == 200:
        return resp.json()
    try:
        detail = resp.json().get("detail", "案例服务错误")
    except Exception:
        detail = "案例服务错误"
    raise HTTPException(status_code=resp.status_code, detail=detail)


def _get(path: str, params: Optional[dict] = None) -> Any:
    base, key = _service_config()
    if not key:
        raise HTTPException(status_code=400, detail="未配置案例检索 API Key，请前往设置页填写")
    try:
        resp = requests.get(base + path, params=params, headers={"X-API-Key": key}, timeout=TIMEOUT)
    except requests.RequestException:
        raise HTTPException(status_code=503, detail="案例库服务暂不可用，请稍后重试")
    return _unwrap(resp)


@router.get("/search")
def search_cases(q: str = Query(default=""), charge: str = Query(default=""),
                 page: int = Query(default=1, ge=1), size: int = Query(default=20, ge=1, le=50)):
    return _get("/api/cases/search", params={"q": q, "charge": charge, "page": page, "size": size})


@router.get("/charges")
def list_charges():
    return _get("/api/charges")


@router.get("/{case_no}")
def get_case_card(case_no: str):
    return _get(f"/api/cases/{quote(case_no, safe='')}")


@router.get("/{case_no}/full")
def get_case_full(case_no: str):
    return _get(f"/api/cases/{quote(case_no, safe='')}/full")


@router.post("/validate")
def validate_key(req: ValidateKeyRequest):
    """设置页「验证」按钮：用用户输入（可能未保存）的 Key 调云端校验"""
    base, _ = _service_config()
    try:
        resp = requests.post(f"{base}/api/keys/validate", json={"api_key": req.api_key}, timeout=TIMEOUT)
    except requests.RequestException:
        raise HTTPException(status_code=503, detail="案例库服务暂不可用，请稍后重试")
    return _unwrap(resp)


def fetch_case_cards(case_nos: List[str]) -> List[Dict[str, Any]]:
    """供阶段 4 注入使用：批量拉取完整卡片，单篇失败跳过（调用方按实际拿到数量注入）"""
    base, key = _service_config()
    cards: List[Dict[str, Any]] = []
    for no in case_nos:
        try:
            resp = requests.get(f"{base}/api/cases/{quote(no, safe='')}",
                                headers={"X-API-Key": key}, timeout=TIMEOUT)
            if resp.status_code == 200:
                cards.append(resp.json())
        except requests.RequestException:
            continue
    return cards
```

- [ ] **Step 5: main.py 注册路由**

import 区（参照现有 `from legal_kb_api import router as legal_kb_router` 写法）加：
```python
from case_search_api import router as case_search_router
```
路由注册区（`app.include_router(legal_kb_router)` 附近）加：
```python
app.include_router(case_search_router)
```

- [ ] **Step 6: 运行测试确认通过**

Run: `python -m pytest tests/test_case_search_api.py -v`
Expected: 6 passed

- [ ] **Step 7: 提交**

```bash
cd /Users/zhanghan/.openclaw/workspace/criminal-llm
git add backend/case_search_api.py backend/config_manager.py backend/main.py tests/test_case_search_api.py
git commit -m "feat: 本地后端案例检索云端代理（Key 配置 + 错误透传）"
```

---

### Task 11: 阶段 4 注入真实案例 + 局部重生成

**Files:**
- Modify: `criminal-llm/backend/analysis_engine.py`（stage_4_legal_regulations 加 reference_cases 参数 + build_reference_block）
- Modify: `criminal-llm/backend/stage_api.py`（run_single_stage 加 reference_case_nos）
- Test: `criminal-llm/tests/test_stage4_reference_cases.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_stage4_reference_cases.py
from analysis_engine import build_reference_block


def make_cards():
    return [
        {
            "case_no": "第1000号",
            "title": "李某甲等寻衅滋事案",
            "charges": ["寻衅滋事罪"],
            "issue": "未成年人多次强取财物如何处理",
            "holding_summary": "未成年人以轻微暴力强索少量财物，定寻衅滋事罪。",
            "reasoning_excerpt": "本案审理中存在两种意见……",
        },
        {
            "case_no": "第1011号",
            "title": "熊海涛盗窃案",
            "charges": ["盗窃罪"],
            "issue": "帮助转移财物如何定性",
            "holding_summary": "明知系未成年人盗卖财物仍帮助转移的……",
            "reasoning_excerpt": "本院认为……",
        },
    ]


def test_build_reference_block_contains_all_fields():
    block = build_reference_block(make_cards())
    assert "【第1000号】李某甲等寻衅滋事案" in block
    assert "【第1011号】熊海涛盗窃案" in block
    assert "寻衅滋事罪" in block
    assert "未成年人多次强取财物如何处理" in block
    assert "本案审理中存在两种意见" in block


def test_build_reference_block_empty():
    assert build_reference_block([]) == ""
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_stage4_reference_cases.py -v`
Expected: FAIL（ImportError: build_reference_block）

- [ ] **Step 3: analysis_engine.py 修改**

（a）文件合适位置（模块级，AnalysisEngine 类之外）新增：
```python
def build_reference_block(cards: List[Dict[str, Any]]) -> str:
    """把选中的真实案例卡片格式化为提示词注入块"""
    blocks = []
    for c in cards:
        charges = "、".join(c.get("charges", []))
        blocks.append(
            f"【{c['case_no']}】{c['title']}\n"
            f"涉及罪名：{charges}\n"
            f"主要问题：{c.get('issue', '')}\n"
            f"裁判要旨：{c.get('holding_summary', '')}\n"
            f"裁判理由摘录：{c.get('reasoning_excerpt', '')}"
        )
    return "\n\n".join(blocks)
```
（确认 analysis_engine.py 顶部有 `from typing import List, Dict, Any, Optional`，无则补。）

（b）`stage_4_legal_regulations` 签名改为：
```python
    async def stage_4_legal_regulations(
        self,
        defendant: str,
        crime_type: Optional[str] = None,
        progress_cb: Optional[Callable] = None,
        reference_cases: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
```

（c）该方法内 system_prompt 赋值之后、调用 LLM 之前，注入参考案例。现有代码：
```python
        system_prompt = """你是一位资深刑事辩护律师，精通中国刑法。
请根据案件涉及的罪名，梳理相关法律法规、司法解释和类案裁判规则。
...（略）...""" + _NO_CHITCHAT
```
在其后追加：
```python
        if reference_cases:
            system_prompt += f"""

参考案例（以下来自《刑事审判参考》的真实案例，案号与内容均真实可查）：
{build_reference_block(reference_cases)}

引用要求：引用类案时仅可引用以上提供的案例，格式为「【案号】案例名 + 裁判要旨」；
除上述案例外，仍不得引用或编造任何其他案号、法院名称或当事人姓名。"""
```

（d）阶段产物原子保存（防重生成失败覆盖旧产物）：找到 AnalysisEngine 的 `_save_stage` 方法，把直接写文件改为"先写临时文件再替换"：

```python
import os
import tempfile

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
```

`_save_stage` 中所有写 md/json 的位置改用 `_atomic_write`（保持方法签名不变，对所有阶段生效）。

- [ ] **Step 4: stage_api.py 修改**

（a）`run_single_stage` 签名加参数（放在 indictment_file 之后）：
```python
    reference_case_nos: Optional[List[str]] = Body(default=None, embed=True),
```

（b）模块级新增辅助函数：
```python
async def _run_stage_4(engine, defendant, crime_type, reference_case_nos):
    """阶段 4：支持注入用户选中的真实案例卡片"""
    if reference_case_nos:
        from case_search_api import fetch_case_cards
        cards = fetch_case_cards(reference_case_nos)
        return await engine.stage_4_legal_regulations(defendant, crime_type, reference_cases=cards)
    return await engine.stage_4_legal_regulations(defendant, crime_type)
```

（c）stage_methods 字典中阶段 4 改为：
```python
        4: lambda: _run_stage_4(engine, defendant, crime_type, reference_case_nos),
```

- [ ] **Step 5: 写集成测试（追加到 tests/test_stage4_reference_cases.py）**

```python
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_run_stage4_passes_reference_cards():
    """_run_stage_4 在有案号时拉卡片并传给引擎"""
    from stage_api import _run_stage_4

    engine = AsyncMock()
    engine.stage_4_legal_regulations = AsyncMock(return_value={"ok": True})
    cards = [{"case_no": "第1000号", "title": "甲案", "charges": [], "issue": "", "holding_summary": "", "reasoning_excerpt": ""}]

    with patch("case_search_api.fetch_case_cards", return_value=cards):
        await _run_stage_4(engine, "被告人", "盗窃罪", ["第1000号"])

    kwargs = engine.stage_4_legal_regulations.call_args.kwargs
    assert kwargs["reference_cases"] == cards


@pytest.mark.asyncio
async def test_run_stage4_without_refs_unchanged():
    from stage_api import _run_stage_4

    engine = AsyncMock()
    engine.stage_4_legal_regulations = AsyncMock(return_value={"ok": True})
    await _run_stage_4(engine, "被告人", "盗窃罪", None)

    kwargs = engine.stage_4_legal_regulations.call_args.kwargs
    assert "reference_cases" not in kwargs or kwargs["reference_cases"] is None
```

注：如项目 pytest 未配置 asyncio 模式，在 tests/ 同级的 `pytest.ini` 或 `pyproject.toml` 加 `asyncio_mode = "auto"`，或安装 `pytest-asyncio` 并在用例上保留 `@pytest.mark.asyncio`。

- [ ] **Step 6: 运行测试确认通过**

Run: `python -m pytest tests/test_stage4_reference_cases.py -v`
Expected: 4 passed

- [ ] **Step 7: 提交**

```bash
git add backend/analysis_engine.py backend/stage_api.py tests/test_stage4_reference_cases.py pyproject.toml
git commit -m "feat: 阶段 4 支持注入真实参考案例重生成"
```

---

# 阶段 E：桌面前端（criminal-llm/frontend）

### Task 12: API 封装 + 案例检索面板组件

**Files:**
- Create: `frontend/src/api/caseSearch.ts`
- Create: `frontend/src/components/report/CaseSearchPanel.tsx`

前端无单测框架（仅 Playwright），本任务以 `npx tsc --noEmit` + `npm run build` 为验证。

- [ ] **Step 1: `src/api/caseSearch.ts`**

`API_BASE` 的导入方式与 `src/api/stages.ts` 第一行完全一致（若 stages.ts 从 './client' 导入则照抄；若本地定义则复制其定义）。

```ts
import { API_BASE } from './client'

export interface CaseSummary {
  case_no: string
  title: string
  charges: string[]
  issue: string
  holding_summary: string
}

export interface CaseSearchResult {
  total: number
  page: number
  size: number
  results: CaseSummary[]
}

export interface CaseCard extends CaseSummary {
  reasoning_excerpt: string
  keywords: string[]
}

export interface CaseFull {
  case_no: string
  title: string
  full_text: string
}

export interface CaseKeyValidation {
  valid: boolean
  prefix?: string
  used_today?: number
  quota_per_day?: number
}

async function request<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`)
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error(data.detail || `请求失败（${res.status}）`)
  }
  return data as T
}

export function searchCases(q: string, charge: string, page: number, size = 20): Promise<CaseSearchResult> {
  const params = new URLSearchParams({ q, charge, page: String(page), size: String(size) })
  return request<CaseSearchResult>(`/case-search/search?${params}`)
}

export function getCharges(): Promise<{ charges: string[] }> {
  return request<{ charges: string[] }>(`/case-search/charges`)
}

export function getCaseFull(caseNo: string): Promise<CaseFull> {
  return request<CaseFull>(`/case-search/${encodeURIComponent(caseNo)}/full`)
}

export async function validateCaseKey(apiKey: string): Promise<CaseKeyValidation> {
  const res = await fetch(`${API_BASE}/case-search/validate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key: apiKey }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error(data.detail || `请求失败（${res.status}）`)
  }
  return data as CaseKeyValidation
}
```

注意：本地后端代理路由前缀是 `/api/case-search`（见顶部修正 3），`API_BASE` 一般以 `/api` 结尾（与 stages.ts 中 `${API_BASE}/stage-analysis/...` 对照确认拼接结果应为 `/api/case-search/search`）。

- [ ] **Step 2: `src/components/report/CaseSearchPanel.tsx`**

`colors` 的导入与 ReportPage.tsx 中的导入路径保持一致（ReportPage 用了 `colors.gold`、`colors.textPrimary` 等，照抄其 import 来源）。

```tsx
import { useCallback, useEffect, useState } from 'react'
import { Search, BookMarked, FileText, X } from 'lucide-react'
import { colors } from '../MacOSLayout'
import { searchCases, getCharges, getCaseFull, CaseSummary } from '../../api/caseSearch'

interface Props {
  regenerating: boolean
  onRegenerate: (caseNos: string[]) => void
}

export default function CaseSearchPanel({ regenerating, onRegenerate }: Props) {
  const [q, setQ] = useState('')
  const [charge, setCharge] = useState('')
  const [chargeOptions, setChargeOptions] = useState<string[]>([])
  const [results, setResults] = useState<CaseSummary[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [fullText, setFullText] = useState<{ title: string; text: string } | null>(null)

  useEffect(() => {
    getCharges().then(d => setChargeOptions(d.charges)).catch(() => {})
  }, [])

  const doSearch = useCallback(async (pageNum: number) => {
    setLoading(true)
    setError('')
    try {
      const data = await searchCases(q, charge, pageNum)
      setResults(data.results)
      setTotal(data.total)
      setPage(pageNum)
    } catch (e: any) {
      setError(e.message || '检索失败')
      setResults([])
      setTotal(0)
    } finally {
      setLoading(false)
    }
  }, [q, charge])

  const toggle = (caseNo: string) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(caseNo)) next.delete(caseNo)
      else next.add(caseNo)
      return next
    })
  }

  const viewFull = async (caseNo: string, title: string) => {
    try {
      const data = await getCaseFull(caseNo)
      setFullText({ title: `【${data.case_no}】${data.title}`, text: data.full_text })
    } catch (e: any) {
      setError(e.message || '加载全文失败')
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / 20))

  return (
    <div style={{ borderTop: '1px solid', borderColor: colors.goldBorder, marginTop: '28px', paddingTop: '20px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
        <BookMarked className="w-4 h-4" style={{ color: colors.gold }} />
        <span style={{ fontSize: '13px', fontWeight: 600, color: colors.textPrimary }}>案例库检索</span>
        <span style={{ fontSize: '11px', color: colors.textTertiary }}>刑事审判参考 · 1750 篇</span>
      </div>

      <div style={{ display: 'flex', gap: '8px', marginBottom: '12px' }}>
        <input
          value={q}
          onChange={e => setQ(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && doSearch(1)}
          placeholder="关键词 / 争议焦点，如：未成年人 强拿硬要"
          style={{
            flex: 1, padding: '7px 10px', fontSize: '12px', borderRadius: '6px',
            border: `1px solid ${colors.goldBorder}`, color: colors.textPrimary, background: colors.cardBg,
          }}
        />
        <select
          value={charge}
          onChange={e => setCharge(e.target.value)}
          style={{
            width: '160px', padding: '7px 8px', fontSize: '12px', borderRadius: '6px',
            border: `1px solid ${colors.goldBorder}`, color: colors.textPrimary, background: colors.cardBg,
          }}
        >
          <option value="">全部罪名</option>
          {chargeOptions.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <button
          onClick={() => doSearch(1)}
          disabled={loading}
          style={{
            display: 'flex', alignItems: 'center', gap: '4px', padding: '6px 14px', fontSize: '12px',
            borderRadius: '6px', background: colors.gold, color: '#fff', border: 'none',
            cursor: loading ? 'not-allowed' : 'pointer', fontWeight: 500,
          }}
        >
          <Search className="w-3 h-3" /> 检索
        </button>
      </div>

      {error && <div style={{ fontSize: '12px', color: '#c62828', marginBottom: '10px' }}>{error}</div>}

      {results.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {results.map(r => (
            <div key={r.case_no} style={{
              padding: '10px 12px', borderRadius: '8px', border: `1px solid ${colors.goldBorder}`,
              background: selected.has(r.case_no) ? colors.goldBg : 'transparent',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <input type="checkbox" checked={selected.has(r.case_no)} onChange={() => toggle(r.case_no)} />
                <span style={{ fontSize: '12px', fontWeight: 600, color: colors.textPrimary, flex: 1 }}>
                  【{r.case_no}】{r.title}
                </span>
                <button
                  onClick={() => viewFull(r.case_no, r.title)}
                  style={{
                    display: 'flex', alignItems: 'center', gap: '3px', fontSize: '11px', padding: '3px 8px',
                    borderRadius: '5px', border: `1px solid ${colors.goldBorder}`, background: 'transparent',
                    color: colors.gold, cursor: 'pointer',
                  }}
                >
                  <FileText className="w-3 h-3" /> 全文
                </button>
              </div>
              <div style={{ fontSize: '11px', color: colors.textSecondary, marginTop: '6px', paddingLeft: '22px' }}>
                {r.issue}
              </div>
              <div style={{ fontSize: '11px', color: colors.textTertiary, marginTop: '4px', paddingLeft: '22px' }}>
                要旨：{r.holding_summary.slice(0, 80)}…
              </div>
            </div>
          ))}

          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: '4px' }}>
            <span style={{ fontSize: '11px', color: colors.textTertiary }}>
              共 {total} 篇 · 第 {page}/{totalPages} 页
            </span>
            <div style={{ display: 'flex', gap: '6px' }}>
              <button disabled={page <= 1 || loading} onClick={() => doSearch(page - 1)}
                style={{ fontSize: '11px', padding: '3px 10px', borderRadius: '5px', border: `1px solid ${colors.goldBorder}`, background: 'transparent', color: colors.gold, cursor: 'pointer' }}>上一页</button>
              <button disabled={page >= totalPages || loading} onClick={() => doSearch(page + 1)}
                style={{ fontSize: '11px', padding: '3px 10px', borderRadius: '5px', border: `1px solid ${colors.goldBorder}`, background: 'transparent', color: colors.gold, cursor: 'pointer' }}>下一页</button>
            </div>
          </div>
        </div>
      )}

      {selected.size > 0 && (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          marginTop: '12px', padding: '10px 12px', borderRadius: '8px', background: colors.goldBg,
        }}>
          <span style={{ fontSize: '12px', color: colors.textPrimary }}>已选 {selected.size} 篇案例</span>
          <button
            onClick={() => onRegenerate(Array.from(selected))}
            disabled={regenerating}
            style={{
              padding: '6px 14px', fontSize: '12px', borderRadius: '6px', border: 'none',
              background: regenerating ? colors.goldBorder : colors.gold, color: '#fff',
              cursor: regenerating ? 'not-allowed' : 'pointer', fontWeight: 500,
            }}
          >
            {regenerating ? '正在重新生成…' : '引用选中案例并重新生成'}
          </button>
        </div>
      )}

      {fullText && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', zIndex: 1000,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }} onClick={() => setFullText(null)}>
          <div style={{
            background: colors.cardBg, borderRadius: '12px', padding: '24px', maxWidth: '720px',
            width: '90%', maxHeight: '80vh', overflowY: 'auto',
          }} onClick={e => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
              <span style={{ fontSize: '14px', fontWeight: 600, color: colors.textPrimary }}>{fullText.title}</span>
              <button onClick={() => setFullText(null)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: colors.textTertiary }}>
                <X className="w-4 h-4" />
              </button>
            </div>
            <div style={{ fontSize: '13px', color: colors.textSecondary, whiteSpace: 'pre-wrap', lineHeight: 1.8 }}>
              {fullText.text}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
```

如 `colors` 中无 `cardBg`/`textSecondary` 等键，以 ReportPage 实际使用过的色键为准替换（只换键名，不改结构）。

- [ ] **Step 3: 类型检查 + 构建**

```bash
cd /Users/zhanghan/.openclaw/workspace/criminal-llm/frontend
npx tsc --noEmit
npm run build
```
Expected: 无类型错误，构建成功

- [ ] **Step 4: 提交**

```bash
cd /Users/zhanghan/.openclaw/workspace/criminal-llm
git add frontend/src/api/caseSearch.ts frontend/src/components/report/CaseSearchPanel.tsx
git commit -m "feat: 报告页案例库检索面板组件"
```

---

### Task 13: ReportPage 挂载 + 重生成接线

**Files:**
- Modify: `frontend/src/pages/ReportPage.tsx`（import、handler、挂载点）
- Modify: `frontend/src/api/stages.ts`（runSingleStage 加第 6 参）

- [ ] **Step 1: stages.ts 的 runSingleStage 加 referenceCaseNos**

```ts
export async function runSingleStage(caseId: string, stageNum: number, defendant: string, charges?: string[], indictmentFile?: string, referenceCaseNos?: string[]): Promise<any> {
  const body: Record<string, any> = { defendant, charges: charges, indictment_file: indictmentFile }
  if (referenceCaseNos && referenceCaseNos.length > 0) {
    body.reference_case_nos = referenceCaseNos
  }
  const res = await fetch(`${API_BASE}/stage-analysis/${caseId}/run-stage/${stageNum}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  })
  return res.json()
}
```

- [ ] **Step 2: ReportPage.tsx 增加 handler（放在现有 handleRegenerateLegalKB 之后，约 line 1115）**

```tsx
  // 引用选中案例重新生成法律法规（阶段 4）
  const handleRegenerateWithCases = useCallback(async (caseNos: string[]) => {
    if (!caseId || !defendant || caseNos.length === 0) return
    setRegeneratingLegal(true)
    try {
      await api.runSingleStage(caseId, 4, defendant, undefined, undefined, caseNos)
      const result = await api.getStageResult(caseId, 4)
      if (result.success && result.markdown) {
        setStageContent(prev => ({ ...prev, stage_4: result.markdown }))
      }
      loadLegalKB()
    } catch { /* ignore */ }
    finally { setRegeneratingLegal(false) }
  }, [caseId, defendant, loadLegalKB])
```

- [ ] **Step 3: ReportPage.tsx 挂载面板**

（a）import 区加：
```tsx
import CaseSearchPanel from '../components/report/CaseSearchPanel'
```

（b）新增渲染函数（放在 renderLegalKBPanel 定义之后）：
```tsx
  // ===== 案例库检索面板 =====
  const renderCaseSearchPanel = () => {
    if (activeTab !== 'stage_4') return null
    return (
      <CaseSearchPanel
        regenerating={regeneratingLegal}
        onRegenerate={handleRegenerateWithCases}
      />
    )
  }
```

（c）在 JSX 中找到 `{renderLegalKBPanel()}` 的调用处，在其前面一行插入：
```tsx
            {renderCaseSearchPanel()}
```
（案例检索面板显示在自定义法律法规面板上方，同属法律法规 tab 底部区域。）

- [ ] **Step 4: 类型检查 + 构建**

```bash
cd frontend && npx tsc --noEmit && npm run build
```
Expected: 通过

- [ ] **Step 5: 手工验证（开发环境）**

```bash
# 终端 1：本地后端
cd backend && python3 main.py
# 终端 2：云端案例服务（本地起一份，CASES_DB_PATH 指向提炼产物）
cd ../.. && cd /Users/zhanghan/.openclaw/workspace/criminal-llm-cases
CASES_DB_PATH=/Users/zhanghan/.openclaw/workspace/criminal-llm/scripts/cases.db \
AUTH_DB_PATH=/tmp/test-auth.db python3 -m uvicorn main:app --port 8001
# 终端 3：前端
cd /Users/zhanghan/.openclaw/workspace/criminal-llm/frontend && npm run dev
```
在设置页填入测试 Key → 报告页法律法规 tab → 检索 → 勾选 → 重新生成 → 确认阶段 4 输出中出现【第X号】引用。

- [ ] **Step 6: 提交**

```bash
git add frontend/src/pages/ReportPage.tsx frontend/src/api/stages.ts
git commit -m "feat: 报告页挂载案例检索面板并接线阶段 4 重生成"
```

---

### Task 14: 设置页 API Key 配置区块

**Files:**
- Modify: `frontend/src/pages/SettingsPage.tsx`

- [ ] **Step 1: 新增「案例检索 API」卡片**

SettingsPage 结构仿照现有 LLM 配置卡片。在设置页合适位置（建议 LLM 配置卡片之后）插入新卡片。以下代码假定页面已有 `config`/`setConfig` 状态与保存函数（先读 SettingsPage 确认其状态管理模式，按其模式接入；若页面用独立 useState per field，则照下述实现）：

```tsx
// state
const [caseApiKey, setCaseApiKey] = useState('')
const [caseServiceUrl, setCaseServiceUrl] = useState('')
const [caseKeyStatus, setCaseKeyStatus] = useState<'idle' | 'checking' | 'ok' | 'fail'>('idle')
const [caseKeyInfo, setCaseKeyInfo] = useState('')

// 初始化（在页面加载配置处，与 llm_api_key_value 同源的数据里取）：
// setCaseApiKey(data.case_api_key_value || ''); setCaseServiceUrl(data.case_service_url || '')

// 验证
const handleValidateCaseKey = async () => {
  setCaseKeyStatus('checking')
  try {
    const { validateCaseKey } = await import('../api/caseSearch')
    const result = await validateCaseKey(caseApiKey)
    if (result.valid) {
      setCaseKeyStatus('ok')
      setCaseKeyInfo(`有效 · 今日已用 ${result.used_today}/${result.quota_per_day}`)
    } else {
      setCaseKeyStatus('fail')
      setCaseKeyInfo('Key 无效或已吊销')
    }
  } catch (e: any) {
    setCaseKeyStatus('fail')
    setCaseKeyInfo(e.message || '验证失败')
  }
}
```

保存：在现有保存配置的处理里把 `case_api_key: caseApiKey` 与 `case_service_url: caseServiceUrl` 并入 PUT /api/config 的 body。

卡片 JSX（样式复用页面现有卡片/输入框/按钮风格）：

```tsx
<卡片容器>
  <标题>案例检索 API</标题>
  <说明>用于报告页检索《刑事审判参考》案例。申请地址：https://casefix.cn/api-access</说明>
  <input type="password" value={caseApiKey} onChange={e => setCaseApiKey(e.target.value)} placeholder="cca_xxxxxxxx" />
  <input value={caseServiceUrl} onChange={e => setCaseServiceUrl(e.target.value)} placeholder="服务地址（留空用默认）" />
  <button onClick={handleValidateCaseKey}>验证</button>
  {caseKeyStatus !== 'idle' && (
    <span style={{ color: caseKeyStatus === 'ok' ? '#2e7d32' : '#c62828', fontSize: 12 }}>
      {caseKeyStatus === 'checking' ? '验证中…' : caseKeyInfo}
    </span>
  )}
</卡片容器>
```

- [ ] **Step 2: 类型检查 + 构建**

```bash
cd frontend && npx tsc --noEmit && npm run build
```
Expected: 通过

- [ ] **Step 3: 提交**

```bash
git add frontend/src/pages/SettingsPage.tsx
git commit -m "feat: 设置页新增案例检索 API Key 配置"
```

---

# 阶段 F：部署与 HTTPS（云服务器 118.196.83.43）

### Task 15: 部署案例微服务 + 更新认证服务

**Files:**
- Create: `criminal-llm-cases/deploy/criminal-llm-cases.service`
- Create: `criminal-llm-cases/deploy/README.md`

- [ ] **Step 1: systemd 单元 `deploy/criminal-llm-cases.service`**

```ini
[Unit]
Description=Criminal LLM Case Search Service
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/criminal-llm-cases
Environment=CASES_DB_PATH=/opt/criminal-llm-cases/data/cases.db
Environment=AUTH_DB_PATH=/opt/criminal-llm-auth/data/auth.db
Environment=USAGE_DB_PATH=/opt/criminal-llm-cases/data/usage.db
ExecStart=/opt/criminal-llm-cases/venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8001
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: 部署（服务器上执行，命令写入 deploy/README.md 并照做）**

```bash
# 本地：推送代码与数据库
cd /Users/zhanghan/.openclaw/workspace/criminal-llm-cases
git archive HEAD | ssh root@118.196.83.43 "mkdir -p /opt/criminal-llm-cases && tar -x -C /opt/criminal-llm-cases"
scp /Users/zhanghan/.openclaw/workspace/criminal-llm/scripts/cases.db root@118.196.83.43:/opt/criminal-llm-cases/data/cases.db

# 服务器：venv + 依赖 + systemd
ssh root@118.196.83.43 '
  cd /opt/criminal-llm-cases
  python3 -m venv venv && venv/bin/pip install -r requirements.txt
  cp deploy/criminal-llm-cases.service /etc/systemd/system/
  systemctl daemon-reload && systemctl enable --now criminal-llm-cases
  systemctl status criminal-llm-cases --no-pager
'

# 认证服务更新（含 api_keys 表与申请/管理页）
cd /Users/zhanghan/.openclaw/workspace/criminal-llm-auth
scp main.py root@118.196.83.43:/opt/criminal-llm-auth/main.py
scp templates/api-access.html templates/admin.html root@118.196.83.43:/opt/criminal-llm-auth/templates/
# 服务器：设置 ADMIN_PASSWORD 并重启认证服务（按现有 nohup/systemd 方式重启）
```

注意：认证服务现有运行方式如为 nohup，重启时确保 `ADMIN_PASSWORD` 与 `JWT_SECRET` 环境变量与之前一致（JWT_SECRET 变了会导致全部用户 token 失效）。

- [ ] **Step 3: 冒烟测试**

```bash
curl http://118.196.83.43:8001/health
# 期望 {"status":"ok"}
curl -X POST http://118.196.83.43:8000/api/register -H 'Content-Type: application/json' -d '{"email":"smoke@test.com","password":"test123"}'
# 走一遍：申请 → 管理页批准 → curl 带 Key 检索
curl "http://118.196.83.43:8001/api/cases/search?q=盗窃" -H "X-API-Key: <刚签发的key>"
```

- [ ] **Step 4: 提交部署文件**

```bash
cd /Users/zhanghan/.openclaw/workspace/criminal-llm-cases
git add deploy/
git commit -m "chore: systemd 单元与部署说明"
```

---

### Task 16: HTTPS（Caddy）+ 桌面端默认地址切换 + 端到端验收

**Files:**
- Modify: `criminal-llm/backend/case_search_api.py`（DEFAULT_CASE_SERVICE_URL）
- Modify: `criminal-llm-auth/main.py`（如需绑定域名路由，否则不动）

- [ ] **Step 1: 服务器安装 Caddy 并配置**

DNS：先把 `api.casefix.cn` A 记录指向 118.196.83.43（需你在域名控制台操作）。

`/etc/caddy/Caddyfile`：
```
api.casefix.cn {
    @cases path /api/cases* /api/charges /api/keys/validate /health
    reverse_proxy @cases 127.0.0.1:8001
    reverse_proxy 127.0.0.1:8000
}
```

```bash
apt install -y caddy   # 或对应发行版方式
systemctl reload caddy
curl -I https://api.casefix.cn/health
```

同时把认证服务的 8000 端口防火墙收敛为仅 127.0.0.1 之外加 Caddy——保守做法：保持 8000 对外（兼容旧桌面端），8001 只监听 127.0.0.1（systemd 单元已如此配置）。

- [ ] **Step 2: 桌面端默认地址切换**

`criminal-llm/backend/case_search_api.py`：
```python
DEFAULT_CASE_SERVICE_URL = "https://api.casefix.cn"
```

```bash
cd /Users/zhanghan/.openclaw/workspace/criminal-llm
python -m pytest tests/test_case_search_api.py -v
git add backend/case_search_api.py
git commit -m "chore: 案例服务默认地址切换为 HTTPS 域名"
```

- [ ] **Step 3: 端到端验收（人工检查点）**

1. 官网 `https://casefix.cn/api-access`（或 Caddy 域名下）注册/登录 → 申请 API
2. `/admin` 管理页批准 → 邮箱收到 Key
3. 桌面端设置页填入 Key → 验证显示"有效 · 今日已用 0/200"
4. 报告页法律法规 tab → 检索"盗窃"→ 勾选 2 篇 → 查看全文 → 引用并重新生成
5. 检查重生成后的阶段 4 输出：出现【第X号】真实引用，无编造案号
6. 配额验证：连续请求超过配额返回 429 且前端提示友好
7. 联网兜底：停掉案例服务，确认面板错误提示 + 原联网版类案检索仍可用

- [ ] **Step 4: 更新说明书**

按 CLAUDE.md 规范更新 `docs/user-manual.html`（新增案例检索功能说明：申请 API Key → 设置页配置 → 报告页检索引用），执行 `./scripts/sync-manual.sh`，提交：
```bash
git add docs/user-manual.html frontend/public/user-manual.html
git commit -m "docs: 说明书新增案例检索功能"
```

---

# 执行顺序与依赖

```
Task 1-3（提炼脚本）──→ Task 4（全量提炼，~2h）
     │
Task 5-6（案例服务）←── 可与 Task 1-4 并行开发，Task 4 产物部署在 Task 15
     │
Task 7-9（认证服务 Key 体系）←── 与 Task 5-6 并行
     │
Task 10-11（桌面后端）←── 依赖 Task 5-6 接口约定
     │
Task 12-14（前端）←── 依赖 Task 10-11
     │
Task 15（部署）←── 依赖 Task 4-9
     │
Task 16（HTTPS + 端到端验收）←── 依赖以上全部
```

**关键人工检查点**：Task 4（提炼质量门）、Task 13 Step 5（本地联调）、Task 16 Step 3（端到端验收）。
