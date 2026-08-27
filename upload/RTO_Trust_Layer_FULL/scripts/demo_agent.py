"""Mock merchant-dispatch agent calling the risk API; shows gates + graceful failure."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.routes import create_app  # noqa: E402

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
        print("\nAll decisions logged to out/audit.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
