"""
配置管理 - 读取和保存应用配置

配置文件：DATA_DIR/criminal-llm-config.json
"""
import json
import os
from pathlib import Path
from typing import Dict, Any

# 使用 config.py 的 DATA_DIR 确保开发和打包模式下路径一致
from config import DATA_DIR
CONFIG_PATH = DATA_DIR / "criminal-llm-config.json"

# 默认配置（阿里云百炼，推荐 qwen3.5-plus）
DEFAULTS = {
    "llm_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "llm_model": "qwen3.5-plus",
    "evidence_concurrency": 3,
}


def _ensure_dir():
    """确保配置目录存在"""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)


def load_config() -> Dict[str, Any]:
    """读取配置，合并默认值"""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user_config = json.load(f)
            return {**DEFAULTS, **user_config}
        except Exception:
            pass
    return {**DEFAULTS}


def save_config(config: Dict[str, Any]):
    """保存配置（只保存用户设置的字段）"""
    _ensure_dir()
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def get_config_status() -> Dict[str, Any]:
    """返回配置状态和实际值（供表单填充）"""
    config = load_config()
    return {
        "mineru_token": bool(config.get("mineru_token")),
        "mineru_token_value": config.get("mineru_token", ""),
        "llm_api_key": bool(config.get("llm_api_key")),
        "llm_api_key_value": config.get("llm_api_key", ""),
        "llm_base_url": config.get("llm_base_url", ""),
        "llm_model": config.get("llm_model", ""),
        "evidence_concurrency": config.get("evidence_concurrency", 3),
    }


def get_config_value(key: str, default: str = "") -> str:
    """获取单个配置值（供其他模块调用）"""
    config = load_config()
    return config.get(key, default)
