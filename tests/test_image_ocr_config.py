"""image_ocr_enabled 配置项测试"""
import json

import config_manager


def test_default_enabled(tmp_path, monkeypatch):
    """未配置时默认开启"""
    monkeypatch.setattr(config_manager, "CONFIG_PATH", tmp_path / "cfg.json")
    assert config_manager.load_config()["image_ocr_enabled"] is True


def test_user_can_disable(tmp_path, monkeypatch):
    """用户显式关闭后读取为 False"""
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"image_ocr_enabled": False}), encoding="utf-8")
    monkeypatch.setattr(config_manager, "CONFIG_PATH", cfg)
    assert config_manager.load_config()["image_ocr_enabled"] is False


def test_existing_config_without_key_defaults_true(tmp_path, monkeypatch):
    """老用户升级场景：配置文件存在但无该键，合并 DEFAULTS 后为 True"""
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"pdf_engine": "mineru"}), encoding="utf-8")
    monkeypatch.setattr(config_manager, "CONFIG_PATH", cfg)
    assert config_manager.load_config()["image_ocr_enabled"] is True


def test_config_status_includes_key(tmp_path, monkeypatch):
    """GET /api/config 的状态字段包含 image_ocr_enabled（供设置页表单填充）"""
    monkeypatch.setattr(config_manager, "CONFIG_PATH", tmp_path / "cfg.json")
    status = config_manager.get_config_status()
    assert status["image_ocr_enabled"] is True
