"""Prometheus text-exposition metrics without external dependencies."""
from __future__ import annotations

import threading
import time


class Metrics:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self.gauges: dict[str, float] = {}
        self.latency_count = 0.0
        self.latency_sum = 0.0

    def inc(self, name: str, labels: dict[str, str] | None = None, by: float = 1.0) -> None:
        key = (name, tuple(sorted((labels or {}).items())))
        with self.lock:
            self.counters[key] = self.counters.get(key, 0.0) + by

    def gauge(self, name: str, value: float) -> None:
        with self.lock:
            self.gauges[name] = value

    def observe_latency(self, seconds: float) -> None:
        with self.lock:
            self.latency_count += 1
            self.latency_sum += seconds

    def render(self) -> str:
        lines: list[str] = []
        with self.lock:
            counters = sorted(self.counters.items())
            gauges = dict(self.gauges)
            count, total = self.latency_count, self.latency_sum
        seen: set[str] = set()
        for (name, labels), val in counters:
            if name not in seen:
                lines.append(f"# TYPE {name} counter")
                seen.add(name)
            lbl = "".join(f'{k}="{v}"' for k, v in labels)
            lines.append(f"{name}{{{lbl}}} {val}")
        for name, val in gauges.items():
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {val}")
        if count:
            lines.append("# TYPE rto_score_latency_seconds summary")
            lines.append(f'rto_score_latency_seconds_count {count}')
            lines.append(f'rto_score_latency_seconds_sum {round(total, 4)}')
        return "\n".join(lines) + "\n"


def now_ms(start: float) -> float:
    return round((time.monotonic() - start) * 1000, 2)
