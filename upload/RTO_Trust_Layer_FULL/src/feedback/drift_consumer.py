"""Drift consumer worker — drains ``model.drift`` stream.

Track G Day 2. The ``drift-consumer`` docker-compose service runs
``python -m src.feedback.drift_consumer``. This is the 3rd consumer
group on the Redis Streams backbone:

* ``rto-workers`` (Track F ``stream-worker``) — drains
  ``risk.scores`` + ``audit.records`` + ``cases.created``.
* ``rto-processors`` (Track F ``stream-processor``) — drains
  ``risk.scores`` for the Microsoft Eventhouse equivalent
  (streaming transforms + anomaly → ``model.drift`` publish).
* **``rto-drift-detectors`` (Track G — this worker)** — drains
  ``model.drift`` for the DDM/ADWIN-equivalent anomaly-run-length
  detector + the ``retrain_request`` trigger.

The consumer doesn't re-implement DDM/ADWIN — the formal Bernoulli
error-stream detectors are wired to the **label side** (the
``LabelFeedbackService.ingest_label`` path, fed by delayed
``is_returned`` ground truth from the ``/v1/feedback/ingest``
endpoint). This consumer is the **anomaly side**: the
stream-processor's ``anomaly_reason`` field is the input, and the
run-length heuristic (3+ consecutive same-reason anomalies) fires
the retrain trigger on a *sustained* shift, not a single false alarm.

The label-side DDM (confirmed via delayed chargeback-style labels)
is the formal 99% confidence trigger; this anomaly-side run-length
heuristic is the fast-reactive 1-minute trigger for sudden shifts
(the fraudster-adapted pattern that the streaming-PSI equivalent
catches within seconds, vs days for the label-side confirmation).
"""
from __future__ import annotations

import sys

from src.feedback.label_service import LabelFeedbackService
from src.stream.consumer import StreamConsumer
from src.stream.producer import STREAM_MODEL_DRIFT


def _make_handler(service: LabelFeedbackService):
    """Build a per-message handler bound to ``service``.

    The handler extracts the ``anomaly_reason`` field from the
    ``model.drift`` message + calls
    ``LabelFeedbackService.consume_anomaly(reason, prediction_id)``.
    That method tracks the run-length + fires a retrain trigger on
    3+ consecutive same-reason anomalies.
    """

    def handler(stream: str, fields: dict) -> None:
        if stream != STREAM_MODEL_DRIFT:
            return
        reason = fields.get("anomaly_reason", "") or "unknown"
        prediction_id = fields.get("prediction_id", "") or ""
        result = service.consume_anomaly(reason, prediction_id)
        # Print to stderr so ``docker compose logs drift-consumer``
        # shows the anomaly flow end-to-end.
        print(
            f"[drift-consumer] anomaly={reason} run={result['run_length']}/"
            f"{result['run_length_threshold']} prediction_id={prediction_id}"
            f"{' DRIFT' if result['drift_detected'] else ''}",
            file=sys.stderr,
        )

    return handler


def run_drift_consumer() -> None:
    """Entrypoint for ``python -m src.feedback.drift_consumer``.

    Constructs a ``LabelFeedbackService`` (the in-memory state holder
    for the run-length heuristic + the retrain-trigger publisher) +
    a ``StreamConsumer`` joined to the ``rto-drift-detectors`` group,
    then blocks on ``XREADGROUP`` from the ``model.drift`` stream
    forever (or until SIGTERM / SIGINT).
    """
    from src.config import get_settings

    settings = get_settings()
    if not settings.redis_url:
        print(
            "[drift-consumer] REDIS_URL not set — drift consumer cannot start. "
            "Set REDIS_URL=redis://redis:6379 in the environment.",
            file=sys.stderr,
        )
        sys.exit(1)
    service = LabelFeedbackService(
        redis_url=settings.redis_url,
        database_url=settings.database_url,
    )
    consumer = StreamConsumer(
        settings.redis_url,
        group="rto-drift-detectors",
        consumer=None,  # auto-derived from hostname+pid
    )
    try:
        consumer.consume([STREAM_MODEL_DRIFT], _make_handler(service))
    finally:
        consumer.close()
        service.close()


if __name__ == "__main__":
    run_drift_consumer()
