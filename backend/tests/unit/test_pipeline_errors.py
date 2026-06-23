"""
流水线异常体系单元测试

测试目标：
1. PipelineError 基类 - 携带 filename/reason/recoverable，to_dict 序列化
2. PDFProcessingError/ConversionError/ExtractionError - reason→消息/可恢复映射
3. detail 拼接、未知 reason 回退 generic
4. 继承关系与 CancelRequested
"""

import pytest

from pipeline_errors import (
    CancelRequested,
    ConversionError,
    ExtractionError,
    PDFProcessingError,
    PipelineError,
)


class TestPipelineErrorBase:
    """基类行为"""

    def test_default_fields(self):
        err = PipelineError("出错了")
        assert err.filename == ""
        assert err.reason == ""
        assert err.recoverable is False
        assert str(err) == "出错了"

    def test_custom_fields(self):
        err = PipelineError("出错了", filename="a.pdf", reason="io_error", recoverable=True)
        assert err.filename == "a.pdf"
        assert err.reason == "io_error"
        assert err.recoverable is True

    def test_to_dict_serialization(self):
        err = PDFProcessingError("a.pdf", "timeout")
        d = err.to_dict()
        assert d["type"] == "PDFProcessingError"
        assert d["filename"] == "a.pdf"
        assert d["reason"] == "timeout"
        assert d["recoverable"] is True
        assert "超时" in d["message"]

    def test_is_exception_subclass(self):
        assert issubclass(PipelineError, Exception)


class TestPDFProcessingError:
    """PDF 处理异常 reason 映射"""

    @pytest.mark.parametrize("reason,expected_recoverable", [
        ("corrupt_stream", True),
        ("wrong_password", False),
        ("needs_password", True),
        ("no_quota", True),
        ("file_not_found", False),
        ("io_error", True),
        ("timeout", True),
        ("generic", True),
    ])
    def test_reason_recoverable_mapping(self, reason, expected_recoverable):
        err = PDFProcessingError("file.pdf", reason)
        assert err.reason == reason
        assert err.recoverable is expected_recoverable
        assert err.filename == "file.pdf"

    def test_unknown_reason_falls_back_to_generic(self):
        err = PDFProcessingError("file.pdf", "unknown_reason_xyz")
        assert err.reason == "unknown_reason_xyz"
        assert err.recoverable is True  # generic 可恢复
        assert "处理失败" in str(err)

    def test_detail_appended_to_message(self):
        err = PDFProcessingError("file.pdf", "wrong_password", detail="密码应为 6 位")
        assert "密码错误" in str(err)
        assert "密码应为 6 位" in str(err)

    def test_no_detail_no_colon(self):
        err = PDFProcessingError("file.pdf", "timeout")
        assert "：" not in str(err)


class TestConversionError:
    """转换异常"""

    @pytest.mark.parametrize("reason,expected_recoverable", [
        ("mineru_error", True),
        ("paddleocr_error", True),
        ("no_quota", True),
        ("file_not_found", False),
        ("timeout", True),
        ("generic", True),
    ])
    def test_reason_mapping(self, reason, expected_recoverable):
        err = ConversionError("file.pdf", reason)
        assert err.recoverable is expected_recoverable

    def test_unknown_reason_fallback(self):
        err = ConversionError("file.pdf", "weird")
        assert err.recoverable is True
        assert "转换失败" in str(err)


class TestExtractionError:
    """证据提取异常"""

    @pytest.mark.parametrize("reason,expected_recoverable", [
        ("llm_error", True),
        ("llm_retry_exhausted", True),
        ("parse_error", True),
        ("file_not_found", False),
        ("cancelled", False),
        ("generic", True),
    ])
    def test_reason_mapping(self, reason, expected_recoverable):
        err = ExtractionError("file.md", reason)
        assert err.recoverable is expected_recoverable

    def test_cancelled_not_recoverable(self):
        err = ExtractionError("file.md", "cancelled")
        assert err.recoverable is False
        assert "取消" in str(err)


class TestInheritanceAndCancel:
    """继承关系与取消异常"""

    def test_all_subclasses_pipeline_error(self):
        for cls in (PDFProcessingError, ConversionError, ExtractionError):
            assert issubclass(cls, PipelineError)

    def test_cancel_requested_is_exception(self):
        assert issubclass(CancelRequested, Exception)
        # CancelRequested 是独立异常，非 PipelineError
        assert not issubclass(CancelRequested, PipelineError)

    def test_can_be_raised_and_caught(self):
        with pytest.raises(PipelineError):
            raise PDFProcessingError("f.pdf", "timeout")
