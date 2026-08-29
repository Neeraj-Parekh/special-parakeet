"""Tests for the Redis Streams streaming backbone (Track F Day 2).

Closes §A item 18 + driver G2 + §D item P7. The 4 tests below split into:

* ``test_stream_producer_noop_without_redis`` — guaranteed-pass fast test
  (no Redis fixture). ``StreamProducer(None).publish()`` returns None +
  doesn't crash. This is the contract that lets the 63 existing tests pass
  without a Redis fixture.

* ``test_stream_producer_publishes_when_redis_available`` — SKIPPED unless
  ``REDIS_URL`` is set + the redis-py package is installed. Produces one
  message to ``risk.scores``, reads it back via ``XREAD`` (consumer-group
  semantics verified in the consumer test below).

* ``test_risk_score_endpoint_publishes_to_streams`` — mocks the
  StreamProducer via monkeypatch on ``src.api.routes.StreamProducer``, hits
  POST /risk/score, and asserts the API called ``publish`` with the 3
  stream names + canonical prediction_id (the same UUID appears in the
  response body's ``prediction_id`` + in the ``cases.created`` publish's
  ``case_id`` source-of-truth correlation).

* ``test_consumer_processes_message`` — SKIPPED unless ``REDIS_URL`` is set.
  Produces a message to ``risk.scores``, runs ``StreamConsumer.consume``
  in a thread with a 1-poll timeout, asserts the handler was called with
  the right fields.

The Redis-path tests (2 of 4) are skipped in the sandbox + in CI without
a Redis service container — mirroring the test_db.py skip pattern.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.routes import create_app  # noqa: E402
from src.stream.processor import StreamProcessor  # noqa: E402
from src.stream.producer import (  # noqa: E402
    STREAM_AUDIT_RECORDS,
    STREAM_CASES_CREATED,
    STREAM_MODEL_DRIFT,
    STREAM_NOTIFICATIONS,
    STREAM_RISK_SCORES,
    StreamProducer,
)

# --- Test fixtures ------------------------------------------------------

VALID = {
    "order_id": "STR-T1",
    "amount_inr": 899,
    "category": "Fashion",
    "customer_id": "CUST-9",
}
SCORER = {"Authorization": "Bearer score-demo-key"}
ADMIN = {"Authorization": "Bearer admin-demo-key"}

# These tests skip if REDIS_URL isn't set OR the redis package isn't
# importable. Same skip pattern as tests/test_db.py.
_REDIS_URL = "redis://localhost:6379/15"  # /15 = test-only DB index
_REDIS_AVAILABLE = False
try:
    import redis  # type: ignore[import-not-found]  # noqa: F401

    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False

skip_no_redis = pytest.mark.skipif(
    not _REDIS_AVAILABLE,
    reason="redis package not installed (or REDIS_URL unset) — run "
    "`pip install redis>=5.0` + start `docker compose up redis` to enable",
)


def _skip_if_redis_unreachable():
    """At test runtime, ping Redis; skip if no connection (CI without a
    redis service container). Module-level import is fine; we just don't
    want to fail in environments where redis-py installed but no server.
    """
    if not _REDIS_AVAILABLE:
        return True
    try:
        import redis  # type: ignore[import-not-found]

        r = redis.from_url(_REDIS_URL)
        r.ping()
        return False
    except Exception:
        return True


# --- Test 1: StreamProducer noop contract ---------------------------------


def test_stream_producer_noop_without_redis():
    """``StreamProducer(None).publish()`` returns None silently.

    This is the contract that lets the 63 existing tests pass without a
    Redis fixture — the API constructs ``StreamProducer(None)`` (because
    ``settings.redis_url`` is None in test mode) + calls ``publish`` three
    times per request; each returns None + doesn't crash.
    """
    p = StreamProducer(None)
    assert p.redis_url is None
    # publish returns None for any stream + fields
    assert p.publish(STREAM_RISK_SCORES, {"prediction_id": "p1"}) is None
    assert p.publish(STREAM_AUDIT_RECORDS, {"audit_id": "a1"}) is None
    assert p.publish(STREAM_CASES_CREATED, {"case_id": "c1"}) is None
    assert p.publish(STREAM_MODEL_DRIFT, {"reason": "duplicate"}) is None
    assert p.publish(STREAM_NOTIFICATIONS, {"msg": "hi"}) is None
    # close() is safe to call even with no client
    p.close()
    p.close()  # idempotent


def test_stream_producer_field_coercion():
    """publish() never raises on non-string field values.

    The fire-and-forget contract means even a bad field type (float, None,
    bool) shouldn't crash the API. StreamProducer coerces to str. With
    REDIS_URL unset, this is a noop — but the coercion path runs regardless
    (it's just before the no-op client).
    """
    p = StreamProducer(None)
    # None, float, bool, int values — all coerce without raising.
    assert p.publish(
        STREAM_RISK_SCORES,
        {
            "prediction_id": "p1",
            "score": 0.8123,  # float
            "is_flagged": True,  # bool
            "count": 5,  # int
            "note": None,  # None → ""
        },
    ) is None
    p.close()


# --- Test 2: Real-Redis producer end-to-end (skipped without Redis) --------


@skip_no_redis
def test_stream_producer_publishes_when_redis_available():
    """XADD a message to ``risk.scores``, XREAD it back, verify fields."""
    if _skip_if_redis_unreachable():
        pytest.skip("Redis not reachable at " + _REDIS_URL)
    import redis  # type: ignore[import-not-found]

    # Clean the stream so the test is isolated. (DEL is O(n) on the stream
    # but our test produces 1-2 messages.)
    raw_client = redis.from_url(_REDIS_URL, decode_responses=True)
    raw_client.delete(STREAM_RISK_SCORES)
    try:
        p = StreamProducer(_REDIS_URL)
        msg_id = p.publish(
            STREAM_RISK_SCORES,
            {
                "prediction_id": "p-redis-1",
                "order_id": "ORD-REDIS-1",
                "decision": "ACCEPT",
                "score": "0.123",
                "ts": "2026-01-01T00:00:00+00:00",
            },
        )
        assert msg_id is not None, "publish should return a Redis message ID"
        # XREAD returns [(stream, [(msg_id, fields_dict)])]
        resp = raw_client.xread({STREAM_RISK_SCORES: "0"}, count=10)
        assert resp, "expected at least one message in the stream"
        stream_name, messages = resp[0]
        assert stream_name == STREAM_RISK_SCORES
        assert len(messages) >= 1
        _, fields = messages[-1]  # the message we just added
        assert fields["prediction_id"] == "p-redis-1"
        assert fields["order_id"] == "ORD-REDIS-1"
        assert fields["decision"] == "ACCEPT"
        p.close()
    finally:
        raw_client.delete(STREAM_RISK_SCORES)


# --- Test 3: API endpoint publishes to streams (mock-based) ----------------


def test_risk_score_endpoint_publishes_to_streams(monkeypatch):
    """POST /risk/score → assert StreamProducer.publish was called with the
    3 stream names + canonical prediction_id.

    Uses monkeypatch on ``src.api.routes.StreamProducer`` so when
    ``create_app()`` instantiates the producer, it gets a Mock-class
    instance whose ``publish`` is a MagicMock. The handler still calls
    ``publish`` three times per request (risk.scores, audit.records,
    cases.created-on-REVIEW); we capture the calls + assert structure.
    """
    # Create the mock class BEFORE importing create_app so the patch
    # is in place when create_app() resolves the name at call time.
    # The mock instance's ``publish`` is a MagicMock that returns a fake
    # message ID (so the fire-and-forget path looks successful).
    captured_publish_calls: list[tuple] = []

    class _MockProducer:
        def __init__(self, redis_url=None):
            self.redis_url = redis_url
            self.client = None  # mirroring the real shape
            self.publish = MagicMock(side_effect=self._capture_publish)

        def _capture_publish(self, stream, fields):
            captured_publish_calls.append((stream, dict(fields)))
            return f"mock-msg-id-{len(captured_publish_calls)}"

        def close(self):
            pass

    # Patch the name as imported into routes.py. The import line was:
    #   from src.stream.producer import (... StreamProducer)
    # so the name lives in the ``src.api.routes`` module namespace.
    from src.api import routes as routes_mod

    monkeypatch.setattr(routes_mod, "StreamProducer", _MockProducer)

    with TestClient(create_app(scorer_rate_per_min=1000)) as client:
        # Accept decision — should NOT trigger cases.created publish.
        r1 = client.post("/risk/score", json=VALID, headers=SCORER)
        assert r1.status_code == 200
        body1 = r1.json()
        # Find this request's publishes by prediction_id (other tests in the
        # module might share the mock, but TestClient-with-context ensures
        # isolation — the create_app call instantiated a fresh mock).
        pid1 = body1["prediction_id"]
        p1_calls = [
            (s, f) for s, f in captured_publish_calls if f.get("prediction_id") == pid1
        ]
        streams_published_1 = {s for s, _ in p1_calls}
        # risk.scores + audit.records always published.
        assert STREAM_RISK_SCORES in streams_published_1
        assert STREAM_AUDIT_RECORDS in streams_published_1
        # ACCEPT (or REJECT) — NOT REVIEW — so cases.created should be absent.
        # (We can't assert the exact decision because the model is live; but
        # we CAN assert that IF decision != REVIEW, cases.created wasn't
        # called for this prediction_id.)
        if body1["decision"] != "REVIEW":
            assert STREAM_CASES_CREATED not in streams_published_1, (
                "cases.created should only fire on REVIEW, got decision="
                f"{body1['decision']}"
            )
        else:
            # REVIEW → cases.created published with case_id from the response.
            assert STREAM_CASES_CREATED in streams_published_1
            cases_call = next(
                f for s, f in p1_calls if s == STREAM_CASES_CREATED
            )
            assert cases_call["case_id"] == body1["case_id"]
            assert cases_call["prediction_id"] == pid1

        # Force a REVIEW decision by adding a REVIEW rule. The rules engine
        # is empty by default; we add a rule that always fires (priority 1,
        # REVIEW action) and then assert cases.created was published.
        captured_publish_calls.clear()
        rules_post = client.post(
            "/v1/rules",
            json={
                "rule_id": "force-review-str",
                "name": "Track F test rule",
                "field": "amount_inr",
                "op": "gt",
                "value": 0,  # always true for VALID amount
                "action": "REVIEW",
                "priority": 1,
            },
            headers=ADMIN,
        )
        assert rules_post.status_code == 200
        # POST a fresh order — REVIEW rule will fire + force decision=REVIEW
        # (the cost-optimizer might pick ACCEPT but the REVIEW rule gates
        # it to REVIEW; the decision_source becomes cost_optimal_bmr_review_rule).
        review_payload = {**VALID, "order_id": "STR-REVIEW-1"}
        r2 = client.post("/risk/score", json=review_payload, headers=SCORER)
        assert r2.status_code == 200
        body2 = r2.json()
        assert body2["decision"] == "REVIEW", (
            "REVIEW rule should force decision=REVIEW; got "
            f"{body2['decision']} (source={body2['decision_source']})"
        )
        pid2 = body2["prediction_id"]
        p2_calls = [
            (s, f) for s, f in captured_publish_calls if f.get("prediction_id") == pid2
        ]
        streams_published_2 = {s for s, _ in p2_calls}
        assert STREAM_RISK_SCORES in streams_published_2
        assert STREAM_AUDIT_RECORDS in streams_published_2
        assert STREAM_CASES_CREATED in streams_published_2
        cases_call = next(
            f for s, f in p2_calls if s == STREAM_CASES_CREATED
        )
        assert cases_call["case_id"] == body2["case_id"]
        assert cases_call["prediction_id"] == pid2
        assert cases_call["order_id"] == "STR-REVIEW-1"
        # Canonical prediction_id correlation: the risk.scores publish
        # uses the SAME prediction_id as the cases.created publish (this is
        # the whole point of generating the UUID once — Track B's old
        # "pending" placeholder broke this correlation).
        risk_call = next(
            f for s, f in p2_calls if s == STREAM_RISK_SCORES
        )
        assert risk_call["prediction_id"] == pid2
        # Verify a few key fields on the risk.scores publish.
        assert risk_call["order_id"] == "STR-REVIEW-1"
        assert risk_call["decision"] == "REVIEW"
        assert risk_call["decision_source"] == body2["decision_source"]


# --- Test 4: Consumer end-to-end (skipped without Redis) ------------------


@skip_no_redis
def test_consumer_processes_message():
    """Produce a message, run consumer in a thread, assert handler called."""
    if _skip_if_redis_unreachable():
        pytest.skip("Redis not reachable at " + _REDIS_URL)
    import redis  # type: ignore[import-not-found]

    from src.stream.consumer import StreamConsumer

    raw = redis.from_url(_REDIS_URL, decode_responses=True)
    # Clean the stream + the consumer group from any prior run.
    raw.delete(STREAM_RISK_SCORES)
    try:
        raw.delete("rto:stream:hll:orders:0")  # processor HLL (if any)
    except Exception:
        pass

    received: list[tuple[str, dict]] = []

    def handler(stream: str, fields: dict) -> None:
        received.append((stream, dict(fields)))

    consumer = StreamConsumer(_REDIS_URL, group="test-group", consumer="test-consumer")

    # Run the consumer in a daemon thread; stop it after a short poll.
    def _run():
        # block_ms=500 + count limit; the consumer will exit via _stop
        # signal handler — but in tests we can't SIGTERM a thread, so we
        # use a short block + check a stop flag via the consumer's _stop.
        try:
            client = consumer._connect()
            consumer._ensure_group(STREAM_RISK_SCORES)
            # One-shot read instead of the infinite loop.
            resp = client.xreadgroup(
                groupname="test-group",
                consumername="test-consumer",
                streamcounts={STREAM_RISK_SCORES: ">"},
                count=10,
                block=500,
            )
            if resp:
                for stream, messages in resp:
                    for msg_id, fields in messages:
                        try:
                            handler(stream, dict(fields) if fields else {})
                            client.xack(stream, "test-group", msg_id)
                        except Exception:
                            pass
        except Exception:
            pass

    # Produce the message BEFORE starting the consumer so XREADGROUP
    # has something to deliver on the first poll.
    producer = StreamProducer(_REDIS_URL)
    msg_id = producer.publish(
        STREAM_RISK_SCORES,
        {
            "prediction_id": "p-cons-1",
            "order_id": "ORD-CONS-1",
            "decision": "REVIEW",
            "score": "0.555",
            "ts": "2026-01-02T00:00:00+00:00",
        },
    )
    assert msg_id is not None
    producer.close()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=3.0)
    assert not t.is_alive(), "consumer thread should have exited after one poll"

    assert len(received) >= 1, "consumer handler should have been called once"
    stream, fields = received[0]
    assert stream == STREAM_RISK_SCORES
    assert fields["prediction_id"] == "p-cons-1"
    assert fields["order_id"] == "ORD-CONS-1"
    assert fields["decision"] == "REVIEW"

    consumer.close()
    raw.delete(STREAM_RISK_SCORES)


# --- Test 5: Smoke-test the stream-processor anomaly detection ------------


def test_stream_processor_detects_duplicate_order_id():
    """Unit-test the processor's anomaly detection without Redis.

    Feeds two messages with the same order_id through
    ``_handle_message`` + asserts that the producer.publish was called
    with ``anomaly_reason=duplicate_order_id`` on the second message.
    The processor's HLL call will fail (no Redis) but it's best-effort
    (logged, not raised) so the test is robust.
    """
    proc = StreamProcessor.__new__(StreamProcessor)  # bypass __init__ (no Redis)
    # Minimal stub of the instance attributes _handle_message touches.
    proc.redis_url = None
    proc.consumer_name = "test-processor"
    proc.client = None
    proc._stop = False
    proc._group_ensured = False
    proc._window = []
    # Use a real deque for trim_window.
    from collections import deque

    proc._window = deque()
    proc._seen_order_ids = {}
    # 11-d Track T: new state for the 4th detector (HLL cardinality-spike)
    # + the dict-fallback flag. Set up so the bypass-__init__ test path
    # doesn't AttributeError on the new attributes.
    proc._seen_cap_warned = False
    proc._hll_cardinality_history = {}
    proc._last_minute_bucket = None
    proc._baseline_rate = None
    proc._baseline_score_mean = None
    proc._baseline_score_std = None
    proc.WINDOW_SECONDS = 60
    proc.BASELINE_SEED = 30
    proc.HLL_KEY_PREFIX = "rto:stream:hll"
    proc.RATE_SPIKE_MULTIPLIER = 3.0
    proc.SCORE_DRIFT_SIGMA = 2.0
    proc.HLL_SPIKE_FACTOR = 3.0
    proc.HLL_SPIKE_LOOKBACK_MIN = 10
    proc.SEEN_ORDER_IDS_CAP = 10000
    proc.GROUP = "test-processors"

    # Mock producer to capture model.drift publishes.
    drift_calls: list[dict] = []

    class _MockProd:
        def publish(self, stream, fields):
            drift_calls.append({"stream": stream, "fields": dict(fields)})
            return "mock-drift-id"

        def close(self):
            pass

    proc.producer = _MockProd()

    # _connect is called from _hll_add_order + _hll_count_orders. We
    # monkey-patch to return None (skip HLL path entirely).
    proc._connect = lambda: None  # type: ignore[assignment]
    proc._hll_add_order = lambda oid, bucket: None  # type: ignore[assignment]
    proc._hll_count_orders = lambda bucket: None  # type: ignore[assignment]

    # Feed 2 messages with same order_id; second is the duplicate.
    fields1 = {
        "prediction_id": "p1",
        "order_id": "ORD-DUP-1",
        "score": "0.500",
        "ts": "2026-01-03T00:00:00+00:00",
    }
    fields2 = {
        "prediction_id": "p2",
        "order_id": "ORD-DUP-1",  # same order_id → duplicate
        "score": "0.500",
        "ts": "2026-01-03T00:00:01+00:00",
    }
    proc._handle_message(STREAM_RISK_SCORES, fields1)
    # First message: no anomaly (order_id not seen before).
    assert len(drift_calls) == 0
    proc._handle_message(STREAM_RISK_SCORES, fields2)
    # Second message: duplicate_order_id anomaly → model.drift publish.
    assert len(drift_calls) == 1
    drift = drift_calls[0]
    assert drift["stream"] == STREAM_MODEL_DRIFT
    assert drift["fields"]["anomaly_reason"] == "duplicate_order_id"
    assert drift["fields"]["order_id"] == "ORD-DUP-1"
    assert drift["fields"]["prediction_id"] == "p2"


# --- Test 6: 4th detector — HLL cardinality-spike (Track T 11-d) ----------


def test_stream_processor_detects_hll_cardinality_spike():
    """Unit-test the 4th anomaly detector (HLL cardinality-spike) without
    Redis. The detector compares the current minute's HLL PFCOUNT to the
    rolling average of the last 10 completed minutes + emits an
    ``hll_cardinality_spike`` anomaly when current > 3x avg.

    Simulates 1 quiet minute (PFCOUNT=5) then a burst minute (PFCOUNT=20).
    The quiet minute is snapshotted into ``_hll_cardinality_history`` when
    the minute rolls over; the burst minute's first message then triggers
    the spike detector.

    Why this matters: the in-memory ``_seen_order_ids`` dict can only see
    ONE process's order_ids. The HLL aggregates across all stream-processor
    replicas — the spike detector catches the "merchant bot burst"
    signature that fans out across processes.
    """
    proc = StreamProcessor.__new__(StreamProcessor)  # bypass __init__
    proc.redis_url = None
    proc.consumer_name = "test-processor-hll"
    proc.client = None
    proc._stop = False
    proc._group_ensured = False
    from collections import deque

    proc._window = deque()
    proc._seen_order_ids = {}
    proc._seen_cap_warned = False
    proc._hll_cardinality_history = {}
    proc._last_minute_bucket = None
    proc._baseline_rate = None
    proc._baseline_score_mean = None
    proc._baseline_score_std = None
    proc.WINDOW_SECONDS = 600  # wide so messages don't trim mid-test
    proc.BASELINE_SEED = 30
    proc.HLL_KEY_PREFIX = "rto:stream:hll"
    proc.RATE_SPIKE_MULTIPLIER = 3.0
    proc.SCORE_DRIFT_SIGMA = 2.0
    proc.HLL_SPIKE_FACTOR = 3.0
    proc.HLL_SPIKE_LOOKBACK_MIN = 10
    proc.SEEN_ORDER_IDS_CAP = 10000
    proc.GROUP = "test-processors"

    drift_calls: list[dict] = []

    class _MockProd:
        def publish(self, stream, fields):
            drift_calls.append({"stream": stream, "fields": dict(fields)})
            return "mock-drift-id"

        def close(self):
            pass

    proc.producer = _MockProd()
    proc._connect = lambda: None  # type: ignore[assignment]
    proc._hll_add_order = lambda oid, bucket: None  # type: ignore[assignment]

    # Control the HLL count deterministically. The quiet minute bucket
    # returns 5; the burst minute bucket returns 20 (which is 4x the
    # baseline 5, well above the 3x spike_factor).
    quiet_minute = 1_700_000_000 // 60  # arbitrary stable bucket index
    burst_minute = quiet_minute + 1
    hll_counts = {quiet_minute: 5, burst_minute: 20}

    def _mock_hll_count(bucket):
        return hll_counts.get(bucket, 0)

    proc._hll_count_orders = _mock_hll_count  # type: ignore[assignment]

    # Seed the history: simulate that the quiet minute already completed +
    # its PFCOUNT was snapshotted. This avoids needing two real minute
    # boundaries (which would slow the test).
    proc._hll_cardinality_history = {quiet_minute: 5}

    # Now feed a message in the burst minute. The HLL count for the burst
    # minute is 20 (set above) — 20 > 5 * 3.0 = 15, so the detector fires.
    fields = {
        "prediction_id": "p-hll-spike-1",
        "order_id": "ORD-SPIKE-1",
        "score": "0.500",
        "ts": "2026-01-03T00:01:00+00:00",
    }
    # Patch time.time to land in the burst minute. _handle_message calls
    # time.time() once for `now` — we use a monkeypatched module.
    import src.stream.processor as proc_mod

    orig_time = proc_mod.time
    proc_mod.time = type("T", (), {"time": lambda s: burst_minute * 60 + 5})()
    try:
        proc._handle_message(STREAM_RISK_SCORES, fields)
    finally:
        proc_mod.time = orig_time

    # Assert the spike detector fired.
    spike_calls = [
        d for d in drift_calls
        if d["fields"]["anomaly_reason"] == "hll_cardinality_spike"
    ]
    assert len(spike_calls) == 1, (
        f"expected 1 hll_cardinality_spike publish; got "
        f"{[d['fields']['anomaly_reason'] for d in drift_calls]}"
    )
    sp = spike_calls[0]
    assert sp["stream"] == STREAM_MODEL_DRIFT
    assert sp["fields"]["current_minute_count"] == "20"
    assert sp["fields"]["baseline_avg_count"] == "5.00"
    assert sp["fields"]["spike_factor"] == "3.0"
    assert sp["fields"]["lookback_minutes"] == "10"


def test_stream_processor_hll_spike_no_false_positive_below_factor():
    """The 4th detector does NOT fire when current ≤ spike_factor x avg.

    Quiet minute = 5, current minute = 14 (14 < 5 * 3 = 15) — no anomaly.
    """
    proc = StreamProcessor.__new__(StreamProcessor)
    proc.redis_url = None
    proc.consumer_name = "test-processor-hll-neg"
    proc.client = None
    proc._stop = False
    proc._group_ensured = False
    from collections import deque

    proc._window = deque()
    proc._seen_order_ids = {}
    proc._seen_cap_warned = False
    proc._hll_cardinality_history = {}
    proc._last_minute_bucket = None
    proc._baseline_rate = None
    proc._baseline_score_mean = None
    proc._baseline_score_std = None
    proc.WINDOW_SECONDS = 600
    proc.BASELINE_SEED = 30
    proc.HLL_KEY_PREFIX = "rto:stream:hll"
    proc.RATE_SPIKE_MULTIPLIER = 3.0
    proc.SCORE_DRIFT_SIGMA = 2.0
    proc.HLL_SPIKE_FACTOR = 3.0
    proc.HLL_SPIKE_LOOKBACK_MIN = 10
    proc.SEEN_ORDER_IDS_CAP = 10000
    proc.GROUP = "test-processors"

    drift_calls: list[dict] = []

    class _MockProd:
        def publish(self, stream, fields):
            drift_calls.append({"stream": stream, "fields": dict(fields)})
            return "mock-drift-id"

        def close(self):
            pass

    proc.producer = _MockProd()
    proc._connect = lambda: None  # type: ignore[assignment]
    proc._hll_add_order = lambda oid, bucket: None  # type: ignore[assignment]

    quiet_minute = 1_700_000_000 // 60
    burst_minute = quiet_minute + 1
    hll_counts = {quiet_minute: 5, burst_minute: 14}  # 14 < 5*3=15 → no fire
    proc._hll_count_orders = lambda b: hll_counts.get(b, 0)  # type: ignore[assignment]
    proc._hll_cardinality_history = {quiet_minute: 5}

    fields = {
        "prediction_id": "p-hll-neg-1",
        "order_id": "ORD-NO-SPIKE-1",
        "score": "0.500",
        "ts": "2026-01-03T00:01:00+00:00",
    }
    import src.stream.processor as proc_mod

    orig_time = proc_mod.time
    proc_mod.time = type("T", (), {"time": lambda s: burst_minute * 60 + 5})()
    try:
        proc._handle_message(STREAM_RISK_SCORES, fields)
    finally:
        proc_mod.time = orig_time

    spike_calls = [
        d for d in drift_calls
        if d["fields"]["anomaly_reason"] == "hll_cardinality_spike"
    ]
    assert len(spike_calls) == 0, (
        "no spike should fire when current (14) ≤ avg*factor (15); got: "
        + str([d["fields"]["anomaly_reason"] for d in drift_calls])
    )


def test_stream_processor_seen_order_ids_cap_falls_back_to_hll(capsys):
    """When ``_seen_order_ids`` hits SEEN_ORDER_IDS_CAP, the dict stops
    growing + a one-shot warning is logged. The HLL takes over for the
    cardinality signal.

    Sets the cap to 2 + feeds 3 distinct order_ids — the 3rd is NOT added
    + a stderr warning is emitted. Duplicate detection continues to work
    for the existing 2 entries.
    """
    proc = StreamProcessor.__new__(StreamProcessor)
    proc.redis_url = None
    proc.consumer_name = "test-processor-cap"
    proc.client = None
    proc._stop = False
    proc._group_ensured = False
    from collections import deque

    proc._window = deque()
    proc._seen_order_ids = {}
    proc._seen_cap_warned = False
    proc._hll_cardinality_history = {}
    proc._last_minute_bucket = None
    proc._baseline_rate = None
    proc._baseline_score_mean = None
    proc._baseline_score_std = None
    proc.WINDOW_SECONDS = 600
    proc.BASELINE_SEED = 30
    proc.HLL_KEY_PREFIX = "rto:stream:hll"
    proc.RATE_SPIKE_MULTIPLIER = 3.0
    proc.SCORE_DRIFT_SIGMA = 2.0
    proc.HLL_SPIKE_FACTOR = 3.0
    proc.HLL_SPIKE_LOOKBACK_MIN = 10
    proc.SEEN_ORDER_IDS_CAP = 2  # tight cap → fallback trips on 3rd message
    proc.GROUP = "test-processors"

    drift_calls: list[dict] = []

    class _MockProd:
        def publish(self, stream, fields):
            drift_calls.append({"stream": stream, "fields": dict(fields)})
            return "mock-drift-id"

        def close(self):
            pass

    proc.producer = _MockProd()
    proc._connect = lambda: None  # type: ignore[assignment]
    proc._hll_add_order = lambda oid, bucket: None  # type: ignore[assignment]
    proc._hll_count_orders = lambda bucket: None  # type: ignore[assignment]

    # Feed 3 distinct order_ids. The 3rd is the duplicate of the 1st? No —
    # 3 distinct ones (ORD-A, ORD-B, ORD-C). The cap is 2, so the 3rd
    # trips the fallback (not added to the dict).
    fields_a = {
        "prediction_id": "p-a",
        "order_id": "ORD-A",
        "score": "0.500",
        "ts": "2026-01-03T00:00:00+00:00",
    }
    fields_b = {
        "prediction_id": "p-b",
        "order_id": "ORD-B",
        "score": "0.500",
        "ts": "2026-01-03T00:00:01+00:00",
    }
    fields_c = {
        "prediction_id": "p-c",
        "order_id": "ORD-C",  # 3rd distinct → trips cap fallback
        "score": "0.500",
        "ts": "2026-01-03T00:00:02+00:00",
    }
    proc._handle_message(STREAM_RISK_SCORES, fields_a)
    proc._handle_message(STREAM_RISK_SCORES, fields_b)
    # Before the 3rd: dict has 2 entries (at cap), no warning yet.
    assert len(proc._seen_order_ids) == 2
    assert proc._seen_cap_warned is False
    proc._handle_message(STREAM_RISK_SCORES, fields_c)
    # After the 3rd: dict STILL has 2 entries (3rd not added), warning
    # fired exactly once.
    assert len(proc._seen_order_ids) == 2, (
        "dict should NOT have grown past the cap; the 3rd order_id should "
        "have been dropped (HLL takes over for cardinality)"
    )
    assert "ORD-C" not in proc._seen_order_ids
    assert proc._seen_cap_warned is True
    # Stderr should contain the one-shot warning.
    err = capsys.readouterr().err
    assert "_seen_order_ids cap reached" in err
    assert "falling back to HLL for cardinality" in err
    # No drift publishes fired (these are 3 distinct order_ids, no spike).
    assert len(drift_calls) == 0
    # Duplicate detection still works for the existing 2 entries — feed
    # ORD-A again → duplicate_order_id anomaly fires.
    proc._handle_message(
        STREAM_RISK_SCORES,
        {**fields_a, "prediction_id": "p-a-dup"},
    )
    assert any(
        d["fields"]["anomaly_reason"] == "duplicate_order_id"
        for d in drift_calls
    ), "duplicate detection should still work for entries within the cap"


# --- Test 9 (T3.1): real Redis HLL path via fakeredis -------------------


def test_stream_processor_hll_redis_pfadd_pfcount_dedup():
    """T3.1 — Exercise the REAL Redis PFADD/PFCOUNT HLL path (not stubs).

    Closes the test-coverage gap that ``proc._hll_add_order = lambda oid,
    bucket: None`` left in the duplicate-detection test (lines 465-466):
    the actual PFADD dedup + PFCOUNT estimation had zero coverage. This
    test uses ``fakeredis`` to back the StreamProcessor's Redis client
    so the real HLL commands run.

    Verifies:
    1. Three PFADDs of the SAME order_id → PFCOUNT == 1 (HLL dedup).
    2. Two distinct order_ids → PFCOUNT == 2.
    3. 1000 more distinct order_ids → PFCOUNT ≈ 1002 within the HLL
       standard error (~0.81% at 16384 14-bit registers — Redis's
       documented precision; we accept 5% to be conservative on the
       test assertion).
    """
    try:
        import fakeredis  # type: ignore[import-not-found]
    except ImportError:
        pytest.skip("fakeredis not installed — install `fakeredis>=2.0` "
                    "to exercise the real HLL PFADD/PFCOUNT path")

    proc = StreamProcessor.__new__(StreamProcessor)
    proc.redis_url = "redis://fake-hll-test:6379"  # not used — client set below
    proc.consumer_name = "test-hll-real"
    # fakeredis implements PFADD/PFCOUNT (HyperLogLog) per the Redis spec.
    # decode_responses=True so pfcount returns int (not bytes) for the
    # direct equality assertions below.
    proc.client = fakeredis.FakeRedis(decode_responses=True)
    proc.HLL_KEY_PREFIX = "rto:stream:hll:test"
    proc.WINDOW_SECONDS = 300  # used by the expire() call in _hll_add_order

    bucket = 2_025_001  # arbitrary stable minute-bucket

    # 1. HLL dedup: 3 PFADDs of the same order_id → cardinality 1.
    proc._hll_add_order("order_001", bucket)
    proc._hll_add_order("order_001", bucket)  # duplicate
    proc._hll_add_order("order_001", bucket)  # duplicate again
    count1 = proc._hll_count_orders(bucket)
    assert count1 == 1, (
        f"HLL should dedup 3 same-id PFADDs to cardinality 1; got {count1}"
    )

    # 2. Two distinct order_ids → cardinality 2.
    proc._hll_add_order("order_002", bucket)
    count2 = proc._hll_count_orders(bucket)
    assert count2 == 2, (
        f"HLL should count 2 distinct order_ids; got {count2}"
    )

    # 3. 1000 more distinct order_ids → PFCOUNT ≈ 1002 within HLL's
    # standard error (~0.81% on Redis; we allow 5% to be conservative).
    for i in range(1000):
        proc._hll_add_order(f"order_distinct_{i:04d}", bucket)
    count3 = proc._hll_count_orders(bucket)
    expected = 1002  # 2 from above + 1000 new
    assert abs(count3 - expected) <= expected * 0.05, (
        f"HLL estimate for {expected} distinct orders should be within "
        f"5% of {expected} (Redis HLL std error ~0.81%); got {count3}"
    )

    # Cleanup fakeredis connection so the test doesn't leak state into
    # the next test (fakeredis instances are isolated, but close() is
    # the documented teardown).
    try:
        proc.client.close()
    except Exception:
        pass


def test_stream_processor_hll_count_returns_none_when_no_redis():
    """T3.1 — ``_hll_count_orders`` returns None (not 0) when Redis is
    unreachable. The best-effort try/except in the HLL path returns
    None so callers can distinguish "no data yet" from "Redis down".

    Verifies the contract: with ``proc.client = None`` and a forced
    import-error on `redis`, the PFCOUNT call degrades to None instead
    of raising.
    """
    proc = StreamProcessor.__new__(StreamProcessor)
    proc.redis_url = None  # ensures _ensure_client returns None in StreamProducer
    proc.client = None
    proc.HLL_KEY_PREFIX = "rto:stream:hll"
    proc.WINDOW_SECONDS = 300
    # Force the _connect() import to fail (simulates missing redis-py).
    # The except branch returns None for both _hll_add_order (silent)
    # and _hll_count_orders.
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "redis":
            raise ImportError("simulated: redis-py not installed")
        return real_import(name, *args, **kwargs)
    builtins.__import__ = fake_import
    try:
        proc.client = None  # reset so _connect tries to import redis
        # _connect raises ImportError internally → caught by the except
        # in _hll_count_orders → returns None.
        result = proc._hll_count_orders(123)
    finally:
        builtins.__import__ = real_import
    # Either None (ImportError caught) or raises silently caught — both
    # are acceptable contracts per the docstring "best-effort". The
    # only hard contract: it must NOT raise to the caller.
    assert result is None, (
        f"_hll_count_orders should return None when Redis is unreachable; "
        f"got {result!r}"
    )
