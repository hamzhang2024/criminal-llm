"""
pytest 配置和共享 fixtures
"""
import importlib
import os
import sys
from pathlib import Path

# 添加 backend 目录到 Python 路径
backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))


import pytest


def _reload_data_modules():
    """重载数据目录相关模块，使新的 CRIMINAL_LLM_DATA_DIR 环境变量生效"""
    import _bootstrap
    importlib.reload(_bootstrap)
    import config
    importlib.reload(config)
    import case_manager
    importlib.reload(case_manager)


@pytest.fixture
def temp_data_dir(tmp_path):
    """创建临时数据目录"""
    data_dir = tmp_path / "test_data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # 设置环境变量
    original_env = os.environ.get("CRIMINAL_LLM_DATA_DIR")
    os.environ["CRIMINAL_LLM_DATA_DIR"] = str(data_dir)

    # 重载模块使环境变量生效
    _reload_data_modules()

    yield data_dir

    # 恢复环境变量
    if original_env:
        os.environ["CRIMINAL_LLM_DATA_DIR"] = original_env
    elif "CRIMINAL_LLM_DATA_DIR" in os.environ:
        del os.environ["CRIMINAL_LLM_DATA_DIR"]
    _reload_data_modules()


@pytest.fixture
def temp_case_dir(temp_data_dir):
    """创建临时案件目录"""
    cases_dir = temp_data_dir / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)

    # 创建一个测试案件
    case_id = "case_test1234"
    case_path = cases_dir / case_id / "测试案件_20260613"
    case_path.mkdir(parents=True, exist_ok=True)

    # 创建子目录
    for subdir in ["original", "processed", "md", "evidence", "analysis"]:
        (case_path / subdir).mkdir(exist_ok=True)

    # 创建 case.json
    import json
    metadata = {
        "id": case_id,
        "name": "测试案件",
        "defendant": "张三",
        "created_at": "2026-06-13",
        "status": "new",
        "case_dir": str(case_path),
    }
    with open(case_path / "case.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    return {
        "case_id": case_id,
        "case_path": case_path,
        "cases_dir": cases_dir,
    }


@pytest.fixture
def mock_llm_response():
    """模拟 LLM 响应"""
    return {
        "content": "这是模拟的 LLM 响应内容",
        "usage": {"total_tokens": 100},
    }


@pytest.fixture
def sample_pdf_path(temp_case_dir):
    """创建示例 PDF 文件（空文件，仅用于路径测试）"""
    pdf_path = temp_case_dir["case_path"] / "original" / "测试文件.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%Test PDF content")
    return pdf_path
