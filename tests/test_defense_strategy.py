"""改动 B：4.75 辩护思路确认卡点"""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import analysis_pipeline
from analysis_pipeline import AnalysisPipeline


def _make_pipeline(tmp_path: Path) -> AnalysisPipeline:
    """构造带 Wiki 与控辩对抗产物的 pipeline"""
    case_path = tmp_path / "case_001"
    analysis_dir = case_path / "analysis"
    wiki = analysis_dir / "indictment_wiki"
    wiki.mkdir(parents=True)
    (wiki / "06-综合结论.md").write_text("综合结论：证据链存在重大缺口", encoding="utf-8")
    (wiki / "05-矛盾记录.md").write_text("矛盾：供述前后不一", encoding="utf-8")
    debate = analysis_dir / "04.5-控辩对抗"
    debate.mkdir(parents=True)
    (debate / "对抗分析.md").write_text("法官裁决：自首认定存疑", encoding="utf-8")
    # step1/2 产物（step5 前置检查用）
    (analysis_dir / "step_1_result.json").write_text(json.dumps({"merged_files": []}), encoding="utf-8")
    return AnalysisPipeline("case_001", case_path)


def test_generates_suggestion_and_awaits_confirmation(tmp_path):
    """生成结构化建议 + 状态置为待确认（非 completed）"""
    pipe = _make_pipeline(tmp_path)
    pipe.llm.chat = AsyncMock(return_value=json.dumps({
        "directions": [
            {"type": "主攻", "direction": "证据链断裂，事实不清证据不足", "basis": "供述矛盾且无物证印证", "risk": "法院可能采纳补强证据"},
            {"type": "备选", "direction": "自首情节", "basis": "自动投案", "risk": "供述不完整"},
        ]
    }, ensure_ascii=False))

    result = asyncio.run(pipe.step475_defense_strategy("张三", "盗窃罪"))

    assert result["awaiting_confirmation"] is True
    assert len(result["suggestion"]["directions"]) == 2
    # 系统建议落盘（JSON + MD 各一份）
    d = tmp_path / "case_001" / "analysis" / "04.75-辩护思路"
    assert (d / "系统建议.json").exists()
    assert (d / "系统建议.md").exists()
    assert "证据链断裂" in (d / "系统建议.md").read_text(encoding="utf-8")
    # 状态为待确认而非完成
    state = json.loads((tmp_path / "case_001" / "analysis" / "analysis_state.json").read_text(encoding="utf-8"))
    assert state["steps"]["4.75"]["status"] == "awaiting_confirmation"


def test_resume_skips_regeneration_when_awaiting(tmp_path):
    """待确认状态下重跑 4.75：直接返回已有建议，不重复调 LLM"""
    pipe = _make_pipeline(tmp_path)
    pipe.llm.chat = AsyncMock(return_value=json.dumps({"directions": [
        {"type": "主攻", "direction": "无罪辩护", "basis": "b", "risk": "r"}]}, ensure_ascii=False))
    asyncio.run(pipe.step475_defense_strategy("张三", "盗窃罪"))

    pipe.llm.chat = AsyncMock(side_effect=AssertionError("不应再调 LLM"))
    result = asyncio.run(pipe.step475_defense_strategy("张三", "盗窃罪"))
    assert result["awaiting_confirmation"] is True
    assert result["suggestion"]["directions"][0]["direction"] == "无罪辩护"


def test_confirm_writes_confirmation_and_marks_done(tmp_path):
    """确认：写思路确认.md（含用户补充与修改痕迹）+ 状态 completed"""
    pipe = _make_pipeline(tmp_path)
    pipe.llm.chat = AsyncMock(return_value=json.dumps({"directions": [
        {"type": "主攻", "direction": "无罪辩护", "basis": "b1", "risk": "r1"},
        {"type": "备选", "direction": "罪轻辩护", "basis": "b2", "risk": "r2"},
    ]}, ensure_ascii=False))
    asyncio.run(pipe.step475_defense_strategy("张三", "盗窃罪"))

    result = asyncio.run(pipe.confirm_defense_strategy(
        selected=[0],
        user_additions=["我认为排非是突破口（讯问超时）"],
        use_system_default=False,
    ))
    assert result["success"] is True

    content = (tmp_path / "case_001" / "analysis" / "04.75-辩护思路" / "思路确认.md").read_text(encoding="utf-8")
    assert "无罪辩护" in content           # 选中的系统建议
    assert "罪轻辩护" not in content.split("律师补充")[0]  # 未选中的不在（在补充之前）
    assert "排非是突破口" in content         # 律师补充
    assert "律师补充" in content

    state = json.loads((tmp_path / "case_001" / "analysis" / "analysis_state.json").read_text(encoding="utf-8"))
    assert state["steps"]["4.75"]["status"] == "completed"


