from __future__ import annotations

import threading
from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerMetricsSnapshot:
    counters: dict[str, int]
    duration_ms: dict[str, int]


class WorkerMetrics:
    """Small process-local metrics set; durable progress remains in the database."""

    def __init__(self) -> None:
        self._counters: Counter[str] = Counter()
        self._duration_ms: Counter[str] = Counter()
        self._lock = threading.Lock()

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] += amount

    def observe_duration(self, task_type: str, seconds: float) -> None:
        with self._lock:
            self._duration_ms[task_type] += max(0, round(seconds * 1000))

    def snapshot(self) -> WorkerMetricsSnapshot:
        with self._lock:
            return WorkerMetricsSnapshot(
                counters=dict(self._counters),
                duration_ms=dict(self._duration_ms),
            )
