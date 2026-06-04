"""
结构化异常体系

每个异常携带 filename、reason、recoverable 标记，
便于前端根据错误类型展示不同的纠错选项。
"""


class PipelineError(Exception):
    """流水线处理基类异常"""

    def __init__(self, message: str, filename: str = "", reason: str = "", recoverable: bool = False):
        self.filename = filename
        self.reason = reason
        self.recoverable = recoverable
        super().__init__(message)

    def to_dict(self) -> dict:
        return {
            "type": self.__class__.__name__,
            "message": str(self),
            "filename": self.filename,
            "reason": self.reason,
            "recoverable": self.recoverable,
        }


class PDFProcessingError(PipelineError):
    """PDF 处理失败（去水印、密码解除）"""

    REASONS = {
        "corrupt_stream": ("PDF 文件损坏，压缩流无法解压", True),
        "wrong_password": ("密码错误，无法解密 PDF", False),
        "needs_password": ("PDF 已加密，需要提供密码", True),
        "no_quota": ("OCR 配额已用完，请稍后重试", True),
        "file_not_found": ("文件不存在", False),
        "io_error": ("文件读写失败", True),
        "timeout": ("处理超时，请稍后重试", True),
        "generic": ("处理失败", True),
    }

    def __init__(self, filename: str, reason: str, detail: str = ""):
        msg_template = self.REASONS.get(reason, self.REASONS["generic"])
        message = msg_template[0]
        recoverable = msg_template[1]
        if detail:
            message = f"{message}：{detail}"
        super().__init__(message, filename, reason, recoverable)


class ConversionError(PipelineError):
    """PDF 转 MD 失败"""

    REASONS = {
        "mineru_error": ("MinerU 转换失败", True),
        "paddleocr_error": ("PaddleOCR 转换失败", True),
        "no_quota": ("转换配额已用完，请稍后重试", True),
        "file_not_found": ("文件不存在", False),
        "io_error": ("文件读写失败", True),
        "timeout": ("转换超时，请稍后重试", True),
        "generic": ("转换失败", True),
    }

    def __init__(self, filename: str, reason: str, detail: str = ""):
        msg_template = self.REASONS.get(reason, self.REASONS["generic"])
        message = msg_template[0]
        recoverable = msg_template[1]
        if detail:
            message = f"{message}：{detail}"
        super().__init__(message, filename, reason, recoverable)


class ExtractionError(PipelineError):
    """证据提取失败"""

    REASONS = {
        "llm_error": ("LLM 调用失败", True),
        "llm_retry_exhausted": ("LLM 重试次数已耗尽", True),
        "no_quota": ("配额已用完", True),
        "parse_error": ("LLM 返回结果解析失败", True),
        "file_not_found": ("文件不存在", False),
        "io_error": ("文件读写失败", True),
        "cancelled": ("用户取消提取", False),
        "generic": ("提取失败", True),
    }

    def __init__(self, filename: str, reason: str, detail: str = ""):
        msg_template = self.REASONS.get(reason, self.REASONS["generic"])
        message = msg_template[0]
        recoverable = msg_template[1]
        if detail:
            message = f"{message}：{detail}"
        super().__init__(message, filename, reason, recoverable)


class CancelRequested(Exception):
    """用户取消操作"""
    pass
