"""Cost-optimal decision policy (Bahnsen Bayes Minimum Risk, ICMLA 2013).

This module implements four pieces of the cost-sensitive decision layer:

1. ``optimal_decision`` — per-order 3-way (ACCEPT/REVIEW/REJECT) Bayes minimum
   risk decision layer. Chooses the action with the lowest expected monetary
   cost given the model probability ``p = P(RTO | order)``. Supports the
   per-amount FN cost from Bahnsen Eq.(5) via the ``amount_inr`` argument
   (Day 4 Track N — V3 §11.6 5-way intervention policy).

2. ``optimal_intervention`` — full V3 §11.6 5-way intervention policy argmin
   over {ship, otp_verify, partial_cod, address_check, hold}. Per-transaction
   FN cost = ``amount_inr`` (Bahnsen Eq.(5) — not constant), per-intervention
   cost + per-intervention effectiveness (Pragma 2025 OTP/address-check RTO
   reduction rates).

3. ``calibrate_probabilities`` — Bahnsen Eq.(6) post-resampling probability
   recalibration. Required when training uses SMOTE/under-sampling so the
   inflated minority prior is undone before cost-sensitive decisions fire.

4. ``cost_curve_sweep`` + ``bootstrap_cost_ci`` + ``find_cost_crossover`` —
   Drummond & Holte cost-curve analysis over a labeled dataset. Threshold sweep
   with bootstrap confidence intervals (row-marginal-preserving resampling per
   their skill ``bootstrap_performance_ci``). Used by the
   ``/v1/policy/cost-curves`` endpoint to power the dashboard explorer.

References
----------
- Bahnsen, Stojanovic, Aouada, Ottersten — *Cost Sensitive Credit Card Fraud
  Detection using Bayes Minimum Risk*, ICMLA 2013, DOI 10.1109/ICMLA.2013.68
  Eq.(5) BMR rule (per-transaction amount = FN cost), Eq.(6) recalibration:
  ``P*(f|x) = P(f|x) · P_orig / P_und``
- Drummond & Holte — *Cost Curves: An Improved Method for Visualizing
  Classifier Performance*, Machine Learning 65:95-130 (2006),
  DOI 10.1007/s10994-006-8199-5
"""
from __future__ import annotations

import math
from typing import Sequence


# ---------------------------------------------------------------------------
# 0. V3 §11.6 5-way intervention vocabulary + default weights (Track N)
# ---------------------------------------------------------------------------
# The full per-transaction intervention set the cost-optimizer can recommend.
# Track C's 3-way optimal_decision() collapses REVIEW into a single OTP step;
# Track N surfaces the granular interventions that the operations team can
# actually execute. The 5 interventions are ordered by escalating friction /
# human-involvement, mirroring the V3 §11.6 policy table.
INTERVENTIONS: tuple[str, ...] = (
    "ship",            # baseline: ship the order, no friction
    "otp_verify",      # selective OTP / IVR call before ship
    "partial_cod",     # collect a partial amount upfront, ship balance COD
    "address_check",   # call-center address verification (NPCI / India Post API)
    "hold",            # manual review queue (case opened, ops SLA timer)
)

