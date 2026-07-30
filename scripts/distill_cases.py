"""《刑事审判参考》离线提炼主脚本：MD 目录 -> cases.db（卡片 + 原文 + FTS5）

用法：
    python3 scripts/distill_cases.py [--md-dir ~/Desktop/刑事审判参考_MD] [--db cases.db] [--limit N]

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
    created_at TEXT NOT NULL,
    issue_source TEXT NOT NULL DEFAULT 'original'
);
CREATE VIRTUAL TABLE IF NOT EXISTS cases_fts USING fts5(
    case_no UNINDEXED,
    content,
    tokenize='unicode61'
);
"""

WORKERS = 5

# 文章体裁案例缺「三、裁判理由」章节时，裁判分析即正文，摘录取全文开头
FALLBACK_REASONING_EXCERPT_LEN = 2000


def build_match_query(q: str) -> str:
    """与检索服务共用的 MATCH 查询构造（bigram 短语）"""
    bg = to_bigrams(q)
    return f'"{bg}"' if bg else '""'


def init_schema(conn: sqlite3.Connection):
    conn.executescript(SCHEMA)
    # 既有库迁移：补充 issue_source 列（'original'=原文章节，'llm'=LLM 总结）
    try:
        conn.execute("ALTER TABLE cases ADD COLUMN issue_source TEXT NOT NULL DEFAULT 'original'")
    except sqlite3.OperationalError:
        pass  # 列已存在


def _fts_content(title: str, charges: list, issue: str, summary: str, keywords: list) -> str:
    return to_bigrams(" ".join([title, " ".join(charges), issue, summary, " ".join(keywords)]))


def insert_case(conn, case_no, title, sections, card, md_text):
    """插入案例 + FTS 索引。案号 UNIQUE 冲突时自动加 -2/-3... 后缀重试，返回最终入库案号"""
    now = datetime.now(timezone.utc).isoformat()
    final_no = case_no
    suffix = 2
    while True:
        try:
            conn.execute(
                "INSERT INTO cases (case_no, title, charges, issue, holding_summary, reasoning_excerpt,"
                " keywords, full_text, created_at, issue_source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    final_no, title, json.dumps(card["charges"], ensure_ascii=False),
                    sections["issue"], card["holding_summary"], sections["reasoning_excerpt"],
                    json.dumps(card["keywords"], ensure_ascii=False), md_text, now,
                    sections.get("issue_source", "original"),
                ),
            )
            break
        except sqlite3.IntegrityError:
            # 仅当冲突确为案号重复时加后缀重试，其余完整性错误照常抛出
            if conn.execute("SELECT 1 FROM cases WHERE case_no = ?", (final_no,)).fetchone():
                final_no = f"{case_no}-{suffix}"
                suffix += 1
            else:
                raise
    conn.execute(
        "INSERT INTO cases_fts (case_no, content) VALUES (?, ?)",
        (final_no, _fts_content(title, card["charges"], sections["issue"], card["holding_summary"], card["keywords"])),
    )
    return final_no


def run(md_dir: Path, db_path: Path, session, base_url: str, api_key: str, model: str,
        limit: int | None = None, progress_cb=print) -> dict:
    conn = sqlite3.connect(str(db_path))
    init_schema(conn)
    existing = {r[0] for r in conn.execute("SELECT case_no FROM cases")}
    stats = {"distilled": 0, "skipped_existing": 0, "skipped_invalid_name": 0, "failed": 0}
    failed = []
    # 写操作均在主线程消费循环执行，锁为防御性保留
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
        # 文章体裁缺「二、主要问题」章节：不再直接失败，改由 LLM 从全文总结主要问题
        need_issue = sections["issue"] is None
        try:
            card = distill_case(session, base_url, api_key, model, title, md_text,
                                need_issue=need_issue)
        except Exception as e:
            return (case_no, None, str(e), None, None, None)
        if need_issue:
            # LLM 总结的 issue 标记为非原文；无章节边界，裁判分析即正文，摘录取全文开头
            sections = {
                "issue": card["issue"],
                "reasoning_excerpt": md_text.strip()[:FALLBACK_REASONING_EXCERPT_LEN],
                "issue_source": "llm",
            }
        else:
            sections["issue_source"] = "original"
        return (case_no, title, None, sections, card, md_text)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(process, p, no, t): no for p, no, t in todo}
        for fut in as_completed(futures):
            case_no, title, err, sections, card, md_text = fut.result()
            if err:
                stats["failed"] += 1
                failed.append({"case_no": case_no, "reason": err})
                progress_cb(f"[失败] {case_no}: {err}")
                continue
            with db_lock:
                try:
                    final_no = insert_case(conn, case_no, title, sections, card, md_text)
                    conn.commit()
                except Exception as e:
                    # DB 异常隔离处理，不中断整批
                    conn.rollback()
                    stats["failed"] += 1
                    failed.append({"case_no": case_no, "reason": f"DB 写入失败: {e}"})
                    progress_cb(f"[失败] {case_no}: DB 写入失败: {e}")
                    continue
            stats["distilled"] += 1
            if final_no != case_no:
                progress_cb(f"[改名] {case_no} 案号重复，入库为 {final_no}")
            progress_cb(f"[完成] {final_no} {title}（累计 {stats['distilled']}）")

    fail_path = Path(db_path).parent / "failed_cases.json"
    if failed:
        fail_path.write_text(json.dumps(failed, ensure_ascii=False, indent=2), encoding="utf-8")
        progress_cb(f"失败清单已写入: {fail_path}（{len(failed)} 篇）")
    elif fail_path.exists():
        # 本轮无失败，清理上一轮残留的失败清单，避免误导
        fail_path.unlink()
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
    stats = run(Path(args.md_dir), Path(args.db), requests.Session(), base_url, api_key, model,
                limit=args.limit)
    print(f"完成: {stats}")
