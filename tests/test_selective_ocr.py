"""选择性 OCR：预筛 + 指定图片列表回填"""
import asyncio
import json
from pathlib import Path

import image_ocr_backfill as mod
from image_ocr_backfill import preselect_ocr_images, backfill_image_ocr, MIN_DIMENSION


def _make_layout(tmp_path: Path, name: str, image_blocks: list) -> Path:
    """构造 mock layout.json（位于 md/ 下），image_blocks 为 [{sub_type, image_path, bbox}]"""
    md_dir = tmp_path / "md"
    md_dir.mkdir(parents=True, exist_ok=True)
    layout = md_dir / f"{name}_layout.json"
    pages = []
    for i, blk in enumerate(image_blocks):
        pages.append({"page_idx": i, "para_blocks": [{
            "type": "image",
            "sub_type": blk.get("sub_type", "?"),
            "bbox": blk["bbox"],
            "blocks": [{"lines": [{"spans": [{"type": "image", "image_path": blk["image_path"]}]}]}],
        }]})
    layout.write_text(json.dumps({"pdf_info": pages}, ensure_ascii=False), encoding="utf-8")
    return layout


def test_preselect_excludes_seal_and_small(tmp_path):
    """预筛：排除 seal 印章 + 小图，保留 ? 类型"""
    _make_layout(tmp_path, "第1卷_去水印", [
        {"sub_type": "seal", "image_path": "seal1.jpg", "bbox": [0, 0, 200, 200]},
        {"sub_type": "?", "image_path": "big.jpg", "bbox": [0, 0, 300, 300]},
        {"sub_type": "?", "image_path": "small.jpg", "bbox": [0, 0, 80, 80]},  # 小图
        {"sub_type": "?", "image_path": "keep.jpg", "bbox": [0, 0, 250, 400]},
    ])
    result = preselect_ocr_images(tmp_path)
    assert "第1卷_去水印" in result
    names = list(result["第1卷_去水印"].keys())
    assert "big.jpg" in names and "keep.jpg" in names
    assert "seal1.jpg" not in names and "small.jpg" not in names


def test_preselect_dedups_and_handles_missing_layout(tmp_path):
    """预筛：图片去重 + layout 缺失不报错"""
    _make_layout(tmp_path, "第2卷_去水印", [
        {"sub_type": "?", "image_path": "dup.jpg", "bbox": [0, 0, 300, 300]},
        {"sub_type": "?", "image_path": "dup.jpg", "bbox": [10, 10, 300, 300]},  # 重复
    ])
    # 一个损坏的 layout 文件不影响
    (tmp_path / "md" / "第3卷_去水印_layout.json").write_text("损坏的 json", encoding="utf-8")
    result = preselect_ocr_images(tmp_path)
    assert len(result["第2卷_去水印"]) == 1  # dup.jpg 去重
    assert "第3卷_去水印" not in result  # 损坏的跳过


def test_backfill_only_names(tmp_path, monkeypatch):
    """回填：only_names 只识别指定图片"""
    md_text = "![](./第1卷_去水印_images/a.jpg)\n\n![](./第1卷_去水印_images/b.jpg)"
    images_dir = tmp_path / "第1卷_去水印_images"
    images_dir.mkdir()
    (images_dir / "a.jpg").write_bytes(b"a")
    (images_dir / "b.jpg").write_bytes(b"b")
    recognized = []

    async def fake_recognize(session, img_path, token, ssl_context):
        recognized.append(Path(img_path).name)
        return "识别文字"

    monkeypatch.setattr(mod, "_recognize_single_image", fake_recognize)
    monkeypatch.setattr(mod, "_get_token", lambda: "fake-token")
    result = asyncio.run(backfill_image_ocr(
        md_text, images_dir, "第1卷_去水印", only_names={"a.jpg"}))
    assert "识别文字" in result
    assert recognized == ["a.jpg"]  # 只识别 a.jpg，b.jpg 跳过


def test_backfill_only_names_does_not_leak_old_cache(tmp_path, monkeypatch):
    """选择性回填不泄漏未选中图片的旧缓存文字"""
    md_text = "![](./第1卷_去水印_images/a.jpg)\n\n![](./第1卷_去水印_images/b.jpg)"
    images_dir = tmp_path / "第1卷_去水印_images"
    images_dir.mkdir()
    (images_dir / "a.jpg").write_bytes(b"a")
    (images_dir / "b.jpg").write_bytes(b"b")
    # 预置 _ocr.json：b.jpg 有旧文字，a.jpg 无
    (images_dir / "_ocr.json").write_text(
        json.dumps({"b.jpg": {"size": 1, "text": "旧文字不应出现"}}, ensure_ascii=False),
        encoding="utf-8")

    async def fake_recognize(session, img_path, token, ssl_context):
        return "a的新文字"

    monkeypatch.setattr(mod, "_recognize_single_image", fake_recognize)
    monkeypatch.setattr(mod, "_get_token", lambda: "t")
    result = asyncio.run(backfill_image_ocr(
        md_text, images_dir, "第1卷_去水印", only_names={"a.jpg"}))
    assert "a的新文字" in result
    assert "旧文字不应出现" not in result  # b.jpg 旧缓存不泄漏
