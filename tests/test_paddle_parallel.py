"""PaddleOCR 任务级共享并发 + 分块并行测试"""
import asyncio
from pathlib import Path

import fitz
import pytest

import paddleocr_async
from paddleocr_async import AsyncPaddleOCRConverter, ConvertResult


@pytest.fixture
def converter():
    return AsyncPaddleOCRConverter(token="fake-token")


def _make_pdf(path: Path, pages: int = 3) -> Path:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(str(path))
    doc.close()
    return path


def test_shared_semaphore_bounds_batch_concurrency(converter, tmp_path, monkeypatch):
    """批量转换：同时在跑的 API 任务数 ≤ 信号量，且确实并行（>1）"""
    pdfs = [_make_pdf(tmp_path / f"f{i}.pdf") for i in range(8)]
    inflight = 0
    max_inflight = 0

    async def fake_convert(pdf_path, output_dir, timeout, progress_cb=None):
        nonlocal inflight, max_inflight
        inflight += 1
        max_inflight = max(max_inflight, inflight)
        await asyncio.sleep(0.05)
        inflight -= 1
        return ConvertResult(file_name=pdf_path.name, success=True, text="x" * 100, pages=1)

    monkeypatch.setattr(converter, "_convert_single_file", fake_convert)
    monkeypatch.setattr(paddleocr_async, "_split_pdf_pages", lambda p, chunk_size=100: [])

    results = asyncio.run(converter.convert_batch(pdfs, tmp_path / "out", max_concurrent=5))
    assert len(results) == 8
    assert all(r.success for r in results)
    assert 1 < max_inflight <= 5, f"并行度异常: {max_inflight}"


def test_chunks_run_in_parallel(converter, tmp_path, monkeypatch):
    """分块并行：同一文件的多个分块同时跑，合并顺序按页码正确"""
    pdf = _make_pdf(tmp_path / "big.pdf", pages=3)
    chunks = [(tmp_path / f"_c{i}.pdf", i * 100 + 1, (i + 1) * 100) for i in range(3)]
    monkeypatch.setattr(paddleocr_async, "_split_pdf_pages", lambda p, chunk_size=100: chunks)

    inflight = 0
    max_inflight = 0

    async def fake_convert(pdf_path, output_dir, timeout, progress_cb=None):
        nonlocal inflight, max_inflight
        inflight += 1
        max_inflight = max(max_inflight, inflight)
        await asyncio.sleep(0.05)
        inflight -= 1
        stem = pdf_path.stem
        return ConvertResult(file_name=pdf_path.name, success=True, text=f"内容-{stem}", pages=100)

    monkeypatch.setattr(converter, "_convert_single_file", fake_convert)

    result = asyncio.run(converter.convert_single(pdf, tmp_path / "out"))
    assert result.success
    assert max_inflight > 1, "分块应并行执行"
    # 合并后 MD 存在且按页码顺序包含各分块内容
    merged = (tmp_path / "out" / "big.md").read_text(encoding="utf-8")
    idx = [merged.index(f"第{i * 100 + 1}-{(i + 1) * 100}页") for i in range(3)]
    assert idx == sorted(idx), "分块应按页码顺序合并"


def test_chunk_failure_isolated(converter, tmp_path, monkeypatch):
    """分块失败隔离：一个分块失败不影响其他分块合并"""
    pdf = _make_pdf(tmp_path / "big2.pdf", pages=3)
    chunks = [(tmp_path / f"_c{i}.pdf", i * 100 + 1, (i + 1) * 100) for i in range(3)]
    monkeypatch.setattr(paddleocr_async, "_split_pdf_pages", lambda p, chunk_size=100: chunks)

    async def fake_convert(pdf_path, output_dir, timeout, progress_cb=None):
        if pdf_path.name == "_c1.pdf":
            return ConvertResult(file_name=pdf_path.name, success=False, error="模拟失败")
        return ConvertResult(file_name=pdf_path.name, success=True, text=f"内容-{pdf_path.stem}", pages=100)

    monkeypatch.setattr(converter, "_convert_single_file", fake_convert)

    result = asyncio.run(converter.convert_single(pdf, tmp_path / "out"))
    assert result.success, "部分分块失败不应导致整体失败"
    merged = (tmp_path / "out" / "big2.md").read_text(encoding="utf-8")
    assert "内容-_c0" in merged and "内容-_c2" in merged
    assert "内容-_c1" not in merged


