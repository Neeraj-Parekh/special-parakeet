import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.breaker import CircuitBreaker  # noqa: E402
from src.api.routes import create_app  # noqa: E402
from src.audit.logger import AuditLogger  # noqa: E402
from src.business.cost_optimizer import (  # noqa: E402
    DEFAULT_INTERVENTION_WEIGHTS,
    INTERVENTIONS,
    bootstrap_cost_ci,
    calibrate_probabilities,
    cost_curve_sweep,
    find_cost_crossover,
    find_intervention_crossover,
    intervention_curve_sweep,
    optimal_decision,
    optimal_intervention,
)
from src.rules.engine import RulesEngine  # noqa: E402

SCORER_H = {"Authorization": "Bearer score-demo-key"}
ADMIN_H = {"Authorization": "Bearer admin-demo-key"}
VALID = {
    "order_id": "SHIP-001",
    "amount_inr": 899,
    "category": "Fashion",
    "customer_id": "CUST-S1",
}


def test_cost_optimizer_matches_decision_theory():
    """Note: with c_block=1000, REVIEW beats REJECT until ~0.93; the widely-copied
    blog example claiming REJECT@0.8 contradicts its own arithmetic."""
    assert optimal_decision(0.10)[0] == "ACCEPT"
    assert optimal_decision(0.40)[0] == "REVIEW"
    assert optimal_decision(0.40)[0] == "REVIEW"
    assert optimal_decision(0.95)[0] == "REJECT"
    _, costs = optimal_decision(0.5)
    assert set(costs) == {"ACCEPT", "REVIEW", "REJECT"}


def test_calibrate_probabilities_bahnsen_eq6_noop_when_priors_equal():
    """Bahnsen Eq.(6) recalibration: P*(f|x) = P(f|x)·P_orig/P_und.
    When no resampling is applied (p_orig == p_und), the ratio is 1.0 and the
    function is a no-op fast path — probabilities returned unchanged."""
    p_orig = p_und = 0.23
    # Scalar input
    assert calibrate_probabilities(0.5, p_orig, p_und) == 0.5
    # Sequence input
    out = calibrate_probabilities([0.1, 0.5, 0.9], p_orig, p_und)
    assert out == [0.1, 0.5, 0.9]


def test_calibrate_probabilities_bahnsen_eq6_rescales_when_undersampled():
    """If S50 under-sampling inflates the positive prior from 0.05 to 0.50,
    a model predicting p=0.5 should recalibrate to 0.05 (the original prior)."""
    p_orig = 0.05
    p_und = 0.50
    out = calibrate_probabilities(0.5, p_orig, p_und)
    assert abs(out - 0.05) < 1e-9
    # Edge: p_und=0 → division-by-zero guard → returns 0 (not +inf)
    assert calibrate_probabilities(0.9, 0.05, 0.0) == 0.0
    # Edge: p_orig=0 → all calibrated probabilities are 0
    assert calibrate_probabilities([0.5, 0.9], 0.0, 0.5) == [0.0, 0.0]
    # Edge: clip inputs outside [0, 1] before applying the ratio
    assert calibrate_probabilities(1.5, 0.5, 0.5) == 1.0  # clipped to 1, ratio=1
    assert calibrate_probabilities(-0.2, 0.5, 0.5) == 0.0  # clipped to 0, ratio=1


def test_cost_curve_sweep_returns_per_threshold_data():
    """Drummond-Holte cost-curve sweep over a labeled mini-dataset."""
    y_true = [0, 0, 0, 1, 1, 1]
    # Include a sample with p=0.95 so the top threshold still flags something
    # — exercises the boundary case at the sweep's last point.
    probs = [0.10, 0.20, 0.30, 0.50, 0.70, 0.95]
    out = cost_curve_sweep(y_true, probs, c_fp=50.0, c_fn=600.0)
    assert len(out) == 19  # 0.05 → 0.95 step 0.05
    r = out[0]  # threshold = 0.05 — every sample flagged
    assert r["threshold"] == 0.05
    assert set(r.keys()) == {
        "threshold", "tp", "fp", "fn", "tn", "cost", "precision", "recall"
    }
    # At t=0.05: all 6 flagged (3 TP, 3 FP, 0 FN, 0 TN); cost = 6*c_fp = 300
    assert r["tp"] == 3 and r["fp"] == 3 and r["fn"] == 0 and r["tn"] == 0
    assert r["cost"] == 300.0
    assert r["recall"] == 1.0  # 3/3
    # At t=0.95: only the p=0.95 sample is flagged (>= comparison)
    r_high = out[-1]
    assert r_high["threshold"] == 0.95
    assert r_high["tp"] == 1 and r_high["fp"] == 0 and r_high["fn"] == 2
    # Cost = 1*50 + 2*600 = 1250
    assert r_high["cost"] == 1250.0
    assert r_high["recall"] == round(1 / 3, 4)  # 1/3 → 0.3333


