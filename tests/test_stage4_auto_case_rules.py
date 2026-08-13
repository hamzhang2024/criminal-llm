"""stage_4 自动检索真实类案注入测试（律师需要真实案号提交类案检索报告）"""
import asyncio
import json
from pathlib import Path

import analysis_engine
from analysis_engine import AnalysisEngine

FAKE_RULES_MD = "# 类案裁判规则\n\n## 【第1020号】王新明合同诈骗案\n\n- 裁判要旨：数额犯既遂未遂并存时择一重处"


def _make_engine(tmp_path: Path, charges=None, keywords=None) -> AnalysisEngine:
    case_path = tmp_path / "case_001"
    analysis_dir = case_path / "analysis"
    analysis_dir.mkdir(parents=True)
    if charges is not None:
        meta = {"charges": charges}
        if keywords:
            meta["suggested_keywords"] = keywords
        (case_path / "case.json").write_text(json.dumps(meta), encoding="utf-8")
    return AnalysisEngine("case_001", case_path)


def _patch_common(monkeypatch, engine):
    monkeypatch.setattr(engine, "_load_evidence_texts", lambda: [
        {"filename": "001_笔录.md", "text": "被告人供述内容", "type": "讯问笔录"}
    ])
    monkeypatch.setattr(analysis_engine, "get_legal_knowledge", lambda: {"articles": "", "interpretations": "", "cases": ""})
    monkeypatch.setattr(analysis_engine, "get_dynamic_legal_knowledge", lambda c: "")


def _fake_client(captured):
    async def fake_chat(messages, **kw):
        captured["system"] = messages[0]["content"]
        captured["user"] = messages[-1]["content"]
        return "法律梳理结果"
    return type("C", (), {"chat": staticmethod(fake_chat)})()


def test_auto_fetch_injects_real_case_numbers(tmp_path, monkeypatch):
    """无 reference_cases 时自动检索：system prompt 含真实案号与引用要求"""
    engine = _make_engine(tmp_path, charges=["合同诈骗罪"], keywords=["数额犯"])
    _patch_common(monkeypatch, engine)
    captured = {}
    monkeypatch.setattr("case_framework.fetch_case_rules",
                        lambda charges, keywords=None, size=3: {"合同诈骗罪": FAKE_RULES_MD})
    monkeypatch.setattr("llm_client.get_llm_client", lambda: _fake_client(captured))
    asyncio.run(engine.stage_4_legal_regulations("张三", "合同诈骗罪"))

    assert "第1020号" in captured["system"], "真实案号应注入 system prompt"
    assert "王新明合同诈骗案" in captured["system"]
    assert "【案号】案例名" in captured["system"], "应含引用格式要求"
    assert "严禁虚构" in captured["user"], "user prompt 仍保留防虚构约束"


def test_fetch_empty_keeps_strict_branch(tmp_path, monkeypatch):
    """检索无结果：保持严禁虚构分支，不注入案号"""
    engine = _make_engine(tmp_path, charges=["合同诈骗罪"])
    _patch_common(monkeypatch, engine)
    captured = {}
    monkeypatch.setattr("case_framework.fetch_case_rules", lambda charges, keywords=None, size=3: {})
    monkeypatch.setattr("llm_client.get_llm_client", lambda: _fake_client(captured))
    asyncio.run(engine.stage_4_legal_regulations("张三", "合同诈骗罪"))

    assert "第1020号" not in captured["system"]
    assert "严禁虚构" in captured["user"]


def test_fetch_exception_degrades_silently(tmp_path, monkeypatch):
    """检索异常（服务不可达）：静默降级，stage_4 正常完成"""
    engine = _make_engine(tmp_path, charges=["合同诈骗罪"])
    _patch_common(monkeypatch, engine)

    def _boom(charges, keywords=None, size=3):
        raise ConnectionError("服务不可达")

    monkeypatch.setattr("case_framework.fetch_case_rules", _boom)
    monkeypatch.setattr("llm_client.get_llm_client", lambda: _fake_client({}))
    result = asyncio.run(engine.stage_4_legal_regulations("张三", "合同诈骗罪"))
    assert result["stage"] == 4


def test_reference_cases_take_priority(tmp_path, monkeypatch):
    """用户手动勾选的参考案例优先于自动检索（不发起检索）"""
    engine = _make_engine(tmp_path, charges=["合同诈骗罪"])
    _patch_common(monkeypatch, engine)
    captured = {}
    fetch_called = {"n": 0}

    def _track(charges, keywords=None, size=3):
        fetch_called["n"] += 1
        return {"合同诈骗罪": FAKE_RULES_MD}

    monkeypatch.setattr("case_framework.fetch_case_rules", _track)
    monkeypatch.setattr("llm_client.get_llm_client", lambda: _fake_client(captured))
    manual = [{"case_no": "第999号", "title": "手动案例", "holding_summary": "要旨", "reasoning_excerpt": "理由", "issue": "问题"}]
    asyncio.run(engine.stage_4_legal_regulations("张三", "合同诈骗罪", reference_cases=manual))

    assert fetch_called["n"] == 0, "手动勾选时不应自动检索"
