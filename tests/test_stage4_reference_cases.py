"""阶段 4 注入真实参考案例测试

- build_reference_block：把选中的真实案例卡片格式化为提示词注入块
- _run_stage_4：有案号时拉卡片并传给引擎，无案号时行为不变
- stage_4_legal_regulations：注入案例后 user_prompt 的"类案裁判规则"小节与 system_prompt 指令一致
"""
import logging

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from analysis_engine import AnalysisEngine, build_reference_block


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


def test_build_reference_block_malformed_card():
    """缺 case_no/title 的畸形卡片不抛异常，按空字符串渲染"""
    cards = [{"charges": ["盗窃罪"], "issue": "定性问题"}]
    block = build_reference_block(cards)
    assert "【】" in block
    assert "盗窃罪" in block


async def _capture_stage4_prompt(tmp_path, reference_cases):
    """运行 stage_4 并返回 LLM 收到的 (system_prompt, user_prompt)"""
    engine = AnalysisEngine("case_x", tmp_path)
    client = MagicMock()
    client.chat = AsyncMock(return_value="# 输出")

    with patch("llm_client.get_llm_client", return_value=client):
        await engine.stage_4_legal_regulations(
            "被告人", "盗窃罪", reference_cases=reference_cases
        )

    messages = client.chat.call_args.args[0]
    return messages[0]["content"], messages[1]["content"]


@pytest.mark.asyncio
async def test_stage4_user_prompt_with_reference_cases(tmp_path):
    """注入参考案例后，user_prompt 的类案小节应要求引用真实案例，与 system_prompt 一致"""
    system_prompt, user_prompt = await _capture_stage4_prompt(tmp_path, make_cards())

    assert "引用系统提示中提供的真实参考案例" in user_prompt
    assert "【案号】案例名 + 裁判要旨" in user_prompt
    assert "不引用具体案例" not in user_prompt
    # system_prompt 含注入的真实案例
    assert "【第1000号】李某甲等寻衅滋事案" in system_prompt


@pytest.mark.asyncio
async def test_stage4_user_prompt_without_reference_cases(tmp_path):
    """无参考案例时保持原文（严禁虚构、不引用具体案例）"""
    _, user_prompt = await _capture_stage4_prompt(tmp_path, None)

    assert "严禁虚构案例" in user_prompt
    assert "不引用具体案例" in user_prompt
    assert "引用系统提示中提供的真实参考案例" not in user_prompt


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


@pytest.mark.asyncio
async def test_run_stage4_warns_on_partial_fetch(caplog):
    """实际拉到卡片数少于请求案号数时记录 warning"""
    from stage_api import _run_stage_4

    engine = AsyncMock()
    engine.stage_4_legal_regulations = AsyncMock(return_value={"ok": True})
    cards = [{"case_no": "第1000号", "title": "甲案"}]

    with patch("case_search_api.fetch_case_cards", return_value=cards):
        with caplog.at_level(logging.WARNING, logger="stage_api"):
            await _run_stage_4(engine, "被告人", "盗窃罪", ["第1000号", "第1011号"])

    assert any("1" in r.message and "2" in r.message for r in caplog.records)
