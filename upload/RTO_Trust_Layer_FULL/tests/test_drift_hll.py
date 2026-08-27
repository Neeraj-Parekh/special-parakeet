"""Tests for the 4th drift detector — HLL cardinality-spike — covering
the two DO BADLY fixes from Wave 3 Subagent 15-a:

* DO BADLY #1 — HLL cold-start false-positive. The detector used to fire
  on the very first burst minute because the rolling baseline was either
  empty or based on a tiny HLL estimate. Fix: a warmup period (the first
  ``WARMUP_MIN_EVENTS=1000`` events are buffered, no spike detection
  fires until the baseline cardinality is established) + a minimum-bucket-
  size guard (``MIN_BUCKET_CARDINALITY=10`` — HLL estimates below this
  threshold are too unreliable to spike-check).

* DO BADLY #2 — Spike-factor calibration. The spike threshold used to be
  a hardcoded ``HLL_SPIKE_FACTOR=3.0`` constant. Fix: derive the threshold
  from the rolling 3σ of per-minute cardinality samples (mean + 3*std of
  the last ``SPIKE_JUMP_HISTORY_SIZE=100`` completed minutes); below
  ``SPIKE_CALIBRATION_MIN_SAMPLES=30`` samples, the conservative legacy
  3.0x multiplier applies while the rolling stats warm up.

Bonus test — the in-memory ``_hll_cardinality_history`` dict is bounded
by ``HLL_HISTORY_CAP=10000`` (defense in depth beyond the lookback trim).

These tests follow the same bypass-__init__ pattern as
``test_streaming.py::test_stream_processor_detects_hll_cardinality_spike``
so they exercise ONLY the HLL spike path without needing Redis.
"""
from __future__ import annotations

import sys
from collections import OrderedDict, deque
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.stream.processor import StreamProcessor  # noqa: E402
from src.stream.producer import (  # noqa: E402
    STREAM_MODEL_DRIFT,
    STREAM_RISK_SCORES,
)


# --- Shared test harness ----------------------------------------------------


def _make_processor(
    *,
    warmup_min_events: int = 0,
    min_bucket_cardinality: int = 10,
    hll_spike_factor: float = 3.0,
    spike_calibration_min_samples: int = 30,
    spike_jump_history_size: int = 100,
    hll_history_cap: int = 10000,
    hll_spike_lookback_min: int = 10,
    warmup_seen: int = 0,
    history: dict[int, int] | None = None,
    jump_history: deque[float] | None = None,
    last_minute_bucket: int | None = None,
) -> StreamProcessor:
    """Build a StreamProcessor instance via ``__new__`` (bypasses
    ``__init__`` so no Redis connection is attempted). Sets the minimum
    instance attributes that ``_handle_message`` + ``_detect_anomalies``
    touch, mirroring the pattern in test_streaming.py.
    """
    proc = StreamProcessor.__new__(StreamProcessor)
    proc.redis_url = None
    proc.consumer_name = "test-drift-hll"
    proc.client = None
    proc._stop = False
    proc._group_ensured = False
    proc._window = deque()
    proc._seen_order_ids = {}
    proc._seen_cap_warned = False
    proc._hll_cardinality_history = (
        OrderedDict(history) if history is not None else OrderedDict()
    )
    proc._last_minute_bucket = last_minute_bucket
    proc._warmup_seen = warmup_seen
    proc._spike_jump_history = (
        jump_history
        if jump_history is not None
        else deque(maxlen=spike_jump_history_size)
    )
    proc._baseline_rate = None
    proc._baseline_score_mean = None
    proc._baseline_score_std = None
    proc.WINDOW_SECONDS = 600
    proc.BASELINE_SEED = 30
    proc.HLL_KEY_PREFIX = "rto:stream:hll"
    proc.RATE_SPIKE_MULTIPLIER = 3.0
    proc.SCORE_DRIFT_SIGMA = 2.0
    proc.HLL_SPIKE_FACTOR = hll_spike_factor
    proc.HLL_SPIKE_LOOKBACK_MIN = hll_spike_lookback_min
    proc.WARMUP_MIN_EVENTS = warmup_min_events
    proc.MIN_BUCKET_CARDINALITY = min_bucket_cardinality
    proc.SPIKE_JUMP_HISTORY_SIZE = spike_jump_history_size
    proc.SPIKE_CALIBRATION_MIN_SAMPLES = spike_calibration_min_samples
    proc.HLL_HISTORY_CAP = hll_history_cap
    proc.SEEN_ORDER_IDS_CAP = 10000
    proc.GROUP = "test-processors"

    # Mock producer to capture model.drift publishes.
    proc.producer = MagicMock()
    proc.producer.publish = MagicMock(side_effect=lambda s, f: "mock-drift-id")
    # Stub out _connect + _hll_add_order so the Redis path is skipped.
    proc._connect = lambda: None  # type: ignore[assignment]
    proc._hll_add_order = lambda oid, bucket: None  # type: ignore[assignment]
    # _hll_count_orders is set per-test (the harness leaves it as None
    # so the test can install its own mock).
    proc._hll_count_orders = lambda bucket: None  # type: ignore[assignment]
    return proc