def test_use_system_default(tmp_path):
    """一键采纳：全部建议 + 无补充"""
    pipe = _make_pipeline(tmp_path)
    pipe.llm.chat = AsyncMock(return_value=json.dumps({"directions": [
        {"type": "主攻", "direction": "无罪辩护", "basis": "b", "risk": "r"}]}, ensure_ascii=False))
    asyncio.run(pipe.step475_defense_strategy("张三", "盗窃罪"))
    result = asyncio.run(pipe.confirm_defense_strategy(use_system_default=True))
    content = (tmp_path / "case_001" / "analysis" / "04.75-辩护思路" / "思路确认.md").read_text(encoding="utf-8")
    assert "无罪辩护" in content
    assert result["success"] is True


def test_next_unfinished_step_includes_475(tmp_path):
    """4.5 完成后、4.75 未完成时，下一个步骤是 4.75"""
    pipe = _make_pipeline(tmp_path)
    # 标记 1-4、4.5 完成
    state = json.loads(json.dumps(analysis_pipeline.DEFAULT_STATE))
    for k in ["1", "2", "3", "4", "4.5"]:
        state["steps"][k]["status"] = "completed"
    (tmp_path / "case_001" / "analysis" / "analysis_state.json").write_text(
        json.dumps(state), encoding="utf-8")
    assert pipe._get_next_unfinished_step() == 4.75
    # 4.75 完成后下一个是 5
    state["steps"]["4.75"]["status"] = "completed"
    (tmp_path / "case_001" / "analysis" / "analysis_state.json").write_text(
        json.dumps(state), encoding="utf-8")
    assert pipe._get_next_unfinished_step() == 5


def test_step5_injects_confirmed_strategy(tmp_path):
    """步骤 5 各节 prompt 头部注入已确认辩护思路"""
    pipe = _make_pipeline(tmp_path)
    # 预置 Wiki 与确认稿
    wiki = tmp_path / "case_001" / "analysis" / "indictment_wiki"
    (wiki / "01-指控要素.md").write_text("指控要素", encoding="utf-8")
    strategy_dir = tmp_path / "case_001" / "analysis" / "04.75-辩护思路"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "思路确认.md").write_text("# 辩护思路（律师已确认）\n\n主攻：排非", encoding="utf-8")

    captured = []

    async def fake_chat(messages, **kw):
        captured.append(messages[-1]["content"])
        return "小节内容"

    pipe.llm.chat = fake_chat
    asyncio.run(pipe.step5_defense_opinion("张三", "盗窃罪"))

    assert captured, "步骤 5 应有 LLM 调用"
    for prompt in captured:
        assert "辩护思路（律师已确认" in prompt
        assert "主攻：排非" in prompt
        assert "律师补充的思路优先级最高" in prompt


def test_step5_without_strategy_unchanged(tmp_path):
    """无确认稿：prompt 不含辩护思路段（向后兼容）"""
    pipe = _make_pipeline(tmp_path)
    wiki = tmp_path / "case_001" / "analysis" / "indictment_wiki"
    (wiki / "01-指控要素.md").write_text("指控要素", encoding="utf-8")

    captured = []

    async def fake_chat(messages, **kw):
        captured.append(messages[-1]["content"])
        return "小节内容"

    pipe.llm.chat = fake_chat
    asyncio.run(pipe.step5_defense_opinion("张三", "盗窃罪"))
    for prompt in captured:
        assert "辩护思路（律师已确认" not in prompt
