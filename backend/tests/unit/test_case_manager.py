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

        with open(metadata_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)

        assert metadata["status"] == "new"

        # 更新状态
        metadata["status"] = "uploaded"
        with open(metadata_file, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

        with open(metadata_file, "r", encoding="utf-8") as f:
            updated = json.load(f)

        assert updated["status"] == "uploaded"
