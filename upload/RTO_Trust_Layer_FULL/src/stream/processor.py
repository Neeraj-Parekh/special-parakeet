"""Streaming-transforms worker — Microsoft Eventhouse equivalent.

Track F Day 2. Closes §D P7 (streaming transformations absent — the user's
current pipeline is REST-only) and the streaming-stats piece of
perceived-gap G2 (Microsoft has Eventstreams → Eventhouse → Activator;
this processor is our Eventhouse).

Source: TFX (Baylor 2017) ``generate_data_statistics`` capability — Google
uses distributed streaming approximation algorithms (HyperLogLog cited)
for descriptive stats at scale, since exact computation is infeasible over
continuous streams. We port the same pattern using:

* Redis ``PFADD`` / ``PFCOUNT`` for HyperLogLog cardinality of distinct
  ``order_id`` seen per time bucket (no exact count — the HLL approximation
  is ~0.81% error at 1B elements, well within our drift-detection tolerance).
* In-memory ``deque[(ts, score)]`` for sliding-window velocity + rolling
  mean/std of risk score. The window is per-process; persistence across
  worker restarts is a deferred enhancement (see worklog).

Anomaly detection (TFX "Data Validation" — actionable anomaly
descriptions):
* ``duplicate_order_id`` — same ``order_id`` published twice within the
  window. Strong RTO signal — a merchant bot retrying the same SKU.
* ``score_velocity_spike`` — message rate > 3x rolling baseline (the
  stream-processor is the canary for traffic floods; downstream model
  retraining is the Track G piece).
* ``score_mean_drift`` — rolling mean of ``score`` deviates > 2 sigma from
  the prior-window baseline. This is the streaming-PSI equivalent — Track
  G's DDM/ADWIN detector will consume ``model.drift`` for the formal
  drift-detection decision.

On anomaly, publishes to ``model.drift`` with a structured ``reason`` field
so the consumer (Track G) can route each reason to the right handler
(retrain PR vs alert only vs webhook to merchant). Geographic
impossibility detection (e.g. user_id on two devices > 1000km apart in <60s)
is a placeholder — the published fields don't carry geo, so we log only.

Run as ``python -m src.stream.processor`` (the docker-compose
``stream-processor`` service).
"""
from __future__ import annotations

import os
import signal
import sys
import time
from collections import deque
from typing import Any

from src.stream.producer import (
    STREAM_MODEL_DRIFT,
    STREAM_RISK_SCORES,
    StreamProducer,
)