# Default weights for the 5-way intervention cost model. Track C's
# DEFAULT_COST_WEIGHTS (3-way) is a *subset* of this — the 5-way policy
# re-uses c_fp / c_fn / c_block from the 3-way layer when the operator's
# decision collapses back to ACCEPT/REJECT equivalents. Per-intervention
# effectiveness rates are from the Pragma 2025 RTO-mitigation benchmark
# (docs/research/INDEX.md) for OTP (0.78-0.84), partial COD (0.60-0.70),
# and address validation (0.42-0.48). Conservative point estimates are used.
DEFAULT_INTERVENTION_WEIGHTS: dict[str, float] = {
    "c_ship_fp": 50.0,                     # FP cost: ship a bad order → review fee (capped)
    "c_ship_fn": 0.0,                      # FN cost for ship = 0 (ship is baseline; loss IS amount)
    "c_otp": 5.0,                          # OTP send + verification labour, INR
    "c_otp_effectiveness": 0.82,           # OTP reduces RTO probability by 82%
    "c_partial_cod": 10.0,                 # partial COD collection cost, INR
    "c_partial_cod_effectiveness": 0.65,   # partial COD reduces RTO by 65%
    "c_address_check": 3.0,                # address validation cost (call/API), INR
    "c_address_check_effectiveness": 0.45, # address check reduces RTO by 45%
    "c_hold": 20.0,                        # hold for manual review ops time, INR
    "c_hold_fn": 0.0,                      # if hold + still ships + returns → FN = amount
    "c_block": 1000.0,                     # block: lost sale + customer churn, INR
    "c_hold_residual_ship_rate": 0.30,     # 30% of held orders still ship + can return
}


# ---------------------------------------------------------------------------
# 1. Per-order Bayes Minimum Risk decision layer (Bahnsen Eq.(5))
# ---------------------------------------------------------------------------

def optimal_decision(
    p: float,
    c_fp: float = 50.0,
    c_fn: float = 600.0,
    c_otp: float = 5.0,
    c_block: float = 1000.0,
    otp_effectiveness: float = 0.82,
    amount_inr: float | None = None,
) -> tuple[str, dict]:
    """Three-way cost-optimal decision.

    Parameters
    ----------
    p : float
        Calibrated ``P(RTO | order)`` in [0, 1]. Use ``calibrate_probabilities``
        first if training resampled the minority class.
    c_fp : float
        Cost of a false positive — a good order held for manual review (INR).
        Industry: admin / review fee ~₹50.
    c_fn : float
        Cost of a false negative — an RTO'd order shipped anyway (INR).
        Industry: reverse logistics + refund + churn ~₹600 on average. This
        is the *constant* fallback; pass ``amount_inr`` to use the true
        per-transaction FN cost from Bahnsen Eq.(5) (Day 4 Track N — V3
        §11.6 intervention policy).
    c_otp : float
        Cost of running selective OTP / address verification on a REVIEW'd
        order (INR). ~₹5 per OTP send + verification labour.
    c_block : float
        Cost of blocking a legitimate order (INR) — goodwill / churn hit.
        ~₹1000 per false-decline (per Razorpay RTO Shield 2024 study).
    otp_effectiveness : float
        Probability that an OTP / address-check catches an RTO'd order.
        Published industry range 0.78–0.84 (we use the conservative 0.82).
    amount_inr : float | None
        Per-transaction order amount (INR). If provided, overrides ``c_fn``
        with the per-amount FN cost (Bahnsen Eq.(5): FN cost = Amt_i, NOT a
        constant). This is the headline of the Bahnsen 2013 paper — the FN
        cost is the actual amount at stake, so a ₹52,000 order has a 86×
        higher FN cost than a ₹600 order, and the cost-optimal decision can
        differ for the *same* probability. None → use ``c_fn`` (constant).

    Returns
    -------
    (decision, costs) : tuple[str, dict]
        decision ∈ {"ACCEPT", "REVIEW", "REJECT"} with the lowest expected
        cost; costs is the full breakdown for audit / dashboard explainability.

    Math
    ----
    fn_cost = amount_inr if amount_inr is not None else c_fn     # Bahnsen Eq.(5)
    cost_accept = p · fn_cost                                     # ship normally
    cost_review = c_otp + (1 − p)·c_fp + p·(1 − otp_eff)·fn_cost # selective OTP
    cost_reject = (1 − p) · c_block                               # block outright
    decision    = argmin over the three.

    This is the Bahnsen BMR rule (Eq.(5)) specialized to three ordered actions
    instead of a binary flag/no-flag. When ``amount_inr`` is supplied the FN
    cost becomes per-transaction (the paper's real-financial-cost matrix,
    Table III), which is the run-up to Track N's full 5-way intervention
    policy in :func:`optimal_intervention`.
    """
    # Per-amount FN cost (Bahnsen Eq.(5)): if the operator passes the order
    # amount, the FN cost IS the amount (the loss of shipping an RTO is the
    # shipment value itself — not a constant). Otherwise fall back to the
    # constant ``c_fn`` (Track C behaviour).
    fn_cost = float(amount_inr) if amount_inr is not None else float(c_fn)
    cost_accept = p * fn_cost
    cost_review = c_otp + (1 - p) * c_fp + p * (1 - otp_effectiveness) * fn_cost
    cost_reject = (1 - p) * c_block
    costs = {
        "ACCEPT": round(cost_accept, 2),
        "REVIEW": round(cost_review, 2),
        "REJECT": round(cost_reject, 2),
    }
    decision = min(costs, key=lambda k: costs[k])
    return decision, costs


