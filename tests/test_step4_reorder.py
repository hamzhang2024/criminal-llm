"""改动 A：步骤 4 顺序 4a→4c→4b→4d + 4c 类案扩展 + 4b 注入法律框架 + LLM 推荐关键词"""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import analysis_pipeline
from analysis_pipeline import AnalysisPipeline


def _make_pipeline(tmp_path: Path) -> AnalysisPipeline:
    case_path = tmp_path / "case_001"
    analysis_dir = case_path / "analysis"
    (analysis_dir / "summaries" / "讯问笔录").mkdir(parents=True)
    (analysis_dir / "preprocess").mkdir(parents=True)
    (analysis_dir / "step_1_result.json").write_text(json.dumps({
        "merged_files": [{"person": "张三", "type": "讯问笔录", "session_count": 1}]
    }), encoding="utf-8")
    (analysis_dir / "step_2_result.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (analysis_dir / "summaries" / "讯问笔录" / "张三_共1次_总结.md").write_text(
        "张三供述：盗窃财物若干。", encoding="utf-8")
    return AnalysisPipeline("case_001", case_path)


def test_step4_order_4c_before_4b(tmp_path, monkeypatch):
    """4c 的 LLM 调用必须先于 4b；4b prompt 含法律框架；类案按罪名存盘"""
    pipe = _make_pipeline(tmp_path)
    calls = []

    async def fake_chat(messages, **kw):
        calls.append(messages[-1]["content"])
        return "分析结果"

    pipe.llm.chat = fake_chat
    monkeypatch.setattr(analysis_pipeline.AnalysisPipeline, "_find_indictment_in_md_files",
                        AsyncMock(return_value=("起诉书内容：指控张三盗窃。", "起诉书")))
    monkeypatch.setattr("case_framework.fetch_case_rules",
                        lambda charges, keywords=None, size=3: {"盗窃罪": "# 类案裁判规则\n\n盗窃裁判规则内容"})

    asyncio.run(pipe.step4_build_case_wiki("张三", "盗窃罪"))

    idx_4c = next(i for i, c in enumerate(calls) if "从刑法知识库检索到的法条" in c)
    idx_4b = next(i for i, c in enumerate(calls) if "待分析证据" in c)
    assert idx_4c < idx_4b, "4c 必须先于 4b"

    prompt_4b = calls[idx_4b]
    assert "法律框架" in prompt_4b
    assert "盗窃裁判规则内容" in prompt_4b

    wiki = tmp_path / "case_001" / "analysis" / "indictment_wiki" / "04-法律依据"
    assert (wiki / "类案裁判规则-盗窃罪.md").exists()


def test_step4_case_rules_failure_degrades(tmp_path, monkeypatch):
    """类案检索失败：静默跳过，法条路径照常"""
    pipe = _make_pipeline(tmp_path)

    async def fake_chat(messages, **kw):
        return "分析结果"

    pipe.llm.chat = fake_chat
    monkeypatch.setattr(analysis_pipeline.AnalysisPipeline, "_find_indictment_in_md_files",
                        AsyncMock(return_value=("起诉书内容", "起诉书")))
    monkeypatch.setattr("case_framework.fetch_case_rules", lambda charges, keywords=None, size=3: {})

    asyncio.run(pipe.step4_build_case_wiki("张三", "盗窃罪"))
    wiki = tmp_path / "case_001" / "analysis" / "indictment_wiki" / "04-法律依据"
    assert (wiki / "适用法条.md").exists()
    assert not list(wiki.glob("类案裁判规则-*.md"))


def test_suggested_keywords_saved_and_used(tmp_path, monkeypatch):
    """4a 后 LLM 推荐关键词存 case.json；4c 检索使用有效关键词"""
    pipe = _make_pipeline(tmp_path)
    captured = {}

    async def fake_chat(messages, **kw):
        if "类案检索关键词" in messages[0]["content"]:
            return "未成年人\n轻微暴力\n多次作案"
        return "分析结果"

    pipe.llm.chat = fake_chat
    monkeypatch.setattr(analysis_pipeline.AnalysisPipeline, "_find_indictment_in_md_files",
                        AsyncMock(return_value=("起诉书内容：指控张三盗窃。", "起诉书")))

    def fake_rules(charges, keywords=None, size=3):
        captured["keywords"] = keywords
        return {}

    monkeypatch.setattr("case_framework.fetch_case_rules", fake_rules)
    asyncio.run(pipe.step4_build_case_wiki("张三", "盗窃罪"))

    meta = json.loads((tmp_path / "case_001" / "case.json").read_text(encoding="utf-8"))
    assert meta["suggested_keywords"] == ["未成年人", "轻微暴力", "多次作案"]
    assert captured["keywords"] == ["未成年人", "轻微暴力", "多次作案"]


def test_user_keywords_override_suggested(tmp_path, monkeypatch):
    """用户编辑的 search_keywords 优先于 LLM 推荐"""
    pipe = _make_pipeline(tmp_path)
    (tmp_path / "case_001" / "case.json").write_text(json.dumps({
        "search_keywords": ["入户", "未遂"]
    }), encoding="utf-8")
    captured = {}

    async def fake_chat(messages, **kw):
        if "类案检索关键词" in messages[0]["content"]:
            return "推荐词一\n推荐词二"
        return "分析结果"

    pipe.llm.chat = fake_chat
    monkeypatch.setattr(analysis_pipeline.AnalysisPipeline, "_find_indictment_in_md_files",
                        AsyncMock(return_value=("起诉书内容", "起诉书")))

    def fake_rules(charges, keywords=None, size=3):
        captured["keywords"] = keywords
        return {}

    monkeypatch.setattr("case_framework.fetch_case_rules", fake_rules)
    asyncio.run(pipe.step4_build_case_wiki("张三", "盗窃罪"))
    assert captured["keywords"] == ["入户", "未遂"]
