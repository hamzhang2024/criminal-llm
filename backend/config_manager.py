"""
配置管理 - 读取和保存应用配置

配置文件：DATA_DIR/criminal-llm-config.json
"""
import json
import os
from pathlib import Path
from typing import Dict, Any, Optional

# 使用 config.py 的 DATA_DIR 确保开发和打包模式下路径一致
from config import DATA_DIR
CONFIG_PATH = DATA_DIR / "criminal-llm-config.json"

# 默认配置（阿里云百炼，推荐 qwen3.5-plus）
DEFAULTS = {
    "llm_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "llm_model": "qwen3.5-plus",
    # 高质量任务模型（分层路由）：空字符串 = 不启用分层，全部走 llm_model
    "llm_model_heavy": "",
    "evidence_concurrency": 3,
    # PDF 转 MD 引擎配置
    "pdf_engine": "mineru",          # "paddleocr" | "mineru"
    "image_ocr_enabled": False,      # PaddleOCR 图片块文字识别开关（转账凭证/流水截图，默认关：单图回填太慢）
    "paddleocr_token": "",            # PaddleOCR Token
    # MinerU 真批量配置
    "pdf_convert_concurrency": 10,    # PDF 转换上传并发数（1-50，真批量下提交/轮询已聚合）
    "mineru_model_version": "vlm",    # MinerU 模型版本：vlm（高精度）/ pipeline（快速）/ MinerU-HTML
    # 案例检索云端服务
    "case_service_url": "",           # 案例检索服务地址，空则用默认 http://118.196.83.43:8001
    "case_api_key": "",               # 案例检索 API Key（设置页填写）
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


def _detect_model_window(model: str):
    """按模型名识别上下文窗口（未知返回 None）"""
    try:
        import context_budget
        return context_budget.get_model_window(model)
    except Exception:
        return None


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
        "llm_model_heavy": config.get("llm_model_heavy", ""),
        "evidence_concurrency": config.get("evidence_concurrency", 3),
        # PDF 转 MD 引擎配置
        "pdf_engine": config.get("pdf_engine", "paddleocr"),
        "paddleocr_token": bool(config.get("paddleocr_token")),
        "paddleocr_token_value": config.get("paddleocr_token", ""),
        "image_ocr_enabled": config.get("image_ocr_enabled", False),
        # MinerU 真批量配置
        "pdf_convert_concurrency": config.get("pdf_convert_concurrency", 10),
        "mineru_model_version": config.get("mineru_model_version", "vlm"),
        # 模型上下文大小（tokens）
        "model_context_limit": config.get("model_context_limit", 250000),
        "model_window_detected": _detect_model_window(config.get("llm_model", "")),
        # 案例检索云端服务
        "case_service_url": config.get("case_service_url", ""),
        "case_api_key": bool(config.get("case_api_key")),
        "case_api_key_value": config.get("case_api_key", ""),
    }


def get_config_value(key: str, default: str = "") -> str:
    """获取单个配置值（供其他模块调用）"""
    config = load_config()
    return config.get(key, default)


def get_heavy_model() -> Optional[str]:
    """
    高质量任务模型（分层路由）

    读 llm_model_heavy：非空返回模型名（最终产物调用用），空返回 None（不启用分层，全部走默认模型）。
    """
    value = get_config_value("llm_model_heavy", "")
    value = value.strip() if isinstance(value, str) else ""
    return value or None
