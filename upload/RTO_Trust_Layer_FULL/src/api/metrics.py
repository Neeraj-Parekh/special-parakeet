"""Prometheus text-exposition metrics without external dependencies."""
from __future__ import annotations

import threading
import time


class Metrics:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self.gauges: dict[str, float] = {}
        # Legacy latency summary (kept for backward-compat — the existing
        # ``rto_score_latency_seconds`` summary is rendered specially in
        # ``render()``). New code should use the generic ``summaries`` dict
        # below via ``observe_summary(name, value)``.
        self.latency_count = 0.0
        self.latency_sum = 0.0
        # Day 2 Track G — generic named summaries (count + sum + min + max)
        # for the drift detector-quality metrics Gama 2014 §5 calls out:
        # detection delay, false-alarm run length. We don't ship pre-defined
        # Prometheus histogram buckets (the survey's metrics are scalar
        # detectors' quality metrics, not distribution histograms) — the
        # summary's count + sum + avg is the demo-able form.
        self.summaries: dict[str, dict[str, float]] = {}

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

    def observe_summary(self, name: str, value: float) -> None:
        """Track a named summary (count + sum + min + max + last_value).

        Day 2 Track G — used by the feedback service to record:

        * ``rto_drift_detection_delay_seconds`` — wall-clock seconds
          between a prediction being made (recorded in the audit body)
          + the DRIFT signal firing on that prediction's delayed label.
          The survey §5 calls this "delay of detection"; the live
          implementation here is the demo-able scalar form (one value
          per detection — count + sum + avg is exposed for the SLO
          dashboard).
        * ``rto_drift_false_alarm_run_length`` — number of samples
          between two consecutive false alarms (WARNING/DRIFT that
          later turned out to be transient). The survey's "average run
          length between false alarms" — the scalar form is good enough
          for the demo.
        """
        with self.lock:
            s = self.summaries.setdefault(
                name,
                {"count": 0.0, "sum": 0.0, "min": float("inf"), "max": float("-inf"), "last": 0.0},
            )
            s["count"] += 1
            s["sum"] += float(value)
            s["min"] = min(s["min"], float(value))
            s["max"] = max(s["max"], float(value))
            s["last"] = float(value)

    def render(self) -> str:
        lines: list[str] = []
        with self.lock:
            counters = sorted(self.counters.items())
            gauges = dict(self.gauges)
            count, total = self.latency_count, self.latency_sum
            summaries = {k: dict(v) for k, v in self.summaries.items()}
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
        # Day 2 Track G — render the named summaries as Prometheus summary
        # families (count + sum + last_value). Each becomes a 3-line block
        # in the text exposition; ``last`` is exposed as a gauge sibling so
        # the Grafana panel can chart the most-recent detection delay +
        # false-alarm run-length over time (the survey's detector-quality
        # SLO dashboard).
        for name, s in summaries.items():
            avg = (s["sum"] / s["count"]) if s["count"] else 0.0
            lines.append(f"# TYPE {name} summary")
            lines.append(f"{name}_count {int(s['count'])}")
            lines.append(f"{name}_sum {round(s['sum'], 4)}")
            lines.append(f"{name}_avg {round(avg, 4)}")
            lines.append(f"{name}_last {round(s['last'], 4)}")
            lines.append(f"{name}_min {round(s['min'], 4) if s['min'] != float('inf') else 0}")
            lines.append(f"{name}_max {round(s['max'], 4) if s['max'] != float('-inf') else 0}")
        return "\n".join(lines) + "\n"


def now_ms(start: float) -> float:
    return round((time.monotonic() - start) * 1000, 2)

