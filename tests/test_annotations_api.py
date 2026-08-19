"""PDF 批注持久化接口 + 单页重转备份：读写 / 迁移兼容 / 修复前备份"""
import asyncio
import json
from pathlib import Path

import case_manager as cm


def _make_case(tmp_path: Path) -> Path:
    case_dir = tmp_path / "案件_批注测试"
    (case_dir / "md").mkdir(parents=True)
    (case_dir / "processed").mkdir()
    return case_dir


def _patch_case_path(monkeypatch, case_dir: Path):
    monkeypatch.setattr(cm, "find_case_path", lambda _cid: case_dir)


def test_annotations_routes_registered():
    """GET/PUT annotations 端点已注册"""
    paths = {(r.path, tuple(sorted(r.methods))) for r in cm.router.routes}
    assert ("/api/cases/{case_id}/annotations", ("GET",)) in paths
    assert ("/api/cases/{case_id}/annotations", ("PUT",)) in paths


def test_get_annotations_empty(tmp_path, monkeypatch):
    """无 annotations.json 时返回空结构"""
    case_dir = _make_case(tmp_path)
    _patch_case_path(monkeypatch, case_dir)
    result = asyncio.run(cm.get_annotations("case_x"))
    assert result == {"version": 1, "annotations": []}


def test_put_then_get_roundtrip(tmp_path, monkeypatch):
    """PUT 全量写入后 GET 读回一致（含 rect 类型字段）"""
    case_dir = _make_case(tmp_path)
    _patch_case_path(monkeypatch, case_dir)
    payload = cm.AnnotationsPayload(annotations=[
        {"id": "a1", "type": "note", "pageNum": 3, "x": 12.5, "y": 30.2,
         "text": "关键供述", "color": "#fff9c4", "pdfFile": "第1卷.pdf",
         "createdAt": "2026-08-19T10:00:00"},
        {"id": "a2", "type": "rect", "pageNum": 3, "x": 10, "y": 10,
         "rect": {"x": 10, "y": 10, "w": 40, "h": 20},
         "text": "", "color": "#ffeb3b", "pdfFile": "第1卷.pdf",
         "createdAt": "2026-08-19T10:01:00"},
    ])
    result = asyncio.run(cm.put_annotations("case_x", payload))
    assert result == {"success": True, "count": 2}

    # 原子写落盘
    f = case_dir / "annotations.json"
    assert f.exists()
    on_disk = json.loads(f.read_text(encoding="utf-8"))
    assert on_disk["annotations"][1]["rect"]["w"] == 40

    got = asyncio.run(cm.get_annotations("case_x"))
    assert len(got["annotations"]) == 2
    assert got["annotations"][0]["text"] == "关键供述"


def test_get_annotations_corrupt_file(tmp_path, monkeypatch):
    """annotations.json 损坏时降级返回空结构，不抛异常"""
    case_dir = _make_case(tmp_path)
    _patch_case_path(monkeypatch, case_dir)
    (case_dir / "annotations.json").write_text("{损坏的json", encoding="utf-8")
    result = asyncio.run(cm.get_annotations("case_x"))
    assert result == {"version": 1, "annotations": []}


def test_reconvert_creates_fix_backup(tmp_path, monkeypatch):
    """单页重转前自动备份原 md 为 .fix-bak（防填错页码无法回滚）"""
    case_dir = _make_case(tmp_path)
    _patch_case_path(monkeypatch, case_dir)
    md = case_dir / "md" / "第1卷.md"
    original = "第一行\n<table>乱码</table>\n第三行\n"
    md.write_text(original, encoding="utf-8")
    pdf = case_dir / "processed" / "第1卷.pdf"
    pdf.write_bytes(b"%PDF-fake")

    # 替身：单页抽取直接产出占位 PDF；MinerU 转换直接产出替换文本
    import page_rotation
    monkeypatch.setattr(
        page_rotation, "extract_single_page",
        lambda src, page, out: Path(out).write_bytes(b"%PDF-page") or Path(out),
    )

    class FakeResult:
        success = True

    class FakeConverter:
        async def convert_batch(self, files, out_dir, max_concurrent=1):
            (Path(out_dir) / "page.md").write_text("替换后的正确文字", encoding="utf-8")
            return [FakeResult()]

    import mineru_async
    monkeypatch.setattr(mineru_async, "AsyncMinerUConverter", FakeConverter)

    req = cm.ReconvertBlockRequest(
        file_path="第1卷.pdf", page=5, md_file="第1卷.md",
        start_line=1, end_line=1, invalidate_evidence=False,
    )
    result = asyncio.run(cm.reconvert_block("case_x", req))
    assert result["success"] is True

    # 备份内容为修复前的原文
    backup = case_dir / "md" / "第1卷.md.fix-bak"
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == original
    # md 已替换为新文本
    assert "替换后的正确文字" in md.read_text(encoding="utf-8")
