"""PaddleOCR 空页/缺页自动重试测试（遗漏证据是严重事故，宁多跑一页不漏一页）"""
import asyncio
from pathlib import Path

import fitz
import pytest

import paddleocr_async
from paddleocr_async import AsyncPaddleOCRConverter


@pytest.fixture
def converter():
    return AsyncPaddleOCRConverter(token="fake-token")


def _make_pdf(path: Path, pages: int) -> Path:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(str(path))
    doc.close()
    return path


def _patch_submit(converter, monkeypatch, pages_by_call, submit_counts):
    """打桩提交/轮询/下载：pages_by_call 是按调用次序返回的 {页码: 文本} 列表"""
    async def fake_submit(session, pdf_path):
        return f"job-{len(submit_counts)}"

    async def fake_poll(session, job_id, timeout, progress_cb=None):
        return f"url-{job_id}"

    async def fake_download(session, jsonl_url, output_dir, stem):
        idx = len(submit_counts)
        submit_counts.append(stem)
        return pages_by_call[min(idx, len(pages_by_call) - 1)], None

    monkeypatch.setattr(converter, "_submit_job", fake_submit)
    monkeypatch.setattr(converter, "_poll_job", fake_poll)
    monkeypatch.setattr(converter, "_download_and_parse", fake_download)


def test_empty_page_retried_and_recovered(converter, tmp_path, monkeypatch):
    """第 2 页为空 → 自动重试 → 恢复内容按原页序合并"""
    pdf = _make_pdf(tmp_path / "case.pdf", 3)
    submit_counts = []
    _patch_submit(converter, monkeypatch, [
        {0: "第一页内容文字足够长用来通过最内容检查" * 3, 1: "", 2: "第三页内容"},  # 首次：第2页空
        {0: "恢复的第二页内容"},  # 重试任务（只含原第2页）
    ], submit_counts)

    result = asyncio.run(converter.convert_single(pdf, tmp_path / "out"))

    assert result.success
    md = (tmp_path / "out" / "case.md").read_text(encoding="utf-8")
    assert "恢复的第二页内容" in md, "空页应被重试恢复"
    assert len(submit_counts) == 2, "应恰好重试一次"
    # 页序正确：第一页 < 恢复的第二页 < 第三页
    assert md.index("第一页") < md.index("恢复的第二页") < md.index("第三页")


def test_unrecoverable_page_gets_warning_marker(converter, tmp_path, monkeypatch):
    """重试 2 次仍为空 → 写入醒目警告标记，不静默遗漏"""
    pdf = _make_pdf(tmp_path / "case.pdf", 2)
    _patch_submit(converter, monkeypatch, [
        {0: "正常内容文字足够长用来通过最少内容检查" * 3, 1: ""},  # 首次
        {0: ""},  # 重试1仍空
        {0: ""},  # 重试2仍空
    ], [])

    result = asyncio.run(converter.convert_single(pdf, tmp_path / "out"))

    assert result.success
    md = (tmp_path / "out" / "case.md").read_text(encoding="utf-8")
    assert "本页识别为空" in md, "不可恢复的空页必须有警告标记"


def test_full_pages_no_retry(converter, tmp_path, monkeypatch):
    """全部有内容：不重试（只提交一次）"""
    pdf = _make_pdf(tmp_path / "case.pdf", 2)
    submit_counts = []
    _patch_submit(converter, monkeypatch, [
        {0: "第一页内容足够长用来通过最少内容检查" * 3, 1: "第二页内容"},
    ], submit_counts)

    result = asyncio.run(converter.convert_single(pdf, tmp_path / "out"))
    assert result.success
    assert len(submit_counts) == 1, "无空页不应重试"
