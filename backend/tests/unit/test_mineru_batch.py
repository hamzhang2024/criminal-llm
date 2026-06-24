"""
MinerU 真批量转换单元测试

测试目标：
1. _convert_batch_cloud - 单元收集（原文件+拆分chunks）、分批提交、结果组装
2. chunks 按页序合并回原文件
3. 单文件降级路径

mock 掉网络方法（_submit_batch_task/_upload_file/_poll_batch_results/_download_and_parse），
验证编排逻辑正确性。
"""

from unittest.mock import AsyncMock, patch

import pytest

from mineru_async import AsyncMinerUConverter, ConvertResult


def _make_converter():
    """构造 cloud 模式转换器（mock token）"""
    return AsyncMinerUConverter(token="test_token")


@pytest.mark.asyncio
async def test_batch_single_files_no_split(tmp_path):
    """多个小文件（无需拆分）→ 各自一个单元，批量提交"""
    conv = _make_converter()
    pdfs = [tmp_path / f"a{i}.pdf" for i in range(3)]
    for p in pdfs:
        p.write_bytes(b"%PDF-1.4")

    # mock：不拆分 + 网络方法
    with patch("mineru_async._split_pdf_pages", return_value=[]), \
         patch.object(conv, "_submit_batch_task", new=AsyncMock(return_value=("bid", ["u0", "u1", "u2"], ""))), \
         patch.object(conv, "_upload_file", new=AsyncMock(return_value=True)), \
         patch.object(conv, "_poll_batch_results", new=AsyncMock(return_value=[
             {"file_name": "a0.pdf", "state": "done", "full_zip_url": "u0"},
             {"file_name": "a1.pdf", "state": "done", "full_zip_url": "u1"},
             {"file_name": "a2.pdf", "state": "done", "full_zip_url": "u2"},
         ])), \
         patch.object(conv, "_download_and_parse", new=AsyncMock(side_effect=[
             ("text0", None), ("text1", None), ("text2", None),
         ])):
        results = await conv._convert_batch_cloud(pdfs, tmp_path)

    assert len(results) == 3
    assert all(r.success for r in results)
    assert results[0].text == "text0"
    assert results[1].text == "text1"
    assert results[2].text == "text2"


@pytest.mark.asyncio
async def test_batch_chunked_file_merged(tmp_path):
    """大文件拆分成 2 chunk → 批量提交 → 按页序合并回原文件"""
    conv = _make_converter()
    pdf = tmp_path / "big.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    chunk1 = tmp_path / "_chunk_1-90_big.pdf"
    chunk2 = tmp_path / "_chunk_91-180_big.pdf"
    chunk1.write_bytes(b"%PDF-1.4")
    chunk2.write_bytes(b"%PDF-1.4")

    with patch("mineru_async._split_pdf_pages", return_value=[(chunk1, 1, 90), (chunk2, 91, 180)]), \
         patch.object(conv, "_submit_batch_task", new=AsyncMock(return_value=("bid", ["u0", "u1"], ""))), \
         patch.object(conv, "_upload_file", new=AsyncMock(return_value=True)), \
         patch.object(conv, "_poll_batch_results", new=AsyncMock(return_value=[
             {"file_name": "_chunk_1-90_big.pdf", "state": "done", "full_zip_url": "u0"},
             {"file_name": "_chunk_91-180_big.pdf", "state": "done", "full_zip_url": "u1"},
         ])), \
         patch.object(conv, "_download_and_parse", new=AsyncMock(side_effect=[
             ("chunk1text", None), ("chunk2text", None),
         ])):
        results = await conv._convert_batch_cloud([pdf], tmp_path)

    assert len(results) == 1
    assert results[0].success
    # 两段文本都应出现在合并结果，且按页序（chunk1 在前）
    assert "chunk1text" in results[0].text
    assert "chunk2text" in results[0].text
    assert results[0].text.index("chunk1text") < results[0].text.index("chunk2text")
    # chunk 临时文件应被清理
    assert not chunk1.exists()
    assert not chunk2.exists()


