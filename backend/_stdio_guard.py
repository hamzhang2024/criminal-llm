"""标准 IO 句柄兜底。

PyInstaller 以 ``--windowed``（GUI 模式）打包时，Windows 不为进程附加控制台，
Python 的 ``sys.stdout`` / ``sys.stderr`` / ``sys.stdin`` 会是 ``None``。uvicorn
初始化日志 formatter 时调用 ``sys.stderr.isatty()`` 会直接 ``AttributeError`` 崩溃
（``uvicorn/logging.py`` 的 ``DefaultFormatter.__init__``），导致后端启动即退出、
8080 永不监听——前端表现为「连接被拒绝 / 后端启动超时」。

必须在 ``import logging`` / ``import uvicorn`` 之前调用 :func:`ensure_stdio`，把
``None`` 句柄兜底为有效文件，避免 startup 崩溃。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = ["ensure_stdio"]


def ensure_stdio(data_dir: Path) -> None:
    """把可能为 ``None`` 的标准 IO 句柄兜底为有效句柄。

    - ``stderr`` / ``stdout`` 兜底到 ``data_dir/backend_stdio.log``（便于排查
      uvicorn 及 Python 运行时日志，命令行手动启动时尤其有用）。
    - ``stdin`` 兜底到 ``os.devnull``。
    - 任一句柄非 ``None`` 时不做任何改动（Tauri 启动时 Rust 已把 stderr 重定向
      到 ``backend_stderr.log``，此时不触发兜底，保留原有行为）。
    """
    if sys.stdout is not None and sys.stderr is not None and sys.stdin is not None:
        return

    data_dir.mkdir(parents=True, exist_ok=True)
    sink_path = data_dir / "backend_stdio.log"
    # 进程级兜底句柄需常驻至进程结束，不主动关闭。
    sink = open(sink_path, "a", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = sink
    if sys.stdout is None:
        sys.stdout = sink
    if sys.stdin is None:
        sys.stdin = open(os.devnull, encoding="utf-8")  # noqa: SIM115
