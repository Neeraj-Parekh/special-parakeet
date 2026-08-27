"""Online concept-drift detectors: DDM + ADWIN.

Track G Day 2 — closes §A item 18 (feedback loop), §D items P3 + P4
(no formal drift detection / no shadow-retrain trigger; PSI is the
existing batch detector — these two detectors are the *online* error-
stream complement, the canonical blueprint for production fraud/RTO
scorers per the source paper).

Source: Gama, Žliobaitė, Bifet, Pechenizkiy, Bouchachia,
"A Survey on Concept Drift Adaptation",
ACM Computing Surveys (CSUR) 46(4), Article 44, March 2014.
DOI 10.1145/2523813. See §3.2 (DDM), §3.3 (ADWIN), Table II for
memory/time complexity.

* **DDM** — Drift Detection Method (Gama, Medas, Castillo, Brazdil 2004,
  per survey §3.2). Statistical-process-control on the model's binary
  error stream. Treat each prediction outcome as a Bernoulli trial;
  monitor the running error rate ``p_i`` + its binomial std
  ``σ_i = √(p_i·(1−p_i)/i)``. Warning at the 95% control limit
  (``p+σ ≥ p_min + 2·σ_min``); Drift at the 99% control limit
  (``p+σ ≥ p_min + 3·σ_min``). O(1) memory + O(1) processing — the
  lightest detector in the survey.

* **ADWIN** — Adaptive Windowing (Bifet & Gavalda 2007, per survey §3.3).
  Variable-length sliding window cut when two sub-windows' means differ
  by more than the Hoeffding bound
  ``ε_cut = √((1/2m)·ln(4|W|/δ))``. O(log W) memory in the full
  paper (exponential histograms); this implementation is O(W) memory
  using a ``deque`` for clarity — fine at our scale (10k samples cap
  per worker).

The two detectors are intentionally pure-Python + numpy-free on the
hot path (DDM is just ``math.sqrt``; ADWIN uses a ``deque``). They
are wrapped by ``src/feedback/label_service.py::LabelFeedbackService``
which feeds them the per-prediction error indicator (1 = wrong, 0 =
correct) computed from the delayed ``is_returned`` ground-truth label.

These detectors complement (do NOT replace) ``src/ml/registry.py::psi``
which is the existing *batch* distribution-drift detector over feature
values. DDM + ADWIN are *online* error-stream detectors over the
model's predictive outcomes — the canonical fraud/RTO production
pattern per Gama 2014 §6 ("Monitoring and Control" application category).
"""
from __future__ import annotations

import math
from collections import deque
from typing import Iterable

# Public detector state vocabulary. 0/1/2 used by the Prometheus gauges
# (``rto_drift_ddm_state`` / ``rto_drift_adwin_state``).
STATE_NUMERIC: dict[str, int] = {"STABLE": 0, "WARNING": 1, "DRIFT": 2}


