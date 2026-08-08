"""步骤 4e 资金流梳理子步骤测试"""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import analysis_pipeline
from analysis_pipeline import AnalysisPipeline


def _make_pipeline(tmp_path: Path, with_fund: bool = True) -> AnalysisPipeline:
    case_path = tmp_path / "case_001"
    analysis_dir = case_path / "analysis"
    (analysis_dir / "summaries" / "讯问笔录").mkdir(parents=True)
    (analysis_dir / "preprocess").mkdir(parents=True)
    (analysis_dir / "step_1_result.json").write_text(json.dumps({
        "merged_files": [{"person": "张三", "type": "讯问笔录", "session_count": 1}]
    }), encoding="utf-8")
    (analysis_dir / "step_2_result.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (analysis_dir / "summaries" / "讯问笔录" / "张三_共1次_总结.md").write_text(
        "张三供述：收到转账。", encoding="utf-8")

    # 证据目录：一份流水（含资金内容）+ 一份无关证据
    evidence_dir = case_path / "evidence"
    evidence_dir.mkdir(parents=True)
    fund_text = "银行账户交易明细\n\n2024年3月15日 收到李某转账 50000元\n\n余额 80000元" if with_fund else "普通谈话记录，仅涉及日常起居事项。"
    (evidence_dir / "001_流水.md").write_text(fund_text, encoding="utf-8")
    (evidence_dir / "002_其他.md").write_text("与案件无关的日常记录。", encoding="utf-8")
    (evidence_dir / "index.json").write_text(json.dumps({
        "evidence": [
            {"name": "银行账户交易明细", "md_file": "001_流水.md", "type": "书证"},
            {"name": "其他材料", "md_file": "002_其他.md", "type": "书证"},
        ]
    }), encoding="utf-8")
    return AnalysisPipeline("case_001", case_path)


def _patch_common(monkeypatch):
    monkeypatch.setattr(analysis_pipeline.AnalysisPipeline, "_find_indictment_in_md_files",
                        AsyncMock(return_value=("起诉书：指控张三诈骗，涉案金额50000元。", "起诉书")))
    monkeypatch.setattr("case_framework.fetch_case_rules", lambda charges, keywords=None, size=3: {})


def test_collect_fund_evidence_filters(tmp_path, monkeypatch):
    """资金段落抽取：只保留含资金关键词的段落，无关证据被过滤"""
    _patch_common(monkeypatch)
    pipe = _make_pipeline(tmp_path)
    result = pipe._collect_fund_evidence(max_chars=10000)
    assert "50000元" in result
    assert "001_流水.md" in result or "银行账户交易明细" in result
    assert "与案件无关的日常记录" not in result


def test_collect_fund_evidence_budget_cap(tmp_path, monkeypatch):
    """预算上限：超出 max_chars 的内容被截断"""
    _patch_common(monkeypatch)
    pipe = _make_pipeline(tmp_path)
    result = pipe._collect_fund_evidence(max_chars=50)
    assert len(result) <= 200  # 允许单块超出少量，但绝不能全量装入


def test_step4e_creates_fund_flow_page(tmp_path, monkeypatch):
    """4e 在 4b 之后、4d 之前执行；产出 02-事实要素/资金流梳理.md；prompt 含来源类型与四档结论要求"""
    _patch_common(monkeypatch)
    pipe = _make_pipeline(tmp_path)
    calls = []

    async def fake_chat(messages, **kw):
        calls.append(messages[-1]["content"])
        return "分析结果"

    pipe.llm.chat = fake_chat
    asyncio.run(pipe.step4_build_case_wiki("张三", "诈骗罪"))

    wiki = tmp_path / "case_001" / "analysis" / "indictment_wiki" / "02-事实要素"
    assert (wiki / "资金流梳理.md").exists()

    idx_4b = next(i for i, c in enumerate(calls) if "待分析证据" in c)
    idx_4e = next(i for i, c in enumerate(calls) if "资金流" in c)
    idx_4d = next(i for i, c in enumerate(calls) if "证据链条的完整性" in c)
    assert idx_4b < idx_4e < idx_4d, "4e 必须位于 4b 之后、4d 之前"

    prompt_4e = calls[idx_4e]
    assert "来源类型" in prompt_4e
    assert "客观证据印证" in prompt_4e
    assert "仅言词证据" in prompt_4e
    assert "50000元" in prompt_4e  # 证据中的资金内容进入 prompt


def test_step4e_no_fund_evidence_degrades(tmp_path, monkeypatch):
    """无资金类证据：输出说明页并标记完成，不调用 LLM，不阻塞流水线"""
    _patch_common(monkeypatch)
    pipe = _make_pipeline(tmp_path, with_fund=False)
    calls = []

    async def fake_chat(messages, **kw):
        calls.append(messages[-1]["content"])
        return "分析结果"

    pipe.llm.chat = fake_chat
    asyncio.run(pipe.step4_build_case_wiki("张三", "诈骗罪"))

    wiki = tmp_path / "case_001" / "analysis" / "indictment_wiki" / "02-事实要素"
    page = (wiki / "资金流梳理.md").read_text(encoding="utf-8")
    assert "未检测到资金类内容" in page
    # 用 4e 专属标记断言（下游 4d 注入资金流页属正常行为，不能笼统断言"资金流"字样）
    assert not any("逐笔对照" in c or "只做资金流重建" in c for c in calls), \
        "无资金证据时不应调用 LLM 做资金流分析"


def test_step4e_no_indictment_rebuild_only(tmp_path, monkeypatch):
    """无起诉书：只做资金流重建，prompt 中说明跳过对照验证"""
    monkeypatch.setattr(analysis_pipeline.AnalysisPipeline, "_find_indictment_in_md_files",
                        AsyncMock(return_value=("", "")))
    monkeypatch.setattr("case_framework.fetch_case_rules", lambda charges, keywords=None, size=3: {})
    pipe = _make_pipeline(tmp_path)
    calls = []

    async def fake_chat(messages, **kw):
        calls.append(messages[-1]["content"])
        return "分析结果"

    pipe.llm.chat = fake_chat
    asyncio.run(pipe.step4_build_case_wiki("张三", "诈骗罪"))

    prompt_4e = next(c for c in calls if "资金流" in c)
    assert "只做资金流重建" in prompt_4e
    wiki = tmp_path / "case_001" / "analysis" / "indictment_wiki" / "02-事实要素"
    assert (wiki / "资金流梳理.md").exists()


def test_step4e_resume_skips(tmp_path, monkeypatch):
    """断点续传：资金流梳理.md 已存在时跳过 4e"""
    _patch_common(monkeypatch)
    pipe = _make_pipeline(tmp_path)
    wiki = tmp_path / "case_001" / "analysis" / "indictment_wiki" / "02-事实要素"
    wiki.mkdir(parents=True)
    (wiki / "资金流梳理.md").write_text("已有分析", encoding="utf-8")
    calls = []

    async def fake_chat(messages, **kw):
        calls.append(messages[-1]["content"])
        return "分析结果"

    pipe.llm.chat = fake_chat
    asyncio.run(pipe.step4_build_case_wiki("张三", "诈骗罪"))
    assert not any("只做资金流重建" in c or "逐笔对照" in c for c in calls)
