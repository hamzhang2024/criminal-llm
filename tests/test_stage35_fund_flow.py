"""stage_35 资金流梳理阶段测试（stage 引擎接入 + 双源抽取 + stage_5 注入）"""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import analysis_engine
from analysis_engine import AnalysisEngine


def _make_engine(tmp_path: Path, with_fund: bool = True, with_indictment: bool = True) -> AnalysisEngine:
    case_path = tmp_path / "case_001"
    analysis_dir = case_path / "analysis"
    analysis_dir.mkdir(parents=True)
    evidence_dir = case_path / "evidence"
    evidence_dir.mkdir(parents=True)
    md_dir = case_path / "md"
    md_dir.mkdir(parents=True)

    # 证据摘要（蒸馏层）：不含资金内容
    (evidence_dir / "001_笔录.md").write_text("被告人供述概要。", encoding="utf-8")
    (evidence_dir / "index.json").write_text(json.dumps({
        "evidence": [{"name": "001_笔录", "md_file": "001_笔录.md", "type": "讯问笔录"}]
    }), encoding="utf-8")

    # 原始 MD（全量层）：含资金内容（如截图 OCR 出的流水）
    if with_fund:
        (md_dir / "第3卷.md").write_text(
            "卷宗正文\n\n转账记录截图识别文字：2024年5月22日 汇款 50000元 收款人李某\n\n其他内容",
            encoding="utf-8")

    # 阶段 1 产物（指控要素）
    if with_indictment:
        stage1 = analysis_dir / "stage_1"
        stage1.mkdir(parents=True)
        (stage1 / "output.md").write_text("指控要素：涉案金额50000元", encoding="utf-8")

    return AnalysisEngine("case_001", case_path)


def _fake_client(calls, response="资金流梳理结果"):
    async def fake_chat(messages, **kw):
        calls.append(messages)
        return response
    return type("C", (), {"chat": staticmethod(fake_chat)})()


def test_stage35_with_indictment_compares(tmp_path, monkeypatch):
    """有起诉书：prompt 含四档对照要求，产物保存到 stage_35"""
    engine = _make_engine(tmp_path)
    calls = []
    monkeypatch.setattr("llm_client.get_llm_client", lambda: _fake_client(calls))
    asyncio.run(engine.stage_35_fund_flow("张三", "非法经营罪"))

    assert len(calls) == 1
    prompt = calls[0][-1]["content"]
    assert "客观证据印证" in prompt and "仅言词证据" in prompt
    assert "涉案金额50000元" in prompt  # 阶段 1 指控要素注入
    assert "50000元" in prompt  # 证据资金内容注入
    out = tmp_path / "case_001" / "analysis" / "stage_35" / "output.md"
    assert out.read_text(encoding="utf-8") == "资金流梳理结果"


def test_stage35_dual_source_catches_raw_md(tmp_path, monkeypatch):
    """双源抽取：资金内容只在原始 md/ 中（证据摘要未提取）也能命中"""
    engine = _make_engine(tmp_path)
    calls = []
    monkeypatch.setattr("llm_client.get_llm_client", lambda: _fake_client(calls))
    asyncio.run(engine.stage_35_fund_flow("张三", "非法经营罪"))

    prompt = calls[0][-1]["content"]
    assert "第3卷.md" in prompt, "原始 MD 全文应被扫描"
    assert "汇款 50000元" in prompt


def test_stage35_no_fund_evidence_degrades(tmp_path, monkeypatch):
    """无资金证据：输出说明页，不调 LLM"""
    engine = _make_engine(tmp_path, with_fund=False)
    calls = []
    monkeypatch.setattr("llm_client.get_llm_client", lambda: _fake_client(calls))
    result = asyncio.run(engine.stage_35_fund_flow("张三", "非法经营罪"))

    assert len(calls) == 0
    out = tmp_path / "case_001" / "analysis" / "stage_35" / "output.md"
    assert "未检测到资金类内容" in out.read_text(encoding="utf-8")
    assert result["stage"] == 35


def test_stage35_no_indictment_rebuild_only(tmp_path, monkeypatch):
    """无阶段 1 产物（无起诉书）：只做资金流重建"""
    engine = _make_engine(tmp_path, with_indictment=False)
    calls = []
    monkeypatch.setattr("llm_client.get_llm_client", lambda: _fake_client(calls))
    asyncio.run(engine.stage_35_fund_flow("张三", "非法经营罪"))

    prompt = calls[0][-1]["content"]
    assert "只做资金流重建" in prompt


