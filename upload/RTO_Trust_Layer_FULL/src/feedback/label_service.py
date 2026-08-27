"""Label-feedback service: delayed is_returned labels → DDM/ADWIN → retrain.

Track G Day 2. Closes the feedback-loop half of §A item 18 + §D items
P3/P4 + perceived-gap driver G3 (partial). The MLOps-DevOps paper's
``plan_drift_triggered_retraining`` capability lands here: the
delayed-label stream feeds DDM + ADWIN, and a confirmed DRIFT fires a
``retrain_request`` notification.

The chargeback-style delayed-label reality
-----------------------------------------
RTO ground truth (``is_returned``) arrives days-weeks after the
prediction was made — the courier attempts delivery, the customer
either accepts or returns, the merchant records the outcome, and the
label is eventually posted back to the model. This is the survey's
canonical "credit-scoring 1-year horizon" delayed-feedback pattern
(Gama 2014 §6, "Monitoring and Control" application category —
production fraud/RTO scorers are the explicit example).

The online error-rate detectors (DDM/ADWIN) handle this elegantly:
each delayed label becomes one Bernoulli trial whose outcome
(``error = 1 if prediction wrong, else 0``) updates the in-control
baseline. The detectors don't care about the temporal gap between
prediction and label — they care about the error rate's stability.
A model that suddenly starts mis-predicting (e.g. a fraudster adapted
their pattern) shows up as a sustained error burst that the 2σ/3σ
control limits catch at the 95%/99% confidence levels.

Error indicator (the Bernoulli trial)
------------------------------------
Given the model's predicted ``P(RTO)`` and the ground-truth
``is_returned`` label:

    error = 1 if (predicted_p >= threshold) != is_returned else 0

This flags both false-negatives (predicted safe, but returned — the
expensive RTO) AND false-positives (predicted RTO, but the order was
delivered fine — a friction cost). The threshold defaults to 0.15
(the cost-optimizer's legacy ACCEPT_T — orders above this go to
REVIEW; both halves of the FN/FP indicator matter for drift).

On DRIFT
--------
Publishes a ``retrain_request`` notification to the ``notifications``
Redis Stream. Track H will install a custom handler on that stream
to actually open a retraining PR (GitHub webhook) or trigger the
shadow-retrain pipeline. For now the notification is the trigger —
the consumer of ``notifications`` is reserved for Track H.

Multi-process safety
--------------------
The DDM/ADWIN state lives in-memory per worker process. In a
multi-worker deployment (``uvicorn --workers N``), each worker's
``LabelFeedbackService`` sees a different slice of the label stream
(the API's load-balancer fans out requests round-robin). This means
each worker's detector state is *partial* — a single worker might
not see enough samples to fire DRIFT even when the aggregate error
rate has clearly shifted. The robust fix is to share detector state
via Redis (a single ``rto:drift:ddm`` hash updated atomically per
label). For the hackathon demo, the single-worker deployment is fine
and the partial-state caveat is documented; the full Redis-shared
state is a deferred enhancement (Track M Day 4 if needed).

The drift consumer (``src.feedback.drift_consumer``) is the
*anomaly-side* complement — it consumes the ``model.drift`` Redis
Stream (produced by Track F's StreamProcessor) and fires a parallel
retrain trigger when 3+ consecutive anomalies of the same reason
arrive, so we react to sudden distribution shifts within seconds
rather than days.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from src.ml.drift import ADWIN, DDM, STATE_NUMERIC

# Threshold for the error indicator: predicted_p >= threshold means
# the model would route the order to REVIEW/REJECT (i.e. predicted
# "likely RTO"). Below it means ACCEPT (predicted "safe"). The default
# of 0.15 matches the cost-optimizer's legacy ACCEPT_T (closes §A
# item 18 — the feedback loop uses the same threshold surface the
# decision layer uses, so the error indicator is semantically
# consistent: "was the prediction right given the decision surface?").
DEFAULT_RETURN_THRESHOLD = 0.15


class LabelFeedbackService:
    """Consumes delayed is_returned labels. Runs DDM + ADWIN per label.
    On DRIFT, triggers shadow-retraining via the notifications stream.

    Source: Gama 2014, Survey on Concept Drift Adaptation, ACM CSUR 46(4).
    """

    # Number of consecutive same-reason anomalies on the model.drift
    # stream before the drift consumer (which uses this service for the
    # retrain trigger) fires a retrain_request. 3 is the heuristic —
    # matches Track F's stream-processor baseline seed (also 30 messages
    # before anomaly detection fires; 3 consecutive anomalies is the
    # "sustained drift" signal vs a single false alarm).
    DRIFT_SIGNAL_RUN_LENGTH = 3

    def __init__(
        self,
        redis_url: str | None = None,
        database_url: str | None = None,
        return_threshold: float = DEFAULT_RETURN_THRESHOLD,
    ) -> None:
        self.redis_url = redis_url
        self.database_url = database_url
        self.return_threshold = return_threshold
        # Per-detector state — one DDM/ADWIN pair per service instance.
        # (In the live path this is one-per-worker; see "Multi-process
        # safety" in the module docstring.)
        self.ddm = DDM()
        self.adwin = ADWIN()
        # Lazy-init the producer — only constructed on first DRIFT
        # publish. Matches Track F's StreamProducer contract: a None
        # ``redis_url`` means the service is a no-op (the test suite
        # passes None).
        self._producer: Any = None
        # Lock around the update path so concurrent requests (uvicorn
        # workers, async concurrent requests) don't race on DDM/ADWIN
        # state.
        self._lock = threading.Lock()
        # In-memory counter of consecutive same-reason anomalies
        # observed on the model.drift stream (consumer side). Keyed by
        # ``anomaly_reason`` (``duplicate_order_id`` /
        # ``score_velocity_spike`` / ``score_mean_drift``). When any
        # count hits ``DRIFT_SIGNAL_RUN_LENGTH``, the consumer fires
        # a retrain_request. Reset on a different reason.
        self._drift_signal_run: dict[str, int] = {}

    # ------------------------------------------------------------------ #
    # Label-side path (called by the /v1/feedback/ingest endpoint)      #
    # ------------------------------------------------------------------ #

    def ingest_label(
        self,
        prediction_id: str,
        is_returned: bool,
        predicted_p: float | None,
        threshold: float | None = None,
    ) -> dict:
        """Ingest one delayed label. Updates DDM + ADWIN; on DRIFT fires
        a ``retrain_request`` notification.

        ``predicted_p`` is the model's predicted ``P(RTO)`` for this
        ``prediction_id``. The caller (the API endpoint) looks it up
        from the audit log (file mode: ``AuditLogger.read(audit_id)``;
        the body has a ``prediction_id`` field). If the prediction
        can't be found, the caller passes ``None`` — the error
        indicator defaults to 0 (no contribution to the drift signal)
        and the response carries a ``"prediction_not_found": True``
        field for the caller's visibility.

        Returns a dict with the current DDM + ADWIN state, the
        per-prediction error indicator, and the total samples
        processed by DDM so far. The caller surfaces this in the
        HTTP response so the dashboard / ops console can show the
        detector state live (Panel 5 + 6 on the Grafana dashboard).
        """
        thr = self.return_threshold if threshold is None else threshold
        # Compute the error indicator: 1 if the prediction would
        # have routed the order to REVIEW/REJECT (predicted_p >= thr)
        # AND the customer didn't return it, OR if the prediction
        # would have ACCEPTed (predicted_p < thr) AND the customer
        # did return it. Otherwise 0 (correct).
        if predicted_p is None:
            error = 0
            not_found = True
        else:
            not_found = False
            # XOR: (predicted RTO?) != (actually returned?) → wrong.
            error = 1 if ((predicted_p >= thr) != bool(is_returned)) else 0

        with self._lock:
            ddm_state = self.ddm.update(error)
            adwin_state = self.adwin.update(float(error))
            drift_detected = (
                ddm_state == "DRIFT" or adwin_state == "DRIFT"
            )
            if drift_detected:
                self._trigger_shadow_retrain(
                    trigger_prediction_id=prediction_id,
                    ddm_state=ddm_state,
                    adwin_state=adwin_state,
                    source="label_feedback",
                )
                # After a confirmed DRIFT, reset the detectors so the
                # new concept (post-retrain) starts with a fresh
                # in-control baseline. The survey's recommendation in
                # §4: "after adaptation, re-establish the baseline
                # from the new concept".
                self.ddm.reset()
                self.adwin.reset()
            n_processed = self.ddm.n
            ddm_p = self.ddm.p
            adwin_window_len = len(self.adwin.window)
        return {
            "prediction_id": prediction_id,
            "is_returned": is_returned,
            "predicted_p": predicted_p,
            "error": error,
            "ddm_state": ddm_state,
            "adwin_state": adwin_state,
            "drift_detected": drift_detected,
            "n_processed": n_processed,
            "ddm_p": round(ddm_p, 6),
            "adwin_window_len": adwin_window_len,
            "prediction_not_found": not_found,
        }

    # ------------------------------------------------------------------ #
    # Anomaly-side path (called by the drift consumer on each            #
    # ``model.drift`` message)                                          #
    # ------------------------------------------------------------------ #

    def consume_anomaly(self, anomaly_reason: str, prediction_id: str) -> dict:
        """Track consecutive same-reason anomalies from ``model.drift``.

        When ``DRIFT_SIGNAL_RUN_LENGTH`` consecutive anomalies of the
        same reason fire, the consumer calls ``_trigger_shadow_retrain``
        with ``source="stream_anomaly_run"`` — the parallel path that
        reacts to sudden distribution shifts within seconds (vs the
        days-later label-side DDM confirmation).

        Resets the run-length on a different reason — a one-off
        ``score_velocity_spike`` followed by a ``score_mean_drift``
        shouldn't fire a retrain (those are different signal classes
        and one alone isn't sustained).
        """
        with self._lock:
            # Reset all OTHER reasons to 0 — only the current reason
            # accumulates. This means a "sustained" run is unbroken.
            for r in list(self._drift_signal_run.keys()):
                if r != anomaly_reason:
                    self._drift_signal_run[r] = 0
            self._drift_signal_run[anomaly_reason] = (
                self._drift_signal_run.get(anomaly_reason, 0) + 1
            )
            run_length = self._drift_signal_run[anomaly_reason]
            drift_detected = run_length >= self.DRIFT_SIGNAL_RUN_LENGTH
            if drift_detected:
                self._trigger_shadow_retrain(
                    trigger_prediction_id=prediction_id,
                    ddm_state="N/A",
                    adwin_state="N/A",
                    source=f"stream_anomaly_run:{anomaly_reason}",
                )
                # Reset the run so the next retrain trigger requires
                # another 3-anomaly run (not the same 3 forever).
                self._drift_signal_run[anomaly_reason] = 0
        return {
            "anomaly_reason": anomaly_reason,
            "prediction_id": prediction_id,
            "run_length": run_length,
            "drift_detected": drift_detected,
            "run_length_threshold": self.DRIFT_SIGNAL_RUN_LENGTH,
        }

    # ------------------------------------------------------------------ #
    # Retrain trigger                                                   #
    # ------------------------------------------------------------------ #

    def _trigger_shadow_retrain(
        self,
        trigger_prediction_id: str,
        ddm_state: str,
        adwin_state: str,
        source: str,
    ) -> None:
        """Publish a ``retrain_request`` to the ``notifications`` stream.

        Track F's ``StreamProducer`` (lazy-connect, fire-and-forget) is
        reused so the contract holds: if Redis is down or ``redis_url``
        is None (test mode), the publish is a no-op + the caller's
        response is unaffected. Track H will install a custom handler
        on the ``notifications`` stream to actually open a GitHub PR
        / trigger the shadow-retrain pipeline; for now the publish IS
        the trigger (a downstream operator sees the notification in
        ``docker compose logs stream-worker`` if they install the
        handler, or in the ``notifications`` stream via ``XREAD``).
        """
        if self._producer is None:
            # Lazy import to keep the test suite (which has no redis
            # package installed) import-clean.
            from src.stream.producer import StreamProducer

            self._producer = StreamProducer(self.redis_url)
        self._producer.publish(
            "notifications",
            {
                "type": "retrain_request",
                "trigger": "drift_detected",
                "source": source,
                "prediction_id": trigger_prediction_id,
                "ddm_state": ddm_state,
                "adwin_state": adwin_state,
                "ts": datetime.now(timezone.utc).isoformat(),
            },
        )

    # ------------------------------------------------------------------ #
    # Metrics helpers                                                   #
    # ------------------------------------------------------------------ #

    def current_state(self) -> dict:
        """Snapshot the current detector state. Called by the /metrics
        endpoint to populate the ``rto_drift_ddm_state`` +
        ``rto_drift_adwin_state`` Prometheus gauges.
        """
        with self._lock:
            return {
                "ddm_state": self.ddm.state,
                "ddm_state_numeric": STATE_NUMERIC.get(self.ddm.state, 0),
                "adwin_state": self.adwin.state,
                "adwin_state_numeric": STATE_NUMERIC.get(self.adwin.state, 0),
                "ddm_n": self.ddm.n,
                "ddm_p": round(self.ddm.p, 6),
                "adwin_window_len": len(self.adwin.window),
            }

    def close(self) -> None:
        """Close the underlying StreamProducer if one was opened."""
        if self._producer is not None:
            try:
                self._producer.close()
            except Exception:
                pass
            self._producer = None
