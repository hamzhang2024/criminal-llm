"""serve-file / 缩略图缓存头：防 WKWebView 启发式缓存返回旋转前旧内容

Bug 背景：serve-file 响应无 Cache-Control，仅 Last-Modified + ETag。
WKWebView 启发式缓存（RFC 9111：freshness = 10% × (Date − Last-Modified)）
对「去水印后几小时才打开预览」的 PDF 给出十几分钟新鲜期——页面管理
旋转保存（saveIncr 已正确写盘）后退出再进，pdfjs 从缓存拿到旋转前的
旧字节，表现为「保存了但没生效」。
修复：响应加 Cache-Control: no-cache 禁用启发式缓存，每次都取最新内容。
（Starlette FileResponse 不处理条件请求，ETag 不会走 304 短路；
  本地 localhost 全量传输成本可忽略，正确性优先。）
"""
from pathlib import Path

import fitz
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import case_manager
from case_manager import router


@pytest.fixture()
def case_with_pdf(tmp_path, monkeypatch):
    """构造带 processed PDF 的临时案件，并把 find_case_path 指过去"""
    case_path = tmp_path / "案件_缓存测试_20260826"
    processed = case_path / "processed"
    processed.mkdir(parents=True)
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    doc.save(processed / "第1卷_去水印.pdf")
    doc.close()
    monkeypatch.setattr(case_manager, "find_case_path", lambda cid: case_path)
    return case_path


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_serve_file_pdf_has_no_cache_header(client, case_with_pdf):
    """serve-file 必须带 Cache-Control: no-cache（否则 WKWebView 启发式缓存旧 PDF）"""
    resp = client.get("/api/cases/case_test/serve-file",
                      params={"file_path": "第1卷_去水印.pdf", "dir": "processed"})
    assert resp.status_code == 200
    assert "no-cache" in resp.headers.get("cache-control", "")


def test_serve_file_keeps_etag_and_last_modified(client, case_with_pdf):
    """保留 ETag/Last-Modified 头（调试/未来条件请求支持所需，当前不作 304 断言）"""
    resp = client.get("/api/cases/case_test/serve-file",
                      params={"file_path": "第1卷_去水印.pdf", "dir": "processed"})
    assert resp.status_code == 200
    assert resp.headers.get("etag")
    assert resp.headers.get("last-modified")


def test_pdf_page_image_has_no_cache_header(client, case_with_pdf, tmp_path, monkeypatch):
    """放大查看的高清页图同样不能启发式缓存（旋转后旧图会被复用）"""
    import config
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    resp = client.get("/api/cases/case_test/pdf-page-image",
                      params={"file_path": "第1卷_去水印.pdf", "dir": "processed", "page": 1})
    assert resp.status_code == 200
    assert "no-cache" in resp.headers.get("cache-control", "")


def test_thumbnails_static_mount_has_no_cache_header(tmp_path, monkeypatch):
    """/thumbnails 静态挂载（阅读视图缩略图侧栏）同样要 no-cache

    旋转后旧缩略图 PNG 被删除重建，但 URL 不变——阅读模式侧栏的 <img>
    无 cache-buster，启发式缓存会展示旋转前的旧缩略图。
    """
    import config
    from fastapi.staticfiles import StaticFiles

    cache_dir = tmp_path / "cache"
    thumb_dir = cache_dir / "thumb" / "case_test" / "doc"
    thumb_dir.mkdir(parents=True)
    (thumb_dir / "page_1.png").write_bytes(b"\x89PNG\r\n\x1a\n")  # 最小 PNG 头即可
    monkeypatch.setattr(config, "CACHE_DIR", cache_dir)

    # 独立 app 复刻 main.py 的挂载方式，验证挂载处的响应头
    from main import app as main_app  # noqa: F401  (确保主 app 可导入)
    app = FastAPI()
    from main import NoCacheStaticFiles  # 修复后 main.py 应提供此类
    app.mount("/thumbnails", NoCacheStaticFiles(directory=cache_dir), name="thumbnails")
    resp = TestClient(app).get("/thumbnails/thumb/case_test/doc/page_1.png")
    assert resp.status_code == 200
    assert "no-cache" in resp.headers.get("cache-control", "")