def test_stage5_injects_fund_flow_and_strategy(tmp_path, monkeypatch):
    """stage_5 prompt 注入资金流梳理与律师确认的辩护思路"""
    engine = _make_engine(tmp_path)
    analysis_dir = tmp_path / "case_001" / "analysis"
    # 补齐 stage_5 前置产物
    (analysis_dir / "stage_35").mkdir(parents=True)
    (analysis_dir / "stage_35" / "output.md").write_text("资金流梳理：仅言词证据支撑", encoding="utf-8")
    strategy_dir = analysis_dir / "04.75-辩护思路"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "思路确认.md").write_text("律师确认思路：主打证据不足", encoding="utf-8")

    # 5B 子阶段打桩，避免展开
    async def fake_5b(defendant, progress_cb=None):
        return "矛盾分析内容"
    monkeypatch.setattr(engine, "stage_5b_contradiction_analysis", fake_5b)

    calls = []
    monkeypatch.setattr("llm_client.get_llm_client", lambda: _fake_client(calls, "三阶层报告"))
    monkeypatch.setattr(analysis_engine, "get_legal_knowledge", lambda: {"articles": "", "interpretations": "", "cases": ""})
    monkeypatch.setattr(analysis_engine, "get_dynamic_legal_knowledge", lambda c: "")
    asyncio.run(engine.stage_5_full_defense("张三", "非法经营罪"))

    defense_prompt = calls[-1][-1]["content"]
    assert "资金流梳理：仅言词证据支撑" in defense_prompt
    assert defense_prompt.startswith("辩护思路（律师已确认"), "思路应置于 prompt 最前"
    assert "主打证据不足" in defense_prompt


def test_stage35_structured_fund_flows_path(tmp_path, monkeypatch):
    """三层分离：有 fund_flows 时走确定性主表路径，LLM 只做分析，输出经机器对账"""
    case_path = tmp_path / "case_001"
    analysis_dir = case_path / "analysis"
    analysis_dir.mkdir(parents=True)
    evidence_dir = case_path / "evidence"
    evidence_dir.mkdir(parents=True)

    # 证据含结构化 fund_flows（新提取产物）
    (evidence_dir / "001_流水.md").write_text("流水全文", encoding="utf-8")
    (evidence_dir / "index.json").write_text(json.dumps({"evidence": [
        {"name": "银行流水", "md_file": "001_流水.md", "type": "书证",
         "fund_flows": ["唐雨父母→程敏洁｜15万元｜2025-04-13｜银行｜结清欠款"]},
        {"name": "赵志强笔录", "md_file": "002_笔录.md", "type": "犯罪嫌疑人供述和辩解",
         "fund_flows": ["唐雨父母→程敏洁｜150000元｜2025年4月13日｜银行转账｜结清"]},
    ]}, ensure_ascii=False), encoding="utf-8")
    (evidence_dir / "002_笔录.md").write_text("笔录全文", encoding="utf-8")

    # 阶段 1 指控要素
    stage1 = analysis_dir / "stage_1"
    stage1.mkdir(parents=True)
    (stage1 / "output.md").write_text("指控要素：诈骗金额 7.93万元", encoding="utf-8")

    engine = AnalysisEngine("case_001", case_path)
    calls = []
    # LLM 输出引用了一个主表/起诉书都不存在的金额 → 校验层应标记
    monkeypatch.setattr("llm_client.get_llm_client",
                        lambda: _fake_client(calls, "流水显示 150000 元，另发现 99999 元异常。"))
    asyncio.run(engine.stage_35_fund_flow("赵志强", "诈骗罪"))

    assert len(calls) == 1
    prompt = calls[0][-1]["content"]
    # 主表注入（确定性聚合：两条 fund_flows 同笔合并，金额规范化一致）
    assert "资金往来主表" in prompt
    assert "150000" in prompt  # 规范化后金额
    assert "7.93万" in prompt or "79300" in prompt  # 指控要素
    # 输出落盘 + 机器对账标记了无来源金额
    out = (analysis_dir / "stage_35" / "output.md").read_text(encoding="utf-8")
    assert "99999" in out and "待人工核对" in out
    assert "150000" in out


def test_stage35_fallback_without_fund_flows(tmp_path, monkeypatch):
    """无 fund_flows（旧案件）→ 回退关键词抽段旧路径"""
    engine = _make_engine(tmp_path)  # 该构造的 index.json 无 fund_flows
    calls = []
    monkeypatch.setattr("llm_client.get_llm_client", lambda: _fake_client(calls))
    asyncio.run(engine.stage_35_fund_flow("张三", "非法经营罪"))
    assert len(calls) == 1
    prompt = calls[0][-1]["content"]
    # 旧路径：从 md 全文抽资金段落
    assert "50000元" in prompt and "证据中的资金相关内容" in prompt
