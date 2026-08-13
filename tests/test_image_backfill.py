"""MinerU 图片文字回填测试（PaddleOCR 单图识别通道）"""
import asyncio
import json
from pathlib import Path

import pytest
from PIL import Image

import image_ocr_backfill
from image_ocr_backfill import backfill_image_ocr


def _make_image(path: Path, size=(800, 600), color=(200, 200, 200)):
    Image.new("RGB", size, color).save(path, "JPEG")


@pytest.fixture
def setup_md(tmp_path, monkeypatch):
    """构造 MinerU 产物：md 文本 + images 目录"""
    monkeypatch.setattr(image_ocr_backfill, "_get_token", lambda: "fake-token")
    images_dir = tmp_path / "doc_images"
    images_dir.mkdir()
    _make_image(images_dir / "receipt.jpg")
    _make_image(images_dir / "icon.jpg", size=(60, 60))  # 小图应跳过
    _make_image(images_dir / "dup.jpg")  # 与 receipt.jpg 同内容 → 去重
    md = ("正文开始\n\n![](./doc_images/receipt.jpg)\n\n"
          "中间文字\n\n![](./doc_images/icon.jpg)\n\n"
          "![](./doc_images/dup.jpg)\n\n结尾")
    return md, images_dir


def test_backfill_inserts_recognized_text(setup_md, monkeypatch):
    """识别文字回填到图片引用之后"""
    md, images_dir = setup_md

    async def fake_recognize(session, img_path, token, ssl):
        return "转账金额 50000元"

    monkeypatch.setattr(image_ocr_backfill, "_recognize_single_image", fake_recognize)
    result = asyncio.run(backfill_image_ocr(md, images_dir, "doc"))

    assert "转账金额 50000元" in result
    assert "📄 图片内容识别" in result
    assert result.index("receipt.jpg") < result.index("转账金额 50000元")


def test_small_image_skipped(setup_md, monkeypatch):
    """小图（图标/签名条）跳过不识别"""
    md, images_dir = setup_md
    calls = []

    async def fake_recognize(session, img_path, token, ssl):
        calls.append(img_path.name)
        return "识别内容"

    monkeypatch.setattr(image_ocr_backfill, "_recognize_single_image", fake_recognize)
    asyncio.run(backfill_image_ocr(md, images_dir, "doc"))

    assert "icon.jpg" not in calls, "小图不应提交识别"


def test_dedup_same_content(setup_md, monkeypatch):
    """同内容图片只识别一次，但两处引用都回填"""
    md, images_dir = setup_md
    calls = []

    async def fake_recognize(session, img_path, token, ssl):
        calls.append(img_path.name)
        return "重复内容文字"

    monkeypatch.setattr(image_ocr_backfill, "_recognize_single_image", fake_recognize)
    result = asyncio.run(backfill_image_ocr(md, images_dir, "doc"))

    assert calls.count("receipt.jpg") + calls.count("dup.jpg") == 1, "同内容只识别一次"
    assert result.count("重复内容文字") == 2, "两处引用都应回填"


def test_cache_avoids_recognize(setup_md, monkeypatch):
    """缓存命中：第二次运行不再调用识别"""
    md, images_dir = setup_md
    calls = []

    async def fake_recognize(session, img_path, token, ssl):
        calls.append(img_path.name)
        return "缓存内容"

    monkeypatch.setattr(image_ocr_backfill, "_recognize_single_image", fake_recognize)
    asyncio.run(backfill_image_ocr(md, images_dir, "doc"))
    first = len(calls)
    asyncio.run(backfill_image_ocr(md, images_dir, "doc"))
    assert len(calls) == first, "第二次应全部命中缓存"


def test_no_token_returns_unchanged(tmp_path, monkeypatch):
    """未配置 PaddleOCR Token：原样返回"""
    monkeypatch.setattr(image_ocr_backfill, "_get_token", lambda: "")
    result = asyncio.run(backfill_image_ocr("内容 ![](./doc_images/a.jpg)", Path("/nonexistent"), "doc"))
    assert "📄" not in result


def test_empty_recognition_no_insert(setup_md, monkeypatch):
    """识别无文字（纯照片）：不插入内容"""
    md, images_dir = setup_md

    async def fake_recognize(session, img_path, token, ssl):
        return ""

    monkeypatch.setattr(image_ocr_backfill, "_recognize_single_image", fake_recognize)
    result = asyncio.run(backfill_image_ocr(md, images_dir, "doc"))
    assert "📄 图片内容识别" not in result
