"""stdio 兜底单元测试。

测试目标：ensure_stdio 在 PyInstaller --windowed（无控制台）导致
sys.stdout / sys.stderr / sys.stdin 为 None 时，把它们兜底为有效句柄，
避免 uvicorn 日志 formatter 初始化时调用 sys.stderr.isatty() 崩溃。
"""
import io
import sys

from _stdio_guard import ensure_stdio


def test_ensure_stdio_fills_none_streams(tmp_path, monkeypatch):
    """stdout/stderr/stdin 为 None 时，兜底为有效句柄且具备 isatty 方法。"""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(sys, "stdin", None)

    ensure_stdio(tmp_path)

    # uvicorn DefaultFormatter 会调用 sys.stderr.isatty()，该方法必须存在
    assert sys.stderr is not None
    assert sys.stdout is not None
    assert sys.stdin is not None
    assert callable(sys.stderr.isatty)
    # 兜底为普通文件（非 tty），isatty() 返回 False，不会触发彩色输出分支
    assert sys.stderr.isatty() is False


def test_ensure_stdio_creates_log_file(tmp_path, monkeypatch):
    """stderr 兜底到 data_dir 下的日志文件，便于排查 uvicorn 运行时输出。"""
    monkeypatch.setattr(sys, "stderr", None)

    ensure_stdio(tmp_path)

    log_file = tmp_path / "backend_stdio.log"
    assert log_file.exists()
    # 写入探测，确认句柄可写
    sys.stderr.write("stdio_guard probe\n")
    sys.stderr.flush()
    assert "stdio_guard probe" in log_file.read_text(encoding="utf-8")


def test_ensure_stdio_noop_when_all_present(tmp_path, monkeypatch):
    """所有句柄均非 None 时不做任何改动（Tauri 已重定向 stderr 的场景）。"""
    kept_err = io.StringIO()
    monkeypatch.setattr(sys, "stderr", kept_err)
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stdin", io.StringIO())

    ensure_stdio(tmp_path)

    assert sys.stderr is kept_err  # 引用未变，未发生兜底
    assert not (tmp_path / "backend_stdio.log").exists()
