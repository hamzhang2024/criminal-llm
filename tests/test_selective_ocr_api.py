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


def test_run_ocr_task_isolates_volume_failure(tmp_path):
    """单卷失败：记录 failed，不中断后续卷"""
    import asyncio
    case_dir = tmp_path / "案件_y"
    md_dir = case_dir / "md"
    md_dir.mkdir(parents=True)
    # 卷1：md 存在；卷2：md 不存在（会被跳过进 failed）；卷3：正常
    for vol, names in [("第1卷_去水印", ["a.jpg"]), ("第3卷_去水印", ["c.jpg"])]:
        (md_dir / f"{vol}.md").write_text(f"![](./{vol}_images/{names[0]})", encoding="utf-8")
        (md_dir / f"{vol}_images").mkdir()
        (md_dir / f"{vol}_images" / names[0]).write_bytes(b"x")

    async def fake_recognize(session, img_path, token, ssl_context):
        return "识别文字"

    backfill_mod._recognize_single_image = fake_recognize
    backfill_mod._get_token = lambda: "t"

    cm.OCR_TASKS.clear()
    asyncio.run(cm._run_ocr_task("case_y", case_dir, {
        "第1卷_去水印": ["a.jpg"],
        "第2卷_去水印": ["b.jpg"],  # md 不存在 → failed
        "第3卷_去水印": ["c.jpg"],
    }))
    task = cm.OCR_TASKS["case_y"]
    assert task["status"] == "completed"
    assert "第2卷_去水印" in task["failed"]  # 跳过卷进 failed
    assert task["done"] == 2  # 卷1 + 卷3 成功，卷2 未计入
    # 卷3 仍被处理（未被卷2 失败中断）
    assert "识别文字" in (md_dir / "第3卷_去水印.md").read_text(encoding="utf-8")


def test_run_ocr_task_failed_volume_not_counted(tmp_path):
    """OCR 异常卷：进 failed、done 不累加"""
    import asyncio
    case_dir = tmp_path / "案件_z"
    md_dir = case_dir / "md"
    md_dir.mkdir(parents=True)
    (md_dir / "第1卷_去水印.md").write_text("![](./第1卷_去水印_images/a.jpg)", encoding="utf-8")
    (md_dir / "第1卷_去水印_images").mkdir()
    (md_dir / "第1卷_去水印_images" / "a.jpg").write_bytes(b"x")

    async def raising_recognize(session, img_path, token, ssl_context):
        raise RuntimeError("识别崩溃")

    backfill_mod._recognize_single_image = raising_recognize
    backfill_mod._get_token = lambda: "t"

    cm.OCR_TASKS.clear()
    asyncio.run(cm._run_ocr_task("case_z", case_dir, {"第1卷_去水印": ["a.jpg"]}))
    task = cm.OCR_TASKS["case_z"]
    assert task["status"] == "completed"
    assert task["done"] == 0  # 失败卷不计入 done
    assert "第1卷_去水印" in task["failed"]