@pytest.mark.asyncio
async def test_batch_partial_failure(tmp_path):
    """批量中部分文件失败 → 失败的标记 success=False，成功的仍返回"""
    conv = _make_converter()
    pdfs = [tmp_path / "a.pdf", tmp_path / "b.pdf"]
    for p in pdfs:
        p.write_bytes(b"%PDF-1.4")

    with patch("mineru_async._split_pdf_pages", return_value=[]), \
         patch.object(conv, "_submit_batch_task", new=AsyncMock(return_value=("bid", ["u0", "u1"], ""))), \
         patch.object(conv, "_upload_file", new=AsyncMock(return_value=True)), \
         patch.object(conv, "_poll_batch_results", new=AsyncMock(return_value=[
             {"file_name": "a.pdf", "state": "done", "full_zip_url": "u0"},
             {"file_name": "b.pdf", "state": "failed", "err_msg": "解析失败"},
         ])), \
         patch.object(conv, "_download_and_parse", new=AsyncMock(return_value=("text_a", None))):
        results = await conv._convert_batch_cloud(pdfs, tmp_path)

    assert len(results) == 2
    assert results[0].success  # a.pdf 成功
    assert not results[1].success  # b.pdf 失败
    assert "转换失败" in results[1].error or "失败" in results[1].error


@pytest.mark.asyncio
async def test_batch_submit_failure_degrades_to_single(tmp_path):
    """批量提交失败 → 该批降级为逐文件单文件转换"""
    conv = _make_converter()
    pdfs = [tmp_path / "a.pdf", tmp_path / "b.pdf"]
    for p in pdfs:
        p.write_bytes(b"%PDF-1.4")

    single_results = [
        ConvertResult(file_name="a.pdf", success=True, text="single_a", images_dir=None),
        ConvertResult(file_name="b.pdf", success=True, text="single_b", images_dir=None),
    ]
    with patch("mineru_async._split_pdf_pages", return_value=[]), \
         patch.object(conv, "_submit_batch_task", new=AsyncMock(return_value=(None, [], "提交失败"))), \
         patch.object(conv, "_convert_single_file", new=AsyncMock(side_effect=single_results)):
        results = await conv._convert_batch_cloud(pdfs, tmp_path)

    assert len(results) == 2
    assert results[0].success
    assert results[0].text == "single_a"


@pytest.mark.asyncio
async def test_batch_splits_into_multiple_batches(tmp_path):
    """超过 MINERU_BATCH_SIZE(50) 的文件 → 分多批提交"""
    conv = _make_converter()
    from mineru_async_helpers import MINERU_BATCH_SIZE
    n = MINERU_BATCH_SIZE + 5  # 55 个，应分 2 批
    pdfs = [tmp_path / f"f{i}.pdf" for i in range(n)]
    for p in pdfs:
        p.write_bytes(b"%PDF-1.4")

    submit_calls = []

    async def fake_submit(session, files):
        submit_calls.append(len(files))
        urls = [f"u{i}" for i in range(len(files))]
        return ("bid", urls, "")

    with patch("mineru_async._split_pdf_pages", return_value=[]), \
         patch.object(conv, "_submit_batch_task", new=fake_submit), \
         patch.object(conv, "_upload_file", new=AsyncMock(return_value=True)), \
         patch.object(conv, "_poll_batch_results", new=AsyncMock(side_effect=[
             [{"file_name": f"f{i}.pdf", "state": "done", "full_zip_url": f"u{i}"} for i in range(MINERU_BATCH_SIZE)],
             [{"file_name": f"f{i}.pdf", "state": "done", "full_zip_url": f"u{i}"} for i in range(MINERU_BATCH_SIZE, n)],
         ])), \
         patch.object(conv, "_download_and_parse", new=AsyncMock(return_value=("t", None))):
        results = await conv._convert_batch_cloud(pdfs, tmp_path)

    # 应分 2 批：50 + 5
    assert len(submit_calls) == 2
    assert submit_calls[0] == MINERU_BATCH_SIZE
    assert submit_calls[1] == 5
    assert len(results) == n
    assert all(r.success for r in results)
