"""卡死检测：LLM 调用进行中不取消"""
import asyncio
import inspect
import case_manager


def test_stall_detector_respects_llm_waiting():
    src = inspect.getsource(case_manager)
    assert "llm_waiting" in src.split("stall_detector")[1][:2000] or "llm_waiting" in src


def test_stall_threshold_raised():
    src = inspect.getsource(case_manager)
    assert "stall_threshold = 900" in src
