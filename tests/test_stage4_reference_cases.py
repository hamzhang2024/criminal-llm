"""阶段 4 注入真实参考案例测试

- build_reference_block：把选中的真实案例卡片格式化为提示词注入块
- _run_stage_4：有案号时拉卡片并传给引擎，无案号时行为不变
"""
import pytest
from unittest.mock import AsyncMock, patch

from analysis_engine import build_reference_block


def make_cards():
    return [
        {
            "case_no": "第1000号",
            "title": "李某甲等寻衅滋事案",
            "charges": ["寻衅滋事罪"],
            "issue": "未成年人多次强取财物如何处理",
            "holding_summary": "未成年人以轻微暴力强索少量财物，定寻衅滋事罪。",
            "reasoning_excerpt": "本案审理中存在两种意见……",
        },
        {
            "case_no": "第1011号",
            "title": "熊海涛盗窃案",
            "charges": ["盗窃罪"],
            "issue": "帮助转移财物如何定性",
            "holding_summary": "明知系未成年人盗卖财物仍帮助转移的……",
            "reasoning_excerpt": "本院认为……",
        },
    ]


def test_build_reference_block_contains_all_fields():
    block = build_reference_block(make_cards())
    assert "【第1000号】李某甲等寻衅滋事案" in block
    assert "【第1011号】熊海涛盗窃案" in block
    assert "寻衅滋事罪" in block
    assert "未成年人多次强取财物如何处理" in block
    assert "本案审理中存在两种意见" in block


def test_build_reference_block_empty():
    assert build_reference_block([]) == ""


@pytest.mark.asyncio
async def test_run_stage4_passes_reference_cards():
    """_run_stage_4 在有案号时拉卡片并传给引擎"""
    from stage_api import _run_stage_4

    engine = AsyncMock()
    engine.stage_4_legal_regulations = AsyncMock(return_value={"ok": True})
    cards = [{"case_no": "第1000号", "title": "甲案", "charges": [], "issue": "", "holding_summary": "", "reasoning_excerpt": ""}]

    with patch("case_search_api.fetch_case_cards", return_value=cards):
        await _run_stage_4(engine, "被告人", "盗窃罪", ["第1000号"])

    kwargs = engine.stage_4_legal_regulations.call_args.kwargs
    assert kwargs["reference_cases"] == cards


@pytest.mark.asyncio
async def test_run_stage4_without_refs_unchanged():
    from stage_api import _run_stage_4

    engine = AsyncMock()
    engine.stage_4_legal_regulations = AsyncMock(return_value={"ok": True})
    await _run_stage_4(engine, "被告人", "盗窃罪", None)

    kwargs = engine.stage_4_legal_regulations.call_args.kwargs
    assert "reference_cases" not in kwargs or kwargs["reference_cases"] is None