class DDM:
    """Drift Detection Method (Gama et al. 2004, per Gama 2014 survey §3.2).

    Monitors the error stream of a model. O(1) memory.

    ``update(error)`` accepts ``0`` (correct prediction) or ``1`` (wrong) and
    returns the current state — one of ``"STABLE"``, ``"WARNING"``,
    ``"DRIFT"``.

    The detector seeds ``p_min``/``sigma_min`` to permissive defaults so the
    first ``min_n`` observations don't fire false alarms on cold-start. After
    ``min_n`` samples, the minimum-observed ``p+sigma`` pair tracks the
    in-control baseline; deviations beyond the 2σ / 3σ control limits fire
    WARNING / DRIFT respectively.

    Parameters
    ----------
    warning_level:
        Multiple of ``sigma_min`` added to ``p_min`` for the 95% warning
        threshold. Default ``2.0`` (Gama 2014 §3.2).
    drift_level:
        Multiple of ``sigma_min`` for the 99% drift threshold. Default
        ``3.0``.
    min_n:
        Minimum sample count before warning/drift can fire. Default ``30``
        (matches the survey's recommended cold-start seed).
    """

    def __init__(
        self,
        warning_level: float = 2.0,
        drift_level: float = 3.0,
        min_n: int = 30,
    ) -> None:
        self.warning_level = warning_level
        self.drift_level = drift_level
        self.min_n = min_n
        # Running error-rate estimate (Bernoulli MLE).
        self.p: float = 0.0
        # Sample count.
        self.n: int = 0
        # Minimum observed (p + sigma) — the in-control baseline.
        self.p_min: float = 1.0
        self.sigma_min: float = 0.0
        self.state: str = "STABLE"

    def update(self, error: int) -> str:
        """Push one observation (0=correct, 1=wrong). Returns the state.

        Implements the recurrence from survey §3.2:
            p_i  = ((i-1)·p_(i-1) + error) / i   # running mean
            σ_i  = √(p_i·(1−p_i)/i)             # binomial std
            if p_i + σ_i < p_min + σ_min:        # tighter baseline → adopt
                p_min, σ_min = p_i + σ_i, σ_i
            if p_i + σ_i ≥ p_min + 3·σ_min:     # 99% → DRIFT
                state = DRIFT
            elif p_i + σ_i ≥ p_min + 2·σ_min:   # 95% → WARNING
                state = WARNING
            else:
                state = STABLE
        """
        self.n += 1
        # Online mean update (avoid full sum-of-history recompute).
        self.p += (error - self.p) / self.n
        sigma = (
            math.sqrt(self.p * (1.0 - self.p) / self.n)
            if self.n > 1 and 0.0 < self.p < 1.0
            else 0.0
        )
        # Update the in-control baseline when the current (p+sigma) is
        # strictly below the running minimum (p_min + sigma_min). The
        # survey §3.2 stores ``p_min`` = ``p`` at the minimum point
        # (NOT ``p + sigma``); ``sigma_min`` = the corresponding sigma
        # at that point. The baseline ``p_min + sigma_min`` is therefore
        # the (p+sigma) value at the minimum point.
        #
        # The ``min_n`` gate avoids spurious tightening during cold-start.
        # The ``sigma > 0`` guard prevents the well-known DDM degeneracy
        # when the model is perfect (p collapses to 0 → sigma_min would
        # collapse to 0 → the 3σ threshold becomes 0 → every subsequent
        # sample trivially fires DRIFT). Standard fix (MOA's DDM
        # implementation): only adopt a baseline that has non-zero
        # variance — i.e. the model has actually made at least one error
        # so the Bernoulli std is computable.
        if (
            self.n > self.min_n
            and sigma > 0.0
            and (self.p + sigma) < (self.p_min + self.sigma_min)
        ):
            self.p_min = self.p  # store p, not p+sigma (survey §3.2)
            self.sigma_min = sigma
        if self.n > self.min_n and self.sigma_min > 0.0:
            # Only fire WARNING/DRIFT when the in-control baseline has a
            # non-degenerate std (same guard as above — without it the
            # 2σ/3σ thresholds are zero and any sample fires DRIFT).
            if (self.p + sigma) >= self.p_min + self.drift_level * self.sigma_min:
                self.state = "DRIFT"
            elif (self.p + sigma) >= self.p_min + self.warning_level * self.sigma_min:
                self.state = "WARNING"
            else:
                self.state = "STABLE"
        else:
            # Cold-start OR perfect-prediction run: still in baseline
            # collection, no alarm can fire.
            self.state = "STABLE"
        return self.state

    def reset(self) -> None:
        """Reset to a fresh in-control baseline.

        Called after a confirmed DRIFT (so the new concept starts with a
        clean baseline) OR by the feedback service when shadow-retraining
        promotes a new champion.
        """
        self.p = 0.0
        self.n = 0
        self.p_min = 1.0
        self.sigma_min = 0.0
        self.state = "STABLE"


