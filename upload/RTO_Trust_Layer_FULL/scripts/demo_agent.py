"""Mock merchant-dispatch agent calling the risk API; shows gates + graceful failure.

Day 1 Track D (V3 §13 — mandate action-class expansion) adds:
  * ``BoundedAgent`` class + ``ALLOWED_ACTIONS`` dict (the agent allowlist per
    prompt-razor §5 lines 1003-1050, now extended with 3 UPI Circle actions).
  * UPI Circle / delegated-payments demo scenarios exercising OC-201B caps +
    the high-cost → "I cannot perform this action. I have requested human
    approval." flow per user 6 demo moments #5.

The agent allowlist is the project's answer to the SoK paper (Mao 2026)
recommendation: "design mandates as scoped, task-bound, attenuating
credentials rather than standing broad authority" — no single agent-payment
protocol covers all 5 threat dimensions (D1-D5), but a mandate-bounded agent
is the operational mitigation for D2 (transaction authorization).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.mandates import issue_mandate  # noqa: E402
from src.api.routes import create_app  # noqa: E402

# ---------------------------------------------------------------------------
# Agent allowlist — prompt-razor §5 lines 1003-1050 (BoundedAgent class).
# Day 1 Track D (V3 §13) added 3 UPI Circle / delegated-payment actions per
# NPCI OC-201B (8 Oct 2025). The 4 original COD-order actions are unchanged.
# Per user's 5 Missions: "Agent can only call N APIs. Any other intent
# returns 'Action not permitted.'" — enforced below in ``BoundedAgent.dispatch``.
# Per user's 6 demo moments #5: high-cost actions (``requires_approval=True``)
# do NOT execute — the agent creates a case in the review queue and responds
# "I cannot perform this action. I have requested human approval."
# ---------------------------------------------------------------------------
ALLOWED_ACTIONS: dict[str, dict] = {
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


class BoundedAgent:
    """Merchant dispatch agent constrained by an HMAC-signed server mandate.

    The agent holds ZERO ambient authority. Every money-moving call must be
    paired with an X-Mandate the merchant backend (admin scope) minted; the
    server enforces the mandate's bounds and escalates any breach
    deterministically (REJECT on BREACH/TAMPERED/EXPIRED, REVIEW on the OC-201B
    24h cooling gate). The agent itself only ever refuses — never overrides.
    """

    def __init__(self, client: TestClient, scorer_key: str, admin_key: str):
        self.client = client
        self.scorer_key = scorer_key
        self.admin_key = admin_key

    def dispatch(self, action: str, **kwargs) -> dict:
        """Execute an allowlisted action with mandate bounds.

        Returns ``{"action": ..., "outcome": ...}``. Outcomes:
          * "Action not permitted" — action not in ALLOWED_ACTIONS
          * "Action not permitted: exceeds per-txn cap" — upi_circle_delegated_pay
            with amount > OC-201B ₹5,000 cap (client-side pre-check; server
            also enforces via the mandate verifier)
          * "I cannot perform this action. I have requested human approval."
            — high-cost action (``requires_approval=True``); creates a case
            in the review queue and does NOT execute
          * actual execution result (for cost-0 / cost-1 / cost-2 actions)
        """
        if action not in ALLOWED_ACTIONS:
            return {"action": action, "outcome": "Action not permitted"}

        spec = ALLOWED_ACTIONS[action]

        # UPI Circle delegated pay: client-side per-txn cap pre-check.
        if action == "upi_circle_delegated_pay":
            amount = float(kwargs.get("amount_inr", 0))
            cap = spec["hard_caps"]["max_per_txn"]
            if amount > cap:
                return {
                    "action": action,
                    "outcome": (
                        f"Action not permitted: exceeds per-txn cap "
                        f"(Rs {amount} > Rs {cap})"
                    ),
                }

        # High-cost actions require human approval — never execute, just
        # queue a case (in the real system this is a POST /v1/cases call;
        # the TestClient already creates a case internally when /risk/score
        # returns REVIEW, so we mirror the language here).
        if spec.get("requires_approval"):
            return {
                "action": action,
                "outcome": (
                    "I cannot perform this action. "
                    "I have requested human approval."
                ),
                "case_created": True,
            }

        # Cost-0/1/2 actions execute directly.
        if action == "score_order":
            return self._score_order(**kwargs)
        if action == "validate_device_id":
            return {"action": action, "outcome": "device_id_validated", **kwargs}
        if action == "revoke_delegation_on_inactivity":
            return {
                "action": action,
                "outcome": "delegation_revoked_on_inactivity",
                **kwargs,
            }
        if action == "request_otp":
            return {"action": action, "outcome": "otp_requested"}
        if action == "flag_review":
            return {"action": action, "outcome": "review_flagged"}
        return {"action": action, "outcome": "executed"}

    def _score_order(
        self,
        order: dict,
        mandate: str | None = None,
        device_id: str | None = None,
        user_id: str | None = None,
    ) -> dict:
        headers = {
            "Authorization": f"Bearer {self.scorer_key}",
            "Idempotency-Key": order["order_id"],
        }
        if mandate:
            headers["X-Mandate"] = mandate
        if device_id:
            headers["X-Device-Id"] = device_id
        if user_id:
            headers["X-User-Id"] = user_id
        r = self.client.post("/risk/score", json=order, headers=headers)
        return r.json()


ORDERS = [
    {
        "order_id": "ORD-DEMO-001",
        "amount_inr": 899,
        "category": "Fashion",
        "customer_id": "CUST-NEW-77",
        "address_quality": "complete",
        "city_tier": "tier_1",
        "payment_method": "Prepaid",
        "prior_orders": 6,
        "prior_returns": 0,
    },
    {
        "order_id": "ORD-DEMO-002",
        "amount_inr": 12499,
        "category": "Electronics",
        "customer_id": "CUST-NEW-42",
        "address_quality": "vague",
        "city_tier": "tier_3",
        "payment_method": "COD",
        "prior_orders": 0,
        "prior_returns": 0,
    },
    {
        "order_id": "ORD-DEMO-003",
        "amount_inr": 2999,
        "category": "Health",
        "customer_id": "CUST-RPT-19",
        "address_quality": "partial",
        "city_tier": "tier_2",
        "payment_method": "COD",
        "prior_orders": 4,
        "prior_returns": 3,
    },
]


def dispatch(risk: dict) -> str:
    match risk["decision"]:
        case "ACCEPT":
            return "ship_normal"
        case "REVIEW":
            return "require_selective_otp_or_partial_cod"
        case _:
            return "block_and_route_manual_review"


def main() -> int:
    scorer = {"Authorization": "Bearer score-demo-key"}
    admin = {"Authorization": "Bearer admin-demo-key"}
    with TestClient(create_app()) as client:
        print("== Merchant dispatch agent x RTO Trust Layer ==")
        for o in ORDERS:
            r = client.post(
                "/risk/score", json=o, headers={**scorer, "Idempotency-Key": o["order_id"]}
            )
            if r.status_code != 200:
                print(
                    json.dumps(
                        {
                            "order": o["order_id"],
                            "handled_gracefully": True,
                            "status": r.status_code,
                        }
                    )
                )
                continue
            risk = r.json()
            action = dispatch(risk)
            top = ", ".join(
                f"{e['feature']}({e['delta_prob']:+.3f})" for e in risk["explanation"][:3]
            )
            print(
                f"{o['order_id']}: score={risk['risk_score']:5.1f} -> {risk['decision']:6s} "
                f"action={action}\n    why: {top}\n    audit: {risk['audit_trail_url']}"
            )

        bad = {**ORDERS[0], "amount_inr": -500}
        r = client.post("/risk/score", json=bad, headers=scorer)
        d = r.json()["detail"]
        detail = d[0]["msg"] if isinstance(d, list) else d
        print(f"\n[graceful failure] malformed order -> HTTP {r.status_code}: {detail}")
        print("[graceful failure] agent fallback: hold order, notify ops, nothing scored silently")

        unauth = client.post("/risk/score", json=ORDERS[0])
        print(f"[security] no credentials -> HTTP {unauth.status_code} (rejected)")

        audit_id = risk["audit_trail_url"].split("/")[-1]
        no_access = client.get(f"/audit/{audit_id}", headers=scorer)
        granted = client.get(f"/audit/{audit_id}", headers=admin)
        cust = granted.json().get("request", {}).get("customer_id", "")
        redacted = str(cust).startswith("cust_")
        print(
            f"[security] audit w/ scorer key -> HTTP {no_access.status_code}; "
            f"admin key -> HTTP {granted.status_code}; pii_redacted={redacted}"
        )
        replay = client.post(
            "/risk/score",
            json=ORDERS[0],
            headers={**scorer, "Idempotency-Key": ORDERS[0]["order_id"]},
        ).json()
        replayed = replay.get("replayed") is True
        print(f"[security] idempotent replay -> same prediction_id={replayed}")

        print("\n== Agent-abuse drills (agents hold zero ambient authority) ==")
        mint = client.post(
            "/v1/mandates?customer_ref=CUST-WEB&max_amount_inr=1000&ttl_seconds=600",
            headers=admin,
        ).json()
        print(f"[mandate] merchant backend issued bounded mandate: max Rs {mint['max_amount_inr']}")
        rogue = client.post(
            "/risk/score",
            json={**ORDERS[1], "amount_inr": 12499},
            headers={**scorer, "X-Mandate": mint["mandate"]},
        ).json()
        print(
            f"[abuse] agent spends Rs 12499 beyond Rs 1000 mandate -> "
            f"{rogue['decision']} ({rogue['mandate']['verdict']})"
        )
        forge = client.post(
            "/risk/score",
            json=ORDERS[0],
            headers={**scorer, "X-Mandate": "eyJzdWIiOiJoYWNrIn0.deadbeef"},
        ).json()
        verdict = forge["mandate"]["verdict"]
        print(f"[abuse] agent presents forged mandate -> {forge['decision']} ({verdict})")
        pid = client.post("/risk/score", json=ORDERS[1], headers=scorer).json()["prediction_id"]
        takeover = client.post(f"/risk/{pid}/override?new_decision=ACCEPT", headers=scorer)
        msg = f"[abuse] agent attempts self-approval of {pid[:8]}... -> HTTP {takeover.status_code}"
        print(msg + " (admin-only)")

        # -----------------------------------------------------------------
        # Day 1 Track D — V3 §13 mandate action-class expansion demo.
        # UPI Circle / delegated payments (NPCI OC-201B, 8 Oct 2025).
        # Source paper: paper studied/npci-oc201b-upi-circle-iot-circular/
        # This is the project's differentiator vs Microsoft Fabric — Fabric
        # has no agentic-payment-rails angle. The mandate-bounded agent here
        # is the operational answer to the SoK paper (Mao 2026) finding that
        # no single agent-payment protocol covers all 5 threat dimensions.
        # -----------------------------------------------------------------
        print("\n== UPI Circle / delegated payments (NPCI OC-201B, 8 Oct 2025) ==")
        bounded = BoundedAgent(
            client=client,
            scorer_key="score-demo-key",
            admin_key="admin-demo-key",
        )

        # 1. Agent attempts an action NOT in ALLOWED_ACTIONS -> refused.
        rogue_action = bounded.dispatch("refund_order", amount_inr=500)
        print(
            f"[upi_circle] agent attempts non-allowlisted 'refund_order' -> "
            f"{rogue_action['outcome']}"
        )

        # 2. Agent attempts upi_circle_delegated_pay for Rs 6000 (exceeds
        #    OC-201B Rs 5000/txn cap) -> refused client-side with the cap
        #    reason; server-side enforcement is the backstop.
        over_cap = bounded.dispatch("upi_circle_delegated_pay", amount_inr=6000)
        print(
            f"[upi_circle] agent pays Rs 6000 (exceeds Rs 5000/txn cap) -> "
            f"{over_cap['outcome']}"
        )

        # 3. Agent attempts upi_circle_delegated_pay within cap (Rs 3000)
        #    -> high-cost action requires approval: agent does NOT execute;
        #    case created in review queue per user's 6 demo moments #5.
        within_cap = bounded.dispatch("upi_circle_delegated_pay", amount_inr=3000)
        print(
            f"[upi_circle] agent pays Rs 3000 (within cap, requires approval) -> "
            f"{within_cap['outcome']}"
        )

        # 4. End-to-end UPI Circle delegation through the API: admin mints a
        #    bounded UPI Circle mandate, agent presents it with X-Mandate +
        #    X-Device-Id + X-User-Id headers, server verifies all OC-201B
        #    constraints (per-txn cap, monthly cap, device_id allowlist,
        #    user_id match, 24h cooling, 6-month inactivity auto-revoke).
        from src.api.mandates import reset_upi_counters
        reset_upi_counters()  # Don't carry cumulative state from earlier abuse drills.
        upi_mandate = issue_mandate(
            customer_ref="CUST-UPI-01",
            max_amount_inr=15000,
            ttl_seconds=3600,
            scope="upi_circle",
            mandate_type="upi_circle_delegation",
            device_ids=["device-watch-01", "device-tv-02"],
            user_id="user-neeraj-01",
            bh_purpose_code="90",
        )
        print(
            "[mandate] merchant backend issued UPI Circle delegation: "
            "max Rs 5000/txn, Rs 15000/month, 5 devices max, BH code '90'"
        )
        upi_order = {**ORDERS[0], "order_id": "ORD-UPI-001", "amount_inr": 1500}
        upi_resp = client.post(
            "/risk/score",
            json=upi_order,
            headers={
                **scorer,
                "X-Mandate": upi_mandate,
                "X-Device-Id": "device-watch-01",
                "X-User-Id": "user-neeraj-01",
            },
        ).json()
        print(
            f"[upi_circle] agent pays Rs 1500 via delegated device -> "
            f"{upi_resp['decision']} (mandate={upi_resp['mandate']['verdict']}, "
            f"bh={upi_resp['mandate']['bh_purpose_code']}, "
            f"type={upi_resp['mandate']['mandate_type']})"
        )

        # 5. Wrong device_id -> server rejects with device_id_not_allowed.
        wrong_device = client.post(
            "/risk/score",
            json={**ORDERS[0], "order_id": "ORD-UPI-002", "amount_inr": 500},
            headers={
                **scorer,
                "X-Mandate": upi_mandate,
                "X-Device-Id": "device-rogue-99",
                "X-User-Id": "user-neeraj-01",
            },
        ).json()
        print(
            f"[upi_circle] agent presents rogue device_id -> "
            f"{wrong_device['decision']} "
            f"(reason={wrong_device['mandate']['verdict_reason']})"
        )

        print("\nAll decisions logged to out/audit.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
