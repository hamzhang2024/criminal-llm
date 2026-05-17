"""并发限流保护器 — 只在触限流时自动降级，不向上试探。"""
import time
import threading


class ConcurrencyController:
    """保护器模式：按用户设定的固定值跑，遇到 429/超时时自动降档，不再往上探。"""

    def __init__(
        self,
        initial: int = 3,
        min_concurrency: int = 1,
    ):
        self._current = initial
        self.min_concurrency = min_concurrency

        # 滑动窗口统计 — 只看限流信号
        self._error_count = 0
        self._recent_latencies: list[float] = []
        self._window_size = 20

        # 线程安全
        self._lock = threading.Lock()

        # 上次降级时间
        self._last_adjust_time = time.time()
        self._adjust_cooldown = 10.0  # 降级后冷却 10 秒

    @property
    def concurrency(self) -> int:
        return self._current

    def record_success(self, latency_ms: float):
        """记录成功请求，不触发上调。"""
        with self._lock:
            self._recent_latencies.append(latency_ms)
            if len(self._recent_latencies) > self._window_size:
                self._recent_latencies.pop(0)
            # 不再调用 _adjust()，成功时不调整

    def record_timeout(self):
        """记录超时，触发降级。"""
        with self._lock:
            self._error_count += 1
            self._adjust()

    def record_error(self, error_type: str = ""):
        """记录错误，仅 429/限流触发降级。"""
        with self._lock:
            if "429" in error_type or "rate_limit" in error_type:
                self._error_count += 1
                self._adjust()
            # 其他 5xx 不触发降级，可能是偶发问题

    def _adjust(self):
        """仅在限流信号出现时降级，不往上探。"""
        now = time.time()
        if now - self._last_adjust_time < self._adjust_cooldown:
            return  # 冷却期内

        old = self._current
        self._current = max(self.min_concurrency, self._current - 1)

        if self._current != old:
            self._last_adjust_time = now

        # 重置计数
        self._error_count = 0

    @property
    def error_rate(self) -> float:
        return 0.0  # 不再需要，保留字段兼容

    @property
    def avg_latency(self) -> float:
        if not self._recent_latencies:
            return 0.0
        return sum(self._recent_latencies) / len(self._recent_latencies)

    def get_status(self) -> dict:
        """返回当前状态快照。"""
        return {
            "current_concurrency": self._current,
            "min_concurrency": self.min_concurrency,
            "mode": "protection",
            "avg_latency_ms": round(self.avg_latency, 1),
            "recent_samples": len(self._recent_latencies),
        }
