"""
config_manager 单元测试

测试目标：
1. load_config - 读取配置，合并默认值，损坏文件容错
2. save_config - 保存配置（往返一致性）
3. get_config_value - 获取单个配置值
4. get_config_status - 配置状态（Token 脱敏、模型上下文信息）

用 temp_data_dir fixture 使 CONFIG_PATH 指向临时目录。
"""

import json

import pytest


class TestLoadConfig:
    """配置读取"""

    def test_returns_defaults_when_no_file(self, temp_data_dir):
        from config_manager import load_config, DEFAULTS

        config = load_config()
        # 无配置文件时返回默认值
        assert config["llm_model"] == DEFAULTS["llm_model"]
        assert config["evidence_concurrency"] == 3
        assert config["pdf_engine"] == "mineru"

    def test_merges_user_config_with_defaults(self, temp_data_dir):
        from config_manager import CONFIG_PATH, load_config

        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps({"llm_model": "custom-model"}), encoding="utf-8")

        config = load_config()
        # 用户值覆盖默认
        assert config["llm_model"] == "custom-model"
        # 默认值仍存在
        assert config["evidence_concurrency"] == 3

    def test_corrupt_json_falls_back_to_defaults(self, temp_data_dir):
        from config_manager import CONFIG_PATH, load_config

        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text("not json{", encoding="utf-8")

        config = load_config()
        # 损坏文件不抛异常，返回默认值
        assert config["llm_model"]  # 有默认值


class TestSaveAndLoadRoundtrip:
    """保存与读取往返"""

    def test_save_then_load(self, temp_data_dir):
        from config_manager import load_config, save_config

        custom = {"llm_model": "test-model", "evidence_concurrency": 10, "pdf_engine": "paddleocr"}
        save_config(custom)

        config = load_config()
        assert config["llm_model"] == "test-model"
        assert config["evidence_concurrency"] == 10
        assert config["pdf_engine"] == "paddleocr"

    def test_save_creates_config_file(self, temp_data_dir):
        from config_manager import CONFIG_PATH, save_config

        save_config({"llm_model": "x"})
        assert CONFIG_PATH.exists()

    def test_save_persists_chinese(self, temp_data_dir):
        from config_manager import load_config, save_config

        save_config({"llm_model": "中文模型"})
        assert load_config()["llm_model"] == "中文模型"


class TestGetConfigValue:
    """单值获取"""

    def test_get_existing_value(self, temp_data_dir):
        from config_manager import save_config, get_config_value

        save_config({"mineru_token": "tok_123"})
        assert get_config_value("mineru_token") == "tok_123"

    def test_get_default_value_when_missing(self, temp_data_dir):
        from config_manager import get_config_value

        # 无配置文件，返回 default 参数
        assert get_config_value("nonexistent_key", default="fallback") == "fallback"

    def test_get_default_value_from_defaults(self, temp_data_dir):
        from config_manager import get_config_value

        # 默认配置中存在的键
        assert get_config_value("pdf_engine") == "mineru"


class TestGetConfigStatus:
    """配置状态（Token 脱敏）"""

    def test_token_returned_as_bool_not_plaintext(self, temp_data_dir):
        from config_manager import save_config, get_config_status

        save_config({"mineru_token": "secret_token_xyz", "llm_api_key": "sk-xxx"})
        status = get_config_status()
        # Token 仅返回布尔，不返回明文
        assert status["mineru_token"] is True
        assert status["llm_api_key"] is True
        # 明文不应出现在状态中
        assert "secret_token_xyz" not in json.dumps(status)

    def test_empty_token_returns_false(self, temp_data_dir):
        from config_manager import get_config_status

        status = get_config_status()
        assert status["mineru_token"] is False
        assert status["llm_api_key"] is False

    def test_status_contains_model_info(self, temp_data_dir):
        from config_manager import get_config_status

        status = get_config_status()
        # 应包含模型上下文信息字段
        assert "model_context_limit" in status
        assert "llm_model" in status

    def test_status_contains_engine_config(self, temp_data_dir):
        from config_manager import save_config, get_config_status

        save_config({"pdf_engine": "paddleocr", "paddleocr_token": "pt_xxx"})
        status = get_config_status()
        assert status["pdf_engine"] == "paddleocr"
        assert status["paddleocr_token"] is True
