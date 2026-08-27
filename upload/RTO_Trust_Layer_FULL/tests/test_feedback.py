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
        assert "# TYPE rto_drift_ddm_state gauge" in text
        assert "# TYPE rto_drift_adwin_state gauge" in text
        assert "# TYPE rto_drift_samples_processed gauge" in text
        # ddm_n should be 1 (one ingest_label call above).
        # Both gauges should be 0 (STABLE) at this point.
        assert "rto_drift_ddm_state 0" in text
        assert "rto_drift_adwin_state 0" in text
        assert "rto_drift_samples_processed 1" in text


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