def test_bootstrap_cost_ci_returns_band_per_threshold():
    """≥500 resamples preserving row marginals (Drummond-Holte skill.yaml)."""
    import random
    rng = random.Random(42)
    n = 200
    y_true = [1 if rng.random() < 0.3 else 0 for _ in range(n)]
    probs = [rng.random() if y else rng.random() * 0.5 for y, _ in zip(y_true, range(n))]
    ci = bootstrap_cost_ci(y_true, probs, n_resamples=100, confidence=0.90, seed=7)
    assert len(ci) == 19
    for key, band in ci.items():
        assert "low" in band and "high" in band and "mean" in band
        assert band["low"] <= band["mean"] <= band["high"]
        assert band["n_resamples"] == 100
        assert band["confidence"] == 0.90


def test_find_cost_crossover_returns_per_region_winner():
    inc = [{"threshold": 0.1, "cost": 100}, {"threshold": 0.5, "cost": 200}]
    cha = [{"threshold": 0.1, "cost": 150}, {"threshold": 0.5, "cost": 150}]
    out = find_cost_crossover(inc, cha)
    assert out["crossover_threshold"] == 0.5
    assert out["max_advantage"] == 50.0  # 200 - 150 at t=0.5
    assert out["per_region_winner"][0]["winner"] == "incumbent"
    assert out["per_region_winner"][1]["winner"] == "challenger"


def test_rules_engine_block_and_review():
    e = RulesEngine()
    blocked = {
        "order_id": "X",
        "amount_inr": 60_000,
        "payment_method": "COD",
        "prior_orders": 0,
        "address_quality": "complete",
    }
    assert e.evaluate(blocked).rule_id == "RULE-001"
    review = {
        "amount_inr": 25_000,
        "payment_method": "COD",
        "address_quality": "vague",
    }
    assert e.evaluate(review).action == "REVIEW"
    assert e.evaluate({"amount_inr": 100}) is None


def test_audit_hash_chain_tamper_evident(tmp_path):
    log = AuditLogger(str(tmp_path / "a.jsonl"))
    ids = [log.log({"n": i, "decision": "ACCEPT"}) for i in range(4)]
    ok, n, bad = log.verify_chain()
    assert ok and n == 4 and bad == ""
    lines = (tmp_path / "a.jsonl").read_text().splitlines()
    import json

    rec = json.loads(lines[1])
    rec["probability"] = 0.99
    lines[1] = json.dumps(rec)
    (tmp_path / "a.jsonl").write_text("\n".join(lines) + "\n")
    ok2, n2, bad2 = log.verify_chain()
    assert not ok2 and bad2 == ids[1]


def test_circuit_breaker_opens_and_falls_back():
    cb = CircuitBreaker(failure_threshold=2)
    for _ in range(2):
        cb.record_failure()
    assert cb.state == "OPEN"
    assert cb.allow_attempt() is False


@pytest.fixture(scope="module")
def client():
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        with TestClient(create_app(audit_path=f"{td}/audit.jsonl")) as c:
            yield c


