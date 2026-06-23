"""
日志脱敏单元测试

测试目标：
1. _redact_log - 遮蔽日志中的密钥/密码/Token，保留 key 名
2. Bearer token 整段遮蔽（不保留前缀）
3. 普通日志内容不受影响（无误伤）
"""

import os

# main.py 在导入时会读取 DATA_DIR 并创建 backend.log，
# 必须在导入前设置临时数据目录，避免污染真实数据目录
os.environ.setdefault("CRIMINAL_LLM_DATA_DIR", "/tmp/cl-redact-test-data")

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(backend_dir))

from main import _redact_log  # noqa: E402


class TestRedactBearerToken:
    """Bearer token 脱敏测试"""

    def test_bearer_token_in_authorization_header(self):
        """Authorization: Bearer <jwt> 整段值被遮蔽"""
        line = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.abc123"
        redacted = _redact_log(line)
        assert "eyJhbGciOiJIUzI1NiJ9" not in redacted
        assert "abc123" not in redacted
        assert "***REDACTED***" in redacted

    def test_bearer_token_without_space_after_colon(self):
        """Authorization:Bearer xxx（冒号后无空格）也能脱敏"""
        line = "Authorization:Bearer eyJ.abcDEF123"
        redacted = _redact_log(line)
        assert "eyJ.abcDEF123" not in redacted
        assert "***REDACTED***" in redacted

    def test_bearer_token_in_json_request_header(self):
        """JSON 请求头中的 Bearer token 被遮蔽"""
        line = '请求头 {"Authorization": "Bearer xyz789ABC"}'
        redacted = _redact_log(line)
        assert "xyz789ABC" not in redacted
        assert "***REDACTED***" in redacted

    def test_bearer_prefix_not_preserved(self):
        """Bearer 关键字本身也一并遮蔽（不保留 Bearer 前缀）"""
        line = "Bearer eyJabc123456"
        redacted = _redact_log(line)
        assert "Bearer" not in redacted


class TestRedactKeyValueSecrets:
    """key=value / key: value 形式脱敏测试"""

    def test_api_key_equals_form(self):
        """api_key=<value> 遮蔽值，保留 key 名"""
        redacted = _redact_log("api_key=sk-1234567890abcdef")
        assert "sk-1234567890abcdef" not in redacted
        assert "api_key=***REDACTED***" in redacted

    def test_llm_api_key_colon_form(self):
        """llm_api_key: <value> 遮蔽值"""
        redacted = _redact_log("llm_api_key: sk-bailian-xyz123456")
        assert "sk-bailian-xyz123456" not in redacted
        assert "llm_api_key: ***REDACTED***" in redacted

    def test_password_colon_form(self):
        """password: <value> 遮蔽值"""
        redacted = _redact_log("password: mySecretPass123")
        assert "mySecretPass123" not in redacted
        assert "password: ***REDACTED***" in redacted

    def test_quoted_value(self):
        """带引号的值：密钥本身被遮蔽"""
        redacted = _redact_log('paddleocr_token: "pt_abcdef123456"')
        assert "pt_abcdef123456" not in redacted
        assert "***REDACTED***" in redacted

    def test_token_no_space_after_colon(self):
        """token:<value>（冒号后无空格）也能脱敏"""
        redacted = _redact_log("token:abc12345")
        assert "abc12345" not in redacted
        assert "***REDACTED***" in redacted

    def test_mineru_and_paddleocr_tokens(self):
        """mineru_token / paddleocr_token 专用字段均脱敏"""
        for line in [
            "mineru_token=token_abc123456",
            "paddleocr_token=token_xyz987654",
        ]:
            redacted = _redact_log(line)
            assert "***REDACTED***" in redacted
            assert "token_abc123456" not in redacted
            assert "token_xyz987654" not in redacted


class TestRedactNoFalsePositives:
    """普通日志不应被误伤"""

    def test_normal_chinese_log_unchanged(self):
        """中文案件日志完整保留"""
        line = "普通日志：用户上传了文件 彭帮生讯问笔录.pdf"
        assert _redact_log(line) == line

    def test_normal_info_line_unchanged(self):
        """不含敏感关键词的普通信息行完整保留"""
        line = "Normal line without secrets, just info about case_001."
        assert _redact_log(line) == line

    def test_short_value_not_redacted(self):
        """过短的值（<6 字符）不触发脱敏，避免误伤普通字段"""
        # "ok" 长度不足，不应被遮蔽
        line = "status: ok"
        assert _redact_log(line) == line


class TestRedactMultipleSecretsInOneLine:
    """单行多密钥同时脱敏"""

    def test_multiple_secrets_in_one_line(self):
        """一行中同时出现多个密钥，均不泄露（值字符类允许空格，贪心匹配可能合并为一次遮蔽，但密钥不会泄露）"""
        line = "config: api_key=sk-abcdef123456 token=eyJtoken123456 password=secretPass1"
        redacted = _redact_log(line)
        # 安全属性：三个密钥值均不得出现在输出中
        assert "sk-abcdef123456" not in redacted
        assert "eyJtoken123456" not in redacted
        assert "secretPass1" not in redacted
        assert "***REDACTED***" in redacted