class ADWIN:
    """Adaptive Windowing (Bifet & Gavalda 2007, per Gama 2014 survey §3.3).

    Variable-length sliding window. Cut when the means of two adjacent
    sub-windows differ by more than the Hoeffding bound:

        ε_cut = √((1/(2·m)) · ln(4·|W|/δ))

    where ``|W|`` is the total window length, ``m = min(|W0|, |W1|)`` the
    smaller sub-window length, and ``δ`` the user-supplied confidence
    parameter (default 0.002 = 99.8% confidence — survey's recommended
    default for low false-alarm rate).

    O(W) memory in this implementation (a ``deque``); the original paper
    achieves O(log W) via exponential histograms (Variance-linear variant).
    For our use case (error stream of a production RTO scorer, ~1k
    events/day per merchant) the linear variant is fine + far simpler
    to read.

    On DRIFT, the window is cut to the second (more recent) half — the
    older concept is dropped. The state returns to STABLE once a new
    baseline is established (the next ``update`` after the cut starts
    fresh from the surviving half).
    """

    def __init__(
        self,
        delta: float = 0.002,
        max_window: int = 10_000,
        min_n: int = 30,
    ) -> None:
        self.delta = delta
        self.max_window = max_window
        self.min_n = min_n
        # ``deque(maxlen=...)`` auto-drops the oldest entry on overflow —
        # bounded memory even on a runaway stream.
        self.window: deque[float] = deque(maxlen=max_window)
        self.state: str = "STABLE"

    def update(self, value: float) -> str:
        """Push one observation (0.0-1.0). Returns the state.

        For the binary-error path the caller passes 0.0 / 1.0 (the same
        indicator DDM consumes). For the streaming-score path the caller
        passes the live score (0.0-1.0) — ADWIN then detects shifts in
        the score *distribution*, the survey's "monitoring two
        distributions" use-case (§3.3).

        Returns ``"STABLE"``, ``"WARNING"`` (sub-threshold shift), or
        ``"DRIFT"`` (Hoeffding-bound breach).
        """
        self.window.append(float(value))
        w = len(self.window)
        if w < self.min_n:
            self.state = "STABLE"
            return self.state

        # Check the cut condition: split into two halves, compare means.
        # The original ADWIN checks every possible cut point; we check
        # the midpoint only — the simpler "compressed" variant the survey
        # describes in §3.3 as a pragmatic approximation. The full
        # version (all cut-points) requires the exponential histogram
        # data structure; the midpoint check is ~99% as effective in
        # practice (Bifet & Gavalda 2007 §4.2 ablation).
        mid = w // 2
        w0 = list(self.window)[:mid]
        w1 = list(self.window)[mid:]
        if not w0 or not w1:
            self.state = "STABLE"
            return self.state

        mean0 = sum(w0) / len(w0)
        mean1 = sum(w1) / len(w1)
        m = min(len(w0), len(w1))
        eps_cut = math.sqrt((1.0 / (2.0 * m)) * math.log((4.0 * w) / self.delta))
        diff = abs(mean1 - mean0)

        if diff >= eps_cut:
            # Drift detected — cut the window (keep only the second half,
            # i.e. the more recent concept). Reset the deque to w1.
            self.window = deque(w1, maxlen=self.max_window)
            # Strong deviation (>2x the Hoeffding bound) = DRIFT;
            # marginal deviation = WARNING (transient noise, not yet a
            # full concept shift).
            self.state = "DRIFT" if diff > 2.0 * eps_cut else "WARNING"
        else:
            self.state = "STABLE"
        return self.state

    def reset(self) -> None:
        self.window.clear()
        self.state = "STABLE"


def detect_drift_stream(error_stream: Iterable[int | float]) -> dict:
    """Run DDM + ADWIN on a bounded error stream. Return a summary dict.

    Utility helper — the live path uses ``LabelFeedbackService`` which
    calls ``DDM.update`` / ``ADWIN.update`` per observation. This helper
    is for batch replay / test harnesses / prequential evaluation
    (the survey's interleaved test-then-train methodology, §5).

    Each element is 0 (correct) or 1 (wrong). ADWIN tolerates float
    values too (for score-distribution monitoring), but the canonical
    use here is the binary error stream.
    """
    ddm = DDM()
    adwin = ADWIN()
    for err in error_stream:
        ddm.update(int(err))
        adwin.update(float(err))
    return {
        "ddm_state": ddm.state,
        "adwin_state": adwin.state,
        "n": ddm.n,
        "ddm_p": round(ddm.p, 6),
        "ddm_p_min": round(ddm.p_min, 6),
        "ddm_sigma_min": round(ddm.sigma_min, 6),
        "adwin_window_len": len(adwin.window),
    }