def test_health_public(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["model_loaded"] is True and body["circuit_state"] == "CLOSED"


def test_rule_fast_path_blocks_without_model(client):
    r = client.post(
        "/risk/score",
        json={**VALID, "order_id": "SHIP-BIG", "amount_inr": 90_000},
        headers=SCORER_H,
    )
    body = r.json()
    assert body["decision"] == "REJECT" and body["rule_fired"] == "RULE-001"


def test_rules_crud_admin_only(client):
    assert client.get("/v1/rules", headers=SCORER_H).status_code == 200
    denied = client.post(
        "/v1/rules",
        json={"rule_id": "RULE-DNY", "name": "denied rule", "field": "items",
              "op": "gt", "value": 999, "action": "BLOCK"},
        headers=SCORER_H,
    )
    assert denied.status_code == 403
    r = client.post(
        "/v1/rules",
        json={"rule_id": "RULE-T90", "name": "test rule", "field": "items",
              "op": "gt", "value": 500, "action": "REVIEW"},
        headers=ADMIN_H,
    )
    assert r.status_code == 200
    rules = client.get("/v1/rules", headers=SCORER_H).json()["rules"]
    assert any(x["rule_id"] == "RULE-T90" for x in rules)
    assert client.delete("/v1/rules/RULE-T90", headers=ADMIN_H).json()["removed"] is True


def test_policy_optimal_endpoint(client):
    r = client.get("/v1/policy/optimal?probability=0.95", headers=SCORER_H)
    assert r.status_code == 200 and r.json()["optimal_action"] == "REJECT"
    r2 = client.get("/v1/policy/optimal?probability=0.05", headers=SCORER_H)
    assert r2.json()["optimal_action"] == "ACCEPT"


def test_chain_verification_endpoint(client):
    r = client.get("/v1/audit/verify-chain", headers=ADMIN_H)
    assert r.status_code == 200 and r.json()["intact"] is True


def test_degraded_mode_when_model_down(client):
    class Exploding:
        def predict_proba(self, _):
            raise RuntimeError("boom")

    original = client.app.state.core["model"]
    client.app.state.core["model"] = Exploding()
    try:
        for i in range(3):
            rr = client.post(
                "/risk/score",
                json={**VALID, "order_id": f"DEG-{i}"},
                headers=SCORER_H,
            )
            assert rr.json()["degraded"] is True
            assert rr.json()["decision"] == "REVIEW"
            # New: degraded path should report its decision_source so the
            # audit trail and dashboard can explain why REVIEW was chosen.
            assert rr.json()["decision_source"] == "degraded_review"
    finally:
        client.app.state.core["model"] = original
    assert client.app.state.core["breaker"].state in {"OPEN", "HALF_OPEN"}


# ---------------------------------------------------------------------------
# Day 1 Track C — cost-optimizer is now the decision path (not policy_hint)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=False)
def _reset_breaker(client):
    """Reset the circuit breaker to CLOSED before cost-optimizer tests.

    The module-scoped ``client`` fixture means earlier tests (notably
    ``test_degraded_mode_when_model_down``) leave the breaker in OPEN / HALF_OPEN
    state. Cost-optimizer tests need the model to actually run, so they must
    start from a clean CLOSED breaker.
    """
    client.app.state.core["breaker"].state = "CLOSED"
    client.app.state.core["breaker"].failures = 0
    yield


def test_decision_uses_cost_optimizer_not_static_thresholds(client, _reset_breaker):
    """Send an order through /risk/score and verify the returned decision
    matches ``optimal_decision(p)`` output (NOT the legacy static 0.15/0.60
    thresholds). Uses a benign order that fires no rules (amount=₹899 COD).

    This is the headline test from Day 1 Track C: before this change, the
    cost-optimizer was stored as ``policy_hint`` but the actual decision came
    from ``if proba < 0.15: ACCEPT``. After this change, ``decision`` *is*
    the cost-optimizer output.
    """
    r = client.post(
        "/risk/score",
        json={**VALID, "order_id": "COST-OPT-1", "amount_inr": 899},
        headers=SCORER_H,
    )
    assert r.status_code == 200
    body = r.json()
    # The benign VALID order fires no rules and no mandate header is sent,
    # so the cost-optimizer must be the decision source.
    assert body["decision_source"] == "cost_optimal_bmr"
    assert body["probability"] is not None
    expected_decision, expected_costs = optimal_decision(body["probability"])
    assert body["decision"] == expected_decision
    # The body's cost_breakdown is computed from the FULL-precision probability
    # (before rounding to 4 dp for the response), so we compare with tolerance.
    for action in ("ACCEPT", "REVIEW", "REJECT"):
        assert abs(body["cost_breakdown"][action] - expected_costs[action]) < 0.5, (
            f"{action}: body={body['cost_breakdown'][action]} "
            f"vs expected={expected_costs[action]}"
        )
    # policy_hint is kept for backward compat — same string as decision when
    # the cost-optimizer was the source.
    assert body["policy_hint"] == expected_decision
    # New gate_thresholds reports the policy in force, not just static numbers.
    assert body["gate_thresholds"]["policy"] == "cost_optimal_bmr"
    # If the legacy static 0.15/0.60 thresholds were still the decision path,
    # we could distinguish by checking a probability in the (0.15, 0.60) band:
    # under the OLD code, any p in (0.15, 0.60) → REVIEW; under the NEW code,
    # the cost-optimizer decides ACCEPT/REVIEW/REJECT by argmin of cost. For
    # p where the cost-optimizer says ACCEPT, the static code would say REVIEW
    # — that's the discriminator. We assert the contract holds for whatever
    # probability the model actually emitted on this order.


def test_decision_uses_cost_optimizer_with_review_rule_gate(client, _reset_breaker):
    """When a REVIEW rule fires (high-value + vague + COD) and the
    cost-optimizer says ACCEPT, the decision must be REVIEW (rule gate).
    This verifies the cost-optimizer is in the decision path AND the rules
    REVIEW gate still takes precedence over ACCEPT."""
    r = client.post(
        "/risk/score",
        json={
            **VALID,
            "order_id": "COST-OPT-2",
            "amount_inr": 25_000,        # > 20k threshold for RULE-002
            "address_quality": "vague",  # + COD → RULE-002 REVIEW fires
            "payment_method": "COD",
        },
        headers=SCORER_H,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["rule_fired"] == "RULE-002"
    # RULE-002 is a REVIEW rule, not BLOCK — so the cost-optimizer still runs.
    assert body["probability"] is not None
    # If the cost-optimizer alone would say ACCEPT, the rule gate forces REVIEW.
    expected_decision, _ = optimal_decision(body["probability"])
    if expected_decision == "ACCEPT":
        assert body["decision"] == "REVIEW"
        assert body["decision_source"] == "cost_optimal_bmr_review_rule"
    else:
        # If the cost-optimizer already said REVIEW/REJECT, that stands.
        assert body["decision"] == expected_decision
        assert body["decision_source"] == "cost_optimal_bmr"


def test_cost_curves_endpoint_returns_valid_structure(client, _reset_breaker):
    """GET /v1/policy/cost-curves returns 200 with thresholds, curves, and
    optimal_threshold. Uses a small n_resamples to keep the test fast."""
    r = client.get(
        "/v1/policy/cost-curves?n_resamples=20",
        headers=SCORER_H,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Required top-level fields per Day 1 Track C spec.
    assert "thresholds" in body
    assert "curves" in body
    assert "bootstrap_ci" in body
    assert "optimal_threshold" in body
    # Thresholds are a sweep from 0.05 to 0.95 (19 points, step 0.05).
    assert len(body["thresholds"]) == 19
    assert body["thresholds"][0] == 0.05
    assert body["thresholds"][-1] == 0.95
    # Each curve row has the documented fields.
    row = body["curves"][0]
    for field in ("threshold", "tp", "fp", "fn", "tn", "cost", "precision", "recall"):
        assert field in row, f"missing field {field} in curve row"
    # Optimal threshold is one of the sweep points.
    assert body["optimal_threshold"] in body["thresholds"]
    # Bootstrap CI has one entry per threshold (keyed by str(threshold)).
    assert len(body["bootstrap_ci"]) == len(body["thresholds"])
    # Cost-model provenance is reported for audit / pitch deck.
    assert "cost_model" in body
    assert "Bahnsen" in body["cost_model"]["source_paper"]
    assert "Drummond" in body["cost_model"]["curve_paper"]


def test_cost_curves_endpoint_503_when_model_unloaded(client):
    """If the model isn't loaded (e.g. circuit breaker OPEN or startup
    warmup failed), the endpoint returns 503 with a clear error."""
    original_model = client.app.state.core.get("model")
    original_cc = client.app.state.core.get("cost_curve")
    client.app.state.core["model"] = None
    client.app.state.core["cost_curve"] = None
    try:
        r = client.get("/v1/policy/cost-curves", headers=SCORER_H)
        assert r.status_code == 503
        assert "unavailable" in r.json()["detail"].lower()
    finally:
        client.app.state.core["model"] = original_model
        client.app.state.core["cost_curve"] = original_cc


# ---------------------------------------------------------------------------
# Day 4 Track N — V3 §11.6 5-way intervention policy (Bahnsen Eq.(5))
# ---------------------------------------------------------------------------
# The 5-way intervention layer extends Track C's 3-way optimal_decision() with
# the full intervention set {ship, otp_verify, partial_cod, address_check,
# hold}. Per Bahnsen 2013 BMR Eq.(5), the FN cost is the per-transaction
# amount (NOT a constant), and per-intervention effectiveness rates come from
# the Pragma 2025 RTO-mitigation benchmark (OTP 0.82, partial COD 0.65,
# address check 0.45). The argmin over the 5 interventions gives the
# cost-optimal next-step recommendation surfaced in /risk/score's
# ``intervention`` field.
#
# Key cost-model property with DEFAULT weights: otp_verify dominates the
# "soft" interventions for any p×amt ≥ 6 INR (because c_otp=5 is cheap AND
# eff_otp=0.82 is the highest effectiveness). For p×amt < 6 INR, ship wins
# (the OTP fee exceeds the residual RTO loss). Hold/partial_cod/address_check
# become optimal when their weights are re-tuned (e.g. higher c_otp in a
# market where SMS is expensive, or lower c_hold + residual ship rate when
# human review is cheap). The tests below verify both the default-weight
# behaviour AND the re-tuned crossover so the cost-model's truthful answer is
# documented — the argmin is a function of (p, amt, weights), not a constant.
# ---------------------------------------------------------------------------


def test_optimal_intervention_ship_at_low_p():
    """At very low p × low amount, the per-amount FN cost is smaller than the
    OTP verification fee, so ship (baseline, no intervention) wins.

    Math: ship = p·amt; otp_verify = c_otp + (1 − eff_otp)·p·amt.
    Ship wins iff p·amt < c_otp / eff_otp = 5 / 0.82 ≈ 6.10 INR.
    Use p=0.01, amt=600 → p·amt = 6 → ship wins (boundary case).
    """
    intervention, costs = optimal_intervention(0.01, 600)
    assert intervention == "ship", (
        f"expected ship at p=0.01 amt=600 (p*amt=6 < 6.10 threshold), "
        f"got {intervention} with costs {costs}"
    )
    # Full 5-way breakdown present
    assert set(costs.keys()) == set(INTERVENTIONS)
    # Ship's cost equals p*amt (Bahnsen Eq.(5): FN = amount)
    assert abs(costs["ship"] - 0.01 * 600) < 0.01


def test_optimal_intervention_otp_at_medium_p():
    """At medium p × medium amount, the OTP fee is small relative to the
    residual RTO loss, so otp_verify wins.

    Use p=0.4, amt=12400 → p·amt = 4960 INR.
    otp_verify cost = 5 + 0.18·4960 = 5 + 892.8 = 897.8 INR — far below ship
    (4960) and below partial_cod / address_check / hold. argmin = otp_verify.
    """
    intervention, costs = optimal_intervention(0.4, 12400)
    assert intervention == "otp_verify", (
        f"expected otp_verify at p=0.4 amt=12400, got {intervention}: {costs}"
    )
    # Verify the OTP cost math (Bahnsen Eq.(5) per-amount FN).
    expected_otp = 5.0 + (1 - 0.82) * 0.4 * 12400
    assert abs(costs["otp_verify"] - expected_otp) < 0.05
    # Verify ship cost = full amount at risk
    assert abs(costs["ship"] - 0.4 * 12400) < 0.05


def test_optimal_intervention_at_high_p():
    """At high p × high amount, the cost-model's truthful answer with DEFAULT
    weights is otp_verify (because OTP effectiveness 0.82 > hold's residual
    ship rate 0.30 — OTP catches more RTOs than manual review). Hold becomes
    optimal only when its weights are re-tuned (lower c_hold + lower residual
    ship rate) — the test below verifies BOTH cases:

    (a) Default weights → otp_verify wins at p=0.9 amt=52000.
    (b) Re-tuned weights (c_hold=1, residual_ship_rate=0.01) → hold wins.
    """
    # (a) Default weights: otp_verify dominates (this is the cost-model's
    # truthful answer — hold's residual 30% ship rate is worse than OTP's
    # 18% residual risk at the default fee levels).
    intervention, costs = optimal_intervention(0.9, 52000)
    assert intervention == "otp_verify", (
        f"with default weights at p=0.9 amt=52000, expected otp_verify "
        f"(eff_otp=0.82 > eff_hold=0.70), got {intervention}: {costs}"
    )
    # Sanity: ship's cost = 0.9*52000 = 46800 (full amount at risk)
    assert abs(costs["ship"] - 46800) < 1.0

    # (b) Re-tuned weights: if a market has very cheap manual review (c_hold=1)
    # AND a much lower residual ship rate (0.01 vs the default 0.30 — e.g. a
    # high-touch ops team that blocks 99% of held orders), hold wins.
    intervention, costs = optimal_intervention(
        0.9, 52000,
        weights={
            "c_hold": 1.0,
            "c_hold_residual_ship_rate": 0.01,
            # Keep other weights at defaults
            "c_otp": 5.0,
            "c_otp_effectiveness": 0.82,
            "c_partial_cod": 10.0,
            "c_partial_cod_effectiveness": 0.65,
            "c_address_check": 3.0,
            "c_address_check_effectiveness": 0.45,
        },
    )
    assert intervention == "hold", (
        f"with re-tuned weights (cheap hold + low residual ship rate), "
        f"expected hold at p=0.9 amt=52000, got {intervention}: {costs}"
    )


def test_per_amount_fn_cost_changes_optimal_intervention():
    """Bahnsen Eq.(5) headline: per-transaction FN cost = amount. At the SAME
    probability, a low-amount order → ship (OTP fee exceeds residual loss),
    while a high-amount order → otp_verify (residual loss dwarfs the OTP fee).

    Use p=0.4 (40% RTO probability) and vary the amount:
    - amt=15  → p*amt=6.0  → ship wins (boundary: p*amt < 6.10 INR)
    - amt=100 → p*amt=40   → otp_verify wins
    """
    # Low amount: ship wins (OTP fee > residual loss)
    i_low, c_low = optimal_intervention(0.4, 15)
    assert i_low == "ship", (
        f"at p=0.4 amt=15 (p*amt=6), expected ship, got {i_low}: {c_low}"
    )
    # High amount: otp_verify wins (residual loss dwarfs OTP fee)
    i_high, c_high = optimal_intervention(0.4, 100)
    assert i_high == "otp_verify", (
        f"at p=0.4 amt=100 (p*amt=40), expected otp_verify, got {i_high}: {c_high}"
    )


def test_optimal_decision_per_amount_fn_cost_flag():
    """Day 4 Track N — optimal_decision() now accepts an optional amount_inr
    that overrides the constant c_fn with the per-transaction amount
    (Bahnsen Eq.(5)). At the same probability, a low-amount order may ACCEPT
    while a high-amount order REJECTs — this is the headline of the Bahnsen
    2013 paper (FN cost ≠ constant).
    """
    p = 0.4
    # Low amount: c_fn = 600 (constant fallback) → REVIEW
    # Same p with amount=600 (Bahnsen Eq.(5)): cost_accept = 0.4 * 600 = 240
    # vs cost_review = 5 + 0.6*50 + 0.4*0.18*600 = 5+30+43.2 = 78.2 → REVIEW
    d_const, c_const = optimal_decision(p)  # constant c_fn=600
    d_amt_low, c_low = optimal_decision(p, amount_inr=600)
    # The constant path and the per-amount path should agree when amount == c_fn
    assert d_const == d_amt_low, (
        f"constant c_fn=600 vs amount_inr=600 should give same decision: "
        f"{d_const} vs {d_amt_low}"
    )
    # Same p with amount=52000 (Bahnsen Eq.(5)): cost_accept = 0.4*52000 = 20800
    # vs cost_reject = 0.6*1000 = 600 → REJECT wins (block is cheaper than ship)
    d_amt_high, c_high = optimal_decision(p, amount_inr=52000)
    assert d_amt_high == "REJECT", (
        f"at p=0.4 amt=52000 (per-amount FN cost), expected REJECT, "
        f"got {d_amt_high}: {c_high}"
    )
    # The per-amount FN cost produces a DIFFERENT decision than the constant
    # c_fn=600 default — the headline of the Bahnsen 2013 paper.
    assert d_amt_low != d_amt_high, (
        "per-amount FN cost should change the decision at the same probability "
        "(Bahnsen Eq.(5) headline: FN cost ≠ constant)"
    )


def test_intervention_curve_sweep_returns_per_threshold_data():
    """intervention_curve_sweep produces one row per threshold with the
    cost-optimal intervention + the full 5-way cost breakdown."""
    sweep = intervention_curve_sweep(12400.0)
    assert len(sweep) == 19  # 0.05 → 0.95 step 0.05 (matches cost_curve_sweep)
    r = sweep[0]
    assert set(r.keys()) == {"threshold", "intervention", "costs"}
    assert r["threshold"] == 0.05
    assert r["intervention"] in INTERVENTIONS
    # Each row's costs is the full 5-way breakdown
    assert set(r["costs"].keys()) == set(INTERVENTIONS)


def test_find_intervention_crossover_returns_regions():
    """find_intervention_crossover collapses the per-threshold sweep into
    contiguous regions where the same intervention is optimal, plus the
    crossover thresholds where the intervention changes.
    """
    # Use a low amount so ship wins at low p (then crosses to otp_verify)
    sweep = intervention_curve_sweep(100.0)
    co = find_intervention_crossover(sweep)
    assert "crossover_thresholds" in co
    assert "per_region_intervention" in co
    assert "regions" in co
    # At amount=100, ship wins at p=0.05 (p*amt=5 < 6.1 threshold), then
    # crosses to otp_verify at p=0.10 (p*amt=10 > 6.1 threshold).
    assert co["crossover_thresholds"] == [0.10], (
        f"expected crossover at 0.10 (ship → otp_verify) for amt=100, "
        f"got {co['crossover_thresholds']}"
    )
    # Regions: [0.05, 0.05] ship → [0.10, 0.95] otp_verify
    assert len(co["regions"]) == 2
    assert co["regions"][0]["intervention"] == "ship"
    assert co["regions"][0]["low_threshold"] == 0.05
    assert co["regions"][0]["high_threshold"] == 0.05
    assert co["regions"][1]["intervention"] == "otp_verify"
    assert co["regions"][1]["low_threshold"] == 0.10
    assert co["regions"][1]["high_threshold"] == 0.95


def test_intervention_costs_in_response(client, _reset_breaker):
    """POST /risk/score response includes the new Day 4 Track N fields:
    ``intervention`` (the cost-optimal 5-way recommendation) +
    ``intervention_costs`` (the full 5-way cost breakdown) +
    ``intervention_weights`` (the cost-model provenance).
    """
    r = client.post(
        "/risk/score",
        json={**VALID, "order_id": "INTVN-1", "amount_inr": 899},
        headers=SCORER_H,
    )
    assert r.status_code == 200
    body = r.json()
    # The new Track N fields are present
    assert "intervention" in body
    assert "intervention_costs" in body
    assert "intervention_weights" in body
    # intervention is one of the 5 valid interventions
    assert body["intervention"] in INTERVENTIONS, (
        f"intervention must be one of {INTERVENTIONS}, got {body['intervention']}"
    )
    # intervention_costs is the full 5-way breakdown
    assert set(body["intervention_costs"].keys()) == set(INTERVENTIONS)
    # intervention_weights exposes the cost model
    assert body["intervention_weights"] == DEFAULT_INTERVENTION_WEIGHTS
    # Cross-check: the intervention in the response matches the argmin of
    # intervention_costs (consistency between the field and the breakdown).
    expected = min(body["intervention_costs"], key=lambda k: body["intervention_costs"][k])
    assert body["intervention"] == expected, (
        f"response intervention={body['intervention']} but argmin of "
        f"intervention_costs={expected} (costs={body['intervention_costs']})"
    )


def test_intervention_response_reflects_per_amount_fn_cost(client, _reset_breaker):
    """For a high-amount order, the per-amount FN cost (Bahnsen Eq.(5)) makes
    otp_verify strongly optimal — the OTP fee (₹5) is dwarfed by the residual
    RTO loss avoided. The intervention field surfaces the cost-optimal
    next-step recommendation the operator should execute.

    Uses amount_inr=45000 — below the RULE-001 BLOCK threshold (50000) so the
    cost-optimizer runs and the intervention field is populated.
    """
    # High-amount order: otp_verify is the dominant cost-optimal intervention
    # (residual RTO loss dwarfs the OTP fee).
    r = client.post(
        "/risk/score",
        json={**VALID, "order_id": "INTVN-2", "amount_inr": 45000},
        headers=SCORER_H,
    )
    assert r.status_code == 200
    body = r.json()
    # The model ran (no rule BLOCK fired) — intervention is populated.
    assert body["intervention"] is not None, (
        f"intervention should be populated for non-BLOCK decision, got None. "
        f"decision_source={body.get('decision_source')}, rule_fired={body.get('rule_fired')}"
    )
    # At amount=45000, otp_verify dominates for any p*amt ≥ 6 INR.
    assert body["intervention"] == "otp_verify", (
        f"high-amount order (₹45000) should recommend otp_verify, got "
        f"{body['intervention']}: {body['intervention_costs']}"
    )
    # Verify the cost math: ship's cost = p * amount (Bahnsen Eq.(5))
    p = body["probability"]
    if p is not None:
        ship_cost = p * 45000
        assert abs(body["intervention_costs"]["ship"] - ship_cost) < 5.0, (
            f"ship cost should equal p*amount (Bahnsen Eq.(5)), got "
            f"{body['intervention_costs']['ship']} vs {ship_cost}"
        )


def test_cost_curves_endpoint_returns_intervention_curves(client, _reset_breaker):
    """GET /v1/policy/cost-curves now also returns the Day 4 Track N 5-way
    intervention sweep + crossover — the dashboard renders these alongside
    the 3-way threshold sweep.
    """
    r = client.get(
        "/v1/policy/cost-curves?n_resamples=20",
        headers=SCORER_H,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # New Track N fields present
    assert "intervention_curves" in body
    assert "intervention_crossover" in body
    assert "intervention_amount_inr" in body
    assert "intervention_weights" in body
    # intervention_curves has one row per threshold (19 points)
    assert len(body["intervention_curves"]) == 19
    row = body["intervention_curves"][0]
    assert set(row.keys()) == {"threshold", "intervention", "costs"}
    assert row["intervention"] in INTERVENTIONS
    assert set(row["costs"].keys()) == set(INTERVENTIONS)
    # intervention_crossover has the documented sub-fields
    co = body["intervention_crossover"]
    assert "crossover_thresholds" in co
    assert "per_region_intervention" in co
    assert "regions" in co
    # Cost model now reports the intervention paper provenance
    assert "intervention_paper" in body["cost_model"]
    assert "Bahnsen" in body["cost_model"]["intervention_paper"]


def test_cost_curves_endpoint_amount_inr_param(client, _reset_breaker):
    """The ``amount_inr`` query param overrides the default representative
    order value so the dashboard can render the 5-way intervention curves for
    any order-value bracket. At amount=100 (low), ship wins at low p."""
    r = client.get(
        "/v1/policy/cost-curves?n_resamples=20&amount_inr=100",
        headers=SCORER_H,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # The override is reflected in the response
    assert body["intervention_amount_inr"] == 100.0
    # At amount=100 + threshold=0.05 (p*amt=5 < 6.10), ship wins
    row_low = body["intervention_curves"][0]
    assert row_low["threshold"] == 0.05
    assert row_low["intervention"] == "ship", (
        f"at amt=100, p=0.05 → ship should win (p*amt=5 < 6.10), got "
        f"{row_low['intervention']}"
    )
    # The crossover (ship → otp_verify) is at p=0.10 (p*amt=10 > 6.10)
    co = body["intervention_crossover"]
    assert 0.10 in co["crossover_thresholds"], (
        f"expected crossover at 0.10 for amt=100, got {co['crossover_thresholds']}"
    )


def test_cost_curves_endpoint_422_on_bad_amount_inr(client):
    """amount_inr outside [1, 1_000_000] returns 422."""
    r = client.get(
        "/v1/policy/cost-curves?amount_inr=0",
        headers=SCORER_H,
    )
    assert r.status_code == 422
    r2 = client.get(
        "/v1/policy/cost-curves?amount_inr=2000000",
        headers=SCORER_H,
    )
    assert r2.status_code == 422