# ---------------------------------------------------------------------------
# 2. V3 §11.6 5-way intervention policy argmin (Track N)
# ---------------------------------------------------------------------------

def optimal_intervention(
    p: float,
    amount_inr: float,
    weights: dict | None = None,
) -> tuple[str, dict]:
    """V3 §11.6 5-way intervention policy argmin.

    Extends Track C's 3-way ``optimal_decision()`` to the full V3 §11.6
    intervention set: ``{ship, otp_verify, partial_cod, address_check, hold}``.
    Per Bahnsen 2013 BMR Eq.(5), the per-transaction FN cost is the order
    amount ``amount_inr`` (NOT a constant). Per Drummond & Holte 2006, the
    cost-curve analysis surfaces the threshold where the cost-optimal
    intervention changes (consumed by ``/v1/policy/cost-curves``'s
    ``intervention_crossover`` field — Track N).

    Parameters
    ----------
    p : float
        Calibrated ``P(RTO | order)`` in [0, 1].
    amount_inr : float
        Order amount in INR. Used as the per-transaction FN cost per Bahnsen
        Eq.(5) — the loss of shipping an RTO is the shipment value.
    weights : dict | None
        Override of :data:`DEFAULT_INTERVENTION_WEIGHTS`. Pass a partial dict
        to override only specific knobs (e.g. a different OTP effectiveness for
        a different country).

    Returns
    -------
    (intervention, costs) : tuple[str, dict]
        intervention ∈ ``INTERVENTIONS`` with the lowest expected cost;
        costs is the full breakdown across all 5 interventions for audit /
        dashboard explainability.

    Math (per intervention)
    -------------------------
    ship          : p · amount                          # ship + return → lose amount
    otp_verify    : c_otp + (1 − eff_otp) · p · amount  # OTP catches 82%
    partial_cod   : c_partial_cod + (1 − eff_cod) · p · amount
    address_check : c_address_check + (1 − eff_addr) · p · amount
    hold          : c_hold + residual_ship_rate · p · amount
                                                            # 30% of held orders still ship
                                                            # + can return → expected FN
    intervention  = argmin over the five.

    The effectiveness rates are taken from the Pragma 2025 RTO-mitigation
    benchmark (OTP 0.78-0.84, partial COD 0.60-0.70, address check 0.42-0.48).
    The 30% residual ship rate for ``hold`` reflects the operational reality
    that a human reviewer will still ship ~30% of held orders after a quick
    manual sanity check.

    This is the headline of the Bahnsen 2013 BMR paper (per-amount FN cost +
    per-intervention cost) specialized to the 5 ordered interventions
    available in the COD e-commerce RTO setting — Track N's V3 §11.6 surface.
    """
    w = {**DEFAULT_INTERVENTION_WEIGHTS, **(weights or {})}
    amt = float(amount_inr)
    p = float(p)

    # Expected cost of each intervention. Each "soft" intervention (OTP /
    # partial COD / address check) costs a small fee upfront AND reduces (not
    # eliminates) the residual RTO probability; the residual RTO probability
    # is multiplied by the per-amount FN cost (Bahnsen Eq.(5)). The "ship"
    # baseline has zero intervention cost but the full amount at risk. The
    # "hold" intervention has a fixed ops cost + a residual ship rate (some
    # held orders still ship after manual review).
    costs = {
        "ship": p * amt,  # baseline: lose amount if it returns
        "otp_verify": w["c_otp"]
        + (1.0 - w["c_otp_effectiveness"]) * p * amt,
        "partial_cod": w["c_partial_cod"]
        + (1.0 - w["c_partial_cod_effectiveness"]) * p * amt,
        "address_check": w["c_address_check"]
        + (1.0 - w["c_address_check_effectiveness"]) * p * amt,
        "hold": w["c_hold"]
        + w["c_hold_residual_ship_rate"] * p * amt,
    }
    # Round for clean audit + dashboard rendering; argmin uses raw values
    # then we re-key the rounded dict for the response. (Tie-break: order
    # of insertion — ship > otp_verify > partial_cod > address_check > hold —
    # the lowest-friction intervention wins ties.)
    rounded = {k: round(v, 2) for k, v in costs.items()}
    intervention = min(costs, key=lambda k: costs[k])
    return intervention, rounded


