import json
import sqlite3

import distill_cases

# post() 的形参 json 会遮蔽模块名，提前取别名供 OkSession 使用
_json_dumps = json.dumps


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
            "json": lambda self: {"choices": [{"message": {"content": _json_dumps(card, ensure_ascii=False)}}]},
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


def test_run_limit(tmp_path):
    md_dir = tmp_path / "md"
    md_dir.mkdir()
    for i in range(1, 4):
        (md_dir / f"【第{i}号】案{i}——问题.md").write_text(make_md(i), encoding="utf-8")

    stats = distill_cases.run(md_dir, tmp_path / "cases.db", OkSession(),
                              "http://fake/v1", "key", "model", limit=1)
    assert stats["distilled"] == 1