def test_file_size_threshold_50mb():
    """文件大小阈值已修正为官方 50MB"""
    assert paddleocr_async.PADDLEOCR_MAX_FILE_SIZE == 50 * 1024 * 1024


def test_resolve_max_concurrent(monkeypatch):
    """并发配置解析：默认 5，配置生效，clamp 1-10"""
    monkeypatch.setattr("config_manager.get_config_value", lambda key, default="": default)
    assert paddleocr_async.resolve_max_concurrent() == 5
    monkeypatch.setattr("config_manager.get_config_value", lambda key, default="": 8)
    assert paddleocr_async.resolve_max_concurrent() == 8
    monkeypatch.setattr("config_manager.get_config_value", lambda key, default="": 99)
    assert paddleocr_async.resolve_max_concurrent() == 10
    monkeypatch.setattr("config_manager.get_config_value", lambda key, default="": 0)
    assert paddleocr_async.resolve_max_concurrent() == 1


def test_chunk_images_merged(tmp_path, monkeypatch, converter):
    """分块图片合并：合并后图片目录包含各分块图片（修复前：目录恒空，图片链接全断）"""
    pdf = _make_pdf(tmp_path / "big3.pdf", pages=3)
    chunks = [(tmp_path / f"_c{i}.pdf", i * 100 + 1, (i + 1) * 100) for i in range(2)]
    monkeypatch.setattr(paddleocr_async, "_split_pdf_pages", lambda p, chunk_size=100: chunks)

    async def fake_convert(pdf_path, output_dir, timeout, progress_cb=None):
        # 模拟真实行为：在临时输出目录生成图片并返回 images_dir
        stem = pdf_path.stem
        images_dir = output_dir / f"{stem}_images"
        images_dir.mkdir(parents=True, exist_ok=True)
        (images_dir / f"img_{stem}.jpg").write_bytes(b"\xff\xd8\xff")  # 假 JPEG 头
        return ConvertResult(
            file_name=pdf_path.name, success=True,
            text=f'内容 <img src="./_chunk_x_{stem}_images/img_{stem}.jpg">',
            images_dir=images_dir, pages=100,
        )

    monkeypatch.setattr(converter, "_convert_single_file", fake_convert)
    out = tmp_path / "out"
    result = asyncio.run(converter.convert_single(pdf, out))

    assert result.success
    merged_images = out / "big3_images"
    assert merged_images.exists()
    imgs = list(merged_images.iterdir())
    assert len(imgs) == 2, f"合并图片数应为 2，实际 {len(imgs)}"


def test_gather_exception_isolated(tmp_path, monkeypatch, converter):
    """某分块协程抛异常：其余分块照常合并（return_exceptions 防御）"""
    pdf = _make_pdf(tmp_path / "big4.pdf", pages=3)
    chunks = [(tmp_path / f"_c{i}.pdf", i * 100 + 1, (i + 1) * 100) for i in range(3)]
    monkeypatch.setattr(paddleocr_async, "_split_pdf_pages", lambda p, chunk_size=100: chunks)

    async def fake_convert(pdf_path, output_dir, timeout, progress_cb=None):
        if pdf_path.name == "_c1.pdf":
            raise RuntimeError("模拟协程异常")
        return ConvertResult(file_name=pdf_path.name, success=True, text=f"内容-{pdf_path.stem}", pages=100)

    monkeypatch.setattr(converter, "_convert_single_file", fake_convert)
    result = asyncio.run(converter.convert_single(pdf, tmp_path / "out"))

    assert result.success, "单个分块异常不应导致整体失败"
    merged = (tmp_path / "out" / "big4.md").read_text(encoding="utf-8")
    assert "内容-_c0" in merged and "内容-_c2" in merged
