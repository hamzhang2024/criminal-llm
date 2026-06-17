"""
路径验证工具单元测试

测试目标：
1. sanitize_filename - 文件名验证
2. validate_path - 路径遍历防护
"""

import pytest
from fastapi import HTTPException
from utils.path_validator import sanitize_filename, validate_path


class TestSanitizeFilename:
    """文件名验证测试"""

    def test_valid_filename_simple(self):
        """测试合法的简单文件名"""
        assert sanitize_filename("test.pdf") == "test.pdf"
        assert sanitize_filename("测试文件.pdf") == "测试文件.pdf"
        assert sanitize_filename("file_123.pdf") == "file_123.pdf"
        assert sanitize_filename("file-name.pdf") == "file-name.pdf"

    def test_valid_filename_chinese(self):
        """测试包含中文的文件名"""
        assert sanitize_filename("张三讯问笔录.pdf") == "张三讯问笔录.pdf"
        assert sanitize_filename("案件材料_001.pdf") == "案件材料_001.pdf"

    def test_empty_filename(self):
        """测试空文件名"""
        with pytest.raises(HTTPException) as exc_info:
            sanitize_filename("")
        assert exc_info.value.status_code == 400
        assert "不能为空" in exc_info.value.detail

    def test_path_traversal_attempt(self):
        """测试路径跳转攻击"""
        with pytest.raises(HTTPException) as exc_info:
            sanitize_filename("../../../etc/passwd")
        assert exc_info.value.status_code == 400
        assert "非法字符" in exc_info.value.detail

    def test_shell_injection_attempt(self):
        """测试 shell 注入攻击"""
        dangerous_names = [
            "file;rm -rf.pdf",
            "file|cat.pdf",
            "file&echo.pdf",
            "file`whoami`.pdf",
            "file$(id).pdf",
        ]
        for name in dangerous_names:
            with pytest.raises(HTTPException) as exc_info:
                sanitize_filename(name)
            assert exc_info.value.status_code == 400

    def test_path_separators(self):
        """测试路径分隔符"""
        with pytest.raises(HTTPException):
            sanitize_filename("path/to/file.pdf")
        with pytest.raises(HTTPException):
            sanitize_filename("path\\to\\file.pdf")


class TestValidatePath:
    """路径验证测试"""

    @pytest.fixture
    def base_dir(self, tmp_path):
        """创建基准目录"""
        base = tmp_path / "safe_dir"
        base.mkdir(parents=True, exist_ok=True)
        return base

    def test_valid_relative_path(self, base_dir):
        """测试合法的相对路径"""
        result = validate_path(base_dir, "test.pdf")
        assert result == base_dir / "test.pdf"

    def test_valid_nested_path(self, base_dir):
        """测试合法的嵌套路径"""
        nested_dir = base_dir / "subdir"
        nested_dir.mkdir(exist_ok=True)
        result = validate_path(base_dir, "subdir/file.pdf")
        assert result == nested_dir / "file.pdf"

    def test_empty_path(self, base_dir):
        """测试空路径"""
        with pytest.raises(HTTPException) as exc_info:
            validate_path(base_dir, "")
        assert exc_info.value.status_code == 400
        assert "不能为空" in exc_info.value.detail

    def test_absolute_path_blocked(self, base_dir):
        """测试绝对路径被阻止"""
        with pytest.raises(HTTPException) as exc_info:
            validate_path(base_dir, "/etc/passwd")
        assert exc_info.value.status_code == 400
        assert "绝对路径" in exc_info.value.detail

    def test_path_traversal_blocked(self, base_dir):
        """测试路径跳转被阻止"""
        with pytest.raises(HTTPException) as exc_info:
            validate_path(base_dir, "../../../etc/passwd")
        assert exc_info.value.status_code == 400
        assert "跳转" in exc_info.value.detail

    def test_path_traversal_encoded_blocked(self, base_dir):
        """测试编码的路径跳转被阻止"""
        with pytest.raises(HTTPException):
            validate_path(base_dir, "..%2F..%2F..%2Fetc%2Fpasswd")

    def test_shell_metacharacters_blocked(self, base_dir):
        """测试 shell 元字符被阻止"""
        dangerous_paths = [
            "file;cat /etc/passwd",
            "file|whoami",
            "file&id",
            "file`pwd`",
        ]
        for path in dangerous_paths:
            with pytest.raises(HTTPException) as exc_info:
                validate_path(base_dir, path)
            assert exc_info.value.status_code == 400

    def test_path_escape_with_dots(self, base_dir):
        """测试使用点号逃逸"""
        with pytest.raises(HTTPException):
            validate_path(base_dir, "subdir/../../../etc/passwd")

    def test_valid_chinese_path(self, base_dir):
        """测试包含中文的合法路径"""
        result = validate_path(base_dir, "测试文件.pdf")
        assert result == base_dir / "测试文件.pdf"

    def test_symlink_escape_blocked(self, base_dir):
        """测试符号链接逃逸"""
        # 创建一个指向上级目录的符号链接
        link_path = base_dir / "escape_link"
        try:
            link_path.symlink_to(base_dir.parent)
        except OSError:
            # 符号链接创建可能失败，跳过此测试
            pytest.skip("无法创建符号链接")

        # 尝试通过符号链接逃逸
        with pytest.raises(HTTPException):
            validate_path(base_dir, "escape_link/../../../etc/passwd")


class TestEdgeCases:
    """边界情况测试"""

    @pytest.fixture
    def base_dir(self, tmp_path):
        base = tmp_path / "test_dir"
        base.mkdir(parents=True, exist_ok=True)
        return base

    def test_unicode_filename(self, base_dir):
        """测试 Unicode 文件名"""
        # 合法的 Unicode 字符（中文、日文、韩文）
        assert sanitize_filename("日本語.pdf") == "日本語.pdf"
        assert sanitize_filename("한국어.pdf") == "한국어.pdf"

    def test_long_filename(self, base_dir):
        """测试长文件名"""
        long_name = "很长的文件名" * 50 + ".pdf"
        # 长文件名本身是合法的
        result = validate_path(base_dir, long_name)
        assert long_name in str(result)

    def test_special_extensions(self, base_dir):
        """测试特殊扩展名"""
        # 合法文件名但不同扩展名
        assert sanitize_filename("file.txt") == "file.txt"
        assert sanitize_filename("file.doc") == "file.doc"

    def test_multiple_dots(self, base_dir):
        """测试多个点的文件名"""
        assert sanitize_filename("file.name.test.pdf") == "file.name.test.pdf"

    def test_whitespace_filename(self, base_dir):
        """测试包含空格的文件名"""
        # 空格会被正则匹配（\w 不包含空格，但实际业务可能允许）
        # 根据当前实现，空格会触发非法字符错误
        with pytest.raises(HTTPException):
            sanitize_filename("file name.pdf")