# ---------------------------------------------------------------------------
# 3. Bahnsen Eq.(6) post-resampling probability recalibration
# ---------------------------------------------------------------------------

def calibrate_probabilities(
    probs: float | Sequence[float],
    p_orig: float,
    p_und: float,
) -> float | list[float]:
    """Recalibrate probabilities after minority-class under-sampling.

    Implements Bahnsen et al. (ICMLA 2013, DOI 10.1109/ICMLA.2013.68) Eq.(6):

        P*(f|x) = P(f|x) · P_orig / P_und
        P*(l|x) = 1 − P*(f|x)

    where ``P_orig`` is the minority (fraud / RTO) prior in the *original* (pre-
    resampling) dataset and ``P_und`` is the minority prior in the *resampled*
    training set. Under-sampling inflates the minority prior; the calibration
    ratio undoes that inflation so the BMR cost comparison is honest.

    Parameters
    ----------
    probs : float | Sequence[float]
        Model's predicted minority-class probabilities after under-sampling.
        Scalar or 1-D sequence (numpy arrays / lists / tuples accepted).
    p_orig : float
        Original minority-class prior ``P(fraud)`` in the raw data — e.g. 0.0467
        for Bahnsen's European card dataset, ~0.23 for our CODScore RTO data.
    p_und : float
        Minority-class prior in the resampled training set — e.g. 0.50 for S1
        balanced sampling, 0.10 for S10, etc. Use ``p_orig`` to make this a
        no-op when no resampling was applied.

    Returns
    -------
    float | list[float]
        Calibrated probabilities, clipped to [0, 1]. Scalar in → scalar out,
        sequence in → list out. When ``p_orig == p_und`` (no resampling), the
        probabilities are returned unchanged (no-op fast path).

    Edge cases
    ----------
    - ``p_und == 0`` → division-by-zero; returns all-zeros (the model produced
      no positive labels in training, so any positive prediction is unsound;
      better to refuse than to inflate to +inf).
    - ``p_orig == 0`` → all calibrated probabilities are 0 (the original data
      had no positives; the calibrated probability is undefined and we
      conservatively refuse to flag anything).
    - NaN inputs → NaNs are propagated (callers must drop them).
    - Inputs already in [0, 1] are preserved; inputs outside [0, 1] are clipped
      before applying the ratio (a numerical safety net).
    """
    # No-op fast path: ratio is 1.0 → unchanged probabilities. This is the
    # default in production until SMOTE / under-sampling is wired in (Day 2+
    # Track G). Skipping the math avoids floating-point drift on calibrated
    # probabilities when no resampling was used. We still clip out-of-range
    # inputs so downstream BMR cost math never sees a negative or >1 prob.
    if p_und == p_orig:
        if isinstance(probs, (list, tuple)):
            return [max(0.0, min(1.0, float(p))) if p is not None and not (isinstance(p, float) and math.isnan(p)) else float("nan") for p in probs]
        if probs is None or (isinstance(probs, float) and math.isnan(probs)):
            return float("nan")
        return max(0.0, min(1.0, float(probs)))

    # Division-by-zero guard: refuse to produce inflated predictions.
    if p_und == 0:
        if isinstance(probs, (list, tuple)):
            return [0.0 for _ in probs]
        return 0.0

    ratio = float(p_orig) / float(p_und)
    # If p_orig == 0, ratio is 0 → all calibrated probabilities are 0 (handled
    # implicitly by the multiplication).

    if isinstance(probs, (list, tuple)):
        out: list[float] = []
        for v in probs:
            if v is None or (isinstance(v, float) and math.isnan(v)):
                out.append(float("nan"))
                continue
            x = max(0.0, min(1.0, float(v)))
            out.append(max(0.0, min(1.0, x * ratio)))
        return out

    # Scalar path
    if probs is None or (isinstance(probs, float) and math.isnan(probs)):
        return float("nan")
    x = max(0.0, min(1.0, float(probs)))
    return max(0.0, min(1.0, x * ratio))


