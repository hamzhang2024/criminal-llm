"""分析端双层消费：prefer_summary=True 用 digest，False 用全文"""
import json

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


def test_stage2_uses_digest(tmp_path, monkeypatch):
    """阶段2人物关系：传 prefer_summary=True（digest 已含人名/角色/关系，全文没必要）"""
    import asyncio
    from analysis_engine import AnalysisEngine

    case_dir = tmp_path / "case"
    (case_dir / "evidence").mkdir(parents=True)
    (case_dir / "evidence" / "index.json").write_text(json.dumps({"evidence": [
        {"name": "张某笔录", "type": "犯罪嫌疑人供述和辩解", "md_file": "001.md",
         "digest": "浓缩摘要", "summary": ""},
    ]}, ensure_ascii=False), encoding="utf-8")
    (case_dir / "evidence" / "001.md").write_text("全文", encoding="utf-8")

    captured = {}

    class FakeEngine(AnalysisEngine):
        def _load_evidence_texts(self, prefer_summary: bool = False):
            captured["prefer_summary"] = prefer_summary
            return [{"filename": "张某笔录", "type": "犯罪嫌疑人供述和辩解", "text": "x"}]

    engine = FakeEngine("c", case_dir)

    async def fake_chat(messages, **kw):
        return '```json\n{"nodes": [], "edges": []}\n```'

    monkeypatch.setattr("llm_client.get_llm_client",
                        lambda: type("C", (), {"chat": staticmethod(fake_chat)})())
    asyncio.run(engine.stage_2_character_relations("张某"))
    assert captured.get("prefer_summary") is True


def test_doc_type_propagated_and_non_evidence_filter(tmp_path):
    """_load_evidence_texts 传播 doc_type；_is_non_evidence 识别封面/目录"""
    from analysis_engine import AnalysisEngine, _is_non_evidence

    case_dir = tmp_path / "case"
    ev_dir = case_dir / "evidence"
    ev_dir.mkdir(parents=True)
    (ev_dir / "001.md").write_text("x", encoding="utf-8")
    (ev_dir / "index.json").write_text(json.dumps({"evidence": [
        {"name": "卷宗封面", "type": "程序性文书", "md_file": "001.md",
         "doc_type": "non_evidence:封面"},
        {"name": "张某笔录", "type": "犯罪嫌疑人供述和辩解", "md_file": "001.md",
         "doc_type": "evidence"},
    ]}, ensure_ascii=False), encoding="utf-8")

    engine = AnalysisEngine("c", case_dir)
    texts = engine._load_evidence_texts()
    by_name = {t["filename"]: t for t in texts}
    assert by_name["卷宗封面"]["doc_type"] == "non_evidence:封面"
    assert _is_non_evidence(by_name["卷宗封面"]) is True
    assert _is_non_evidence(by_name["张某笔录"]) is False


def test_ensure_digests_triggers_only_when_missing(tmp_path, monkeypatch):
    """digest 缺失的旧证据自动补摘要；齐全时不触发"""
    import asyncio
    from analysis_engine import AnalysisEngine

    case_dir = tmp_path / "case"
    ev_dir = case_dir / "evidence"
    ev_dir.mkdir(parents=True)

    called = []

    async def fake_summarize(client, case_dir, concurrency=3, should_abort=None):
        called.append(True)
        return {"total": 1, "done": 1, "cached": 0, "skipped": 0, "failed": 0, "aborted": False}

    monkeypatch.setattr("evidence_summarizer.summarize_evidence", fake_summarize)
    monkeypatch.setattr("llm_client.get_llm_client", lambda: None)
    engine = AnalysisEngine("c", case_dir)

    # 缺 digest → 触发
    (ev_dir / "index.json").write_text(json.dumps({"evidence": [
        {"name": "张某笔录", "type": "犯罪嫌疑人供述和辩解", "md_file": "001.md"},
    ]}, ensure_ascii=False), encoding="utf-8")
    asyncio.run(engine._ensure_digests())
    assert len(called) == 1

    # 有 digest → 不触发
    called.clear()
    (ev_dir / "index.json").write_text(json.dumps({"evidence": [
        {"name": "张某笔录", "type": "犯罪嫌疑人供述和辩解", "md_file": "001.md", "digest": "有"},
    ]}, ensure_ascii=False), encoding="utf-8")
    asyncio.run(engine._ensure_digests())
    assert len(called) == 0

    # 起诉书无 digest → 不触发（本来就不用摘要）
    called.clear()
    (ev_dir / "index.json").write_text(json.dumps({"evidence": [
        {"name": "起诉书", "type": "起诉书", "md_file": "002.md"},
    ]}, ensure_ascii=False), encoding="utf-8")
    asyncio.run(engine._ensure_digests())
    assert len(called) == 0
