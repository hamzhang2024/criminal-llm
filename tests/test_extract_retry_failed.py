"""提取入口自动重提失败条目：prune_failed_evidence 清理失败空壳

修复背景：按份提取失败的文书会在 evidence/ 下留下空壳（内容含"按份提取失败"），
且 index.json 中保留条目。文件级断点续传只看 source 是否已有条目，
导致失败文档所属卷被整体跳过、永远不会重试。提取入口需先清理这些空壳。
"""
import json
from pathlib import Path

from case_manager import prune_failed_evidence, _next_evidence_id

# 真实失败空壳的内容形态（441 字节左右，头部 + 失败标记）
FAILED_SHELL = """# 赵某某第十次讯问笔录

| 项目 | 内容 |
|------|------|
| **证据类型** | 犯罪嫌疑人供述和辩解 |
| **来源文件** | 第2卷_去水印.md |
| **涉案人员** |  |
| **关联要件** | 无 |

## 关联信息



## 关键事实

无

## 详细摘要

⚠️ 本文书提取失败或校验未通过，请重新提取。目录日期：2026-1-27

## 原文摘录



## 矛盾提示

⚠️ 按份提取失败，需重提"""

SUCCESS_CONTENT = """# 张某某第一次讯问笔录

## 详细摘要

问：你把事情经过讲一下？答：2026年3月12日那天……（成功提取的正文内容）"""


