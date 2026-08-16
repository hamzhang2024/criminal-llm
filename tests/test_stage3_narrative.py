"""stage_3 事件拆解测试（拆分后契约：固定两次聚焦调用）

第一次调用只输出时间线 JSON，第二次只输出事件拆解叙述（输入含时间线产物）。
合并产物 = 时间线（mermaid）+ 叙述；叙述为空时保留时间线，不崩溃。
"""
import asyncio
from pathlib import Path

import analysis_engine  # noqa: F401  # 确保模块可导入
from analysis_engine import AnalysisEngine


def _make_engine(tmp_path: Path) -> AnalysisEngine:
    case_path = tmp_path / "case_001"
    analysis_dir = case_path / "analysis"
    analysis_dir.mkdir(parents=True)
    engine = AnalysisEngine("case_001", case_path)
    return engine


TIMELINE_ONLY = """```json
{"title":"案件时间线","events":[{"date":"2025-12-22","title":"转账","evidence":["见证据001"]}]}
```"""

NARRATIVE = """### 二、事件拆解与证据归组

#### 事件 1：2025年12月22日 转账
- 时间：2025年12月22日
- 地点：江阴市
- 简述：被告人通过银行转账收取资金
- 相关证据：见证据001（银行流水）——证明当日有 5 万元入账
- 初步观察：与被告人供述一致，无矛盾

#### 事件 2：2025年12月25日 分赃
- 时间：2025年12月25日
- 地点：江阴市某茶楼
- 简述：被告人与同案人分配赃款
- 相关证据：见证据002（讯问笔录）——被告人供述分赃经过
- 初步观察：同案人证言与供述存在金额差异，需进一步核实分赃比例与各自所得

#### 事件 3：2026年1月5日 退赃
- 时间：2026年1月5日
- 地点：江阴市公安局
- 简述：被告人家属代为退缴部分赃款
- 相关证据：见证据003（扣押清单）——载明退缴现金 3 万元；见证据004（收据）——收款单位确认入账
- 初步观察：退赃金额与涉案金额差距较大，量刑时可作为酌定从轻情节，但需与审计报告核对退缴比例

#### 事件 4：2026年1月10日 到案
- 时间：2026年1月10日
- 地点：江阴市某派出所
- 简述：被告人经电话通知后主动到案
- 相关证据：见证据005（到案经过）——侦查机关出具的到案说明
- 初步观察：到案方式涉及自首认定，需结合首次讯问笔录判断是否如实供述"""


def _patch_evidence(monkeypatch, engine):
    """打桩证据加载，避免依赖真实案件文件"""
    monkeypatch.setattr(engine, "_load_evidence_texts", lambda prefer_summary=False: [
        {"filename": "001_流水.md", "text": "2025年12月22日收到转账50000元", "type": "书证"}
    ])


def _patch_llm(monkeypatch, fake_chat):
    """打桩 llm_client.get_llm_client，返回带 chat 方法的假客户端"""
    import llm_client
    fake_client = type("C", (), {"chat": staticmethod(fake_chat)})()
    monkeypatch.setattr(llm_client, "get_llm_client", lambda: fake_client)


def test_timeline_and_narrative_two_calls(tmp_path, monkeypatch):
    """拆分后固定两次调用：时间线 JSON + 事件拆解叙述，合并产物两者皆含"""
    engine = _make_engine(tmp_path)
    _patch_evidence(monkeypatch, engine)
    calls = []

    async def fake_chat(messages, **kw):
        calls.append(messages)
        if len(calls) == 1:
            return TIMELINE_ONLY  # 第一次：时间线 JSON
        return NARRATIVE  # 第二次：事件拆解叙述

    _patch_llm(monkeypatch, fake_chat)
    asyncio.run(engine.stage_3_event_timeline("张三", "诈骗罪"))

    assert len(calls) == 2, "拆分后应固定两次调用"
    output = (tmp_path / "case_001" / "analysis" / "stage_3" / "output.md").read_text(encoding="utf-8")
    assert "事件拆解" in output
    assert "mermaid" in output or "timeline" in output, "时间线部分应保留"


def test_second_call_carries_timeline(tmp_path, monkeypatch):
    """第二次调用的材料携带第一次生成的时间线（保持两部分连贯）"""
    engine = _make_engine(tmp_path)
    _patch_evidence(monkeypatch, engine)
    calls = []

    async def fake_chat(messages, **kw):
        calls.append(messages)
        if len(calls) == 1:
            return TIMELINE_ONLY
        return NARRATIVE

    _patch_llm(monkeypatch, fake_chat)
    asyncio.run(engine.stage_3_event_timeline("张三", "诈骗罪"))

    second_user = calls[1][-1]["content"]
    assert "见证据001" in second_user, "第二次调用应包含第一次的时间线产物"


def test_empty_narrative_keeps_timeline(tmp_path, monkeypatch):
    """第二次调用返回空：保留时间线产物，不崩溃"""
    engine = _make_engine(tmp_path)
    _patch_evidence(monkeypatch, engine)

    async def fake_chat(messages, **kw):
        return TIMELINE_ONLY

    _patch_llm(monkeypatch, fake_chat)
    result = asyncio.run(engine.stage_3_event_timeline("张三", "诈骗罪"))

    output = (tmp_path / "case_001" / "analysis" / "stage_3" / "output.md").read_text(encoding="utf-8")
    assert len(output) > 0, "时间线产物应保留"
    assert result["stage"] == 3
