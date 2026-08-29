"""Label feedback service + drift consumer (Track G Day 2).

Closes §A item 18 (feedback loop), §D items P3 (formal drift detection)
+ P4 (shadow-retrain trigger) + perceived-gap driver G3 (partial —
formal detectors land here; the PSI reference distribution population
is deferred). The MLOps-DevOps paper's ``plan_drift_triggered_retraining``
capability is closed in full here: the label-feedback path produces the
retrain signal AND fires the trigger.

Two services live in this package:

* ``LabelFeedbackService`` — wraps DDM + ADWIN over the *delayed* label
  stream (``is_returned`` arrives days-weeks after the prediction was
  made — the survey's "credit-scoring 1-year horizon" delayed-feedback
  pattern, Gama 2014 §6). The service is constructed once at API boot
  (``state["feedback"]`` in routes.py) so the in-memory DDM/ADWIN
  state persists across requests within one worker process. On DRIFT,
  publishes a ``retrain_request`` notification to the ``notifications``
  Redis Stream (Track F reserved that stream for this consumer).

* ``run_drift_consumer`` — worker entrypoint that drains the
  ``model.drift`` stream (produced by Track F's StreamProcessor on
  duplicate-order / score-velocity / score-mean anomalies) via a 3rd
  consumer group ``rto-drift-detectors``. The consumer is the anomaly-
  side complement to the label-side DDM/ADWIN: when the stream
  processor emits 3+ consecutive DRIFT signals of the same
  ``anomaly_reason``, the consumer ALSO fires a retrain-request (so we
  don't have to wait for delayed labels to react to a sudden
  distribution shift — the streaming PSI equivalent triggers
  shadow-retraining first, the formal label-side DDM confirmation
  follows days later).

Source: Gama, Žliobaitė, Bifet, Pechenizkiy, Bouchachia,
"A Survey on Concept Drift Adaptation",
ACM Computing Surveys (CSUR) 46(4), Article 44, March 2014.
DOI 10.1145/2523813.
"""
from src.feedback.drift_consumer import run_drift_consumer  # noqa: F401
from src.feedback.label_service import LabelFeedbackService  # noqa: F401

__all__ = ["LabelFeedbackService", "run_drift_consumer"]