def _make_case(tmp_path: Path, entries: list) -> Path:
    """构造案件目录：evidence/index.json + 每个条目对应的证据 md 文件

    entries 中 _failed=True 的条目写入失败空壳内容，其余写入成功内容。
    """
    case_path = tmp_path / "案件_测试_20260816"
    evidence_dir = case_path / "evidence"
    evidence_dir.mkdir(parents=True)
    index_entries = []
    for i, ev in enumerate(entries, start=1):
        md_name = f"{i:03d}_{ev['name']}.md"
        content = FAILED_SHELL if ev.get("_failed") else SUCCESS_CONTENT
        (evidence_dir / md_name).write_text(content, encoding="utf-8")
        index_entries.append({
            "id": i,
            "name": ev["name"],
            "type": "犯罪嫌疑人供述和辩解",
            "source": ev["source"],
            "md_file": md_name,
        })
    index_data = {
        "case_id": "case_test",
        "total_evidence": len(index_entries),
        "evidence": index_entries,
        "case_charges": ["测试罪"],
        "generated_at": "2026-08-16T00:00:00",
    }
    (evidence_dir / "index.json").write_text(
        json.dumps(index_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return case_path


def _read_index(case_path: Path) -> dict:
    return json.loads((case_path / "evidence" / "index.json").read_text(encoding="utf-8"))


def test_prune_removes_failed_entry_and_shell_file(tmp_path):
    """2 成功 + 1 失败：失败条目移除、空壳删除、成功条目保留"""
    case_path = _make_case(tmp_path, [
        {"name": "张某某第一次讯问笔录", "source": "第1卷_去水印.md"},
        {"name": "张某某第二次讯问笔录", "source": "第1卷_去水印.md"},
        {"name": "赵某某第十次讯问笔录", "source": "第2卷_去水印.md", "_failed": True},
    ])
    shell_file = case_path / "evidence" / "003_赵某某第十次讯问笔录.md"

    removed = prune_failed_evidence(case_path)

    assert removed == ["赵某某第十次讯问笔录"]
    index = _read_index(case_path)
    names = [ev["name"] for ev in index["evidence"]]
    assert names == ["张某某第一次讯问笔录", "张某某第二次讯问笔录"]
    assert index["total_evidence"] == 2
    assert not shell_file.exists()
    assert (case_path / "evidence" / "001_张某某第一次讯问笔录.md").exists()
    assert (case_path / "evidence" / "002_张某某第二次讯问笔录.md").exists()


def test_prune_deletes_summary_cache_of_failed_entry(tmp_path):
    """失败条目的摘要缓存（summaries/{stem}.md + .meta.json）一并删除，成功条目的保留"""
    case_path = _make_case(tmp_path, [
        {"name": "张某某第一次讯问笔录", "source": "第1卷_去水印.md"},
        {"name": "赵某某第十次讯问笔录", "source": "第2卷_去水印.md", "_failed": True},
    ])
    summaries = case_path / "evidence" / "summaries"
    summaries.mkdir()
    ok_cache = summaries / "001_张某某第一次讯问笔录.md"
    ok_cache.write_text("成功条目的摘要缓存", encoding="utf-8")
    fail_cache = summaries / "002_赵某某第十次讯问笔录.md"
    fail_cache.write_text("失败空壳的摘要缓存", encoding="utf-8")
    fail_meta = summaries / "002_赵某某第十次讯问笔录.meta.json"
    fail_meta.write_text('{"src_len": 441, "warning": false}', encoding="utf-8")

    removed = prune_failed_evidence(case_path)

    assert removed == ["赵某某第十次讯问笔录"]
    assert not fail_cache.exists()
    assert not fail_meta.exists()
    assert ok_cache.exists()


def test_prune_all_failed_leaves_empty_evidence(tmp_path):
    """全失败：index.json 的 evidence 为空数组"""
    case_path = _make_case(tmp_path, [
        {"name": "赵某某第十次讯问笔录", "source": "第2卷_去水印.md", "_failed": True},
    ])

    removed = prune_failed_evidence(case_path)

    assert removed == ["赵某某第十次讯问笔录"]
    index = _read_index(case_path)
    assert index["evidence"] == []
    assert index["total_evidence"] == 0


def test_prune_no_failed_returns_empty_and_keeps_index(tmp_path):
    """无失败条目：返回空列表，index.json 保持不变"""
    case_path = _make_case(tmp_path, [
        {"name": "张某某第一次讯问笔录", "source": "第1卷_去水印.md"},
        {"name": "张某某第二次讯问笔录", "source": "第1卷_去水印.md"},
    ])
    before = (case_path / "evidence" / "index.json").read_text(encoding="utf-8")

    removed = prune_failed_evidence(case_path)

    assert removed == []
    after = (case_path / "evidence" / "index.json").read_text(encoding="utf-8")
    assert json.loads(after) == json.loads(before)


def test_prune_without_index_returns_empty(tmp_path):
    """无 index.json（尚未提取过）：返回空列表，不报错"""
    case_path = tmp_path / "案件_测试_20260816"
    case_path.mkdir()

    assert prune_failed_evidence(case_path) == []


# ── 证据编号唯一性：中部删除后重提不得撞号 ──
# 背景：next_id 原为 len(all_evidence) + 1，prune/invalidate 删除清单中部条目后
# 重提时 next_id ≤ 现存最大编号 → 新证据与现存条目编号重复（报告页"证据NNN"
# 超链接歧义、{id:03d}_姓名.md 文件互相覆盖）

def test_next_evidence_id_after_middle_deletion():
    """中部条目被 prune/invalidate 删除（id 1,2,5,6）：next_id 必须是 7 而非 len+1=5"""
    evidence = [
        {"id": 1, "name": "证据一", "md_file": "001_证据一.md"},
        {"id": 2, "name": "证据二", "md_file": "002_证据二.md"},
        {"id": 5, "name": "证据五", "md_file": "005_证据五.md"},
        {"id": 6, "name": "证据六", "md_file": "006_证据六.md"},
    ]
    assert _next_evidence_id(evidence) == 7


def test_next_evidence_id_falls_back_to_md_file_prefix():
    """旧版起诉书条目无 id 字段：回退 md_file 数字前缀取最大值"""
    evidence = [
        {"id": 3, "name": "证据三", "md_file": "003_证据三.md"},
        {"name": "起诉意见书 — 第1卷", "md_file": "007_起诉意见书 — 第1卷.md"},
    ]
    assert _next_evidence_id(evidence) == 8


def test_next_evidence_id_empty_list():
    """空清单（首次提取）：从 1 开始"""
    assert _next_evidence_id([]) == 1
