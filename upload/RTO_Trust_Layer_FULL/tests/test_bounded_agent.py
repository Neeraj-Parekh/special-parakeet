"""T3.3 — BoundedAgent test coverage (Track U Day 7).

Closes the test-coverage gap noted by Subagent 11-a: 22 mandate tests + 0
BoundedAgent tests. Previously only the manual ``python scripts/demo_agent.py``
exercised the 7-action allowlist (``ALLOWED_ACTIONS``) + the
``BoundedAgent.dispatch`` client-side pre-checks. These 10 tests are
the programmatic coverage:

* 8 ``test_dispatch_*`` — exercise every allowlisted action through the
  ``BoundedAgent`` client (refund rejection, UPI Circle cap breach, UPI
  Circle within-cap requires-approval, score_order execution,
  validate_device_id, revoke_delegation_on_inactivity, request_otp,
  flag_review).
* 2 ``test_check_agent_action_function_*`` — exercise the server-side
  ``check_agent_action`` enforcement function (permit + reject).

The 7-action allowlist + ``check_agent_action`` are imported from
``src.api.agent_allowlist`` (the production module imported by both
``scripts.demo_agent`` AND ``src.api.routes`` per Subagent 11-a's
single-source-of-truth refactor). The ``BoundedAgent`` class is imported
from ``scripts.demo_agent``.

Auth keys + VALID order match the patterns in test_mandates.py +
test_feedback.py (the demo keys seeded by ``default_keys()`` in
``src/api/security.py``).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.demo_agent import BoundedAgent  # noqa: E402
from src.api.agent_allowlist import (  # noqa: E402
    ALLOWED_ACTIONS,
    check_agent_action,
)
from src.api.routes import create_app  # noqa: E402

SCORER_KEY = "score-demo-key"
ADMIN_KEY = "admin-demo-key"

VALID_ORDER = {
    "order_id": "BND-AGENT-1",
    "amount_inr": 899,
    "category": "Fashion",
    "customer_id": "CUST-BA-1",
}


@pytest.fixture(scope="module")
def agent() -> BoundedAgent:
    """Module-scoped fixture: a BoundedAgent over a TestClient + the
    in-process model. Cheaper than per-test instantiation (the model
    train-on-startup only happens once)."""
    with TestClient(create_app(scorer_rate_per_min=1000)) as client:
        yield BoundedAgent(
            client=client,
            scorer_key=SCORER_KEY,
            admin_key=ADMIN_KEY,
        )


# ---------------------------------------------------------------------------
# Action-not-in-allowlist rejection
# ---------------------------------------------------------------------------


def test_dispatch_rejects_action_not_in_allowlist(agent: BoundedAgent):
    """Mission 3: an action NOT in ALLOWED_ACTIONS is refused by the
    client-side ``BoundedAgent.dispatch`` before any API call. The
    outcome carries the verbatim "Action not permitted" language.
    """
    result = agent.dispatch("refund_order", amount_inr=500)
    assert result["action"] == "refund_order"
    assert result["outcome"] == "Action not permitted", (
        f"non-allowlisted action must return 'Action not permitted'; "
        f"got {result['outcome']}"
    )
    assert "refund_order" not in ALLOWED_ACTIONS


# ---------------------------------------------------------------------------
# UPI Circle delegated pay (NPCI OC-201B)
# ---------------------------------------------------------------------------


def test_dispatch_upi_circle_exceeds_per_txn_cap(agent: BoundedAgent):
    """UPI Circle delegated pay exceeding the ₹5,000 OC-201B per-txn cap
    is refused client-side with a cap-specific message. The server-side
    mandate verifier is the backstop; this is the client-side guard.
    """
    result = agent.dispatch("upi_circle_delegated_pay", amount_inr=6000)
    assert result["action"] == "upi_circle_delegated_pay"
    assert "exceeds per-txn cap" in result["outcome"], (
        f"amount above cap must surface the cap-breach message; got "
        f"{result['outcome']}"
    )
    assert "6000" in result["outcome"]
    assert "5000" in result["outcome"]  # cap value surfaced


def test_dispatch_upi_circle_within_cap(agent: BoundedAgent):
    """UPI Circle delegated pay WITHIN the ₹5,000 cap is still subject to
    the requires_approval=True gate (OC-201B §3 — explicit user action).
    The agent does NOT execute; it queues a case per user 6 demo moments
    #5: "I cannot perform this action. I have requested human approval."
    """
    result = agent.dispatch("upi_circle_delegated_pay", amount_inr=3000)
    assert result["action"] == "upi_circle_delegated_pay"
    assert "I cannot perform this action" in result["outcome"], (
        f"requires_approval=True action must surface the human-approval "
        f"language; got {result['outcome']}"
    )
    assert "requested human approval" in result["outcome"]
    assert result.get("case_created") is True, (
        "within-cap UPI Circle action should set case_created=True so the "
        "dashboard/agent orchestrator knows a case is queued upstream"
    )


# ---------------------------------------------------------------------------
# Cost-0/1/2 actions execute directly
# ---------------------------------------------------------------------------


def test_dispatch_score_order_executes(agent: BoundedAgent):
    """score_order (cost-0) executes by hitting POST /risk/score with the
    scorer key + Idempotency-Key (the order_id). Returns the full API
    response body (decision, probability, audit_trail_url, etc.).
    """
    result = agent.dispatch("score_order", order=VALID_ORDER)
    assert isinstance(result, dict)
    # The result is the raw /risk/score response body (no "outcome" wrapper
    # — the score_order path returns r.json() directly).
    assert "decision" in result
    assert result["decision"] in {"ACCEPT", "REVIEW", "REJECT"}, (
        f"decision must be one of ACCEPT/REVIEW/REJECT; got {result['decision']}"
    )
    assert "probability" in result
    assert result["probability"] is not None
    assert "audit_trail_url" in result
    assert "prediction_id" in result
    # Idempotency-Key was the order_id → the API should set replayed=False
    # on the first call (no replay).
    assert result.get("replayed") in (False, None)


def test_dispatch_validate_device_id(agent: BoundedAgent):
    """validate_device_id (OC-201B §3.7 Issuer Bank duty) is a read-only
    check against the mandate's allowlist. cost-1, requires_approval=False
    → executes directly with the verbatim "device_id_validated" outcome.
    """
    result = agent.dispatch("validate_device_id", device_id="dev1")
    assert result["action"] == "validate_device_id"
    assert result["outcome"] == "device_id_validated"
    assert result["device_id"] == "dev1"  # kwargs propagated


def test_dispatch_revoke_on_inactivity(agent: BoundedAgent):
    """revoke_delegation_on_inactivity (OC-201B 6-month auto-revoke) is
    the safety-net action. cost-2, requires_approval=False (auto-triggered,
    no human approval needed). Returns the verbatim "delegation_revoked_
    on_inactivity" outcome.
    """
    result = agent.dispatch("revoke_delegation_on_inactivity")
    assert result["action"] == "revoke_delegation_on_inactivity"
    assert result["outcome"] == "delegation_revoked_on_inactivity"


def test_dispatch_request_otp(agent: BoundedAgent):
    """request_otp is a cost-1, requires_approval=False action. Returns
    the verbatim "otp_requested" outcome.
    """
    result = agent.dispatch("request_otp")
    assert result["action"] == "request_otp"
    assert result["outcome"] == "otp_requested"


def test_dispatch_flag_review(agent: BoundedAgent):
    """flag_review is a cost-2, requires_approval=False action. Returns
    the verbatim "review_flagged" outcome.
    """
    result = agent.dispatch("flag_review")
    assert result["action"] == "flag_review"
    assert result["outcome"] == "review_flagged"


# ---------------------------------------------------------------------------
# check_agent_action (server-side enforcement function)
# ---------------------------------------------------------------------------


def test_check_agent_action_function_permit():
    """``check_agent_action("score_order")`` → ``(True, "permitted")``.

    score_order is in ALLOWED_ACTIONS + requires_approval=False → the
    server-side gate permits the action. The Subagent 11-routes
    ``enforce_agent_action`` middleware in ``src/api/routes.py`` calls
    this function before forwarding the action to its handler.
    """
    allowed, reason = check_agent_action("score_order")
    assert allowed is True
    assert reason == "permitted", (
        f"permitted action's reason should be 'permitted'; got {reason}"
    )


def test_check_agent_action_function_rejects_unknown():
    """``check_agent_action("unknown_action")`` → ``(False, "action
    'unknown_action' not in allowlist")``.

    The reason string includes the action name so the agent orchestrator
    can log + route the rejection (Mission 3 — "Action not permitted.").
    """
    allowed, reason = check_agent_action("unknown_action")
    assert allowed is False
    assert "action 'unknown_action' not in allowlist" == reason, (
        f"rejection reason should verbatim include the action name + "
        f"'not in allowlist'; got {reason}"
    )
    # Also verify a 2nd unknown action to ensure the function doesn't
    # special-case the first string.
    allowed2, reason2 = check_agent_action("refund_order")
    assert allowed2 is False
    assert reason2 == "action 'refund_order' not in allowlist"