# ---------------------------------------------------------------------------
# 3. Drummond-Holte cost-curve analysis (2006)
# ---------------------------------------------------------------------------

def _default_thresholds() -> list[float]:
    return [round(0.05 * i, 2) for i in range(1, 20)]  # 0.05, 0.10, ..., 0.95


def cost_curve_sweep(
    y_true: Sequence[int],
    probs: Sequence[float],
    c_fp: float = 50.0,
    c_fn: float = 600.0,
    thresholds: Sequence[float] | None = None,
) -> list[dict]:
    """Threshold sweep producing a Drummond-Holte cost-curve table.

    For each threshold ``t``, the binary decision is "REJECT if p ≥ t else
    ACCEPT". The confusion matrix is computed against ground truth ``y_true``
    (1 = RTO returned, 0 = delivered). Cost uses the Bahnsen cost matrix:

        - TP (caught RTO, correctly rejected): ``c_fp`` (admin / review fee)
        - FP (good order, wrongly rejected):   ``c_fp`` (goodwill + churn)
        - FN (missed RTO, shipped anyway):    ``c_fn`` (reverse logistics + refund)
        - TN (correctly shipped):              0

    Parameters
    ----------
    y_true, probs : sequences of equal length
        Ground-truth labels (0/1) and model probabilities. Padded / NaN entries
        raise ValueError — clean them upstream.
    c_fp, c_fn : float
        Cost weights; defaults match ``optimal_decision`` so the cost-minimizing
        threshold reported here aligns with the per-order BMR policy.
    thresholds : sequence of float | None
        Sweep points; defaults to 0.05 → 0.95 step 0.05 (19 points).

    Returns
    -------
    list of dict, one per threshold:
        {threshold, tp, fp, fn, tn, cost, precision, recall}

    Notes
    -----
    Precision / recall use the standard definitions:
        precision = TP / (TP + FP)
        recall    = TP / (TP + FN)
    """
    n = len(y_true)
    if len(probs) != n:
        raise ValueError(
            f"y_true and probs length mismatch: {n} vs {len(probs)}"
        )
    if n == 0:
        raise ValueError("empty input — need at least one labeled sample")

    ts = list(thresholds) if thresholds is not None else _default_thresholds()

    # Vectorize with plain Python so the function has zero external deps —
    # numpy callers can wrap the output for plotting; pure-Python keeps the
    # BMR skill importable in minimal envs (per skill.yaml dep spec).
    y = [int(bool(v)) for v in y_true]
    p = [float(v) for v in probs]

    total_pos = sum(y)
    total_neg = n - total_pos

    out: list[dict] = []
    for t in ts:
        tp = fp = fn = tn = 0
        for yi, pi in zip(y, p):
            flagged = pi >= t
            if yi == 1 and flagged:
                tp += 1
            elif yi == 0 and flagged:
                fp += 1
            elif yi == 1 and not flagged:
                fn += 1
            else:  # yi == 0 and not flagged
                tn += 1
        cost = (tp + fp) * c_fp + fn * c_fn
        precision = (tp / (tp + fp)) if (tp + fp) > 0 else 0.0
        recall = (tp / total_pos) if total_pos > 0 else 0.0
        out.append({
            "threshold": float(t),
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "cost": round(cost, 2),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
        })
    return out