def _fields(order_id: str, score: str = "0.500") -> dict:
    return {
        "prediction_id": f"pid-{order_id}",
        "order_id": order_id,
        "score": score,
        "ts": "2026-01-03T00:01:00+00:00",
    }


def _patch_time(proc_mod, minute: int):
    """Replace ``proc_mod.time`` so ``time.time()`` returns a fixed
    minute. Returns (orig_time, fake_time) so the caller can restore.
    """
    orig = proc_mod.time
    fake = type("T", (), {"time": staticmethod(lambda: minute * 60 + 5)})()
    proc_mod.time = fake
    return orig, fake


def _spike_publishes(captured: list[dict]) -> list[dict]:
    """Filter the captured publish calls down to hll_cardinality_spike
    publishes (the 4th detector's reason)."""
    return [
        d
        for d in captured
        if d["stream"] == STREAM_MODEL_DRIFT
        and d["fields"].get("anomaly_reason") == "hll_cardinality_spike"
    ]


# --- DO BADLY #1 — HLL cold-start warmup ------------------------------------


def test_hll_cold_start_warmup_first_999_events_no_spike():
    """The first ``WARMUP_MIN_EVENTS=1000`` events don't fire a spike
    even when a real spike is present (current_count = 200, way above
    the conservative-default threshold of avg*3 = 50*3 = 150). The
    warmup gate suppresses the spike check until the baseline
    cardinality is established.

    This is the false-positive protection — without the warmup gate,
    the very first burst would fire a spike based on a 1-sample
    "baseline" (the pre-seeded 50) which is statistically meaningless.
    """
    proc = _make_processor(
        warmup_min_events=1000,  # default; first 1000 events buffered
        min_bucket_cardinality=10,
        warmup_seen=0,  # cold-start
        history={1_700_000_000 // 60: 50},  # 1 completed minute baseline
    )
    current_minute = (1_700_000_000 // 60) + 1
    # Mock _hll_count_orders to return a "real spike" count for the
    # current minute (200 > 50*3=150 would normally fire). Returns the
    # pre-seeded baseline count (50) for the historical minute.
    proc._hll_count_orders = lambda b: (  # type: ignore[assignment]
        200 if b == current_minute else 50
    )

    captured: list[dict] = []
    proc.producer.publish = MagicMock(
        side_effect=lambda s, f: captured.append(
            {"stream": s, "fields": dict(f)}
        )
        or "mock-drift-id"
    )

    import src.stream.processor as proc_mod

    orig_time, fake_time = _patch_time(proc_mod, current_minute)
    try:
        # Feed 999 unique events — all in the burst minute. Each one
        # would individually fire a spike (count=200 > 150), but the
        # warmup gate suppresses the spike check until event 1000.
        for i in range(999):
            proc._handle_message(STREAM_RISK_SCORES, _fields(f"ORD-{i:04d}"))
        spikes = _spike_publishes(captured)
        assert len(spikes) == 0, (
            f"warmup should suppress HLL spikes for first 999 events; "
            f"got {len(spikes)} spike publishes (warmup_seen="
            f"{proc._warmup_seen})"
        )
        # Sanity: warmup counter advanced exactly 999.
        assert proc._warmup_seen == 999, (
            f"warmup counter should be 999 after 999 events; "
            f"got {proc._warmup_seen}"
        )

        # Event 1000 — warmup done. The same real-spike condition
        # (count=200 > 150) now fires exactly one spike.
        proc._handle_message(STREAM_RISK_SCORES, _fields("ORD-1000"))
        spikes = _spike_publishes(captured)
        assert len(spikes) == 1, (
            f"warmup done at event 1000; expected exactly 1 spike "
            f"publish (count=200 > avg*3=150); got {len(spikes)} "
            f"(warmup_seen={proc._warmup_seen})"
        )
        # The published spike_factor is the conservative default 3.0
        # because the 3σ calibration floor (30 samples) isn't met yet.
        assert spikes[0]["fields"]["spike_factor"] == "3.0"
        assert spikes[0]["fields"]["calibration"] == "conservative_default"
        # Warmup counter advanced to 1000.
        assert proc._warmup_seen == 1000
    finally:
        proc_mod.time = orig_time


def test_hll_cold_start_min_bucket_size_guard():
    """The minimum-bucket-size guard skips the spike check when the HLL
    estimate is below ``MIN_BUCKET_CARDINALITY`` — the estimate is
    unreliable on tiny buckets (HLL's ~0.81% relative error is an
    absolute error of ~8 elements on a 1000-element bucket; on a 5-elem
    bucket PFCOUNT can swing wildly because there are too few register
    collisions to converge).
    """
    # Warmup done (warmup_seen=1000, threshold=1000).
    proc = _make_processor(
        warmup_min_events=1000,
        min_bucket_cardinality=100,  # tight guard — count must be >= 100
        warmup_seen=1000,
        history={1_700_000_000 // 60: 50},
    )
    current_minute = (1_700_000_000 // 60) + 1
    # Mock returns 60 — would normally be a spike (60 > 50*3=150? NO,
    # 60 < 150). Wait — we need a setup where count would normally fire
    # but the guard skips it. Let me redo: baseline=20, count=100,
    # threshold=20*3=60. 100 > 60 → would fire. But guard requires
    # count >= 100 → 100 == 100 → passes guard. Hmm.
    # Better: set the guard to 200. count=100 < 200 → guard skips.
    proc.MIN_BUCKET_CARDINALITY = 200
    proc._hll_count_orders = lambda b: (  # type: ignore[assignment]
        100 if b == current_minute else 20
    )
    proc._hll_cardinality_history = OrderedDict(
        {(1_700_000_000 // 60): 20}
    )

    captured: list[dict] = []
    proc.producer.publish = MagicMock(
        side_effect=lambda s, f: captured.append(
            {"stream": s, "fields": dict(f)}
        )
        or "mock-drift-id"
    )

    import src.stream.processor as proc_mod

    orig_time, _ = _patch_time(proc_mod, current_minute)
    try:
        proc._handle_message(STREAM_RISK_SCORES, _fields("ORD-TINY"))
        spikes = _spike_publishes(captured)
        # count=100 < MIN_BUCKET_CARDINALITY=200 → guard skips spike
        # check entirely. Even though 100 > 20*3=60 (would normally fire),
        # the guard suppresses it.
        assert len(spikes) == 0, (
            f"MIN_BUCKET_CARDINALITY guard should skip the spike check "
            f"when count (100) < threshold (200); got {len(spikes)} "
            f"spike publishes"
        )
    finally:
        proc_mod.time = orig_time


# --- DO BADLY #2 — Spike-factor 3σ calibration -------------------------------


def test_hll_spike_factor_calibration_3sigma_fires_on_huge_jump():
    """Once ``SPIKE_CALIBRATION_MIN_SAMPLES=30`` samples accrue in the
    rolling jump-history deque, the spike threshold becomes
    ``mean + 3*std`` of the deque (rolling 3σ above the rolling mean of
    cardinality jumps). A huge jump (well above mean+3σ) fires the
    spike detector with ``calibration=rolling_3sigma``.
    """
    # Pre-seed the jump history with 100 samples of count=50 (so
    # mean=50, std=0 → threshold=50 + 3*0 = 50). Any count > 50 fires.
    # But to make the "huge jump" meaningful, set std slightly > 0.
    # Use 100 samples: 50 of count=40 + 50 of count=60 → mean=50,
    # std=10 (population). threshold = 50 + 30 = 80.
    jump_history = deque(
        [40.0] * 50 + [60.0] * 50, maxlen=100
    )
    proc = _make_processor(
        warmup_min_events=0,  # warmup done immediately
        min_bucket_cardinality=10,
        warmup_seen=0,
        history={1_700_000_000 // 60: 50},  # 1 completed minute in dict
        jump_history=jump_history,
        spike_calibration_min_samples=30,  # 100 samples > 30 → 3σ active
    )
    current_minute = (1_700_000_000 // 60) + 1
    # current_count = 200 — well above the 3σ threshold of 80.
    proc._hll_count_orders = lambda b: (  # type: ignore[assignment]
        200 if b == current_minute else 50
    )

    captured: list[dict] = []
    proc.producer.publish = MagicMock(
        side_effect=lambda s, f: captured.append(
            {"stream": s, "fields": dict(f)}
        )
        or "mock-drift-id"
    )

    import src.stream.processor as proc_mod

    orig_time, _ = _patch_time(proc_mod, current_minute)
    try:
        proc._handle_message(STREAM_RISK_SCORES, _fields("ORD-HUGE-JUMP"))
        spikes = _spike_publishes(captured)
        assert len(spikes) == 1, (
            f"3σ calibration: 200 > mean(50)+3*std(10)=80 should fire "
            f"a spike; got {len(spikes)} publishes"
        )
        sp = spikes[0]
        # Calibration field reports rolling_3sigma (not conservative).
        assert sp["fields"]["calibration"] == "rolling_3sigma", (
            f"calibration should be 'rolling_3sigma' when deque has "
            f">= 30 samples; got {sp['fields']['calibration']}"
        )
        # The spike_factor field reports the effective multiplier
        # (threshold / avg_count). threshold = 80, avg = 50 → 1.6.
        # (avg_count is the rolling mean of _hll_cardinality_history
        # values, which is {50} → 50.0.)
        spike_factor = float(sp["fields"]["spike_factor"])
        # 80 / 50 = 1.6 (within float tolerance).
        assert 1.55 <= spike_factor <= 1.65, (
            f"spike_factor (threshold/avg) should be ~1.6; got "
            f"{spike_factor}"
        )
    finally:
        proc_mod.time = orig_time


def test_hll_spike_factor_calibration_3sigma_no_fire_on_same_magnitude():
    """When the rolling jump-history deque is full of samples with the
    SAME magnitude (std=0), the 3σ threshold = mean + 3*0 = mean. A
    new sample with the SAME magnitude (count == mean) does NOT exceed
    the threshold → no spike fires. This is the calibration's job:
    a stable cardinality baseline shouldn't fire on every minute that
    matches it.
    """
    # 100 samples all = 50 → mean=50, std=0, threshold=50.
    jump_history = deque([50.0] * 100, maxlen=100)
    proc = _make_processor(
        warmup_min_events=0,
        min_bucket_cardinality=10,
        warmup_seen=0,
        history={1_700_000_000 // 60: 50},
        jump_history=jump_history,
        spike_calibration_min_samples=30,
    )
    current_minute = (1_700_000_000 // 60) + 1
    # current_count = 50 — exactly the mean. 50 > 50? No → no fire.
    proc._hll_count_orders = lambda b: (  # type: ignore[assignment]
        50 if b == current_minute else 50
    )

    captured: list[dict] = []
    proc.producer.publish = MagicMock(
        side_effect=lambda s, f: captured.append(
            {"stream": s, "fields": dict(f)}
        )
        or "mock-drift-id"
    )

    import src.stream.processor as proc_mod

    orig_time, _ = _patch_time(proc_mod, current_minute)
    try:
        proc._handle_message(STREAM_RISK_SCORES, _fields("ORD-SAME-MAG"))
        spikes = _spike_publishes(captured)
        assert len(spikes) == 0, (
            f"3σ calibration: count (50) == mean (50) → NOT > mean+3*0 "
            f"→ no spike should fire; got {len(spikes)} publishes"
        )
    finally:
        proc_mod.time = orig_time


def test_hll_spike_factor_calibration_conservative_default_below_floor():
    """Below the ``SPIKE_CALIBRATION_MIN_SAMPLES=30`` calibration floor,
    the conservative legacy ``HLL_SPIKE_FACTOR=3.0x`` multiplier applies
    (so existing behavior is preserved while the rolling stats warm
    up). The calibration field reports ``conservative_default``.
    """
    # Only 5 samples in the deque (< 30 floor) → conservative default.
    jump_history = deque([50.0] * 5, maxlen=100)
    proc = _make_processor(
        warmup_min_events=0,
        min_bucket_cardinality=10,
        warmup_seen=0,
        history={1_700_000_000 // 60: 50},
        jump_history=jump_history,
        spike_calibration_min_samples=30,
    )
    current_minute = (1_700_000_000 // 60) + 1
    # current_count = 200 — well above 50*3=150 conservative default.
    proc._hll_count_orders = lambda b: (  # type: ignore[assignment]
        200 if b == current_minute else 50
    )

    captured: list[dict] = []
    proc.producer.publish = MagicMock(
        side_effect=lambda s, f: captured.append(
            {"stream": s, "fields": dict(f)}
        )
        or "mock-drift-id"
    )

    import src.stream.processor as proc_mod

    orig_time, _ = _patch_time(proc_mod, current_minute)
    try:
        proc._handle_message(STREAM_RISK_SCORES, _fields("ORD-CONSERVATIVE"))
        spikes = _spike_publishes(captured)
        assert len(spikes) == 1, (
            f"conservative default: 200 > 50*3=150 should fire; got "
            f"{len(spikes)} publishes"
        )
        sp = spikes[0]
        assert sp["fields"]["calibration"] == "conservative_default"
        assert sp["fields"]["spike_factor"] == "3.0"
    finally:
        proc_mod.time = orig_time


# --- Bonus — Bounded LRU on _hll_cardinality_history ------------------------


def test_hll_history_lru_bound_caps_at_hll_history_cap():
    """The in-memory ``_hll_cardinality_history`` dict is bounded by
    ``HLL_HISTORY_CAP=10000``. When a minute rollover drives the dict
    over the cap, the oldest entries (FIFO; OrderedDict preserves
    insertion order) are evicted until len == cap.
    """
    proc = _make_processor(
        warmup_min_events=0,  # warmup done
        warmup_seen=0,
        hll_history_cap=10000,
        # Huge lookback so the time-based trim doesn't evict anything
        # — we want to test the LRU bound in isolation.
        hll_spike_lookback_min=10_000_000,
    )
    # Insert 10001 keys directly — all in the far future so the
    # lookback trim (cutoff = current_minute - lookback) won't evict.
    base = 1_000_000
    for i in range(10001):
        proc._hll_cardinality_history[base + i] = 50
    assert len(proc._hll_cardinality_history) == 10001

    # Drive a minute rollover from minute base+10002 → base+10003. The
    # trim block will:
    # 1. snapshot prev_count (mocked to return 50) for minute base+10002
    #    → history grows to 10002.
    # 2. lookback trim with cutoff = (base+10003) - 10_000_000 — all
    #    inserted minutes (>= base) are well above this cutoff, so
    #    nothing is evicted by lookback.
    # 3. LRU bound: while len > HLL_HISTORY_CAP (10000), evict oldest.
    #    Evicts 2 entries (the oldest two: base+0 and base+1).
    prev_minute = base + 10002
    current_minute = base + 10003
    proc._last_minute_bucket = prev_minute
    proc._hll_count_orders = lambda b: 50  # type: ignore[assignment]

    import src.stream.processor as proc_mod

    orig_time, _ = _patch_time(proc_mod, current_minute)
    try:
        proc._detect_anomalies(_fields("ORD-LRU"), current_minute * 60 + 5)
    finally:
        proc_mod.time = orig_time

    assert len(proc._hll_cardinality_history) == 10000, (
        f"LRU bound: dict should be capped at HLL_HISTORY_CAP=10000; "
        f"got {len(proc._hll_cardinality_history)}"
    )
    # The OLDEST two entries (base+0, base+1) should have been evicted.
    assert (base + 0) not in proc._hll_cardinality_history
    assert (base + 1) not in proc._hll_cardinality_history
    # The newest entry (base+10000) should still be present.
    assert (base + 10000) in proc._hll_cardinality_history
    # The snapshot of the prev_minute should be present.
    assert prev_minute in proc._hll_cardinality_history
    # The jump-history deque should have grown by 1 (the snapshot).
    assert len(proc._spike_jump_history) == 1
    assert proc._spike_jump_history[0] == 50.0
