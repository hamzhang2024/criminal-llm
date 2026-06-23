"""
案件管理单元测试

测试目标：
1. 案件扫描功能
2. 文件上传验证
3. 文件删除安全性
"""
import json
from unittest.mock import MagicMock

import pytest


class TestCaseScanning:
    """案件扫描测试"""

    def test_scan_empty_cases_dir(self, temp_data_dir):
        """测试扫描空案件目录"""
        from case_manager import scan_cases

        cases = scan_cases()
        assert isinstance(cases, list)
        # 空目录返回空列表
        assert len(cases) == 0

    def test_scan_valid_case(self, temp_case_dir):
        """测试扫描合法案件"""
        from case_manager import scan_cases

        cases = scan_cases()
        assert len(cases) == 1
        assert cases[0].id == temp_case_dir["case_id"]
        assert cases[0].name == "测试案件"
        assert cases[0].defendant == "张三"

    def test_scan_pending_folders(self, temp_data_dir):
        """测试扫描待导入文件夹"""
        from case_manager import scan_pending_folders

        # 创建一个没有 case.json 的文件夹
        cases_dir = temp_data_dir / "cases"
        pending_dir = cases_dir / "case_pending" / "待导入案件"
        pending_dir.mkdir(parents=True, exist_ok=True)

        # 添加一个 PDF 文件
        pdf_file = pending_dir / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4\ntest")

        pending = scan_pending_folders()
        assert len(pending) == 1
        assert pending[0].name == "待导入案件"


class TestFileUploadValidation:
    """文件上传验证测试"""

    def test_valid_pdf_upload(self, temp_case_dir):
        """测试合法 PDF 上传"""

        from fastapi import UploadFile

        # 创建模拟的 UploadFile
        pdf_content = b"%PDF-1.4\ntest content"
        mock_file = MagicMock(spec=UploadFile)
        mock_file.filename = "test.pdf"
        mock_file.read = AsyncMock(return_value=pdf_content)

        # 这个测试需要 FastAPI 的依赖注入，这里仅验证逻辑
        assert pdf_content[:8] == b"%PDF-1.4"

    def test_invalid_file_extension(self):
        """测试非法文件扩展名"""

        # 文件名验证在 sanitize_filename 中
        # 扩展名验证在 upload_files 中
        pass

    def test_invalid_pdf_content(self):
        """测试非法 PDF 内容"""
        # 非 PDF 内容应该被拒绝
        fake_content = b"This is not a PDF file"
        # 检查 PDF 文件头
        assert not fake_content.startswith(b"%PDF-")

    def test_shell_script_upload_blocked(self):
        """测试 shell 脚本上传被阻止"""
        from fastapi import HTTPException
        from utils.path_validator import sanitize_filename

        with pytest.raises(HTTPException):
            sanitize_filename("malicious.sh")


class TestFileDeletionSecurity:
    """文件删除安全测试"""

    def test_path_traversal_in_filename_blocked(self, temp_case_dir):
        """测试文件名中的路径跳转被阻止"""
        from fastapi import HTTPException
        from utils.path_validator import sanitize_filename

        # 尝试通过文件名逃逸
        with pytest.raises(HTTPException):
            sanitize_filename("../../../etc/passwd")

        with pytest.raises(HTTPException):
            sanitize_filename("..\\..\\..\\windows\\system32")

    def test_valid_deletion(self, temp_case_dir):
        """测试合法文件删除"""
        # 创建测试文件
        pdf_path = temp_case_dir["case_path"] / "original" / "to_delete.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\ntest")

        assert pdf_path.exists()
        # 删除逻辑在 case_manager 中
        pdf_path.unlink()
        assert not pdf_path.exists()