def bootstrap_cost_ci(
    y_true: Sequence[int],
    probs: Sequence[float],
    c_fp: float = 50.0,
    c_fn: float = 600.0,
    thresholds: Sequence[float] | None = None,
    n_resamples: int = 500,
    confidence: float = 0.90,
    seed: int | None = 42,
) -> dict[str, dict]:
    """Bootstrap confidence intervals on cost at each threshold.

    Implements Drummond & Holte's ``bootstrap_performance_ci`` capability:
    resample the confusion matrix conditioned on row marginals (deployment
    class frequency fixed). Two binomials are sampled per bootstrap iteration
    — one for the positive class, one for the negative — so the class priors
    are preserved. This is the statistically correct way to put CIs on cost
    (the standard "resample rows with replacement" trick conflates prior
    uncertainty with conditional-performance uncertainty; the row-marginals
    fix preserves the class prior — see Drummond-Holte §3.6).

    Parameters
    ----------
    y_true, probs : sequences
    c_fp, c_fn : float
    thresholds : sequence | None
    n_resamples : int
        ≥500 recommended by Drummond-Holte skill.yaml. Default 500.
    confidence : float
        Coverage of the CI — 0.90 for 90% CI (5th and 95th percentile).
    seed : int | None
        Random seed for reproducibility. None → non-deterministic.

    Returns
    -------
    dict mapping str(threshold) → {"low": float, "high": float, "mean": float}

    Performance
    -----------
    Pure Python; ~500 resamples × 19 thresholds × N samples. For our 7,235-row
    training set this is ~70M ops, completing in ~3-5 sec. Acceptable for a
    dashboard endpoint. Numpy refactor Day 2 Track F if needed.
    """
    import random as _random

    if n_resamples < 1:
        raise ValueError(f"n_resamples must be ≥ 1, got {n_resamples}")
    if not (0.0 < confidence < 1.0):
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")

    ts = list(thresholds) if thresholds is not None else _default_thresholds()
    n = len(y_true)
    if len(probs) != n:
        raise ValueError("y_true and probs length mismatch")
    if n == 0:
        raise ValueError("empty input — cannot bootstrap")

    rng = _random.Random(seed) if seed is not None else _random.Random()

    # Split indices by class — preserves row marginals per Drummond-Holte
    pos_idx = [i for i, y in enumerate(y_true) if int(bool(y)) == 1]
    neg_idx = [i for i, y in enumerate(y_true) if int(bool(y)) == 0]

    if not pos_idx or not neg_idx:
        # Cannot compute CIs without both classes; return degenerate band
        return {
            str(float(t)): {"low": 0.0, "high": 0.0, "mean": 0.0}
            for t in ts
        }

    # Precompute per-class probs + threshold indicators
    pos_p = [float(probs[i]) for i in pos_idx]
    neg_p = [float(probs[i]) for i in neg_idx]
    n_pos = len(pos_idx)
    n_neg = len(neg_idx)

    # cost per resample per threshold
    samples: dict[float, list[float]] = {float(t): [] for t in ts}

    for _ in range(n_resamples):
        # Resample within each class (preserves row marginals — Drummond-Holte §3.6)
        pos_sample = [pos_p[rng.randrange(n_pos)] for _ in range(n_pos)]
        neg_sample = [neg_p[rng.randrange(n_neg)] for _ in range(n_neg)]
        for t in ts:
            tp = sum(1 for p in pos_sample if p >= t)
            fp = sum(1 for p in neg_sample if p >= t)
            fn = n_pos - tp
            cost = (tp + fp) * c_fp + fn * c_fn
            samples[float(t)].append(cost)

    alpha = (1.0 - confidence) / 2.0
    lo_pct = alpha * 100.0
    hi_pct = (1.0 - alpha) * 100.0

    out: dict[str, dict] = {}
    for t in ts:
        key = str(float(t))
        dist = sorted(samples[float(t)])
        lo = dist[int(lo_pct / 100.0 * len(dist))]
        hi = dist[int(hi_pct / 100.0 * len(dist)) - 1] if len(dist) > 1 else dist[0]
        mean = sum(dist) / len(dist)
        out[key] = {
            "low": round(lo, 2),
            "high": round(hi, 2),
            "mean": round(mean, 2),
            "n_resamples": n_resamples,
            "confidence": confidence,
        }
    return out


