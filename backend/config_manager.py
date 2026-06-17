"""
配置管理 - 读取和保存应用配置

配置文件：DATA_DIR/criminal-llm-config.json
"""
import json
from typing import Any, Dict

# 使用 config.py 的 DATA_DIR 确保开发和打包模式下路径一致
from config import DATA_DIR

CONFIG_PATH = DATA_DIR / "criminal-llm-config.json"

# 默认配置（阿里云百炼，推荐 qwen3.5-plus）
DEFAULTS = {
    "llm_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "llm_model": "qwen3.5-plus",
    "evidence_concurrency": 3,
    # PDF 转 MD 引擎配置
    "pdf_engine": "mineru",          # "paddleocr" | "mineru"
    "paddleocr_token": "",            # PaddleOCR Token
    # MinerU 配置
    "mineru_mode": "cloud",           # "cloud" | "local"
    "mineru_local_url": "",           # 本地 MinerU 服务器地址，如 http://192.168.1.100:3000
    # 元典法律案例搜索 API
    "yuandian_token": "",             # 元典 API Token
    # 模型上下文限制（可选，用户手动指定时优先使用，单位：tokens）
    "model_context_limit": None,      # 如 200000 表示 200k tokens
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
    """返回配置状态（不返回敏感 Token 明文，供前端显示状态）"""
    config = load_config()
    model = config.get("llm_model", "")
    user_specified_limit = config.get("model_context_limit")

    # 获取模型上下文信息
    try:
        from llm_client import get_model_context_limit
        model_info = get_model_context_limit(model, user_specified_limit)
    except Exception:
        model_info = {
            "limit": 128_000,
            "limit_k": "128k(估)",
            "strategy": "小案件模式",
            "warning": "无法获取模型信息",
            "small_case_limit": 40_000,
            "is_estimated": True,
        }

    return {
        # Token 仅返回是否配置（布尔值），不返回明文
        "mineru_token": bool(config.get("mineru_token")),
        "mineru_mode": config.get("mineru_mode", "cloud"),
        "mineru_local_url": config.get("mineru_local_url", ""),
        "llm_api_key": bool(config.get("llm_api_key")),
        "llm_base_url": config.get("llm_base_url", ""),
        "llm_model": model,
        "evidence_concurrency": config.get("evidence_concurrency", 3),
        # PDF 转 MD 引擎配置
        "pdf_engine": config.get("pdf_engine", "paddleocr"),
        "paddleocr_token": bool(config.get("paddleocr_token")),
        # 元典法律案例搜索 API
        "yuandian_token": bool(config.get("yuandian_token")),
        # 模型上下文能力信息
        "model_context_limit": model_info["limit"],
        "model_context_limit_k": model_info["limit_k"],
        "model_strategy": model_info["strategy"],
        "model_warning": model_info.get("warning", ""),
        "model_small_case_limit": model_info.get("small_case_limit", 0),
        "model_is_estimated": model_info.get("is_estimated", False),
        # 用户手动指定的上下文限制
        "user_context_limit": user_specified_limit,
    }


def get_config_value(key: str, default: str = "") -> str:
    """获取单个配置值（供其他模块调用）"""
    config = load_config()
    return config.get(key, default)
