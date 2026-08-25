"""清除证据彻底性验证：目录重建 + 状态取消 + 防复活 + 残留清单

用户反馈"感觉清除的不干净"——逐项验证清除后端到端行为。
"""
import asyncio
import json
from pathlib import Path

import case_manager
from case_manager import clear_evidence, EXTRACT_TASKS


def _make_case(tmp_path: Path) -> Path:
    """构造含完整证据+分析状态的案件目录"""
    case_path = tmp_path / "案件_测试_20260825"
    evidence_dir = case_path / "evidence"
    (evidence_dir / "summaries").mkdir(parents=True)
    (evidence_dir / "_temp_extract" / "第1卷_去水印").mkdir(parents=True)
    # 证据条目 + 摘要缓存 + 续传状态
    (evidence_dir / "001_张某第一次讯问笔录.md").write_text("# 证据", encoding="utf-8")
    (evidence_dir / "summaries" / "001_张某第一次讯问笔录.md").write_text("摘要", encoding="utf-8")
    (evidence_dir / "_temp_extract" / "第1卷_去水印" / "_perdoc_000.json").write_text("{}", encoding="utf-8")
    (evidence_dir / "_temp_extract" / "第1卷_去水印.done").write_text("12345", encoding="utf-8")
    (evidence_dir / "completeness_report.json").write_text("{}", encoding="utf-8")
    (evidence_dir / "index.json").write_text(json.dumps({
        "evidence": [{"id": 1, "name": "张某第一次讯问笔录", "source": "第1卷_去水印.md",
                      "md_file": "001_张某第一次讯问笔录.md"}],
        "total_evidence": 1,
        "completed_sources": ["第1卷_去水印.md"],
    }, ensure_ascii=False), encoding="utf-8")
    # 分析产物（消耗证据的上游结果）
    (case_path / "analysis" / "stage_1").mkdir(parents=True)
    (case_path / "analysis" / "stage_1" / "output.md").write_text("分析结果", encoding="utf-8")
    return case_path


def test_clear_removes_all_evidence_state(tmp_path, monkeypatch):
    """清除后：index/证据文件/摘要/temp/完整性报告全部消失，任务状态为取消"""
    case_path = _make_case(tmp_path)
    monkeypatch.setattr(case_manager, "find_case_path", lambda cid: case_path)

    result = asyncio.run(clear_evidence("case_test"))

    assert result["success"] is True
    evidence_dir = case_path / "evidence"
    # 目录存在但应完全为空
    assert evidence_dir.exists()
    leftovers = list(evidence_dir.rglob("*")
                   )
    assert leftovers == [], f"清除后仍有残留: {leftovers}"
    # 任务状态为取消（防复活：摘要层的 should_abort 会拦截写回）
    assert EXTRACT_TASKS.get("case_test") == "cancelled"


def test_clear_then_extract_status_not_running(tmp_path, monkeypatch):
    """清除后 extract-status 不再返回 running（前端不会误判还在提取）"""
    case_path = _make_case(tmp_path)
    monkeypatch.setattr(case_manager, "find_case_path", lambda cid: case_path)
    asyncio.run(clear_evidence("case_test"))

    from case_manager import get_extract_status
    status = asyncio.run(get_extract_status("case_test"))
    assert status.get("status") != "running"


def test_clear_does_not_touch_analysis_dir(tmp_path, monkeypatch):
    """现状记录：清除证据不动 analysis/ 目录（上游分析产物保留）

    注意：这是当前实现的行为，不是断言"应该如此"。
    分析产物基于旧证据生成，清除证据后这些结果在语义上已过期——
    是否需要联动清除见设计讨论。
    """
    case_path = _make_case(tmp_path)
    monkeypatch.setattr(case_manager, "find_case_path", lambda cid: case_path)
    asyncio.run(clear_evidence("case_test"))

    # analysis/ 目录仍在（现状）
    assert (case_path / "analysis" / "stage_1" / "output.md").exists()
