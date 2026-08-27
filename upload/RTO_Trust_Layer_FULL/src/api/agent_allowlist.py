"""Agent action allowlist (Mission 3 — server-side enforcement).

Per user's 5 Missions: "Agent can only call N APIs. Any other intent
returns 'Action not permitted.'" The allowlist below is the canonical,
production-importable definition. ``scripts/demo_agent.py`` is the demo
client; this module is the server-side authority that
``src/api/routes.py`` (and any future agent-gateway endpoint) imports to
gate agent calls. Per Mao 2026 (SoK: Security of Autonomous LLM Agents in
Agentic Commerce), D2 (transaction-authorization): "design mandates as
scoped, task-bound, attenuating credentials rather than standing broad
authority" — the allowlist is the operational expression of that
attenuation.

Day 1 Track D (V3 §13 — mandate action-class expansion) added 3 UPI
Circle / delegated-payment actions per NPCI OC-201B (8 Oct 2025). The
4 original COD-order actions are unchanged. 7 actions total.

Track P (Task 11-a) — this module is the production home of
``ALLOWED_ACTIONS``; the demo script imports it rather than defining its
own copy. The interface contract:

  * Module path: ``src.api.agent_allowlist``
  * Exports   : ``ALLOWED_ACTIONS`` (dict[str, dict])
                ``check_agent_action(action, mandate_scope=None) -> tuple[bool, str]``
"""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Agent allowlist — prompt-razor §5 lines 1003-1050 (BoundedAgent class).
# Day 1 Track D (V3 §13) added 3 UPI Circle / delegated-payment actions per
# NPCI OC-201B (8 Oct 2025). The 4 original COD-order actions are unchanged.
# Per user's 5 Missions: "Agent can only call N APIs. Any other intent
# returns 'Action not permitted.'" — enforced by ``check_agent_action`` +
# the consuming routes.py path. Per user's 6 demo moments #5: high-cost
# actions (``requires_approval=True``) do NOT execute — the agent creates a
# case in the review queue and responds "I cannot perform this action. I
# have requested human approval."
# ---------------------------------------------------------------------------
ALLOWED_ACTIONS: dict[str, dict[str, Any]] = {
    # --- Original 4 COD-order actions (prompt-razor §5) ---
    "score_order": {"cost": 0, "requires_approval": False},
    "request_otp": {"cost": 1, "requires_approval": False},
    "flag_review": {"cost": 2, "requires_approval": False},
    "block_order": {"cost": 10, "requires_approval": True},
    # --- UPI Circle / delegated payments (NPCI OC-201B, 8 Oct 2025) ---
    "upi_circle_delegated_pay": {
        "cost": 5,
        "requires_approval": True,  # explicit user action per OC-201B §3
        "hard_caps": {
            "max_per_txn": 5000,    # OC-201B: ₹5,000 per transaction
            "max_per_month": 15000,  # OC-201B: ₹15,000 per delegation/month
            "cooling_24h": 5000,    # OC-201B: 24h ₹5,000 cumulative cooling
            "max_devices": 5,       # OC-201B: max 5 IoT/software per user
        },
    },
    "validate_device_id": {
        # OC-201B §3.7 Issuer Bank duty — per-txn device validation. No
        # approval needed (read-only check against the mandate's allowlist).
        "cost": 1,
        "requires_approval": False,
    },
    "revoke_delegation_on_inactivity": {
        # OC-201B: auto-revoke after 6 months inactivity or on tampering.
        # Auto-triggered — no human approval needed (this is the safety net).
        "cost": 2,
        "requires_approval": False,
        "auto_trigger_days": 180,
    },
}


def check_agent_action(
    action: str,
    mandate_scope: str | None = None,
) -> tuple[bool, str]:
    """Server-side gate for an agent's intended action.

    Returns ``(allowed, reason)``. The consuming endpoint in ``routes.py``
    (Subagent 11-routes) calls this before forwarding the action to its
    handler. ``allowed=False`` always short-circuits with a human-readable
    ``reason``; ``allowed=True`` means the action is in the allowlist AND
    does NOT require human approval (cost-0/1/2 actions execute directly).

    Logic:
      * If ``action not in ALLOWED_ACTIONS`` → ``(False, f"action '{action}' not in allowlist")``
        (Mission 3 — "Action not permitted.")
      * If the action's ``requires_approval`` flag is True → ``(False, "requires human approval")``
        (user's 6 demo moments #5 — high-cost actions are flagged; the agent
        queues a case instead of executing). The action IS still in the
        allowlist (so a future approved-tokens path can execute it); this
        function just signals the caller must do the human-approval dance
        before the action runs.
      * Otherwise → ``(True, "permitted")``.

    ``mandate_scope`` is accepted for forward-compat — future policy hooks
    may want to allowlist different action sets per mandate scope (e.g.
    ``upi_circle`` vs ``cod_order``). For now the allowlist is uniform across
    scopes; the parameter is ignored but typed so call sites can pass it
    without breakage.
    """
    if action not in ALLOWED_ACTIONS:
        return False, f"action '{action}' not in allowlist"
    spec = ALLOWED_ACTIONS[action]
    if spec.get("requires_approval"):
        return False, "requires human approval"
    return True, "permitted"
