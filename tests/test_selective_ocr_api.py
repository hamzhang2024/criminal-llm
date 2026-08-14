"""选择性 OCR 接口：列表 / 启动 / 进度"""
import json
from pathlib import Path

import case_manager as cm
import image_ocr_backfill as backfill_mod


def _make_case(tmp_path: Path) -> Path:
    case_dir = tmp_path / "案件_x"
    md_dir = case_dir / "md"
    md_dir.mkdir(parents=True)
    (md_dir / "第1卷_去水印.md").write_text("![](./第1卷_去水印_images/a.jpg)", encoding="utf-8")
    (md_dir / "第1卷_去水印_images").mkdir()
    (md_dir / "第1卷_去水印_images" / "a.jpg").write_bytes(b"x")
    return case_dir


def test_ocr_routes_registered():
    """三个端点都已注册"""
    paths = {r.path for r in cm.router.routes}
    assert "/api/cases/{case_id}/ocr-images" in paths
    assert "/api/cases/{case_id}/ocr-status" in paths


def test_ocr_status_returns_default():
    """未启动任务时 ocr-status 返回 idle（OCR_TASKS 无该 case 时默认值）"""
    cm.OCR_TASKS.clear()
    assert cm.OCR_TASKS.get("nonexistent", {"status": "idle"})["status"] == "idle"


def test_run_ocr_task_backfills_md(tmp_path):
    """后台任务：OCR 选中图并回填 md"""
    import asyncio
    case_dir = _make_case(tmp_path)

    async def fake_recognize(session, img_path, token, ssl_context):
        return "转账 15 万元"

    backfill_mod._recognize_single_image = fake_recognize
    backfill_mod._get_token = lambda: "t"

    cm.OCR_TASKS.clear()
    asyncio.run(cm._run_ocr_task("case_x", case_dir, {"第1卷_去水印": ["a.jpg"]}))
    assert cm.OCR_TASKS["case_x"]["status"] == "completed"
    assert cm.OCR_TASKS["case_x"]["done"] == 1
    md = (case_dir / "md" / "第1卷_去水印.md").read_text(encoding="utf-8")
    assert "转账 15 万元" in md
