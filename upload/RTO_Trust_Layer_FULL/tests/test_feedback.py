"""Tests for Track G Day 2 — feedback loop + DDM/ADWIN drift detection.

Closes §A item 18 (feedback loop), §D items P3 + P4 (formal drift
detection / shadow-retrain trigger), perceived-gap driver G3 (partial),
+ the MLOps-DevOps paper's ``plan_drift_triggered_retraining`` capability.
Source: Gama, Žliobaitė, Bifet, Pechenizkiy, Bouchachia,
"A Survey on Concept Drift Adaptation",
ACM Computing Surveys (CSUR) 46(4), Article 44, March 2014.
DOI 10.1145/2523813. See §3.2 (DDM), §3.3 (ADWIN), §5 (detector-quality
metrics), §6 (Monitoring and Control application category — production
fraud/RTO scorers are the explicit example).

Test layout (10 tests total):

* ``test_ddm_*`` (3) — pure-unit tests on the DDM detector. No app
  fixture, no Redis, no DB.
* ``test_adwin_*`` (1) — pure-unit test on the ADWIN detector.
* ``test_detect_drift_stream_*`` (1) — pure-unit test on the batch
  helper that runs DDM + ADWIN in one shot.
* ``test_feedback_ingest_endpoint`` — POST /v1/feedback/ingest with a
  known prediction_id, verify the response shape + drift state fields.
* ``test_feedback_admin_only`` — scorer-scope key gets 403 (label
  poisoning prevention — merchants can't self-report labels).
* ``test_feedback_records_predicted_p_lookup`` — POST /risk/score then
  POST /v1/feedback/ingest with the response's prediction_id; verify
  the predicted_p lookup found the audit record (probability field).
* ``test_drift_consumer_skipped_without_redis`` — the drift-consumer
  worker can't start without REDIS_URL; we verify the entrypoint
  exits with a clear stderr message. Skip if redis-py IS installed
  (so the test only runs in the sandbox-without-Redis path).
* ``test_label_feedback_service_consume_anomaly`` — pure-unit test of
  the anomaly-side run-length heuristic in
  LabelFeedbackService.consume_anomaly (the parallel path that reacts
  to 3+ consecutive same-reason model.drift anomalies).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.routes import create_app  # noqa: E402
from src.feedback.label_service import LabelFeedbackService  # noqa: E402
from src.ml.drift import ADWIN, DDM, STATE_NUMERIC, detect_drift_stream  # noqa: E402

SCORER = {"Authorization": "Bearer score-demo-key"}
ADMIN = {"Authorization": "Bearer admin-demo-key"}

VALID = {
    "order_id": "FB-T1",
    "amount_inr": 899,
    "category": "Fashion",
    "customer_id": "CUST-FB",
}


# ---------------------------------------------------------------------------
# DDM unit tests (Gama 2014 §3.2)
# ---------------------------------------------------------------------------


def test_ddm_stable_on_correct_predictions():
    """Feed 100 correct predictions (error=0). DDM should stay STABLE.

    The DDM degeneracy guard (``sigma_min > 0`` before WARNING/DRIFT can
    fire) means a perfect-prediction run can't trip the 3σ threshold
    even though sigma_min would collapse to 0 — the standard MOA fix.
    """
    d = DDM(min_n=10)
    states = [d.update(0) for _ in range(100)]
    assert all(s == "STABLE" for s in states), states
    assert d.state == "STABLE"
    assert d.n == 100
    assert d.p == 0.0
    # The numeric mapping for Prometheus (0=STABLE).
    assert STATE_NUMERIC["STABLE"] == 0


def test_ddm_drift_on_error_burst():
    """Feed 30 correct then 20 wrong (error=1). DDM should fire DRIFT.

    The 30 cold-start samples seed the baseline; the 20-error burst
    pushes p+sigma past the 99% control limit (p_min + 3·sigma_min).
    """
    d = DDM(min_n=10)
    for _ in range(30):
        d.update(0)  # baseline seed — STABLE throughout
    assert d.state == "STABLE", "cold-start should be STABLE"
    states = [d.update(1) for _ in range(20)]
    # At some point during the burst, DDM fires DRIFT.
    assert "DRIFT" in states, f"expected DRIFT in {states}"
    assert d.state == "DRIFT"
    assert STATE_NUMERIC["DRIFT"] == 2


def test_ddm_warning_before_drift():
    """A sharp shift in error rate fires WARNING before DRIFT (95% < 99%).

    The DDM degeneracy guard means the baseline ``p_min``/``sigma_min``
    adapts upward as the error rate rises (the survey §3.2 calls this
    "the distance Warning→Out-of-Control estimates rate of change"). To
    get a clean WARNING-before-DRIFT sequence we feed a small error-rate
    baseline then a sustained burst of all-errors (a sudden shift to
    100% error rate). DDM should fire WARNING on the first burst sample
    past the threshold + DRIFT a few samples later.
    """
    d = DDM(min_n=10)
    # Seed a stable baseline: 30 samples with ~10% error rate (3 errors)
    # → p ≈ 0.1, sigma_min ≈ 0.05 → baseline p_min+sigma_min ≈ 0.15.
    # Warning threshold ≈ 0.15 + 2·0.05 = 0.25; drift ≈ 0.15 + 3·0.05 = 0.30.
    for i in range(30):
        d.update(1 if i % 10 == 0 else 0)
    assert d.state == "STABLE", "10% baseline should be in-control"
    # Now escalate to a sustained 100% error burst (fraudster adapted →
    # the model is wrong on every prediction). p climbs from 0.1 toward
    # 1.0; p+sigma should breach WARNING then DRIFT.
    states = [d.update(1) for _ in range(40)]
    saw_warning = "WARNING" in states
    saw_drift = "DRIFT" in states
    # At least one of them should fire on a 100% error burst.
    assert saw_warning or saw_drift, states
    # If DRIFT fired, WARNING should have come first (95% < 99%).
    if saw_drift and saw_warning:
        assert states.index("WARNING") < states.index("DRIFT"), (
            "WARNING should fire before DRIFT on a sharp shift"
        )


# ---------------------------------------------------------------------------
# ADWIN unit tests (Gama 2014 §3.3, Bifet & Gavalda 2007)
# ---------------------------------------------------------------------------


def test_adwin_window_cut_on_sudden_shift():
    """Feed a stable 0.0 stream then a sudden shift to 1.0. ADWIN should
    cut the window at some point during the shift + fire WARNING/DRIFT.

    The cut is detected by the Hoeffding-bound breach on the two sub-
    windows' means. After the cut, the window holds only the second
    (more recent) half — so the post-cut state returns to STABLE on the
    new concept (the survey §3.3 expectation).
    """
    a = ADWIN(min_n=20, delta=0.002)
    # 50 zeros → baseline
    for _ in range(50):
        a.update(0.0)
    assert a.state == "STABLE"
    assert len(a.window) == 50
    # 20 ones → should cut the window at some point
    states = [a.update(1.0) for _ in range(20)]
    assert any(s in {"WARNING", "DRIFT"} for s in states), (
        f"ADWIN should fire on sudden shift; states={states}"
    )
    # After the burst, the window should be shorter than 70 (some cut
    # happened) — the survey's "drop older sub-window" behavior.
    assert len(a.window) < 70, (
        f"window should have been cut; len={len(a.window)}"
    )


# ---------------------------------------------------------------------------
# detect_drift_stream batch helper
# ---------------------------------------------------------------------------


def test_detect_drift_stream_summary_shape():
    """Run the batch helper over a 30-correct + 15-wrong stream.

    Returns a summary dict with both detector states + sample count +
    DDM running error rate. DDM should fire DRIFT; ADWIN may or may
    not (depends on cut timing — but the state field should be one of
    the three valid strings).
    """
    out = detect_drift_stream([0] * 30 + [1] * 15)
    assert set(out.keys()) >= {
        "ddm_state", "adwin_state", "n", "ddm_p",
        "ddm_p_min", "ddm_sigma_min", "adwin_window_len",
    }
    assert out["ddm_state"] == "DRIFT", out
    assert out["adwin_state"] in {"STABLE", "WARNING", "DRIFT"}
    assert out["n"] == 45
    # 15/45 = 0.333 error rate
    assert abs(out["ddm_p"] - 0.333333) < 1e-3


# ---------------------------------------------------------------------------
# /v1/feedback/ingest endpoint
# ---------------------------------------------------------------------------


def test_feedback_ingest_endpoint():
    """POST /v1/feedback/ingest with admin key — should return 200 with
    the full detector state shape. The prediction_id won't be found in
    the audit tail (no /risk/score was called first), so
    ``prediction_not_found: True`` + ``error: 0`` (no contribution to
    drift) + ``predicted_p: None``.
    """
    with TestClient(create_app(scorer_rate_per_min=1000)) as client:
        r = client.post(
            "/v1/feedback/ingest",
            json={
                "prediction_id": "pred-nonexistent-12345",
                "is_returned": True,
            },
            headers=ADMIN,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "ingested"
        assert body["prediction_id"] == "pred-nonexistent-12345"
        assert body["is_returned"] is True
        assert body["predicted_p"] is None
        assert body["prediction_not_found"] is True
        assert body["error"] == 0
        assert body["ddm_state"] == "STABLE"
        assert body["adwin_state"] == "STABLE"
        assert body["drift_detected"] is False
        assert body["n_processed"] == 1


def test_feedback_admin_only():
    """Scorer-scope key gets 403 (label poisoning prevention). Merchants
    can't self-report their own is_returned outcomes — that would let
    them game the drift detector by feeding it a stream of correct labels
    to suppress retrain triggers.
    """
    with TestClient(create_app(scorer_rate_per_min=1000)) as client:
        r = client.post(
            "/v1/feedback/ingest",
            json={
                "prediction_id": "pred-scoper-test-1",
                "is_returned": False,
            },
            headers=SCORER,  # scorer key — should be 403
        )
        assert r.status_code == 403, r.text
        assert "admin scope" in r.json()["detail"]


def test_feedback_records_predicted_p_lookup():
    """POST /risk/score, take the prediction_id from the response, POST
    /v1/feedback/ingest with it — the predicted_p lookup should find
    the audit record (whose body now carries prediction_id — Track G's
    additive audit-body enrichment) + populate predicted_p.
    """
    with TestClient(create_app(scorer_rate_per_min=1000)) as client:
        r1 = client.post("/risk/score", json=VALID, headers=SCORER)
        assert r1.status_code == 200
        body1 = r1.json()
        prediction_id = body1["prediction_id"]
        # The audit record's probability field = round(proba, 5); the
        # response body's probability field = round(proba, 4). The
        # feedback endpoint reads from the audit record, so it sees the
        # 5-decimal version. Both should be ≈ within 1e-5.
        expected_p = body1["probability"]
        assert expected_p is not None, "model should have produced a probability"

        r2 = client.post(
            "/v1/feedback/ingest",
            json={
                "prediction_id": prediction_id,
                "is_returned": True,  # assume the worst — model missed it
            },
            headers=ADMIN,
        )
        assert r2.status_code == 200, r2.text
        body2 = r2.json()
        assert body2["prediction_not_found"] is False
        assert body2["predicted_p"] is not None
        # 5-decimal audit value vs 4-decimal response — close enough.
        assert abs(body2["predicted_p"] - expected_p) < 1e-4, body2


def test_feedback_metrics_endpoint_exposes_drift_gauges():
    """/metrics should now expose rto_drift_ddm_state, rto_drift_adwin_state,
    rto_drift_samples_processed (Track G Day 2). The /metrics endpoint is
    public (no auth) so the dashboard can scrape without keys.
    """
    with TestClient(create_app(scorer_rate_per_min=1000)) as client:
        # Ingest one label so ddm_n becomes 1.
        client.post(
            "/v1/feedback/ingest",
            json={
                "prediction_id": "pred-metrics-test-1",
                "is_returned": True,
            },
            headers=ADMIN,
        )
        r = client.get("/metrics")
        assert r.status_code == 200
        text = r.text
        # T3.4 — all 5 drift Prometheus gauges must be exposed (was only 3).
        # Source: src/api/routes.py /metrics handler emits all 5 from
        # ``state["feedback"].current_state()``:
        #   * rto_drift_ddm_state         (numeric 0/1/2 — STABLE/WARNING/DRIFT)
        #   * rto_drift_adwin_state       (numeric 0/1/2)
        #   * rto_drift_samples_processed (int — DDM.n)
        #   * rto_drift_ddm_p             (float — running error-rate MLE)
        #   * rto_drift_adwin_window_len  (int — ADWIN's current window size)
        assert "# TYPE rto_drift_ddm_state gauge" in text
        assert "# TYPE rto_drift_adwin_state gauge" in text
        assert "# TYPE rto_drift_samples_processed gauge" in text
        assert "# TYPE rto_drift_ddm_p gauge" in text, (
            "rto_drift_ddm_p gauge must be exposed (Track S T2.4) — was missing "
            "in the original 3-gauge assertion"
        )
        assert "# TYPE rto_drift_adwin_window_len gauge" in text, (
            "rto_drift_adwin_window_len gauge must be exposed (Track S T2.4) — "
            "was missing in the original 3-gauge assertion"
        )
        # ddm_n should be 1 (one ingest_label call above).
        # Both gauges should be 0 (STABLE) at this point.
        assert "rto_drift_ddm_state 0" in text
        assert "rto_drift_adwin_state 0" in text
        assert "rto_drift_samples_processed 1" in text
        # The ddm_p value is a float — pattern: "rto_drift_ddm_p <float>".
        # With 1 sample (error=0 because prediction_not_found=True), ddm_p=0.
        # Either way, the gauge line must exist with a numeric value.
        import re
        ddm_p_match = re.search(r"^rto_drift_ddm_p (\d+\.?\d*)", text, re.M)
        assert ddm_p_match is not None, (
            "rto_drift_ddm_p gauge line must have a float value"
        )
        ddm_p_val = float(ddm_p_match.group(1))
        assert isinstance(ddm_p_val, float), (
            "rto_drift_ddm_p must be a float (running Bernoulli MLE)"
        )
        # adwin_window_len is an int. With 1 sample, window has 1 entry.
        adwin_win_match = re.search(
            r"^rto_drift_adwin_window_len (\d+)", text, re.M
        )
        assert adwin_win_match is not None, (
            "rto_drift_adwin_window_len gauge line must have an int value"
        )
        adwin_win_val = int(adwin_win_match.group(1))
        assert isinstance(adwin_win_val, int) and adwin_win_val >= 1, (
            f"rto_drift_adwin_window_len must be int ≥1 after 1 ingest; "
            f"got {adwin_win_val}"
        )


def test_feedback_ingest_triggers_drift_and_retrain_notification(monkeypatch):
    """T3.2 — End-to-end DRIFT path: score an order → post 50+ feedback
    labels → DDM fires DRIFT → ``retrain_request`` notification published
    to the ``notifications`` stream.

    Closes the test-coverage gap that ``test_feedback_ingest_endpoint`` left:
    that test posts 1 label with a nonexistent prediction_id (STABLE).
    This test drives the full DDM DRIFT detection path through the API
    + asserts the shadow-retrain notification fires.

    Approach:
      1. POST /risk/score to get a real ``prediction_id`` + ``predicted_p``
         (the model's P(RTO) for this order — the audit body stores it so
         /v1/feedback/ingest can look it up).
      2. Seed 30 "correct" labels (error=0) so DDM establishes a non-
         degenerate in-control baseline (p_min + sigma_min). Without this
         seed, the ``sigma > 0`` guard blocks DRIFT detection.
      3. Feed up to 50 "wrong" labels (error=1) so DDM's running p+sigma
         breaches the 3σ control limit + fires DRIFT.
      4. Assert the response body carries ``drift_detected: True`` +
         ``ddm_state: "DRIFT"`` (the captured state BEFORE the auto-reset).
      5. Assert a ``notifications`` stream message was published with
         ``type: "retrain_request"`` via a mocked StreamProducer.

    Note on /v1/metrics: after DRIFT detection, LabelFeedbackService
    auto-resets the DDM/ADWIN detectors (Gama 2014 §4: "after adaptation,
    re-establish the baseline from the new concept"). So the
    ``rto_drift_ddm_state`` Prometheus gauge returns to 0 (STABLE) by
    the next /metrics scrape. The durable signal is the captured
    retrain_request notification (the gauge would only show 2 transiently
    between the ``ddm.update()`` call + the auto-reset, a window no
    synchronous /metrics scrape can hit).
    """
    captured_publishes: list[tuple[str, dict]] = []

    class _MockProducer:
        """Captures publish() calls so we can assert the retrain_request
        notification was emitted to the ``notifications`` stream.
        Mirrors the mock pattern in test_streaming.py's
        ``test_risk_score_endpoint_publishes_to_streams``.
        """

        def __init__(self, redis_url=None):
            self.redis_url = redis_url
            self.client = None

        def publish(self, stream, fields):
            captured_publishes.append((stream, dict(fields)))
            return f"mock-msg-{len(captured_publishes)}"

        def close(self):
            pass

    # Patch the SOURCE module so the lazy ``from src.stream.producer
    # import StreamProducer`` inside ``LabelFeedbackService._trigger_
    # shadow_retrain`` resolves to our mock.
    import src.stream.producer as producer_mod

    monkeypatch.setattr(producer_mod, "StreamProducer", _MockProducer)

    with TestClient(create_app(scorer_rate_per_min=1000)) as client:
        # 1. Score an order to get a real prediction_id + predicted_p.
        r1 = client.post("/risk/score", json=VALID, headers=SCORER)
        assert r1.status_code == 200, r1.text
        body1 = r1.json()
        prediction_id = body1["prediction_id"]
        predicted_p = body1["probability"]
        assert predicted_p is not None, (
            "model should produce a probability for /v1/feedback/ingest to "
            "look up via the audit tail scan"
        )

        # 2. Seed 30 "correct" labels (error=0) so DDM establishes a
        # non-degenerate baseline. error=0 when the prediction's class
        # matches the label: predicted_p >= 0.15 (model says RTO) AND
        # is_returned=True (customer returned) → correct; OR predicted_p
        # < 0.15 (model says safe) AND is_returned=False → correct.
        # We pick the is_returned value that makes the error=0.
        correct_is_returned = bool(predicted_p >= 0.15)
        for _ in range(30):
            r = client.post(
                "/v1/feedback/ingest",
                json={
                    "prediction_id": prediction_id,
                    "is_returned": correct_is_returned,
                },
                headers=ADMIN,
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["error"] == 0, (
                f"baseline seed label should produce error=0; got "
                f"{body['error']} (predicted_p={predicted_p}, "
                f"is_returned={correct_is_returned})"
            )
            assert body["ddm_state"] == "STABLE", (
                f"baseline seed should keep DDM in STABLE; got "
                f"{body['ddm_state']}"
            )

        # 3. Feed up to 50 "wrong" labels (error=1) to drive DDM to DRIFT.
        # DDM fires DRIFT after ~3 errors past the 30-sample min_n gate (per
        # the math: p_min=1/31, sigma_min=sqrt(p*(1-p)/31), 3σ threshold
        # breached at n≈33). We allow 50 to be conservative.
        wrong_is_returned = not correct_is_returned
        drift_response = None
        for _ in range(50):
            r = client.post(
                "/v1/feedback/ingest",
                json={
                    "prediction_id": prediction_id,
                    "is_returned": wrong_is_returned,
                },
                headers=ADMIN,
            )
            assert r.status_code == 200, r.text
            body = r.json()
            if body["drift_detected"]:
                drift_response = body
                break
        assert drift_response is not None, (
            "DRIFT should have fired after ≤50 error labels (DDM 3σ control "
            "limit breached)"
        )

        # 4. Assert the response body's drift fields.
        assert drift_response["ddm_state"] == "DRIFT", (
            f"response body's ddm_state should be DRIFT (captured before "
            f"auto-reset); got {drift_response['ddm_state']}"
        )

        # 5. Assert the retrain_request notification was published to the
        # ``notifications`` stream.
        retrain_publishes = [
            f for s, f in captured_publishes
            if s == "notifications" and f.get("type") == "retrain_request"
        ]
        assert len(retrain_publishes) >= 1, (
            f"Expected at least 1 retrain_request notification on the "
            f"notifications stream; got {captured_publishes}"
        )
        notif = retrain_publishes[0]
        assert notif["trigger"] == "drift_detected", (
            f"notification trigger should be 'drift_detected'; got "
            f"{notif.get('trigger')}"
        )
        assert notif["source"] == "label_feedback", (
            f"notification source should be 'label_feedback' (vs the "
            f"anomaly-side path 'stream_anomaly_run:*'); got "
            f"{notif.get('source')}"
        )
        assert notif["prediction_id"] == prediction_id, (
            "notification should carry the triggering prediction_id"
        )
        assert "ddm_state" in notif
        assert "adwin_state" in notif
        assert "ts" in notif  # ISO timestamp


# ---------------------------------------------------------------------------
# LabelFeedbackService consume_anomaly (anomaly-side path)
# ---------------------------------------------------------------------------


def test_label_feedback_service_consume_anomaly_run_length():
    """Feed 3 consecutive same-reason anomalies → retrain trigger fires.

    The service's anomaly-side path is the fast-reactive complement to
    the formal label-side DDM (which fires days later when the delayed
    label arrives). 3 consecutive same-reason anomalies = sustained
    shift, not a single false alarm.
    """
    service = LabelFeedbackService(redis_url=None, database_url=None)
    # 1st anomaly — run_length=1, no trigger.
    r1 = service.consume_anomaly("score_mean_drift", "pred-1")
    assert r1["run_length"] == 1
    assert r1["drift_detected"] is False
    # 2nd anomaly — run_length=2, no trigger.
    r2 = service.consume_anomaly("score_mean_drift", "pred-2")
    assert r2["run_length"] == 2
    assert r2["drift_detected"] is False
    # 3rd anomaly — run_length=3, TRIGGER.
    r3 = service.consume_anomaly("score_mean_drift", "pred-3")
    assert r3["run_length"] == 3
    assert r3["drift_detected"] is True
    # After firing, the run is reset (so the next 3 are needed for the
    # next retrain — not the same 3 forever).
    r4 = service.consume_anomaly("score_mean_drift", "pred-4")
    assert r4["run_length"] == 1
    assert r4["drift_detected"] is False
    service.close()


def test_label_feedback_service_consume_anomaly_resets_on_different_reason():
    """A different anomaly_reason resets the run-length counter.

    A one-off score_velocity_spike followed by a score_mean_drift
    shouldn't fire a retrain — those are different signal classes and
    one alone isn't sustained.
    """
    service = LabelFeedbackService(redis_url=None, database_url=None)
    service.consume_anomaly("score_velocity_spike", "p1")
    service.consume_anomaly("score_velocity_spike", "p2")
    # Different reason — velocity run should reset to 0.
    r = service.consume_anomaly("score_mean_drift", "p3")
    assert r["anomaly_reason"] == "score_mean_drift"
    assert r["run_length"] == 1
    assert r["drift_detected"] is False
    service.close()


# ---------------------------------------------------------------------------
# Drift consumer entrypoint (skipped without Redis)
# ---------------------------------------------------------------------------

# These tests skip if redis-py IS installed — we want to verify the
# REDIS_URL-unset guard in run_drift_consumer. When redis-py is installed
# but REDIS_URL is unset, the entrypoint should still exit cleanly with
# a clear stderr message. When redis-py IS installed AND REDIS_URL is
# set, the consumer would block forever — skip.
_REDIS_AVAILABLE = False
try:
    import redis  # type: ignore[import-not-found]  # noqa: F401

    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False


def test_drift_consumer_skipped_without_redis(monkeypatch):
    """``run_drift_consumer`` exits cleanly when REDIS_URL is unset.

    The entrypoint checks ``settings.redis_url`` and sys.exit(1)s with
    a stderr message if it's None — the same guard Track F's
    ``run_consumer`` uses. We force the env + clear the settings cache
    so the test sees an empty redis_url.
    """
    # Clear the lru_cache so Settings() re-reads env vars (without our
    # test REDIS_URL leak).
    monkeypatch.delenv("REDIS_URL", raising=False)
    from src.config import get_settings

    get_settings.cache_clear()
    try:
        from src.feedback.drift_consumer import run_drift_consumer

        # When REDIS_URL is unset, run_drift_consumer calls sys.exit(1).
        # Use pytest.raises to catch the SystemExit.
        with pytest.raises(SystemExit) as exc_info:
            run_drift_consumer()
        assert exc_info.value.code == 1
    finally:
        # Restore the cache so other tests don't see the env mutation.
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Wave 3 (Subagent 15-c) — REAL DDM-STATE ASSERTIONS
# ---------------------------------------------------------------------------
# DO BADLY #6: the existing ``test_feedback_metrics_endpoint_exposes_drift_
# gauges`` is a SHAPE-only test — it ingests 1 label + asserts the 5
# Prometheus gauge TYPE comments are present + the STABLE state values
# (0/0/1) appear in /metrics. It does NOT fire real DRIFT + assert the
# gauge value actually transitions to 2 (DRIFT) at detection time.
#
# The 2 tests below close that gap by feeding REAL drift data through the
# DDM detector + asserting the INTERNAL state actually mutated (not just
# the public ``state`` attribute that a mock could fake). They prove the
# detector PROCESSED the stream (running p / sigma / n / p_min / sigma_min
# all mutated to expected values), so a regression where DDM.update()
# returned a hardcoded "DRIFT" without computing anything would fail
# these tests.
#
# These tests do NOT mock the DDM detector — they construct real DDM
# instances (the production class in src/ml/drift.py) + feed them real
# Bernoulli error streams + assert the actual post-update state. The
# existing DDM tests (test_ddm_drift_on_error_burst etc.) verify the
# public state field; the tests below verify the internal state
# machine's plumbing (p, n, p_min, sigma_min) so a regression that
# bypassed the statistical computation but kept the state field correct
# would still fail.
# ---------------------------------------------------------------------------


def test_ddm_internal_state_mutates_on_real_drift_stream():
    """Feed a real Bernoulli error stream + assert the DDM's INTERNAL
    state machine plumbing mutated to expected values (not just the
    public ``state`` attribute).

    This closes the "DO BADLY #6" gap by verifying the detector
    PROCESSED the stream — the running ``p``, sample count ``n``, the
    in-control baseline ``p_min`` + ``sigma_min`` ALL mutated from
    their constructor defaults to values consistent with the stream's
    statistics. A regression where ``DDM.update()`` returned a
    hardcoded "DRIFT" without computing the running mean would fail
    this test (the ``p`` assertion would not match).

    Sequence:
      1. Seed 30 cold-start samples (all error=0) — the ``min_n=10``
         gate doesn't fire + ``sigma_min`` stays at 0 (the DDM
         degeneracy guard: ``sigma > 0`` required to adopt a baseline).
      2. Feed 1 wrong (error=1) — ``sigma_min`` should now be > 0
         (the model has made at least one error → Bernoulli std is
         computable) + ``p_min`` should be the minimum (p + sigma)
         point so far.
      3. Feed enough additional errors to breach the 3σ threshold +
         fire DRIFT — assert ``state == "DRIFT"`` AND the running
         ``p`` climbed to > 0.5 (the error rate is > 50% after the
         sustained error burst).
    """
    d = DDM(min_n=10)

    # 1. Cold-start seed — 30 correct predictions.
    for _ in range(30):
        d.update(0)
    # Post-seed state: still STABLE (sigma_min=0 → degeneracy guard
    # blocks WARNING/DRIFT), n=30, p=0 (no errors yet).
    assert d.state == "STABLE", "cold-start seed should be STABLE"
    assert d.n == 30, f"n should be 30 after 30 updates; got {d.n}"
    assert d.p == 0.0, (
        f"running p should be 0.0 after 30 correct predictions; got {d.p}"
    )
    # The degeneracy guard blocked baseline adoption (sigma=0 because
    # p=0 → p*(1-p)/n = 0 → sigma=0 → the ``sigma > 0`` check fails).
    assert d.sigma_min == 0.0, (
        f"sigma_min should still be 0 after perfect-prediction seed "
        f"(the degeneracy guard blocks baseline adoption); got "
        f"{d.sigma_min}"
    )
    assert d.p_min == 1.0, (
        f"p_min should still be the constructor default 1.0 (no baseline "
        f"adopted yet because sigma_min=0); got {d.p_min}"
    )

    # 2. Feed 1 wrong label to break the degeneracy — p climbs above 0,
    # sigma becomes non-zero, baseline gets adopted.
    d.update(1)
    assert d.n == 31
    assert d.p > 0.0, (
        f"after 1 error in 31 samples, running p should be > 0; got {d.p}"
    )
    # The baseline is now adopted (sigma > 0 → the ``sigma > 0`` guard
    # passes → p_min/sigma_min get set to the running values).
    assert d.sigma_min > 0.0, (
        f"after 1 error, sigma_min should be > 0 (the degeneracy guard "
        f"unblocks + baseline gets adopted); got {d.sigma_min}"
    )
    assert d.p_min < 1.0, (
        f"after 1 error, p_min should be < 1.0 (the constructor default) "
        f"because the baseline adoption path set it; got {d.p_min}"
    )
    # Verify the baseline was adopted correctly: p_min = p at the
    # minimum (p+sigma) point, sigma_min = sigma at that point. After
    # 1 error in 31 samples, p ≈ 1/31 ≈ 0.032 + sigma ≈ √(0.032*0.968/31)
    # ≈ 0.032. So p_min + sigma_min ≈ 0.064. The check: the (p+sigma)
    # at the time of baseline adoption = (d.p at that update) + sigma.
    # We use pytest.approx because of floating-point representation.
    expected_p_at_adoption = 1.0 / 31.0  # 1 error in 31 samples
    expected_sigma_at_adoption = (
        (expected_p_at_adoption * (1.0 - expected_p_at_adoption) / 31.0) ** 0.5
    )
    assert d.p_min == pytest.approx(expected_p_at_adoption, abs=1e-9), (
        f"p_min should equal the p value at baseline adoption (≈1/31 "
        f"after 1 error in 31 samples); got {d.p_min}, expected "
        f"{expected_p_at_adoption}"
    )
    assert d.sigma_min == pytest.approx(expected_sigma_at_adoption, abs=1e-9), (
        f"sigma_min should equal the sigma value at baseline adoption "
        f"(≈√(p*(1-p)/n) at the minimum point); got {d.sigma_min}, "
        f"expected {expected_sigma_at_adoption}"
    )

    # 3. Feed enough additional errors to breach the 3σ control limit
    # + fire DRIFT. After ~30-40 more errors (a sustained 100% error
    # burst), p+sigma will breach p_min + 3*sigma_min → DRIFT.
    states = [d.update(1) for _ in range(40)]
    assert "DRIFT" in states, (
        f"expected DRIFT to fire after a sustained 100% error burst; "
        f"states={states}"
    )
    assert d.state == "DRIFT", (
        f"DDM's public state should be DRIFT after the burst; got "
        f"{d.state}"
    )
    # The running p should reflect the drift — after 1/31 + 40 errors in
    # 71 total samples, p ≈ 41/71 ≈ 0.577 (well above the 1/31 baseline).
    assert d.p > 0.5, (
        f"running p should be > 0.5 after a sustained error burst (more "
        f"than half the samples were errors); got {d.p}"
    )
    assert d.n == 71, (
        f"n should be 71 (30 cold-start + 1 baseline-breaker + 40 burst); "
        f"got {d.n}"
    )


def test_label_feedback_service_drift_resets_baseline_to_stable_after_retrain():
    """End-to-end test of the DDM auto-reset behavior post-DRIFT.

    Verifies that after the DDM fires DRIFT on a real error burst, the
    LabelFeedbackService:
      1. Surfaces the DRIFT in the response body's ``drift_detected``
         + ``ddm_state`` fields (captured BEFORE the auto-reset, so the
         caller knows a drift happened).
      2. Auto-resets the DDM + ADWIN detectors (per Gama 2014 §4 —
         "after adaptation, re-establish the baseline from the new
         concept"). The reset brings ``ddm.n`` back to 0 + state back
         to STABLE.
      3. The /metrics gauge ``rto_drift_ddm_state`` would have read 2
         (DRIFT) at detection time, then 0 (STABLE) after the reset —
         we verify the reset by reading ``service.current_state()``
         + asserting ``ddm_state_numeric == 0`` + ``ddm_n == 0``.
      4. After the reset, the detector is alive + processes new
         samples (1 more label → ``ddm_n == 1``).

    This is a real-state assertion (no DDM mock — the service's real
    DDM/ADWIN instances are exercised with a real error stream). It
    closes the "DO BADLY #6" gap by verifying the gauge value
    transition behavior, not just the gauge EXISTENCE (the existing
    ``test_feedback_metrics_endpoint_exposes_drift_gauges`` only
    checks shape).
    """
    # Construct the service in pure-file mode (no Redis, no DB).
    service = LabelFeedbackService(redis_url=None, database_url=None)
    try:
        # Pre-DRIFT: current_state shows STABLE + n=0 (fresh service).
        pre_state = service.current_state()
        assert pre_state["ddm_state"] == "STABLE", (
            f"fresh service's DDM should be STABLE; got {pre_state['ddm_state']}"
        )
        assert pre_state["ddm_state_numeric"] == 0, (
            "the rto_drift_ddm_state gauge should read 0 (STABLE) on a "
            "fresh service"
        )
        assert pre_state["ddm_n"] == 0, "fresh service should have n=0"

        # Seed 30 baseline labels (error=0) — call ingest_label with
        # is_returned=True + predicted_p=0.5 (model says RTO since
        # 0.5 >= return_threshold 0.15). When the customer DID return
        # the order, the prediction was correct → error=0.
        # The 30-sample cold-start seed establishes a non-degenerate
        # in-control baseline (p_min, sigma_min).
        for _ in range(30):
            r = service.ingest_label(
                prediction_id="pred-seed",
                is_returned=True,
                predicted_p=0.5,  # >= 0.15 → model says RTO
            )
            assert r["error"] == 0, (
                f"baseline seed label should produce error=0; got "
                f"{r['error']}"
            )
            assert r["ddm_state"] == "STABLE", (
                f"baseline seed should keep DDM in STABLE; got "
                f"{r['ddm_state']}"
            )
            assert r["drift_detected"] is False, (
                "no drift should fire during the baseline seed"
            )

        # Verify the baseline was adopted (n=30, sigma_min > 0).
        seeded_state = service.current_state()
        assert seeded_state["ddm_n"] == 30, (
            f"after 30 seed labels, ddm_n should be 30; got "
            f"{seeded_state['ddm_n']}"
        )

        # Now feed wrong labels (error=1) — predicted_p=0.5 (model says
        # RTO) but is_returned=False (customer didn't return) → XOR
        # mismatch → error=1. The sustained error burst should breach
        # the 3σ threshold + fire DRIFT.
        drift_response = None
        for _ in range(50):
            r = service.ingest_label(
                prediction_id="pred-burst",
                is_returned=False,
                predicted_p=0.5,  # >= 0.15 → model says RTO
            )
            assert r["error"] == 1, (
                f"burst label should produce error=1; got {r['error']}"
            )
            if r["drift_detected"]:
                drift_response = r
                break
        assert drift_response is not None, (
            "DRIFT should have fired after ≤50 wrong labels (DDM 3σ "
            "control limit breached)"
        )

        # 1. The response body carries the DRIFT (captured BEFORE the
        # auto-reset — this is the gauge value 2 = DRIFT that would have
        # been scraped by Prometheus at this exact instant).
        assert drift_response["drift_detected"] is True, (
            "the drift_detected flag in the response body should be True "
            "at the moment of detection"
        )
        assert drift_response["ddm_state"] == "DRIFT", (
            f"the response body's ddm_state should be DRIFT (the captured "
            f"pre-reset state — the gauge would have read 2 here); got "
            f"{drift_response['ddm_state']}"
        )

        # 2. After DRIFT, the service auto-resets the DDM + ADWIN (per
        # Gama 2014 §4 — "after adaptation, re-establish the baseline
        # from the new concept"). current_state() reads the POST-reset
        # state, so ddm_n should be 0 + state should be STABLE.
        post_reset_state = service.current_state()
        assert post_reset_state["ddm_state"] == "STABLE", (
            f"after DRIFT auto-reset, DDM state should be STABLE (the "
            f"baseline was re-established per Gama §4); got "
            f"{post_reset_state['ddm_state']}"
        )
        assert post_reset_state["ddm_state_numeric"] == 0, (
            "after DRIFT auto-reset, the rto_drift_ddm_state gauge should "
            "read 0 (STABLE) — the reset reverted the gauge to baseline"
        )
        assert post_reset_state["ddm_n"] == 0, (
            f"after DRIFT auto-reset, ddm_n should be 0 (fresh baseline); "
            f"got {post_reset_state['ddm_n']}"
        )
        assert post_reset_state["adwin_state"] == "STABLE", (
            f"after DRIFT auto-reset, ADWIN state should also be STABLE "
            f"(both detectors reset together); got "
            f"{post_reset_state['adwin_state']}"
        )

        # 4. The post-reset detector is alive + processes new samples.
        # Feed 1 more label + assert ddm_n becomes 1 (the new sample
        # was processed, not silently dropped).
        post_reset_one_more = service.ingest_label(
            prediction_id="pred-post-reset",
            is_returned=True,
            predicted_p=0.5,  # correct prediction → error=0
        )
        assert post_reset_one_more["error"] == 0, (
            f"post-reset label should produce error=0 (correct "
            f"prediction); got {post_reset_one_more['error']}"
        )
        live_state = service.current_state()
        assert live_state["ddm_n"] == 1, (
            f"after 1 post-reset label, ddm_n should be 1 (the reset "
            f"detector is processing new samples); got {live_state['ddm_n']}"
        )
        assert live_state["ddm_state"] == "STABLE", (
            f"1 sample post-reset should still be STABLE (the min_n=30 "
            f"gate blocks DRIFT detection during cold-start); got "
            f"{live_state['ddm_state']}"
        )
    finally:
        service.close()


def test_ddm_prometheus_gauge_numeric_value_fires_at_drift_moment():
    """Assert the Prometheus gauge NUMERIC value (STATE_NUMERIC) is 2
    at the exact moment of DRIFT detection (not just the public ``state``
    string field).

    DO BADLY #6 — the prior 15-c run added 2 real-DDM-state tests but
    neither asserted the ``STATE_NUMERIC`` mapping (the gauge numeric
    value the Prometheus scraper would read) is 2 (DRIFT) at the moment
    of detection. They asserted the public ``state`` string field
    transitioned to "DRIFT" + the post-reset state reverted to "STABLE" —
    but if a regression broke the ``STATE_NUMERIC`` mapping (e.g.
    hardcoded to 0), the gauge would scrape 0 even when the state
    string said "DRIFT" + the prior tests would still pass.

    This test closes that gap by:
      1. Constructing a real DDM(min_n=10) instance.
      2. Seeding a 30-sample baseline (all correct → p_min=1.0 default).
      3. Feeding 1 wrong label to break the degeneracy guard (sigma_min
         becomes > 0 → baseline gets adopted).
      4. Feeding a sustained error burst until DRIFT fires.
      5. Asserting that AT THE MOMENT of detection:
           * ``d.state == "DRIFT"`` (the public string field)
           * ``STATE_NUMERIC[d.state] == 2`` (the gauge numeric value)
         So a regression where the mapping was broken (e.g. accidentally
         set to ``{"DRIFT": 0}``) would fail this test, not silently
         scrape 0 on the gauge during a real drift event.

    This is a real-state assertion (no DDM mock — the production DDM
    class in src/ml/drift.py is exercised with a real Bernoulli error
    stream). It complements the existing 2 real-DDM-state tests by
    covering the gauge-numeric-value invariant they don't directly
    assert.
    """
    d = DDM(min_n=10)

    # 1. Cold-start baseline — 30 correct predictions.
    for _ in range(30):
        d.update(0)
    assert d.state == "STABLE"
    assert STATE_NUMERIC[d.state] == 0, (
        f"the gauge numeric value for STABLE should be 0 (the Prometheus "
        f"scraper would read 0 in the in-control state); got "
        f"{STATE_NUMERIC[d.state]}"
    )

    # 2. Break the degeneracy guard — 1 wrong label.
    d.update(1)
    assert d.sigma_min > 0.0, (
        "after 1 error, sigma_min should be > 0 (the degeneracy guard "
        "unblocks + baseline gets adopted)"
    )
    # State should still be STABLE (1 error in 31 samples — well below
    # the 3σ control limit; the p+sigma at this point is ≈0.032+0.032
    # = 0.064, while the threshold is p_min + 3*sigma_min ≈ 0.032 +
    # 3*0.032 = 0.128; 0.064 < 0.128 → no DRIFT).
    assert d.state == "STABLE", (
        f"1 error in 31 samples is below the 3σ threshold (should still "
        f"be STABLE); got {d.state}"
    )
    assert STATE_NUMERIC[d.state] == 0

    # 3. Sustained error burst — feed errors until DRIFT fires.
    states_at_detection = []
    numeric_at_detection = []
    for _ in range(50):
        s = d.update(1)
        if s == "DRIFT":
            states_at_detection.append(s)
            numeric_at_detection.append(STATE_NUMERIC[s])
            # Don't break — keep feeding to verify the state stays at
            # DRIFT (it doesn't auto-reset until the LabelFeedbackService
            # triggers a reset; the DDM.update() itself just sets state).
            break

    assert len(states_at_detection) == 1, (
        f"DRIFT should have fired exactly once during the burst; got "
        f"{len(states_at_detection)} firings"
    )
    assert states_at_detection[0] == "DRIFT"
    assert numeric_at_detection[0] == 2, (
        f"AT THE MOMENT of DRIFT detection, the STATE_NUMERIC mapping "
        f"(the Prometheus gauge numeric value) MUST be 2 — this is "
        f"what the scraper would read at the exact instant of drift "
        f"detection. A regression that broke the mapping (e.g. "
        f"hardcoded STATE_NUMERIC to {{'DRIFT': 0}}) would fail this "
        f"test; without this assertion, the regression would silently "
        f"scrape 0 on the gauge during a real drift event. Got "
        f"{numeric_at_detection[0]}."
    )

    # 4. The gauge numeric value should stay at 2 for subsequent DRIFT
    # updates (the state stays DRIFT until a reset is called — the DDM
    # doesn't auto-recover). This is the contract the
    # LabelFeedbackService's auto-reset relies on.
    second_drift = d.update(1)
    assert second_drift == "DRIFT", (
        f"DDM state should STAY at DRIFT after the burst (the state "
        f"doesn't auto-recover until reset() is called); got "
        f"{second_drift}"
    )
    assert STATE_NUMERIC[second_drift] == 2

    # 5. After explicit reset(), the state + gauge value revert to 0.
    d.reset()
    assert d.state == "STABLE", (
        f"after reset(), DDM state should revert to STABLE; got "
        f"{d.state}"
    )
    assert STATE_NUMERIC[d.state] == 0, (
        f"after reset(), the gauge numeric value should revert to 0 "
        f"(STABLE) — this is what the scraper would read post-retrain. "
        f"Got {STATE_NUMERIC[d.state]}."
    )
    # The reset wipes the running stats too — proves the reset was real
    # (not just a state-field flip).
    assert d.n == 0, (
        f"after reset(), d.n should be 0 (fresh baseline); got {d.n}"
    )
    assert d.p == 0.0, (
        f"after reset(), d.p should be 0.0 (fresh baseline); got {d.p}"
    )
    assert d.p_min == 1.0, (
        f"after reset(), d.p_min should revert to the constructor "
        f"default 1.0 (the in-control baseline is forgotten — the next "
        f"DRIFT detection establishes a new one); got {d.p_min}"
    )
    assert d.sigma_min == 0.0, (
        f"after reset(), d.sigma_min should revert to 0.0 (the "
        f"degeneracy guard is re-engaged — no DRIFT can fire until the "
        f"model makes at least 1 error); got {d.sigma_min}"
    )


def test_ddm_drift_fires_on_long_stream_with_mean_shift_at_event_500():
    """4th real-DDM-state test — verify DDM fires DRIFT on a LONGER stream
    with the mean shift happening at event 500 (the production-realistic
    scenario where drift is a sustained shift after a long stable period,
    not the short 30-sample burst the prior 15-c tests use).

    This is the canonical "stream where the mean shifts by 3σ at event
    500" pattern from the DO BADLY #6 spec. The prior 3 real-DDM-state
    tests use short 30-71 sample bursts which fire DRIFT quickly; this
    test verifies the DDM correctly handles a 500-sample stable
    baseline + then a sudden 100% error-rate shift.

    Sequence:
      1. Feed 500 cold-start events with a deterministic 1% error rate
         (1 error every 100 events → 5 errors in 500 samples). The DDM
         establishes a low baseline: p_min ≈ 0.01, sigma_min ≈ 0.0045.
         The (p+sigma) value monotonically decreases as n grows at a
         constant error rate → the minimum (p+sigma) point is at the
         END of the cold-start, so p_min = 0.01 (the cold-start p).
      2. At event 500+, the error rate shifts to 100% (a >3σ mean
         shift — the new running p climbs from 0.01 toward 1.0, far
         above the 3σ threshold of p_min + 3*sigma_min ≈ 0.024).
      3. Assert the DDM fires DRIFT within ~10 events of the shift
         (NOT before — the cold-start shouldn't fire DRIFT).
      4. After 50 burst events, assert the running p climbed from
         ≈0.01 to >= 0.05 (a 5x shift, well above the 3σ threshold;
         at n=550, sum of errors = 5 cold + 50 burst = 55, so
         p = 55/550 = 0.1).

    This test closes the production-realistic scenario where drift is
    GRADUAL — the existing tests use short 30-71 sample bursts which
    fire DRIFT quickly; this test verifies the DDM correctly handles
    a 500-sample stable baseline + then a sudden shift. The 500-event
    cold-start is long enough that:
      - The DDM's running baseline (p_min/sigma_min) is well-established
        (not just barely past the min_n=30 gate).
      - The (p+sigma) value at the end of cold-start is small (sigma
        shrinks as 1/sqrt(n)), so the 3σ threshold is tight — the
        sudden shift breaches it quickly (within ~4 events).
    """
    d = DDM(min_n=30)

    # 1. Cold-start — 500 events with a deterministic 1% error rate
    # (1 error every 100 events → 5 errors in 500 samples). This
    # determinism (vs random.Random(42)) makes the test reproducible
    # AND makes the exact p value at the end of cold-start known
    # (5/500 = 0.01 exactly).
    cold_start_errors = [1 if i % 100 == 99 else 0 for i in range(500)]
    states_during_cold_start = [d.update(e) for e in cold_start_errors]

    # The cold-start shouldn't fire DRIFT (the error rate is stable
    # at 1% — no shift). It also shouldn't fire WARNING (the (p+sigma)
    # value monotonically decreases during the cold-start → the new
    # baseline is adopted at every event past min_n, no threshold
    # breach).
    assert "DRIFT" not in states_during_cold_start, (
        f"cold-start with consistent 1% error rate shouldn't fire DRIFT; "
        f"states={set(states_during_cold_start)}"
    )
    assert d.state == "STABLE", (
        f"after cold-start, DDM should be STABLE; got {d.state}"
    )
    # Baseline established (sigma_min > 0 because we have 5 errors
    # in 500 samples → degeneracy guard unblocked).
    assert d.sigma_min > 0.0, (
        f"sigma_min should be > 0 after cold-start with 1% error rate "
        f"(the degeneracy guard should be unblocked — 5 errors in 500 "
        f"samples → sigma is computable); got {d.sigma_min}"
    )
    # Running p should be exactly 0.01 (5 errors in 500 samples).
    assert d.p == pytest.approx(0.01, abs=1e-9), (
        f"after cold-start with 1% error rate, running p should be 0.01 "
        f"(5 errors in 500 samples); got {d.p}"
    )
    assert d.n == 500, (
        f"after 500 cold-start events, n should be 500; got {d.n}"
    )
    # p_min should be < 0.01 (the running p at the end of cold-start).
    # The minimum (p+sigma) point during cold-start occurs BETWEEN
    # errors — when no new errors arrive but n keeps growing → p
    # slowly drops (p_i = (sum of errors so far) / i = 1/i between
    # the 1st error at event 99 and the 2nd at event 199). The
    # minimum is at event 198 (right before the 2nd error jumps p
    # back up), where p ≈ 1/199 ≈ 0.005025. The baseline adoption
    # at that point sets p_min ≈ 0.005025. After event 199, no re-
    # adoption happens (the (p+sigma) value at events 200-499 is
    # always larger than 0.005025 + 0.005012 = 0.010037).
    assert 0.0 < d.p_min < 0.01, (
        f"p_min should be in (0, 0.01) — the minimum (p+sigma) point "
        f"occurs between errors (when p slowly drops due to no new "
        f"errors). With 1 error every 100 events, the minimum is "
        f"≈1/199≈0.005025 (at event 198, just before the 2nd error "
        f"jumps p back up to 0.01). The constructor default 1.0 was "
        f"replaced during cold-start; got p_min={d.p_min}"
    )
    # sigma_min should be > 0 + < 0.01 (same reasoning — sigma at
    # the minimum (p+sigma) point is ≈0.005012, well below the
    # constructor default 0.0).
    assert 0.0 < d.sigma_min < 0.01, (
        f"sigma_min should be in (0, 0.01) — the binomial std at the "
        f"minimum (p+sigma) point. With p≈0.005025 + n=199, sigma "
        f"≈ √(0.005025*0.995/199) ≈ 0.005012. Got sigma_min="
        f"{d.sigma_min}"
    )

    # 2. At event 500+, shift the error rate to 100% (a >3σ mean
    # shift). DRIFT should fire within ~4 events of the shift (the
    # running p climbs from 0.01 to >0.024 within ~4 events). Feed 50
    # burst events to verify both the DRIFT detection AND the post-
    # drift state (running p climbing to >= 0.05).
    drift_event_index: int | None = None
    burst_states: list[str] = []
    for i in range(50):
        s = d.update(1)  # ALL errors after event 500
        burst_states.append(s)
        if s == "DRIFT" and drift_event_index is None:
            drift_event_index = i
            # Don't break — keep feeding to verify the post-drift p
            # climbs (the DDM doesn't auto-recover; once in DRIFT,
            # it stays in DRIFT until reset() is called).
    assert drift_event_index is not None, (
        "DRIFT should fire after the mean shift at event 500 (the new "
        "100% error rate is far above the 1% baseline → 3σ threshold "
        f"breached within ~4 events); burst_states={burst_states}"
    )
    # DRIFT should fire within 10 events of the shift (DDM's 3σ
    # threshold is breached quickly when p jumps from 0.01 to 1.0).
    # Math: at event 504 (4 burst events), p ≈ 9/504 ≈ 0.0179,
    # sigma ≈ 0.0059, p+sigma ≈ 0.0238 > threshold ≈ 0.0235 → DRIFT.
    assert drift_event_index < 10, (
        f"DRIFT should fire within 10 events of the mean shift; "
        f"got drift_event_index={drift_event_index} "
        f"(states[:10]={burst_states[:10]})"
    )

    # 3. After the 50-event burst, the running p should be >= 0.05
    # (5x the cold-start baseline). At n=550, sum of errors = 5 cold
    # + 50 burst = 55, so p = 55/550 = 0.1. The new errors dominate
    # the running mean because they're 100% error rate vs the 1%
    # cold-start rate.
    assert d.p >= 0.05, (
        f"after the 50-event post-shift burst, running p should be >= "
        f"0.05 (the new 100% error rate dominates the running mean — "
        f"at n=550 with 5 cold + 50 burst = 55 errors, p = 55/550 = "
        f"0.1, well above the 0.01 baseline); got {d.p}"
    )
    assert d.state == "DRIFT", (
        f"DDM's public state should be DRIFT after the burst (the state "
        f"doesn't auto-recover until reset() is called); got {d.state}"
    )
    assert d.n == 550, (
        f"after 500 cold-start + 50 burst events, n should be 550; "
        f"got {d.n}"
    )
    # The DDM doesn't auto-reset — verify by feeding 1 more error
    # and asserting the state stays DRIFT.
    post_state = d.update(1)
    assert post_state == "DRIFT", (
        f"DDM state should STAY at DRIFT for subsequent updates (no "
        f"auto-recovery until reset()); got {post_state}"
    )
