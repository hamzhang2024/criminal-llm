"""B'：起诉书/意见书提取件带原文全文（LLM 只定位，代码切片，不经 LLM 转述）"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import case_manager
from case_manager import _slice_section_by_markers, _process_indictment_single


def test_slice_by_markers():
    middle = "中间内容。" * 50
    raw = f"## 拘留证\n内容A\n## 起诉意见书\n第一行正文\n{middle}\n最后一行\n## 逮捕证\n内容B"
    out = _slice_section_by_markers(raw, "第一行正文", "最后一行")
    assert out is not None
    assert out.startswith("第一行正文")
    assert out.endswith("最后一行")
    assert "中间内容。" in out
    assert "内容A" not in out


def test_slice_marker_not_found():
    assert _slice_section_by_markers("内容", "不存在首行", "不存在末行") is None


def test_slice_reversed_markers():
    """末行先于首行出现（LLM 给错）返回 None"""
    raw = "最后一行\n中间\n第一行正文"
    assert _slice_section_by_markers(raw, "第一行正文", "最后一行") is None


def test_indictment_evidence_contains_fulltext(tmp_path, monkeypatch):
    """提取件包含原文全文段（非 LLM 转述）"""
    body = "被告人张三于2025年1月盗窃财物，价值人民币五千元整。" * 10
    raw_md = f"## 卷内目录\n目录内容\n## 起诉意见书\n澄公刑诉字（2025）697号\n{body}\n此致\n某某检察院\n## 其他文书\n内容"

    chat_count = {"n": 0}

    class FakeClient:
        async def chat(self, messages, **kw):
            chat_count["n"] += 1
            user = messages[-1]["content"]
            if "首行" in user or "末行" in user:
                # 定位调用：返回原文首行/末行
                return "首行：澄公刑诉字（2025）697号\n末行：某某检察院"
            return "结构化提取结果"

    monkeypatch.setattr("llm_client.get_llm_client", lambda: FakeClient())
    md_file = tmp_path / "第1卷.md"
    md_file.write_text(raw_md, encoding="utf-8")
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()

    out_path = asyncio.run(_process_indictment_single(md_file, raw_md, evidence_dir, 31))
    content = out_path.read_text(encoding="utf-8")
    assert "## 原文全文" in content
    assert "澄公刑诉字（2025）697号" in content
    assert "此致" in content
    assert "目录内容" not in content.split("## 原文全文")[1]  # 切片外内容不混入


def test_slice_too_short_returns_none():
    """切片 <200 字符视为定位失败"""
    assert _slice_section_by_markers("首行\n末行", "首行", "末行") is None


def test_slice_normal_still_works():
    raw = "前奏\n" + "正文内容。" * 50 + "\n结尾行\n后续"
    out = _slice_section_by_markers(raw, "正文内容。正文内容。", "结尾行")
    assert out is not None
    assert "正文内容。" in out
    assert out.endswith("结尾行")


def test_indictment_halfwidth_colon_locate(tmp_path, monkeypatch):
    """定位结果使用半角冒号（首行: / 末行:）也能解析"""
    body = "被告人张三于2025年1月盗窃财物，价值人民币五千元整。" * 10
    raw_md = f"## 卷内目录\n目录内容\n## 起诉意见书\n澄公刑诉字（2025）697号\n{body}\n此致\n某某检察院\n## 其他文书\n内容"

    class FakeClient:
        async def chat(self, messages, **kw):
            user = messages[-1]["content"]
            if "首行" in user or "末行" in user:
                # 定位调用：返回半角冒号格式
                return "首行: 澄公刑诉字（2025）697号\n末行: 某某检察院"
            return "结构化提取结果"

    monkeypatch.setattr("llm_client.get_llm_client", lambda: FakeClient())
    md_file = tmp_path / "第1卷.md"
    md_file.write_text(raw_md, encoding="utf-8")
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()

    out_path = asyncio.run(_process_indictment_single(md_file, raw_md, evidence_dir, 32))
    content = out_path.read_text(encoding="utf-8")
    assert "## 原文全文" in content
    assert "澄公刑诉字（2025）697号" in content


def test_indictment_short_slice_falls_back(tmp_path, monkeypatch):
    """定位成功但切片 <200 字符时，视为定位失败，仅保留结构化提取"""
    raw_md = "## 起诉意见书\n澄公刑诉字（2025）697号\n被告人张三盗窃。\n某某检察院"

    class FakeClient:
        async def chat(self, messages, **kw):
            user = messages[-1]["content"]
            if "首行" in user or "末行" in user:
                return "首行：澄公刑诉字（2025）697号\n末行：某某检察院"
            return "结构化提取结果"

    monkeypatch.setattr("llm_client.get_llm_client", lambda: FakeClient())
    md_file = tmp_path / "第1卷.md"
    md_file.write_text(raw_md, encoding="utf-8")
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()

    out_path = asyncio.run(_process_indictment_single(md_file, raw_md, evidence_dir, 33))
    content = out_path.read_text(encoding="utf-8")
    assert "## 原文全文" not in content  # 切片过短 → 兜底为结构化提取
    assert "结构化提取结果" in content
