#!/usr/bin/env python3
"""Criminal LLM Worker — 后台计算进程

通过 stdin/stdout JSON-RPC 与 Rust Tauri 后端通信。
不做 HTTP 服务器，只做计算。

通信协议：
  输入 (stdin):  {"id": "xxx", "method": "xxx", "params": {...}, "data_dir": "/path"}
  输出 (stdout): {"id": "xxx", "result": {...}}
  错误 (stdout): {"id": "xxx", "error": "message"}

支持的方法:
  - extract_evidence: 提取证据
  - convert_to_md: PDF 转 Markdown
  - analyze_case: 案卷分析
  - chat_analysis: LLM 对话分析
  - pipeline_step: 流水线步骤
  - config_test: 测试配置连接
"""

import json
import sys
import os
import traceback
import logging

# 配置 logging（输出到 stderr）
logging.basicConfig(level=logging.INFO, stream=sys.stderr, format='[%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


def setup_data_dir(data_dir: str) -> None:
    """设置数据目录环境"""
    os.environ["CRIMINAL_LLM_DATA_DIR"] = data_dir
    # 确保 _bootstrap.py 能找到数据目录
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def handle_request(request: dict) -> dict:
    """处理单个 JSON-RPC 请求"""
    method = request.get("method", "")
    params = request.get("params", {})
    data_dir = request.get("data_dir", "")
    req_id = request.get("id", "")

    if data_dir:
        setup_data_dir(data_dir)

    try:
        if method == "ping":
            return {"id": req_id, "result": "pong"}

        elif method == "extract_evidence":
            return handle_extract_evidence(req_id, params)

        elif method == "convert_to_md":
            return handle_convert_to_md(req_id, params)

        elif method == "analyze_case":
            return handle_analyze_case(req_id, params)

        elif method == "chat_analysis":
            return handle_chat_analysis(req_id, params)

        elif method == "pipeline_step":
            return handle_pipeline_step(req_id, params)

        elif method == "stop_extract":
            return handle_stop_extract(req_id, params)

        elif method == "split_suggestion":
            return handle_split_suggestion(req_id, params)

        elif method == "config_test":
            return handle_config_test(req_id, params)

        else:
            return {"id": req_id, "error": f"未知方法: {method}"}

    except Exception as e:
        return {
            "id": req_id,
            "error": f"{e.__class__.__name__}: {str(e)}",
        }


def handle_extract_evidence(req_id: str, params: dict) -> dict:
    """证据提取 — 调用 LLM 客户端"""
    try:
        from case_manager import extract_evidence_for_case
        case_id = params.get("case_id", "")
        result = extract_evidence_for_case(case_id)
        return {"id": req_id, "result": result}
    except ImportError as e:
        return {"id": req_id, "error": f"无法导入 case_manager: {e}"}


def handle_convert_to_md(req_id: str, params: dict) -> dict:
    """PDF 转 Markdown"""
    try:
        from background_tasks import convert_case_to_md
        case_id = params.get("case_id", "")
        result = convert_case_to_md(case_id)
        return {"id": req_id, "result": result}
    except ImportError as e:
        return {"id": req_id, "error": f"无法导入 background_tasks: {e}"}


def handle_analyze_case(req_id: str, params: dict) -> dict:
    """案卷分析"""
    try:
        from analysis_pipeline import analyze_case
        case_id = params.get("case_id", "")
        result = analyze_case(case_id)
        return {"id": req_id, "result": result}
    except ImportError as e:
        return {"id": req_id, "error": f"无法导入 analysis_pipeline: {e}"}


def handle_chat_analysis(req_id: str, params: dict) -> dict:
    """LLM 对话分析"""
    try:
        from analyzer_api import chat_about_evidence
        case_id = params.get("case_id", "")
        query = params.get("query", "")
        result = chat_about_evidence(case_id, query)
        return {"id": req_id, "result": result}
    except ImportError as e:
        return {"id": req_id, "error": f"无法导入 analyzer_api: {e}"}


def handle_pipeline_step(req_id: str, params: dict) -> dict:
    """运行单个流水线步骤"""
    try:
        from stage_api import run_stage_step
        case_id = params.get("case_id", "")
        stage_id = params.get("stage_id", "")
        result = run_stage_step(case_id, stage_id)
        return {"id": req_id, "result": result}
    except ImportError as e:
        return {"id": req_id, "error": f"无法导入 stage_api: {e}"}


def handle_stop_extract(req_id: str, params: dict) -> dict:
    """停止证据提取 — 标记取消信号"""
    case_id = params.get("case_id", "")
    try:
        from background_tasks import cancel_task
        cancel_task(case_id)
        return {"id": req_id, "result": {"stopped": True, "case_id": case_id}}
    except ImportError:
        # 如果无法导入，仍然标记为已停止
        return {"id": req_id, "result": {"stopped": True, "case_id": case_id, "note": "import fallback"}}


def handle_split_suggestion(req_id: str, params: dict) -> dict:
    """文书拆分建议"""
    try:
        from case_splitter import suggest_split
        case_id = params.get("case_id", "")
        result = suggest_split(case_id)
        return {"id": req_id, "result": result}
    except ImportError as e:
        return {"id": req_id, "error": f"无法导入 case_splitter: {e}"}


def handle_config_test(req_id: str, params: dict) -> dict:
    """测试配置连接"""
    engine = params.get("engine", "llm")

    if engine == "llm":
        try:
            from llm_client import get_llm_client
            client = get_llm_client()
            client.reload_config()
            response = client.chat([{"role": "user", "content": "ping"}])
            return {"id": req_id, "result": {"success": True, "message": f"LLM 连接成功: {response[:100]}"}}
        except Exception as e:
            return {"id": req_id, "result": {"success": False, "message": str(e)}}

    elif engine == "mineru":
        try:
            from mineru_async import check_mineru_quota
            quota = check_mineru_quota()
            return {"id": req_id, "result": {"success": True, "message": str(quota)}}
        except Exception as e:
            return {"id": req_id, "result": {"success": False, "message": str(e)}}

    else:
        return {"id": req_id, "result": {"success": False, "message": f"未知引擎: {engine}"}}


def main():
    """Worker 主循环 — 读 stdin JSON, 写 stdout JSON"""
    logger.info("Criminal LLM Worker ready")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            error_resp = json.dumps({"id": "", "error": f"JSON parse error: {e}"})
            sys.stdout.write(error_resp + "\n")
            sys.stdout.flush()
            continue

        response = handle_request(request)
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
