"""并发限流保护器 — 遇到 429/超时时自动降级，连续成功后逐步恢复。"""
import time
import threading


class ConcurrencyController:
    """保护器模式：遇到 429/超时时自动降档，连续成功后逐步恢复到初始值。"""

    def __init__(
        self,
        initial: int = 3,
        min_concurrency: int = 1,
    ):
        self._initial = initial
        self._current = initial
        self.min_concurrency = min_concurrency

        # 滑动窗口统计 — 只看限流信号
        self._error_count = 0
        self._recent_latencies: list[float] = []
        self._window_size = 20

        # 线程安全
        self._lock = threading.Lock()

        # 上次调整时间
        self._last_adjust_time = time.time()
        self._adjust_cooldown = 10.0  # 调整后冷却 10 秒

        # 连续成功计数（用于恢复）
        self._success_streak = 0
        self._recover_threshold = 10  # 连续成功 N 次后恢复一档

    @property
    def concurrency(self) -> int:
        return self._current

    def record_success(self, latency_ms: float):
        """记录成功请求，连续成功后尝试恢复并发数。"""
        with self._lock:
            self._recent_latencies.append(latency_ms)
            if len(self._recent_latencies) > self._window_size:
                self._recent_latencies.pop(0)
            self._success_streak += 1
            # 连续成功后恢复
            if self._success_streak >= self._recover_threshold:
                self._try_recover()

    def record_timeout(self):
        """记录超时，触发降级。"""
        with self._lock:
            self._error_count += 1
            self._success_streak = 0  # 重置成功 streak
            self._adjust()

    def record_error(self, error_type: str = ""):
        """记录错误，仅 429/限流触发降级。"""
        with self._lock:
            if "429" in error_type or "rate_limit" in error_type:
                self._error_count += 1
                self._success_streak = 0  # 重置成功 streak
                self._adjust()
            # 其他 5xx 不触发降级，可能是偶发问题

    def _try_recover(self):
        """尝试恢复一档并发（不超过初始值）"""
        now = time.time()
        if now - self._last_adjust_time < self._adjust_cooldown:
            return  # 冷却期内
        if self._current >= self._initial:
            return  # 已恢复到初始值，无需再升

        old = self._current
        self._current = min(self._initial, self._current + 1)
        if self._current != old:
            self._last_adjust_time = now
            print(f"[并发控制] 恢复: {old} → {self._current}")

        self._success_streak = 0  # 重置计数
        self._error_count = 0

    def _adjust(self):
        """在限流信号出现时降级。"""
        now = time.time()
        if now - self._last_adjust_time < self._adjust_cooldown:
            return  # 冷却期内

        old = self._current
        self._current = max(self.min_concurrency, self._current - 1)

        if self._current != old:
            self._last_adjust_time = now
            print(f"[并发控制] 降级: {old} → {self._current}")

        # 重置计数
        self._error_count = 0
        self._success_streak = 0

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
            "mode": "adaptive",
            "avg_latency_ms": round(self.avg_latency, 1),
            "recent_samples": len(self._recent_latencies),
            "success_streak": self._success_streak,
        }
