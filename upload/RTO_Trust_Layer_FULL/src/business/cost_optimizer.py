"""Three-way cost-optimal decision policy.

Chooses ACCEPT vs REVIEW(selective OTP) vs REJECT by minimum expected cost.
OTP effectiveness from published industry data: selective OTP cuts COD fraud
78-84% at 4-7% conversion cost; we use the conservative 0.82.
"""
from __future__ import annotations


def optimal_decision(
    p: float,
    c_fp: float = 50.0,
    c_fn: float = 600.0,
    c_otp: float = 5.0,
    c_block: float = 1000.0,
    otp_effectiveness: float = 0.82,
) -> tuple[str, dict]:
    """Returns (decision, expected_costs). p = P(RTO | order)."""
    cost_accept = p * c_fn
    cost_review = c_otp + (1 - p) * c_fp + p * (1 - otp_effectiveness) * c_fn
    cost_reject = (1 - p) * c_block
    costs = {
        "ACCEPT": round(cost_accept, 2),
        "REVIEW": round(cost_review, 2),
        "REJECT": round(cost_reject, 2),
    }
    decision = min(costs, key=lambda k: costs[k])
    return decision, costs
