"""资金流梳理页下游消费测试（4d / 4.5 / 步骤5）"""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import analysis_pipeline
from analysis_pipeline import AnalysisPipeline

FUND_PAGE = "# 资金流梳理\n\n🗣 指控金额仅言词证据支撑，无流水印证。"


def _make_pipeline_with_fund_page(tmp_path: Path) -> AnalysisPipeline:
    case_path = tmp_path / "case_001"
    analysis_dir = case_path / "analysis"
    wiki = analysis_dir / "indictment_wiki"
    (wiki / "02-事实要素").mkdir(parents=True)
    (wiki / "03-证据分析").mkdir(parents=True)
    (wiki / "04-法律依据").mkdir(parents=True)
    (wiki / "02-事实要素" / "资金流梳理.md").write_text(FUND_PAGE, encoding="utf-8")
    (wiki / "03-证据分析" / "张三_讯问笔录.md").write_text("证据分析内容", encoding="utf-8")
    (wiki / "01-指控要素.md").write_text("指控要素内容", encoding="utf-8")
    (wiki / "06-综合结论.md").write_text("综合结论内容", encoding="utf-8")
    (wiki / "05-矛盾记录.md").write_text("矛盾记录内容", encoding="utf-8")
    (analysis_dir / "step_1_result.json").write_text(json.dumps({
        "merged_files": [{"person": "张三", "type": "讯问笔录", "session_count": 1}]
    }), encoding="utf-8")
    (analysis_dir / "step_2_result.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    return AnalysisPipeline("case_001", case_path)


def test_4d_prompt_includes_fund_flow(tmp_path, monkeypatch):
    """4d 综合结论 prompt 注入资金流梳理内容"""
    pipe = _make_pipeline_with_fund_page(tmp_path)
    # 4d 有断点续传：06-综合结论.md 已存在会跳过，删除以强制重新生成
    (tmp_path / "case_001" / "analysis" / "indictment_wiki" / "06-综合结论.md").unlink()
    calls = []

    async def fake_chat(messages, **kw):
        calls.append(messages[-1]["content"])
        return "分析结果"

    pipe.llm.chat = fake_chat
    monkeypatch.setattr(analysis_pipeline.AnalysisPipeline, "_find_indictment_in_md_files",
                        AsyncMock(return_value=("起诉书内容", "起诉书")))
    monkeypatch.setattr("case_framework.fetch_case_rules", lambda charges, keywords=None, size=3: {})
    asyncio.run(pipe.step4_build_case_wiki("张三", "诈骗罪"))

    prompt_4d = next(c for c in calls if "证据链条的完整性" in c)
    assert "资金流梳理" in prompt_4d
    assert "仅言词证据支撑" in prompt_4d


def test_step45_context_includes_fund_flow(tmp_path, monkeypatch):
    """4.5 控辩对抗的上下文包含资金流梳理"""
    pipe = _make_pipeline_with_fund_page(tmp_path)
    calls = []

    async def fake_chat(messages, **kw):
        calls.append(messages[-1]["content"])
        return "对抗结果"

    pipe.llm.chat = fake_chat
    asyncio.run(pipe.step45_debate_simulation("张三", "诈骗罪"))

    assert any("仅言词证据支撑" in c for c in calls), "4.5 各方 prompt 应包含资金流梳理内容"


def test_step5_context_includes_fund_flow(tmp_path, monkeypatch):
    """步骤 5 辩护意见的上下文中包含资金流梳理"""
    pipe = _make_pipeline_with_fund_page(tmp_path)
    calls = []

    async def fake_chat(messages, **kw):
        calls.append(messages[-1]["content"])
        return "辩护章节"

    pipe.llm.chat = fake_chat
    asyncio.run(pipe.step5_defense_opinion("张三", "诈骗罪"))

    assert any("仅言词证据支撑" in c for c in calls), "步骤 5 prompt 应包含资金流梳理内容"


def test_step5_fund_flow_survives_large_context(tmp_path, monkeypatch):
    """大案件中资金流段不被 context[:20000] 截掉（前三项截断 + 资金流前移的回归锁定）"""
    pipe = _make_pipeline_with_fund_page(tmp_path)
    wiki = tmp_path / "case_001" / "analysis" / "indictment_wiki"
    # 构造超大前置内容（超过 20000 字符的场景）
    (wiki / "01-指控要素.md").write_text("指控" * 9000, encoding="utf-8")
    (wiki / "06-综合结论.md").write_text("结论" * 9000, encoding="utf-8")
    calls = []

    async def fake_chat(messages, **kw):
        calls.append(messages[-1]["content"])
        return "辩护章节"

    pipe.llm.chat = fake_chat
    asyncio.run(pipe.step5_defense_opinion("张三", "诈骗罪"))

    assert any("仅言词证据支撑" in c for c in calls), "大案件中资金流段应存活"


def test_no_fund_page_no_injection(tmp_path, monkeypatch):
    """负路径：无资金流页时 prompt 不含资金流段（旧案件兼容）"""
    pipe = _make_pipeline_with_fund_page(tmp_path)
    # 删除资金流页
    (tmp_path / "case_001" / "analysis" / "indictment_wiki" / "02-事实要素" / "资金流梳理.md").unlink()
    calls = []

    async def fake_chat(messages, **kw):
        calls.append(messages[-1]["content"])
        return "辩护章节"

    pipe.llm.chat = fake_chat
    asyncio.run(pipe.step5_defense_opinion("张三", "诈骗罪"))

    assert not any("资金流梳理" in c for c in calls)