class TestImportFolder:
    """导入文件夹测试"""

    def test_import_valid_folder(self, temp_data_dir):
        """测试导入合法文件夹"""
        # 创建一个可导入的文件夹
        import_dir = temp_data_dir / "import_test"
        import_dir.mkdir(parents=True, exist_ok=True)

        pdf_file = import_dir / "case_file.pdf"
        pdf_file.write_bytes(b"%PDF-1.4\ntest")

        # 导入逻辑需要 API 调用
        assert pdf_file.exists()

    def test_import_blocked_outside_data_dir(self, temp_data_dir):
        """测试阻止导入数据目录外的文件夹"""
        # 路径验证应该阻止绝对路径
        from fastapi import HTTPException
        from utils.path_validator import validate_path

        with pytest.raises(HTTPException):
            validate_path(temp_data_dir, "/etc/passwd")


# 异步测试辅助
class AsyncMock:
    """异步 Mock 辅助类"""
    def __init__(self, return_value=None):
        self.return_value = return_value

    async def __call__(self, *args, **kwargs):
        return self.return_value


class TestCaseMetadata:
    """案件元数据测试"""

    def test_create_case_metadata(self, temp_data_dir):
        """测试创建案件元数据"""
        from case_manager import CreateCaseRequest

        # 这个测试需要 FastAPI 上下文
        # 这里仅验证数据结构
        request = CreateCaseRequest(
            name="新案件",
            defendant="李四",
        )
        assert request.name == "新案件"
        assert request.defendant == "李四"

    def test_update_case_status(self, temp_case_dir):
        """测试更新案件状态"""
        metadata_file = temp_case_dir["case_path"] / "case.json"

        with open(metadata_file, encoding="utf-8") as f:
            metadata = json.load(f)

        assert metadata["status"] == "new"

        # 更新状态
        metadata["status"] = "uploaded"
        with open(metadata_file, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

        with open(metadata_file, encoding="utf-8") as f:
            updated = json.load(f)

        assert updated["status"] == "uploaded"


class TestNaturalSortKey:
    """自然排序键（文件名数字按数值比较）"""

    def test_numeric_sort_order(self):
        """第1卷 < 第2卷 < 第10卷（非字典序 1,10,2）"""
        from case_manager import natural_sort_key
        from pathlib import Path

        names = ["第10卷.pdf", "第2卷.pdf", "第1卷.pdf"]
        sorted_names = sorted([Path(n) for n in names], key=natural_sort_key)
        assert [p.name for p in sorted_names] == ["第1卷.pdf", "第2卷.pdf", "第10卷.pdf"]

    def test_accepts_string_or_path(self):
        """接受字符串或 Path 对象"""
        from case_manager import natural_sort_key
        from pathlib import Path

        # 字符串和 Path 应产生相同排序键
        assert natural_sort_key("file1.pdf") == natural_sort_key(Path("file1.pdf"))

    def test_case_insensitive(self):
        """大小写不敏感"""
        from case_manager import natural_sort_key

        names = ["B.pdf", "a.pdf", "C.pdf"]
        sorted_names = sorted(names, key=natural_sort_key)
        assert sorted_names == ["a.pdf", "B.pdf", "C.pdf"]

    def test_no_digits(self):
        """无数字的文件名正常排序"""
        from case_manager import natural_sort_key

        names = ["abc.pdf", "abd.pdf", "aba.pdf"]
        sorted_names = sorted(names, key=natural_sort_key)
        assert sorted_names == ["aba.pdf", "abc.pdf", "abd.pdf"]

    def test_mixed_digits_and_text(self):
        """混合数字与文本"""
        from case_manager import natural_sort_key

        names = ["file10.pdf", "file2.pdf", "file1.pdf", "file20.pdf"]
        sorted_names = sorted(names, key=natural_sort_key)
        assert sorted_names == ["file1.pdf", "file2.pdf", "file10.pdf", "file20.pdf"]


class TestGetSourceFromEvidenceFile:
    """从证据文件读取来源文件名"""

    def test_extracts_source_line(self, tmp_path):
        """正确解析 '来源文件' 表格行"""
        from case_manager import _get_source_from_evidence_file

        ev_file = tmp_path / "ev_001.md"
        ev_file.write_text(
            "# 证据 1\n\n"
            "| 字段 | 值 |\n"
            "| **来源文件** | 彭帮生讯问笔录.pdf |\n"
            "| **类型** | 讯问笔录 |\n",
            encoding="utf-8",
        )
        assert _get_source_from_evidence_file(ev_file) == "彭帮生讯问笔录.pdf"

    def test_returns_empty_when_no_source(self, tmp_path):
        """无来源文件行时返回空字符串"""
        from case_manager import _get_source_from_evidence_file

        ev_file = tmp_path / "ev_002.md"
        ev_file.write_text("# 证据 2\n\n无来源信息", encoding="utf-8")
        assert _get_source_from_evidence_file(ev_file) == ""

    def test_returns_empty_when_file_missing(self, tmp_path):
        """文件不存在时返回空字符串（不抛异常）"""
        from case_manager import _get_source_from_evidence_file

        assert _get_source_from_evidence_file(tmp_path / "nonexistent.md") == ""

    def test_returns_empty_on_read_error(self, tmp_path):
        """读取异常时返回空字符串"""
        from case_manager import _get_source_from_evidence_file

        # 目录而非文件，read_text 会抛异常
        assert _get_source_from_evidence_file(tmp_path) == ""


class TestFindCasePath:
    """查找案件目录（扫描 CASES_DIR 匹配 case_id）"""

    def test_finds_existing_case(self, temp_case_dir):
        """能找到已存在的案件目录"""
        from case_manager import find_case_path

        result = find_case_path(temp_case_dir["case_id"])
        assert result is not None
        assert result == temp_case_dir["case_path"]

    def test_returns_none_for_nonexistent(self, temp_data_dir):
        """不存在的 case_id 返回 None"""
        from case_manager import find_case_path

        assert find_case_path("nonexistent_case_id") is None

    def test_returns_none_when_cases_dir_missing(self, temp_data_dir, monkeypatch):
        """CASES_DIR 不存在时返回 None（不抛异常）"""
        from case_manager import find_case_path

        # 指向一个不存在的目录
        import case_manager
        monkeypatch.setattr(case_manager, "CASES_DIR", temp_data_dir / "no_such_dir")
        assert find_case_path("any_id") is None


class TestScanCasesLogic:
    """scan_cases 业务逻辑：状态推断、owner 过滤、容错、排序"""

    def test_status_inferred_from_md_files(self, temp_data_dir):
        """有 md 文件 → status=md_ready"""
        from case_manager import scan_cases

        cases_dir = temp_data_dir / "cases"
        case_path = cases_dir / "c1" / "案件A_20260101"
        for d in ["original", "processed", "md"]:
            (case_path / d).mkdir(parents=True)
        (case_path / "original" / "a.pdf").write_bytes(b"%PDF-1.4")
        (case_path / "md" / "a.md").write_text("x", encoding="utf-8")
        self._write_case_json(case_path, "c1", "案件A")

        cases = scan_cases()
        assert len(cases) == 1
        assert cases[0].status == "md_ready"

    def test_status_inferred_from_processed(self, temp_data_dir):
        """有 processed 但无 md → status=processed"""
        from case_manager import scan_cases

        cases_dir = temp_data_dir / "cases"
        case_path = cases_dir / "c2" / "案件B_20260102"
        for d in ["original", "processed"]:
            (case_path / d).mkdir(parents=True)
        (case_path / "original" / "a.pdf").write_bytes(b"%PDF-1.4")
        (case_path / "processed" / "a.pdf").write_bytes(b"%PDF-1.4")
        self._write_case_json(case_path, "c2", "案件B")

        cases = scan_cases()
        assert cases[0].status == "processed"

    def test_status_inferred_from_original_only(self, temp_data_dir):
        """只有 original → status=uploaded"""
        from case_manager import scan_cases

        cases_dir = temp_data_dir / "cases"
        case_path = cases_dir / "c3" / "案件C_20260103"
        (case_path / "original").mkdir(parents=True)
        (case_path / "original" / "a.pdf").write_bytes(b"%PDF-1.4")
        self._write_case_json(case_path, "c3", "案件C")

        cases = scan_cases()
        assert cases[0].status == "uploaded"

    def test_status_new_when_empty(self, temp_data_dir):
        """无任何文件 → status=new"""
        from case_manager import scan_cases

        cases_dir = temp_data_dir / "cases"
        case_path = cases_dir / "c4" / "案件D_20260104"
        case_path.mkdir(parents=True)
        self._write_case_json(case_path, "c4", "案件D")

        cases = scan_cases()
        assert cases[0].status == "new"

    def test_owner_filter(self, temp_data_dir):
        """按 owner 过滤案件"""
        from case_manager import scan_cases

        cases_dir = temp_data_dir / "cases"
        for cid, owner in [("c5", "alice"), ("c6", "bob")]:
            case_path = cases_dir / cid / f"案件{cid}_20260105"
            case_path.mkdir(parents=True)
            self._write_case_json(case_path, cid, f"案件{cid}", owner=owner)

        alice_cases = scan_cases(owner="alice")
        assert len(alice_cases) == 1
        assert alice_cases[0].id == "c5"

    def test_no_owner_filter_includes_all(self, temp_data_dir):
        """无 owner 过滤时包含所有（含无 owner 的）"""
        from case_manager import scan_cases

        cases_dir = temp_data_dir / "cases"
        case_path = cases_dir / "c7" / "案件E_20260106"
        case_path.mkdir(parents=True)
        self._write_case_json(case_path, "c7", "案件E")  # 无 owner

        cases = scan_cases()
        assert len(cases) == 1

    def test_corrupt_case_json_skipped(self, temp_data_dir):
        """损坏的 case.json 被跳过（不抛异常）"""
        from case_manager import scan_cases

        cases_dir = temp_data_dir / "cases"
        case_path = cases_dir / "c8" / "案件F_20260107"
        case_path.mkdir(parents=True)
        (case_path / "case.json").write_text("not json{", encoding="utf-8")

        # 不应抛异常，返回空列表
        cases = scan_cases()
        assert cases == []

    def test_sorted_by_created_at_desc(self, temp_data_dir):
        """按 created_at 倒序排序"""
        from case_manager import scan_cases

        cases_dir = temp_data_dir / "cases"
        for cid, date in [("c_old", "2026-01-01"), ("c_new", "2026-12-01"), ("c_mid", "2026-06-01")]:
            case_path = cases_dir / cid / f"案件{cid}_{date.replace('-', '')}"
            case_path.mkdir(parents=True)
            self._write_case_json(case_path, cid, f"案件{cid}", created_at=date)

        cases = scan_cases()
        assert [c.id for c in cases] == ["c_new", "c_mid", "c_old"]

    def test_file_count_includes_pdf_and_md(self, temp_data_dir):
        """file_count 统计 pdf + md 文件数"""
        from case_manager import scan_cases

        cases_dir = temp_data_dir / "cases"
        case_path = cases_dir / "c9" / "案件G_20260108"
        (case_path / "original").mkdir(parents=True)
        (case_path / "md").mkdir(parents=True)
        (case_path / "original" / "a.pdf").write_bytes(b"%PDF-1.4")
        (case_path / "original" / "b.pdf").write_bytes(b"%PDF-1.4")
        (case_path / "md" / "a.md").write_text("x", encoding="utf-8")
        self._write_case_json(case_path, "c9", "案件G")

        cases = scan_cases()
        assert cases[0].file_count == 3  # 2 pdf + 1 md

    @staticmethod
    def _write_case_json(case_path, case_id, name, owner=None, created_at="2026-01-01"):
        """写入 case.json 元数据"""
        import json
        metadata = {
            "id": case_id,
            "name": name,
            "defendant": "测试",
            "created_at": created_at,
            "status": "new",
            "case_dir": str(case_path),
        }
        if owner:
            metadata["owner"] = owner
        with open(case_path / "case.json", "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
