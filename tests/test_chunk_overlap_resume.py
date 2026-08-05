"""提取分块：块间重叠防跨块事实切断 + 块级断点续传"""
import asyncio
import json
from pathlib import Path

import case_manager
from case_manager import _split_content_by_tokens


def test_chunks_have_overlap():
    """多块时，后一块开头包含前一块尾部（重叠区）"""
    sec1 = "## 第一份笔录\n\n" + "甲说。" * 300
    sec2 = "## 第二份笔录\n\n" + "乙说。" * 300
    # 注：单个 "甲说。" 占 4 tokens，每段约 1208 tokens；
    # 预算 1300 保证每段独立成块、两段合计超预算拆为 2 块（标题边界拆分路径）
    chunks = _split_content_by_tokens(sec1 + "\n" + sec2, 1300, "测试.md")
    assert len(chunks) == 2
    # 第二块应包含第一块尾部的重叠内容
    assert "甲说" in chunks[1]["text"][:600]


def test_single_chunk_no_overlap():
    chunks = _split_content_by_tokens("## 短\n\n内容", 100000, "测试.md")
    assert len(chunks) == 1
    assert chunks[0]["label"] == "测试.md"


def test_chunk_level_resume(tmp_path, monkeypatch):
    """多块文件：已完成块跳过，只处理缺失块"""
    md_text = "## 段一\n\n" + "甲。" * 500 + "\n## 段二\n\n" + "乙。" * 500 + "\n## 段三\n\n" + "丙。" * 500
    md_file = tmp_path / "大文件.md"
    md_file.write_text(md_text, encoding="utf-8")
    file_temp = tmp_path / "temp" / "大文件"
    file_temp.mkdir(parents=True)

    calls = []

    class FakeClient:
        async def chat(self, messages, **kw):
            label = messages[-1]["content"].split("## 案卷文件：")[1].split("\n")[0]
            calls.append(label)
            return "[]"

    monkeypatch.setattr("llm_client.get_llm_client", lambda: FakeClient())

    # 预置第 1 块已完成
    (file_temp / "_chunk_0_blocks.json").write_text("[]", encoding="utf-8")
    (file_temp / ".chunk_0.done").write_text("", encoding="utf-8")

    monkeypatch.setattr(case_manager, "_split_content_by_tokens", lambda text, budget, name: [
        {"label": "大文件.md - 分块 1/3", "text": "块1"},
        {"label": "大文件.md - 分块 2/3", "text": "块2"},
        {"label": "大文件.md - 分块 3/3", "text": "块3"},
    ])

    asyncio.run(case_manager._extract_single_file(md_file, md_text, file_temp, []))
    assert len(calls) == 2  # 只处理块 2、3
    assert "分块 2/3" in calls[0] and "分块 3/3" in calls[1]


def test_chunk_cache_invalidated_on_budget_change(tmp_path, monkeypatch):
    """预算变化后旧块缓存失效，全部重提"""
    md_text = "## 段一\n\n" + "甲。" * 500 + "\n## 段二\n\n" + "乙。" * 500 + "\n## 段三\n\n" + "丙。" * 500
    md_file = tmp_path / "大文件.md"
    md_file.write_text(md_text, encoding="utf-8")
    file_temp = tmp_path / "temp" / "大文件"
    file_temp.mkdir(parents=True)

    calls = []

    class FakeClient:
        async def chat(self, messages, **kw):
            label = messages[-1]["content"].split("## 案卷文件：")[1].split("\n")[0]
            calls.append(label)
            return "[]"

    monkeypatch.setattr("llm_client.get_llm_client", lambda: FakeClient())

    # 预置：旧 meta（budget=1000，与当前预算不一致）+ 第 1 块缓存
    (file_temp / "_chunking_meta.json").write_text(
        json.dumps({"budget": 1000, "text_len": len(md_text), "chunks": 3}, ensure_ascii=False),
        encoding="utf-8",
    )
    (file_temp / "_chunk_0_blocks.json").write_text("[]", encoding="utf-8")
    (file_temp / ".chunk_0.done").write_text("", encoding="utf-8")

    monkeypatch.setattr(case_manager, "_split_content_by_tokens", lambda text, budget, name: [
        {"label": "大文件.md - 分块 1/3", "text": "块1"},
        {"label": "大文件.md - 分块 2/3", "text": "块2"},
        {"label": "大文件.md - 分块 3/3", "text": "块3"},
    ])

    asyncio.run(case_manager._extract_single_file(md_file, md_text, file_temp, []))
    assert len(calls) == 3  # 旧缓存失效，包括分块 1/3 在内全部重提
    assert "分块 1/3" in calls[0]


def test_chunk_cache_kept_when_meta_matches(tmp_path, monkeypatch):
    """meta 一致时块缓存仍然有效（不会误失效）"""
    md_text = "## 段一\n\n" + "甲。" * 500 + "\n## 段二\n\n" + "乙。" * 500
    md_file = tmp_path / "大文件.md"
    md_file.write_text(md_text, encoding="utf-8")
    file_temp = tmp_path / "temp" / "大文件"
    file_temp.mkdir(parents=True)

    calls = []

    class FakeClient:
        async def chat(self, messages, **kw):
            label = messages[-1]["content"].split("## 案卷文件：")[1].split("\n")[0]
            calls.append(label)
            return "[]"

    monkeypatch.setattr("llm_client.get_llm_client", lambda: FakeClient())

    # 预置第 1 块已完成（无 meta 文件 → 视为未知来源，按旧行为保留缓存）
    (file_temp / "_chunk_0_blocks.json").write_text("[]", encoding="utf-8")
    (file_temp / ".chunk_0.done").write_text("", encoding="utf-8")

    fake_chunks = [
        {"label": "大文件.md - 分块 1/2", "text": "块1"},
        {"label": "大文件.md - 分块 2/2", "text": "块2"},
    ]
    monkeypatch.setattr(case_manager, "_split_content_by_tokens", lambda text, budget, name: fake_chunks)

    # 第一轮：生成 meta 文件
    asyncio.run(case_manager._extract_single_file(md_file, md_text, file_temp, []))
    assert (file_temp / "_chunking_meta.json").exists()
    assert len(calls) == 1  # 块 1 命中缓存，只处理块 2

    # 第二轮：meta 一致 + 两块缓存齐全 → 全部命中缓存
    calls.clear()
    asyncio.run(case_manager._extract_single_file(md_file, md_text, file_temp, []))
    assert len(calls) == 0
