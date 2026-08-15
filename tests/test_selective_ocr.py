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


def test_mineru_convert_has_no_auto_backfill():
    """MinerU 转换路径不再调用 backfill_image_ocr（由选择性 OCR 取代）"""
    import mineru_async
    import inspect
    src = inspect.getsource(mineru_async)
    assert "backfill_image_ocr" not in src


def test_pdf_to_md_has_no_auto_backfill():
    """同步转换路径不再调用 backfill_image_ocr"""
    import pdf_to_md
    import inspect
    src = inspect.getsource(pdf_to_md)
    assert "backfill_image_ocr" not in src


def test_preselect_remaps_chunk_volumes_to_merged(tmp_path):
    """分块卷（__c1/__c181）重映射到合并卷：第12卷_去水印__c1 → 第12卷_去水印

    MinerU 分块转换遗留 {卷}__c{N}_layout.json，其图片已合并进 {卷}_images/。
    预筛应把它们归并到合并卷名下，否则按分块卷 OCR 时找不到 md/图片目录。
    """
    # 分块布局（合并后的卷没有自己的 layout.json）
    _make_layout(tmp_path, "第12卷_去水印__c1", [
        {"sub_type": "?", "image_path": "c1_img.jpg", "bbox": [0, 0, 300, 300]},
    ])
    _make_layout(tmp_path, "第12卷_去水印__c181", [
        {"sub_type": "?", "image_path": "c181_img.jpg", "bbox": [0, 0, 300, 300]},
    ])
    # 合并卷 md 已生成（转换完成）
    (tmp_path / "md" / "第12卷_去水印.md").write_text("内容", encoding="utf-8")
    (tmp_path / "md" / "第12卷_去水印_images").mkdir()

    result = preselect_ocr_images(tmp_path)
    # 不应出现分块卷名
    assert "第12卷_去水印__c1" not in result
    assert "第12卷_去水印__c181" not in result
    # 应归并到合并卷，含两分块的图片
    assert "第12卷_去水印" in result
    names = result["第12卷_去水印"]
    assert "c1_img.jpg" in names and "c181_img.jpg" in names


def test_preselect_skips_chunk_when_merged_md_missing(tmp_path):
    """分块卷但合并 md 不存在（转换中断）：跳过，不列出无法回填的卷"""
    _make_layout(tmp_path, "第3卷_去水印__c1", [
        {"sub_type": "?", "image_path": "x.jpg", "bbox": [0, 0, 300, 300]},
    ])
    result = preselect_ocr_images(tmp_path)
    assert "第3卷_去水印__c1" not in result
    assert "第3卷_去水印" not in result


def test_recognize_retries_on_429(tmp_path, monkeypatch):
    """单图识别遇 HTTP 429 限流：退避重试后成功"""
    calls = {"post": 0}

    class FakeResp:
        def __init__(self, status, payload=None, text=""):
            self.status = status
            self._payload = payload or {}
            self._text = text

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def json(self):
            return self._payload

        async def text(self):
            return self._text

    class FakeSession:
        def post(self, *a, **kw):
            calls["post"] += 1
            if calls["post"] <= 2:
                return FakeResp(429)  # 前两次限流
            return FakeResp(200, {"data": {"jobId": "j1"}})

        def get(self, url, **kw):
            if url.endswith("/j1"):
                return FakeResp(200, {"data": {"state": "done", "resultUrl": {"jsonUrl": "http://x/result"}}})
            # 结果下载
            return FakeResp(200, text='{"result": {"layoutParsingResults": [{"markdown": {"text": "转账 15 万元"}}]}}')

    # 退避时间设为 0，避免测试等待
    monkeypatch.setattr(mod, "RATE_LIMIT_BACKOFF", [0, 0, 0])
    img = tmp_path / "x.jpg"
    img.write_bytes(b"x")
    text = asyncio.run(mod._recognize_single_image(FakeSession(), img, "token", None))
    assert calls["post"] == 3  # 429,429,200
    assert "转账 15 万元" in text


def test_backfill_does_not_cache_failed_images(tmp_path, monkeypatch):
    """识别失败（空文字）的图片不写缓存，下次重跑可重试（防 429 失败永久锁死）"""
    md_text = "![](./第1卷_去水印_images/a.jpg)"
    images_dir = tmp_path / "第1卷_去水印_images"
    images_dir.mkdir()
    (images_dir / "a.jpg").write_bytes(b"a")

    async def fake_recognize_fail(session, img_path, token, ssl_context):
        return ""  # 429 失败返回空

    monkeypatch.setattr(mod, "_recognize_single_image", fake_recognize_fail)
    monkeypatch.setattr(mod, "_get_token", lambda: "t")
    asyncio.run(backfill_image_ocr(md_text, images_dir, "第1卷_去水印"))

    cache_path = images_dir / "_ocr.json"
    if cache_path.exists():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        # 失败图片（空文字）不得写入缓存，否则下次重跑被跳过、永远无法重试
        assert "a.jpg" not in cache
    # 不存在缓存文件也符合预期
