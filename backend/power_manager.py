"""
macOS 电源管理模块

在长时间操作（PDF 转换、证据提取）期间防止系统休眠。
"""
import logging
import subprocess
import sys
import threading

logger = logging.getLogger(__name__)


class PowerInhibitor:
    """防止系统休眠。

    使用 macOS caffeinate 工具阻止显示器关闭和硬盘休眠。
    进入时启动 caffeinate 子进程，退出时终止。
    """

    def __init__(self, reason: str = "刑事案件处理"):
        self._process = None
        self._lock = threading.Lock()
        self.reason = reason

    def __enter__(self):
        if sys.platform == "darwin":
            with self._lock:
                # caffeinate -d: 阻止显示器休眠
                # caffeinate -i: 阻止空闲休眠（CPU/硬盘）
                self._process = subprocess.Popen(
                    ["caffeinate", "-d", "-i"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                logger.info(f"[电源管理] 已阻止系统休眠: {self.reason}")
        return self

    def __exit__(self, *args):
        self.release()

    def release(self):
        """恢复电源管理（终止 caffeinate 进程）"""
        with self._lock:
            if self._process:
                self._process.terminate()
                try:
                    self._process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                self._process = None
                logger.info("[电源管理] 已恢复系统休眠")