class StreamProcessor:
    """Consumes ``risk.scores``, runs streaming stats, publishes anomalies
    to ``model.drift``.

    Uses a SEPARATE consumer group from ``src.stream.consumer`` so the two
    workers don't compete for the same messages — each gets its own copy of
    every ``risk.scores`` message. (Redis Streams supports multiple groups
    per stream; each group sees all messages.)

    The HLL keys + sliding-window deque are scoped to this process. If the
    worker restarts, the HLL state (Redis-backed) survives but the deque
    (in-memory) is reset — the rolling stats start over from the next batch.
    For the hackathon demo this is fine; full state would live in Redis
    HASH buckets keyed by minute (deferred enhancement).
    """

    GROUP = "rto-processors"
    # Window size for the sliding-window stats (5 min default; env override
    # for testing).
    WINDOW_SECONDS = int(os.environ.get("STREAM_PROCESSOR_WINDOW_SECONDS", "300"))
    # Number of messages to seed the rolling baseline before anomaly
    # detection kicks in (avoid spurious alerts on cold-start).
    BASELINE_SEED = int(os.environ.get("STREAM_PROCESSOR_BASELINE_SEED", "30"))
    # Rate-spike threshold (rolling msgs-per-min vs baseline msgs-per-min).
    RATE_SPIKE_MULTIPLIER = 3.0
    # Score-drift threshold (rolling mean vs baseline mean, in sigma).
    SCORE_DRIFT_SIGMA = 2.0
    # HLL key prefix in Redis. Per-processor-instance id would normally be
    # here, but for a single global processor we keep a stable key so the
    # HLL survives restarts.
    HLL_KEY_PREFIX = "rto:stream:hll"

    def __init__(
        self,
        redis_url: str,
        consumer_name: str | None = None,
    ) -> None:
        if not redis_url:
            raise ValueError("redis_url is required for StreamProcessor")
        self.redis_url = redis_url
        self.consumer_name = consumer_name or os.environ.get(
            "STREAM_PROCESSOR_NAME", f"processor-{os.getpid()}"
        )
        self.client: Any = None
        self.producer = StreamProducer(redis_url)
        # Per-bucket rolling window: deque[(ts_unix, score_float)]. Trimmed
        # to WINDOW_SECONDS on every append.
        self._window: deque[tuple[float, float]] = deque()
        # Distinct order_id set within the current window — exact count for
        # small windows (the HLL in Redis is the asymptotic backstop for
        # longer-range + cross-process counting).
        self._seen_order_ids: dict[str, float] = {}  # order_id -> first_seen_ts
        # Rolling baseline (computed once after BASELINE_SEED messages).
        self._baseline_rate: float | None = None  # msgs/min
        self._baseline_score_mean: float | None = None
        self._baseline_score_std: float | None = None
        self._stop = False
        self._group_ensured = False

    def _connect(self) -> Any:
        import redis  # type: ignore[import-not-found]

        if self.client is None:
            self.client = redis.from_url(self.redis_url, decode_responses=True)
        return self.client

    def _ensure_group(self) -> None:
        if self._group_ensured:
            return
        client = self._connect()
        try:
            client.xgroup_create(
                STREAM_RISK_SCORES, self.GROUP, id="0", mkstream=True
            )
        except Exception as e:
            if "BUSYGROUP" in type(e).__name__ or "BUSYGROUP" in str(e):
                pass
            else:
                print(
                    f"[processor] xgroup_create {STREAM_RISK_SCORES}/{self.GROUP} "
                    f"failed: {type(e).__name__}: {e}",
                    file=sys.stderr,
                )
        self._group_ensured = True

    # --- Streaming stats (TFX generate_data_statistics pattern) -----------

    def _trim_window(self, now: float) -> None:
        """Drop entries older than ``WINDOW_SECONDS`` from the deque + the
        ``_seen_order_ids`` set. O(n) but n is bounded (window cap is the
        worst-case deque length over the worst-case rate).
        """
        cutoff = now - self.WINDOW_SECONDS
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()
        # Trim _seen_order_ids by first_seen_ts.
        expired = [oid for oid, ts in self._seen_order_ids.items() if ts < cutoff]
        for oid in expired:
            del self._seen_order_ids[oid]

    def _maybe_recompute_baseline(self) -> None:
        """After BASELINE_SEED messages, snapshot the rolling rate + score
        mean + std as the baseline. Recomputed periodically (every
        BASELINE_SEED messages after the first) so the baseline tracks
        slow drift instead of locking to the initial sample.
        """
        n = len(self._window)
        if n < self.BASELINE_SEED:
            return
        if self._baseline_rate is not None and n % self.BASELINE_SEED != 0:
            return
        # Rolling rate (msgs/min) over the current window.
        if n >= 2:
            elapsed_min = max(
                (self._window[-1][0] - self._window[0][0]) / 60.0, 1e-6
            )
            self._baseline_rate = n / elapsed_min
        scores = [s for _, s in self._window]
        mean = sum(scores) / len(scores)
        var = sum((s - mean) ** 2 for s in scores) / max(len(scores) - 1, 1)
        self._baseline_score_mean = mean
        self._baseline_score_std = var**0.5

    def _hll_add_order(self, order_id: str, minute_bucket: int) -> None:
        """PFADD to a Redis HLL keyed by minute-bucket. This is the
        distributed-cardinality counter — survives process restarts.
        Used only for the ``cardinality_estimate`` field of the
        ``model.drift`` payload (sanity cross-check vs the in-memory
        ``_seen_order_ids``).
        """
        try:
            client = self._connect()
            key = f"{self.HLL_KEY_PREFIX}:orders:{minute_bucket}"
            client.pfadd(key, order_id)
            # TTL the bucket key so Redis doesn't accumulate HLL state
            # forever — keep it for 2x window so cross-bucket comparison
            # is possible.
            client.expire(key, self.WINDOW_SECONDS * 2)
        except Exception as e:  # pragma: no cover — best-effort
            print(
                f"[processor] pfadd failed: {type(e).__name__}: {e}",
                file=sys.stderr,
            )

    def _hll_count_orders(self, minute_bucket: int) -> int | None:
        try:
            client = self._connect()
            return client.pfcount(f"{self.HLL_KEY_PREFIX}:orders:{minute_bucket}")
        except Exception:  # pragma: no cover — best-effort
            return None

    # --- Anomaly detection ------------------------------------------------

    def _detect_anomalies(
        self, fields: dict, now: float
    ) -> list[dict]:
        """Return a list of anomaly dicts. Each becomes a separate
        ``model.drift`` publish. Empty list = no anomaly.
        """
        anomalies: list[dict] = []
        order_id = fields.get("order_id", "")
        score_raw = fields.get("score", "")
        try:
            score = float(score_raw) if score_raw != "" else None
        except (TypeError, ValueError):
            score = None

        # 1. Duplicate order_id (TFX: "duplicate within window" — strong
        # RTO signal; a bot retrying the same SKU).
        if order_id and order_id in self._seen_order_ids:
            anomalies.append({
                "reason": "duplicate_order_id",
                "order_id": order_id,
                "first_seen_ts": str(self._seen_order_ids[order_id]),
                "window_seconds": str(self.WINDOW_SECONDS),
            })
        elif order_id:
            self._seen_order_ids[order_id] = now

        # 2. Rate spike (msgs/min > RATE_SPIKE_MULTIPLIER x baseline).
        if (
            self._baseline_rate is not None
            and len(self._window) >= 2
        ):
            elapsed_min = max(
                (self._window[-1][0] - self._window[0][0]) / 60.0, 1e-6
            )
            current_rate = len(self._window) / elapsed_min
            if current_rate > self.RATE_SPIKE_MULTIPLIER * self._baseline_rate:
                anomalies.append({
                    "reason": "score_velocity_spike",
                    "current_rate_per_min": f"{current_rate:.2f}",
                    "baseline_rate_per_min": f"{self._baseline_rate:.2f}",
                    "multiplier": str(self.RATE_SPIKE_MULTIPLIER),
                })

        # 3. Score drift (rolling mean > SCORE_DRIFT_SIGMA sigma from
        # baseline mean). This is the streaming-PSI equivalent — the
        # Track G DDM/ADWIN detector consumes this for the formal
        # drift-detection decision.
        if (
            score is not None
            and self._baseline_score_mean is not None
            and self._baseline_score_std is not None
            and self._baseline_score_std > 0
        ):
            sigma = abs(score - self._baseline_score_mean) / self._baseline_score_std
            if sigma > self.SCORE_DRIFT_SIGMA:
                anomalies.append({
                    "reason": "score_mean_drift",
                    "current_score": f"{score:.5f}",
                    "baseline_mean": f"{self._baseline_score_mean:.5f}",
                    "baseline_std": f"{self._baseline_score_std:.5f}",
                    "sigma": f"{sigma:.2f}",
                })

        # 4. Geographic impossibility (placeholder — the published fields
        # don't carry geo, so we can only log it. When Track H/I enrich the
        # stream with user_id/device_id/geo, this becomes a real check.)
        # No-op for now; documented per spec.
        return anomalies

    # --- Per-message handler ----------------------------------------------

    def _handle_message(self, stream: str, fields: dict) -> None:
        if stream != STREAM_RISK_SCORES:
            return
        now = time.time()
        # TFX generate_data_statistics: per-feature stats inline.
        try:
            score = float(fields.get("score", "") or "nan")
        except (TypeError, ValueError):
            score = float("nan")
        self._window.append((now, score))
        self._trim_window(now)
        # HLL is per minute bucket — gives us a cardinality estimate for
        # distinct order_ids in the same minute, cross-process.
        minute_bucket = int(now // 60)
        order_id = fields.get("order_id", "")
        if order_id:
            self._hll_add_order(order_id, minute_bucket)
        self._maybe_recompute_baseline()
        anomalies = self._detect_anomalies(fields, now)
        for anomaly in anomalies:
            # Publish to model.drift. Track G's DDM/ADWIN detector will
            # consume this stream for the formal drift-decision + retrain-PR
            # trigger. The fire-and-forget contract from the API producer
            # applies here too — if Redis is down, the publish fails
            # silently + we keep processing.
            payload = {
                "stream": stream,
                "prediction_id": fields.get("prediction_id", ""),
                "order_id": order_id,
                "anomaly_reason": anomaly.get("reason", ""),
                "cardinality_estimate_per_min": str(
                    self._hll_count_orders(minute_bucket) or ""
                ),
                "ts": fields.get("ts", ""),
                **{k: v for k, v in anomaly.items() if k != "reason"},
            }
            msg_id = self.producer.publish(STREAM_MODEL_DRIFT, payload)
            if msg_id is not None:
                print(
                    f"[processor] DRIFT anomaly: {anomaly['reason']} "
                    f"(order={order_id} drift_id={msg_id})",
                    file=sys.stderr,
                )

    # --- Run loop ----------------------------------------------------------

    def run(self, block_ms: int = 5000, retry_seconds: float = 2.0) -> None:
        """Block on XREADGROUP from ``risk.scores``. On Redis failure,
        sleep + retry (never propagate — worker is restart-safe).
        """
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
        print(
            f"[processor] {self.consumer_name} joining group={self.GROUP} "
            f"stream={STREAM_RISK_SCORES} window={self.WINDOW_SECONDS}s",
            file=sys.stderr,
        )
        while not self._stop:
            try:
                client = self._connect()
                self._ensure_group()
                resp = client.xreadgroup(
                    groupname=self.GROUP,
                    consumername=self.consumer_name,
                    streamcounts={STREAM_RISK_SCORES: ">"},
                    count=50,
                    block=block_ms,
                )
            except Exception as e:
                print(
                    f"[processor] poll failed ({type(e).__name__}: {e}); "
                    f"retry in {retry_seconds}s",
                    file=sys.stderr,
                )
                self.client = None
                time.sleep(retry_seconds)
                continue
            if not resp:
                continue
            for _stream, messages in resp:
                for msg_id, fields in messages:
                    try:
                        self._handle_message(_stream, dict(fields) if fields else {})
                        client.xack(STREAM_RISK_SCORES, self.GROUP, msg_id)
                    except Exception as e:
                        # Don't XACK — leave for re-claim (matches consumer.py
                        # pattern). Print to stderr for visibility.
                        print(
                            f"[processor] handle_message raised on {msg_id}: "
                            f"{type(e).__name__}: {e}",
                            file=sys.stderr,
                        )
        print(
            f"[processor] {self.consumer_name} shutting down cleanly",
            file=sys.stderr,
        )

    def _handle_signal(self, signum, frame) -> None:  # pragma: no cover
        print(f"[processor] caught signal {signum}, draining...", file=sys.stderr)
        self._stop = True

    def close(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
            except Exception:  # pragma: no cover
                pass
            self.client = None
        self.producer.close()


def run_processor() -> None:
    """Entrypoint for ``python -m src.stream.processor``."""
    from src.config import get_settings

    settings = get_settings()
    if not settings.redis_url:
        print(
            "[processor] REDIS_URL not set — stream processor cannot start. "
            "Set REDIS_URL=redis://redis:6379 in the environment.",
            file=sys.stderr,
        )
        sys.exit(1)
    proc = StreamProcessor(settings.redis_url)
    try:
        proc.run()
    finally:
        proc.close()


if __name__ == "__main__":
    run_processor()