def find_cost_crossover(
    incumbent: list[dict],
    challenger: list[dict],
) -> dict:
    """Find the threshold where challenger beats incumbent (Drummond-Holte).

    Implements the ``find_model_crossover`` capability: scan the cost sweep of
    two candidate models and return the threshold(s) where one becomes cheaper
    than the other, plus the per-region winner and the maximum vertical
    advantage (cost gap).

    Parameters
    ----------
    incumbent, challenger : list of dict
        Both must be outputs of ``cost_curve_sweep`` — list of dicts each
        containing ``threshold`` and ``cost`` keys, sorted by threshold.

    Returns
    -------
    dict with keys:
        ``crossover_threshold`` (float | None)  — first threshold where
            challenger's cost < incumbent's cost; None if challenger never wins.
        ``per_region_winner`` (list[dict])  — for each threshold:
            {threshold, incumbent_cost, challenger_cost, winner}.
        ``max_advantage`` (float)  — max (incumbent_cost − challenger_cost) at
            any threshold where challenger wins; 0.0 if challenger never wins.
    """
    if not incumbent or not challenger:
        raise ValueError("both incumbent and challenger sweeps are required")
    inc_by_t = {float(r["threshold"]): r for r in incumbent}
    cha_by_t = {float(r["threshold"]): r for r in challenger}
    ts = sorted(set(inc_by_t) & set(cha_by_t))
    if not ts:
        raise ValueError("incumbent and challenger have no thresholds in common")

    per_region: list[dict] = []
    crossover: float | None = None
    max_adv: float = 0.0
    for t in ts:
        ic = float(inc_by_t[t]["cost"])
        cc = float(cha_by_t[t]["cost"])
        if cc < ic:
            winner = "challenger"
            if crossover is None:
                crossover = t
            max_adv = max(max_adv, ic - cc)
        elif cc > ic:
            winner = "incumbent"
        else:
            winner = "tie"
        per_region.append({
            "threshold": t,
            "incumbent_cost": round(ic, 2),
            "challenger_cost": round(cc, 2),
            "winner": winner,
        })
    return {
        "crossover_threshold": crossover,
        "per_region_winner": per_region,
        "max_advantage": round(max_adv, 2),
    }


# ---------------------------------------------------------------------------
# 5. V3 §11.6 intervention-curve sweep + crossover (Track N)
# ---------------------------------------------------------------------------

