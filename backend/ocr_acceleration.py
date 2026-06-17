"""
OCR 加速模块

自动检测并使用最佳推理引擎：
- Apple Silicon: CoreML (GPU/NPU)
- NVIDIA: CUDA
- 其他: CPU
"""
import platform
from typing import Any, Dict
import logging

logger = logging.getLogger(__name__)


def detect_gpu_device() -> str:
    """
    检测可用的加速方式

    Returns:
        "coreml" | "cuda" | "cpu"
    """
    # Apple Silicon: CoreML
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        try:
            import onnxruntime
            providers = onnxruntime.get_available_providers()
            if "CoreMLExecutionProvider" in providers:
                return "coreml"
        except Exception:
            pass

    # NVIDIA: CUDA
    try:
        import onnxruntime
        providers = onnxruntime.get_available_providers()
        if "CUDAExecutionProvider" in providers:
            return "cuda"
    except Exception:
        pass

    return "cpu"


def init_rapidocr_with_acceleration(device: str = None):
    """
    根据加速方式初始化 RapidOCR

    Args:
        device: "coreml" | "cuda" | "cpu"，不传则自动检测

    Returns:
        RapidOCR 实例
    """
    from rapidocr import EngineType, LangRec, RapidOCR

    if device is None:
        device = detect_gpu_device()

    params: Dict[str, Any] = {
        "Det.engine_type": EngineType.ONNXRUNTIME,
        "Cls.engine_type": EngineType.ONNXRUNTIME,
        "Rec.engine_type": EngineType.ONNXRUNTIME,
        "Rec.lang_type": LangRec.CH,
    }

    if device == "coreml":
        params["Inference.use_coreml"] = True
        logger.info("[OCR] 启用 CoreML 加速 (Apple Silicon)")
    elif device == "cuda":
        params["Inference.use_cuda"] = True
        logger.info("[OCR] 启用 CUDA 加速 (NVIDIA)")
    else:
        logger.info("[OCR] 使用 CPU 推理")

    return RapidOCR(params=params)


def create_acceleration_info() -> Dict[str, str]:
    """
    返回当前环境的加速信息（用于前端显示）

    Returns:
        {"device": "...", "providers": [...]}
    """
    device = detect_gpu_device()
    try:
        import onnxruntime
        providers = onnxruntime.get_available_providers()
    except Exception:
        providers = []

    return {
        "device": device,
        "providers": providers,
        "platform": f"{platform.system()} {platform.machine()}",
    }
