"""辩护思路重新确认后步骤 5 产物失效测试"""
import asyncio
import json
from pathlib import Path

from analysis_pipeline import AnalysisPipeline


def _make_pipeline(tmp_path: Path, with_step5_outputs: bool = True) -> AnalysisPipeline:
    case_path = tmp_path / "case_001"
    analysis_dir = case_path / "analysis"
    strategy_dir = analysis_dir / "04.75-辩护思路"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "系统建议.json").write_text(json.dumps({
        "directions": [{"type": "无罪辩护", "direction": "事实不清证据不足", "basis": "流水缺失", "risk": "低"}]
    }), encoding="utf-8")

    if with_step5_outputs:
        # 模拟步骤 5 已跑过：章节文件 + 步骤结果 + 状态
        defense_dir = analysis_dir / "05-辩护意见"
        defense_dir.mkdir(parents=True)
        for fn in ["01-案件概述.md", "02-证据评估.md", "03-矛盾利用.md",
                   "04-三阶层辩护.md", "05-量刑情节.md", "06-结论建议.md"]:
            (defense_dir / fn).write_text(f"旧内容-{fn}", encoding="utf-8")
        (analysis_dir / "step_5_result.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
        (analysis_dir / "辩护分析报告_张三.md").write_text("旧报告", encoding="utf-8")
        # 模拟 stage 引擎产物：stage_5/53 + 完整报告（含罪名层）
        for d in ["stage_5", "stage_53"]:
            sd = analysis_dir / d
            sd.mkdir(parents=True, exist_ok=True)
            (sd / "output.md").write_text(f"旧{d}", encoding="utf-8")
            (sd / "output.json").write_text("{}", encoding="utf-8")
        charge_dir = analysis_dir / "诈骗罪" / "stage_5"
        charge_dir.mkdir(parents=True)
        (charge_dir / "output.md").write_text("罪名层旧报告", encoding="utf-8")
        (analysis_dir / "full_defense_report.md").write_text("旧完整报告", encoding="utf-8")
        state = {"steps": {"4.75": {"status": "awaiting_confirmation"},
                           "5": {"status": "completed", "sub_steps": {"5a": "done"}}}}
        (analysis_dir / "analysis_state.json").write_text(json.dumps(state), encoding="utf-8")
    else:
        state = {"steps": {"4.75": {"status": "awaiting_confirmation"}}}
        (analysis_dir / "analysis_state.json").write_text(json.dumps(state), encoding="utf-8")

    return AnalysisPipeline("case_001", case_path)


def test_confirm_invalidates_step5_outputs(tmp_path):
    """确认思路后：步骤 5 旧章节/结果被清除，状态重置为未完成"""
    pipe = _make_pipeline(tmp_path, with_step5_outputs=True)
    asyncio.run(pipe.confirm_defense_strategy(use_system_default=True))

    analysis_dir = tmp_path / "case_001" / "analysis"
    assert (analysis_dir / "04.75-辩护思路" / "思路确认.md").exists(), "确认稿应写入"
    defense_dir = analysis_dir / "05-辩护意见"
    remaining = list(defense_dir.glob("*.md")) if defense_dir.exists() else []
    assert remaining == [], f"旧章节应被清除，实际残留: {remaining}"
    assert not (analysis_dir / "step_5_result.json").exists(), "step_5_result.json 应被清除"
    assert not (analysis_dir / "辩护分析报告_张三.md").exists(), "旧汇总报告应被清除"
    # stage 引擎产物也应失效（含罪名层）
    assert not (analysis_dir / "stage_5").exists(), "stage_5 产物应被清除"
    assert not (analysis_dir / "stage_53").exists(), "stage_53 产物应被清除"
    assert not (analysis_dir / "诈骗罪" / "stage_5").exists(), "罪名层 stage_5 应被清除"
    assert not (analysis_dir / "full_defense_report.md").exists(), "stage 完整报告应被清除"
    state = json.loads((analysis_dir / "analysis_state.json").read_text(encoding="utf-8"))
    assert state["steps"]["5"]["status"] != "completed", "步骤 5 状态应重置"


def test_first_confirm_no_step5_outputs(tmp_path):
    """首次确认（步骤 5 未跑过）：正常完成，不报错"""
    pipe = _make_pipeline(tmp_path, with_step5_outputs=False)
    result = asyncio.run(pipe.confirm_defense_strategy(use_system_default=True))
    assert result["success"] is True
    analysis_dir = tmp_path / "case_001" / "analysis"
    assert (analysis_dir / "04.75-辩护思路" / "思路确认.md").exists()


def test_rerun_step5_after_confirm_regenerates(tmp_path):
    """确认后重跑步骤 5：LLM 被调用且 prompt 含确认的思路（不再全部跳过）"""
    pipe = _make_pipeline(tmp_path, with_step5_outputs=True)
    asyncio.run(pipe.confirm_defense_strategy(use_system_default=True))

    # 补齐步骤 5 运行所需的前置 wiki 材料
    wiki = tmp_path / "case_001" / "analysis" / "indictment_wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    (wiki / "01-指控要素.md").write_text("指控要素内容", encoding="utf-8")
    (tmp_path / "case_001" / "analysis" / "step_1_result.json").write_text(json.dumps({
        "merged_files": [{"person": "张三", "type": "讯问笔录", "session_count": 1}]
    }), encoding="utf-8")

    calls = []

    async def fake_chat(messages, **kw):
        calls.append(messages[-1]["content"])
        return "辩护章节"

    pipe.llm.chat = fake_chat
    asyncio.run(pipe.step5_defense_opinion("张三", "诈骗罪"))

    assert len(calls) >= 6, f"5a-5f 应全部重新生成，实际 LLM 调用 {len(calls)} 次"
    assert any("事实不清证据不足" in c for c in calls), "确认的思路应注入 prompt"