def intervention_curve_sweep(
    amount_inr: float,
    thresholds: Sequence[float] | None = None,
    weights: dict | None = None,
) -> list[dict]:
    """Threshold sweep producing the V3 §11.6 5-way intervention-curve table.

    For each probability threshold ``t`` in ``thresholds`` (default 0.05 →
    0.95 step 0.05), call :func:`optimal_intervention` and record the chosen
    intervention + the full cost breakdown across the 5 interventions. This
    is the 5-way analog of :func:`cost_curve_sweep`'s 3-way binary sweep.

    Parameters
    ----------
    amount_inr : float
        Per-transaction FN cost (Bahnsen Eq.(5)) — the order amount in INR.
    thresholds : sequence of float | None
        Sweep points; defaults to 0.05 → 0.95 step 0.05 (19 points), matching
        :func:`cost_curve_sweep` so the dashboard renders the 3-way and 5-way
        curves on the same threshold axis.
    weights : dict | None
        Override of :data:`DEFAULT_INTERVENTION_WEIGHTS`.

    Returns
    -------
    list of dict, one per threshold:
        {threshold, intervention, costs}
    where ``costs`` is the full {ship, otp_verify, partial_cod,
    address_check, hold} → cost-INR breakdown.
    """
    ts = list(thresholds) if thresholds is not None else _default_thresholds()
    out: list[dict] = []
    for t in ts:
        intervention, costs = optimal_intervention(float(t), amount_inr, weights)
        out.append({
            "threshold": float(t),
            "intervention": intervention,
            "costs": costs,
        })
    return out


def find_intervention_crossover(sweep: list[dict]) -> dict:
    """Find the threshold(s) where the cost-optimal intervention changes.

    Implements the Drummond-Holte ``find_model_crossover`` capability for
    the V3 §11.6 5-way intervention sweep: walk the sweep and detect every
    threshold where the argmin intervention changes (e.g. ship → otp_verify
    at one threshold, otp_verify → partial_cod at a later one). Returns the
    per-region intervention labels and the boundary thresholds.

    Parameters
    ----------
    sweep : list of dict
        Output of :func:`intervention_curve_sweep`.

    Returns
    -------
    dict with keys:
        ``crossover_thresholds`` (list[float])  — every threshold where the
            optimal intervention changed vs the previous threshold. Empty if
            the same intervention is optimal across the whole sweep.
        ``per_region_intervention`` (list[dict])  — for each threshold:
            {threshold, intervention}.
        ``regions`` (list[dict])  — collapsed contiguous ranges:
            {low_threshold, high_threshold, intervention, n_points}.
    """
    if not sweep:
        raise ValueError("empty intervention sweep — cannot find crossover")
    per_region: list[dict] = []
    for r in sweep:
        per_region.append({
            "threshold": float(r["threshold"]),
            "intervention": r["intervention"],
        })
    crossovers: list[float] = []
    prev_intervention = per_region[0]["intervention"]
    for entry in per_region[1:]:
        if entry["intervention"] != prev_intervention:
            crossovers.append(entry["threshold"])
            prev_intervention = entry["intervention"]
    # Collapse into contiguous regions for dashboard rendering
    regions: list[dict] = []
    cur_intervention = per_region[0]["intervention"]
    cur_lo = per_region[0]["threshold"]
    cur_count = 1
    for i, entry in enumerate(per_region[1:], start=1):
        if entry["intervention"] == cur_intervention:
            cur_count += 1
        else:
            regions.append({
                "low_threshold": cur_lo,
                "high_threshold": per_region[i - 1]["threshold"],
                "intervention": cur_intervention,
                "n_points": cur_count,
            })
            cur_intervention = entry["intervention"]
            cur_lo = entry["threshold"]
            cur_count = 1
    # Final region
    regions.append({
        "low_threshold": cur_lo,
        "high_threshold": per_region[-1]["threshold"],
        "intervention": cur_intervention,
        "n_points": cur_count,
    })
    return {
        "crossover_thresholds": crossovers,
        "per_region_intervention": per_region,
        "regions": regions,
    }
