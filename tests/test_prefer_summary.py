"""分析端双层消费：prefer_summary=True 用 digest，False 用全文"""
import json
from pathlib import Path

from analysis_engine import AnalysisEngine, _apply_digest


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


def test_load_evidence_texts_digest_for_evidence_not_indictment(tmp_path):
    """集成：prefer_summary=True 时证据用 digest，起诉书保留全文（不摘要）"""
    case_dir = tmp_path / "case"
    ev_dir = case_dir / "evidence"
    ev_dir.mkdir(parents=True)

    (ev_dir / "001_笔录.md").write_text("笔录全文很长", encoding="utf-8")
    (ev_dir / "002_起诉书.md").write_text("起诉书原文全文", encoding="utf-8")

    (ev_dir / "index.json").write_text(json.dumps({
        "evidence": [
            {"id": 1, "name": "张某讯问笔录", "type": "犯罪嫌疑人供述和辩解",
             "md_file": "001_笔录.md", "digest": "浓缩摘要"},
            {"id": 2, "name": "起诉书", "type": "起诉书",
             "md_file": "002_起诉书.md", "digest": "不该被用的起诉书摘要"},
        ]
    }, ensure_ascii=False), encoding="utf-8")

    engine = AnalysisEngine("case_id", case_dir)
    texts = engine._load_evidence_texts(prefer_summary=True)

    by_name = {t["filename"]: t for t in texts}
    # 普通证据用 digest，不保留全文
    assert "浓缩摘要" in by_name["张某讯问笔录"]["text"]
    assert "笔录全文很长" not in by_name["张某讯问笔录"]["text"]
    # 起诉书保留全文，不用 digest
    assert "起诉书原文全文" in by_name["起诉书"]["text"]
    assert "不该被用的起诉书摘要" not in by_name["起诉书"]["text"]
