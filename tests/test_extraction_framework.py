"""改动 C：提取指引法律框架（要件拆解 + 类案裁判规则）"""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import extraction_framework
from extraction_framework import build_extraction_framework, framework_prompt_prefix


def test_framework_cached(tmp_path, monkeypatch):
    """同一案件只构建一次：缓存到 evidence/legal_framework.json"""
    calls = []

    class FakeClient:
        async def chat(self, messages, **kw):
            calls.append(messages)
            return "虚构交易\n资金支付结算\n信用卡套现"

    monkeypatch.setattr(extraction_framework, "get_llm_client", lambda *a, **kw: FakeClient())
    monkeypatch.setattr("case_framework.fetch_case_rules",
                        lambda charges, keywords=None, size=2: {"非法经营罪": "# 类案裁判规则\n\n规则内容"})

    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    fw1 = asyncio.run(build_extraction_framework(evidence_dir, ["非法经营罪"], ["虚构交易"]))
    fw2 = asyncio.run(build_extraction_framework(evidence_dir, ["非法经营罪"], ["虚构交易"]))
    assert len(calls) == 1  # 第二次走缓存
    assert "虚构交易" in fw1["elements"]
    assert "非法经营罪" in fw1["case_rules"]
    assert (evidence_dir / "legal_framework.json").exists()


def test_degrades_without_llm(tmp_path, monkeypatch):
    """LLM 失败：elements 为空，流程不崩"""
    class BrokenClient:
        chat = AsyncMock(side_effect=RuntimeError("down"))

    monkeypatch.setattr(extraction_framework, "get_llm_client", lambda *a, **kw: BrokenClient())
    monkeypatch.setattr("case_framework.fetch_case_rules", lambda charges, keywords=None, size=2: {})

    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    fw = asyncio.run(build_extraction_framework(evidence_dir, ["诈骗罪"], []))
    assert fw["elements"] == []
    assert fw["case_rules"] == {}


def test_prompt_prefix_stable():
    """固定前缀包含要件与裁判规则，供 system 消息缓存"""
    fw = {
        "charges": ["非法经营罪"],
        "elements": ["虚构交易", "资金支付结算"],
        "case_rules": {"非法经营罪": "# 类案裁判规则\n\n规则A"},
    }
    prefix = framework_prompt_prefix(fw)
    assert "关联要件" in prefix
    assert "虚构交易" in prefix
    assert "规则A" in prefix
    assert "供分析参考" in prefix


def test_prompt_prefix_empty_framework():
    assert framework_prompt_prefix({"charges": ["诈骗罪"], "elements": [], "case_rules": {}}) == ""


def test_extract_single_file_injects_framework(tmp_path, monkeypatch):
    """_extract_single_file：framework_prefix 出现在 system 消息中，且 elements 透传到证据项"""
    import case_manager

    seen = {}

    class FakeClient:
        async def chat(self, messages, **kw):
            seen["system"] = messages[0]["content"]
            return """```json
[{"name": "测试证据", "type": "书证", "summary": "摘要内容", "elements": ["虚构交易"]}]
```"""

    monkeypatch.setattr("llm_client.get_llm_client", lambda *a, **kw: FakeClient())
    md_file = tmp_path / "测试.md"
    md_file.write_text("## 书证\n\n内容", encoding="utf-8")
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    name, evidence_list = asyncio.run(case_manager._extract_single_file(
        md_file, "内容", temp_dir, ["非法经营罪"], framework_prefix="\n\n**本案法律框架**\n虚构交易"))
    assert "本案法律框架" in seen["system"]
    assert evidence_list[0]["elements"] == ["虚构交易"]
