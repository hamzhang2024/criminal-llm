"""
ZIP 安全解压单元测试

测试目标：
1. safe_extractall - 路径穿越（Zip Slip）防护
2. safe_extractall - Zip Bomb 防护（累计体积/单条目体积）
3. safe_extract_zip - 文件体积预校验
4. 正常 ZIP 解压成功

安全属性：含 ../ 的条目必须被拒绝，不得写入目标目录外。
"""

import io
import zipfile
from pathlib import Path

import pytest

from utils.zip_safe import (
    MAX_EXTRACTED_SIZE,
    MAX_MEMBER_SIZE,
    MAX_ZIP_SIZE,
    safe_extractall,
    safe_extract_zip,
)


def _make_zip(entries: dict[str, bytes]) -> bytes:
    """构造内存 ZIP，entries 为 {文件名: 内容}。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


class TestNormalExtraction:
    """正常解压"""

    def test_extract_simple_files(self, tmp_path):
        target = tmp_path / "out"
        target.mkdir()
        zip_bytes = _make_zip({"a.txt": "hello", "b.txt": "world"})
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            safe_extractall(zf, target)
        assert (target / "a.txt").read_text() == "hello"
        assert (target / "b.txt").read_text() == "world"

    def test_extract_nested_dirs(self, tmp_path):
        target = tmp_path / "out"
        target.mkdir()
        zip_bytes = _make_zip({"dir/sub.txt": "nested"})
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            safe_extractall(zf, target)
        assert (target / "dir" / "sub.txt").read_text() == "nested"

    def test_empty_zip(self, tmp_path):
        target = tmp_path / "out"
        target.mkdir()
        zip_bytes = _make_zip({})
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            safe_extractall(zf, target)
        # 空 ZIP 不报错，目录内无文件
        assert list(target.iterdir()) == []


class TestZipSlipProtection:
    """路径穿越防护（安全关键）"""

    def test_dotdot_path_rejected(self, tmp_path):
        """含 ../ 的条目必须拒绝，不得写出"""
        target = tmp_path / "out"
        target.mkdir()
        # 构造恶意条目：试图写到目标目录上层
        zip_bytes = _make_zip({"../evil.txt": "malicious"})
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            with pytest.raises(ValueError, match="路径穿越"):
                safe_extractall(zf, target)
        # 确认恶意文件未被写出
        assert not (tmp_path / "evil.txt").exists()

    def test_absolute_path_rejected(self, tmp_path):
        """绝对路径条目应被拒绝（resolve 后不在 target 内）"""
        target = tmp_path / "out"
        target.mkdir()
        zip_bytes = _make_zip({"/etc/passwd": "x"})
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            with pytest.raises(ValueError):
                safe_extractall(zf, target)

    def test_deep_dotdot_rejected(self, tmp_path):
        """多层 ../ 穿越也应拒绝"""
        target = tmp_path / "out"
        target.mkdir()
        zip_bytes = _make_zip({"../../etc/evil": "x"})
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            with pytest.raises(ValueError, match="路径穿越"):
                safe_extractall(zf, target)


class TestZipBombProtection:
    """Zip Bomb 防护"""

    def test_oversize_single_member_rejected(self, tmp_path):
        """单条目超 MAX_MEMBER_SIZE 拒绝（校验在 extract 前，用 mock info 触发）"""
        from unittest.mock import MagicMock

        target = tmp_path / "out"
        target.mkdir()
        # 构造 mock ZipFile，其 infolist 返回一个 file_size 超限的假条目
        mock_zf = MagicMock()
        big_info = MagicMock()
        big_info.filename = "big.bin"
        big_info.file_size = MAX_MEMBER_SIZE + 1
        mock_zf.infolist.return_value = [big_info]
        with pytest.raises(ValueError, match="条目过大"):
            safe_extractall(mock_zf, target)
        # 校验在 extract 前，不应调用 extract
        mock_zf.extract.assert_not_called()

    def test_total_size_rejected(self, tmp_path):
        """累计解压体积超 MAX_EXTRACTED_SIZE 拒绝（多个小条目累计超限）"""
        from unittest.mock import MagicMock

        target = tmp_path / "out"
        target.mkdir()
        mock_zf = MagicMock()
        # 每个条目 < MAX_MEMBER_SIZE，但累计 > MAX_EXTRACTED_SIZE
        # 用 MAX_MEMBER_SIZE 作为单条目大小，6 个即超 1GB（6*200MB=1.2GB）
        infos = []
        for i in range(6):
            info = MagicMock()
            info.filename = f"f{i}.bin"
            info.file_size = MAX_MEMBER_SIZE  # 200MB，单条目合法
            infos.append(info)
        mock_zf.infolist.return_value = infos
        with pytest.raises(ValueError, match="总量超过上限"):
            safe_extractall(mock_zf, target)


class TestSafeExtractZip:
    """文件级解压（含体积预校验）"""

    def test_normal_file_extract(self, tmp_path):
        target = tmp_path / "out"
        target.mkdir()
        zip_path = tmp_path / "test.zip"
        zip_path.write_bytes(_make_zip({"a.txt": "hi"}))
        safe_extract_zip(zip_path, target)
        assert (target / "a.txt").read_text() == "hi"

    def test_oversize_zip_file_rejected(self, tmp_path):
        """ZIP 文件本身超 MAX_ZIP_SIZE 拒绝（预校验）"""
        target = tmp_path / "out"
        target.mkdir()
        zip_path = tmp_path / "big.zip"
        # 写一个超限的假 ZIP（内容无需合法，预校验在打开前）
        zip_path.write_bytes(b"x" * (MAX_ZIP_SIZE + 1))
        with pytest.raises(ValueError, match="文件过大"):
            safe_extract_zip(zip_path, target)
