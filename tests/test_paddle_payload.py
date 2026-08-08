"""PaddleOCR optionalPayload 构造函数测试"""
import paddleocr_remote
import paddleocr_async


def test_payload_enabled_by_default(monkeypatch):
    """image_ocr_enabled 开启时 payload 包含图片识别参数"""
    monkeypatch.setattr("config_manager.get_config_value", lambda key, default="": True)
    payload = paddleocr_remote.build_optional_payload()
    assert payload["useOcrForImageBlock"] is True
    assert payload["useSealRecognition"] is True


def test_payload_disabled(monkeypatch):
    """image_ocr_enabled 关闭时 payload 回退旧行为（不含新参数）"""
    monkeypatch.setattr("config_manager.get_config_value", lambda key, default="": False)
    payload = paddleocr_remote.build_optional_payload()
    assert "useOcrForImageBlock" not in payload
    assert "useSealRecognition" not in payload
    # 原有参数不受影响
    assert payload["useLayoutDetection"] is True
    assert payload["mergeTables"] is True


def test_async_module_shares_builder():
    """paddleocr_async 不再重复定义 payload，改为共享 paddleocr_remote 的构造函数"""
    assert not hasattr(paddleocr_async, "PADDLEOCR_OPTIONAL_PAYLOAD"), \
        "paddleocr_async 应删除重复定义的 PADDLEOCR_OPTIONAL_PAYLOAD"
    assert paddleocr_async.build_optional_payload is paddleocr_remote.build_optional_payload
