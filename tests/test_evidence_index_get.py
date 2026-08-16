"""evidence-index GET 只读：failed 字段在内存补齐，不回写磁盘

修复背景：get_evidence_index 原在补齐 failed 字段后回写 index.json，
与后台提取任务/摘要写回形成读-改-写竞态，可把刚完成的提取结果整体抹掉。
GET 必须只在内存中补齐返回，不产生任何写盘。
"""
import asyncio
import json
from pathlib import Path

import case_manager
from case_manager import get_evidence_index


def _make_case_with_legacy_index(tmp_path: Path) -> Path:
    """构造旧版 index.json（条目无 failed 字段）+ 对应证据 md 文件"""
    case_path = tmp_path / "案件_测试_20260816"
    evidence_dir = case_path / "evidence"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "001_张某某第一次讯问笔录.md").write_text(
        "# 张某某第一次讯问笔录\n\n## 详细摘要\n\n成功提取的正文", encoding="utf-8")
    index_data = {
        "case_id": "case_test",
        "total_evidence": 1,
        "evidence": [{
            "id": 1,
            "name": "张某某第一次讯问笔录",
            "type": "犯罪嫌疑人供述和辩解",
            "source": "第1卷_去水印.md",
            "md_file": "001_张某某第一次讯问笔录.md",
            # 旧版 index：无 failed 字段
        }],
        "case_charges": ["测试罪"],
    }
    (evidence_dir / "index.json").write_text(
        json.dumps(index_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return case_path


def test_get_evidence_index_fills_failed_in_memory_without_write_back(tmp_path, monkeypatch):
    """旧版 index 缺 failed 字段：返回数据内存补齐，但磁盘文件保持不变"""
    case_path = _make_case_with_legacy_index(tmp_path)
    index_file = case_path / "evidence" / "index.json"
    before = index_file.read_bytes()

    monkeypatch.setattr(case_manager, "find_case_path", lambda cid: case_path)
    result = asyncio.run(get_evidence_index("case_test"))

    # 返回数据在内存中补齐 failed（该证据文件内容成功 → False）
    assert result["evidence"][0]["failed"] is False
    # 关键断言：GET 不产生写盘（避免与提取/摘要写回的读-改-写竞态）
    assert index_file.read_bytes() == before
    assert not (case_path / "evidence" / "index.json.tmp").exists()
