"""类案检索关键词推荐（含无起诉书回退）

背景：步骤 4a 原仅在 indictment_analyzed=True 时推荐关键词；
羁押/侦查阶段案件无起诉书 → suggested_keywords=None → 前端只能回退显示罪名。
修复：无起诉书时回退用阶段 3（事件拆解）/阶段 1 产物作为推荐材料来源。
"""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

from analysis_pipeline import AnalysisPipeline


def _make_pipeline(tmp_path: Path) -> AnalysisPipeline:
    """构造最小 AnalysisPipeline（仅案件目录与 analysis 目录）"""
    case_path = tmp_path / "case_001"
    (case_path / "analysis").mkdir(parents=True)
    return AnalysisPipeline("case_001", case_path)


def _read_case_json(tmp_path: Path) -> dict:
    meta = tmp_path / "case_001" / "case.json"
    return json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}


def test_keywords_from_indictment(tmp_path):
    """有起诉书：从指控要素推荐关键词并存 case.json"""
    pipe = _make_pipeline(tmp_path)
    pipe.llm.chat = AsyncMock(return_value="1. 入户盗窃\n2、多次作案\n3) 流窜作案\n- 数额巨大\n• 销赃\n第六条超出截断")

    result = asyncio.run(pipe._suggest_search_keywords("指控要素：被告人入户盗窃，多次作案……"))

    # 编号/列表符号剥离 + [:5] 截断
    assert result == ["入户盗窃", "多次作案", "流窜作案", "数额巨大", "销赃"]
    assert _read_case_json(tmp_path)["suggested_keywords"] == result

    # prompt 用「案件材料」表述（无起诉书时也贴切的统一措辞）
    system_prompt = pipe.llm.chat.call_args[0][0][0]["content"]
    assert "案件材料" in system_prompt
    assert "不要包含罪名本身" in system_prompt


def test_keywords_fallback_to_stage3_without_indictment(tmp_path):
    """无起诉书：回退用阶段3产物推荐（高利贷/恶意垒高债权类实质词）"""
    pipe = _make_pipeline(tmp_path)
    analysis_dir = tmp_path / "case_001" / "analysis"
    (analysis_dir / "stage_3").mkdir()
    (analysis_dir / "stage_3" / "output.md").write_text(
        "事件拆解：被告人以高利贷放贷，通过虚增债务恶意垒高债权，并以扣押车辆相要挟……",
        encoding="utf-8",
    )
    captured = []

    async def fake_chat(messages, **kw):
        captured.append(messages)
        return "1. 高利贷\n2. 恶意垒高债权\n3. 套路押车"

    pipe.llm.chat = fake_chat

    # 复刻步骤 4a 调用点逻辑：无起诉书 → 回退阶段 3/1 产物
    kw_source = pipe._keyword_source_text(indictment_analyzed=False, indictment_content="")
    assert "高利贷" in kw_source  # 确实取到了阶段 3 产物
    if kw_source.strip():
        asyncio.run(pipe._suggest_search_keywords(kw_source))

    assert _read_case_json(tmp_path)["suggested_keywords"] == ["高利贷", "恶意垒高债权", "套路押车"]
    # 阶段 3 文本进入了 user 消息
    assert "恶意垒高债权" in captured[0][-1]["content"]


def test_keywords_fallback_prefers_stage3_over_stage1(tmp_path):
    """无起诉书且阶段 3/1 均有产物：优先阶段 3"""
    pipe = _make_pipeline(tmp_path)
    analysis_dir = tmp_path / "case_001" / "analysis"
    (analysis_dir / "stage_3").mkdir()
    (analysis_dir / "stage_1").mkdir()
    (analysis_dir / "stage_3" / "output.md").write_text("阶段三内容", encoding="utf-8")
    (analysis_dir / "stage_1" / "output.md").write_text("阶段一内容", encoding="utf-8")

    assert pipe._keyword_source_text(indictment_analyzed=False, indictment_content="") == "阶段三内容"


def test_keywords_fallback_to_stage1_when_stage3_missing(tmp_path):
    """无起诉书且阶段 3 缺失：回退阶段 1 产物"""
    pipe = _make_pipeline(tmp_path)
    analysis_dir = tmp_path / "case_001" / "analysis"
    (analysis_dir / "stage_1").mkdir()
    (analysis_dir / "stage_1" / "output.md").write_text("阶段一内容", encoding="utf-8")

    assert pipe._keyword_source_text(indictment_analyzed=False, indictment_content="") == "阶段一内容"


def test_keywords_skipped_when_no_source(tmp_path):
    """无起诉书且无阶段产物：不推荐、不写 case.json、不抛异常"""
    pipe = _make_pipeline(tmp_path)
    pipe.llm.chat = AsyncMock(side_effect=AssertionError("不应调用 LLM"))

    kw_source = pipe._keyword_source_text(indictment_analyzed=False, indictment_content="")
    assert kw_source == ""
    # 调用点守卫：空来源不调 LLM、不写 case.json
    if kw_source.strip():
        asyncio.run(pipe._suggest_search_keywords(kw_source))

    pipe.llm.chat.assert_not_called()
    assert "suggested_keywords" not in _read_case_json(tmp_path)


def test_suggest_keywords_empty_llm_response_no_write(tmp_path):
    """LLM 返回空：不写 case.json（避免覆盖已有推荐）"""
    pipe = _make_pipeline(tmp_path)
    pipe.llm.chat = AsyncMock(return_value="\n  \n")

    result = asyncio.run(pipe._suggest_search_keywords("案件材料"))

    assert result == []
    assert "suggested_keywords" not in _read_case_json(tmp_path)
