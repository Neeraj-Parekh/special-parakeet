import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.mandates import (  # noqa: E402
    MandateVerdict,
    decode_mandate,
    issue_mandate,
    reset_upi_counters,
    simulate_inactivity,
    verify_mandate,
)
from src.api.routes import create_app  # noqa: E402

SCORER_H = {"Authorization": "Bearer score-demo-key"}
ADMIN_H = {"Authorization": "Bearer admin-demo-key"}
VALID = {
    "order_id": "MND-001",
    "amount_inr": 899,
    "category": "Fashion",
    "customer_id": "CUST-M1",
}


def test_valid_mandate_passes():
    m = issue_mandate("CUST-M1", max_amount_inr=5000, ttl_seconds=600)
    assert verify_mandate(m, 899)[0] == MandateVerdict.VALID


def test_tampered_mandate_rejected():
    m = issue_mandate("CUST-M1", max_amount_inr=500, ttl_seconds=600)
    forged = m[:-4] + "beef"
    assert verify_mandate(forged, 899)[0] == MandateVerdict.TAMPERED


def test_over_limit_is_breach():
    m = issue_mandate("CUST-M1", max_amount_inr=500, ttl_seconds=600)
    assert verify_mandate(m, 899)[0] == MandateVerdict.BREACH


def test_expired_mandate_rejected():
    import os

    old = os.environ.get("RTO_MANDATE_SECRET")
    os.environ["RTO_MANDATE_SECRET"] = "x"
    try:
        m = issue_mandate("CUST-M1", 5000, ttl_seconds=-10)
        assert verify_mandate(m, 100)[0] == MandateVerdict.EXPIRED
    finally:
        if old is None:
            os.environ.pop("RTO_MANDATE_SECRET", None)
        else:
            os.environ["RTO_MANDATE_SECRET"] = old


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as c:
        yield c


def test_breach_escalates_to_reject(client):
    mandate = issue_mandate("CUST-M1", max_amount_inr=100, ttl_seconds=600)
    payload = {**VALID, "amount_inr": 2500}
    r = client.post("/risk/score", json=payload, headers={**SCORER_H, "X-Mandate": mandate})
    body = r.json()
    assert r.status_code == 200 and body["decision"] == "REJECT"
    assert body["mandate"]["verdict"] == "breach"


def test_forged_mandate_header_rejected(client):
    r = client.post(
        "/risk/score",
        json=VALID,
        headers={**SCORER_H, "X-Mandate": "eyJzdWIiOiJoYWNrIn0.deadbeef"},
    )
    assert r.json()["mandate"]["verdict"] == "tampered"


def test_agent_cannot_self_approve(client):
    scored = client.post("/risk/score", json=VALID, headers=SCORER_H).json()
    pid = scored["prediction_id"]
    r = client.post(
        f"/risk/{pid}/override?new_decision=ACCEPT", headers=SCORER_H
    )
    assert r.status_code == 403


def test_admin_can_override(client):
    scored = client.post("/risk/score", json=VALID, headers=SCORER_H).json()
    pid = scored["prediction_id"]
    r = client.post(
        f"/risk/{pid}/override?new_decision=REVIEW", headers=ADMIN_H
    )
    assert r.status_code == 200 and r.json()["new_decision"] == "REVIEW"


def test_only_admin_mints_mandates(client):
    r = client.post("/v1/mandates?customer_ref=C1&max_amount_inr=999", headers=SCORER_H)
    assert r.status_code == 401


# ============================================================================
# Day 1 Track D — UPI Circle / delegated-payments mandates (NPCI OC-201B).
# Source: paper studied/npci-oc201b-upi-circle-iot-circular/ + paper studied/
# upi-delegated-payments-npci-oc201b-lexology/. The circular's hard caps:
#   * ₹5,000 per txn, ₹15,000 per delegation/month, ₹5,000 24h cooling
#   * max 5 IoT devices/software per user, 6-month inactivity auto-revoke
#   * per-txn device_id + user_id validation by the issuer (this server)
#   * BH purpose code tagging for raw-file audit trail (default "90")
# Each test calls ``reset_upi_counters`` first so the in-memory cumulative
# state from a previous test does not bleed in.
# ============================================================================


def _issue_upi_circle(
    customer_ref: str = "CUST-UPI-1",
    *,
    device_ids: list[str] | None = None,
    user_id: str = "user-01",
    bh_purpose_code: str = "90",
    max_per_txn_inr: float = 5000,
    max_per_month_inr: float = 15000,
    cooling_24h_inr: float = 5000,
    inactivity_revoke_days: int = 180,
    ttl_seconds: int = 3600,
) -> str:
    return issue_mandate(
        customer_ref,
        max_amount_inr=max_per_month_inr,
        ttl_seconds=ttl_seconds,
        scope="upi_circle",
        mandate_type="upi_circle_delegation",
        device_ids=device_ids or ["device-01", "device-02"],
        user_id=user_id,
        bh_purpose_code=bh_purpose_code,
        max_per_txn_inr=max_per_txn_inr,
        max_per_month_inr=max_per_month_inr,
        cooling_24h_inr=cooling_24h_inr,
        inactivity_revoke_days=inactivity_revoke_days,
    )


def test_upi_circle_valid_delegation():
    """Issue + verify a valid UPI Circle delegation within all OC-201B caps."""
    reset_upi_counters()
    m = _issue_upi_circle()
    verdict, payload = verify_mandate(
        m, 1500, device_id="device-01", user_id="user-01"
    )
    assert verdict == MandateVerdict.VALID
    assert payload["verdict_reason"] == "ok"
    assert payload["mandate_type"] == "upi_circle_delegation"
    assert payload["bh_purpose_code"] == "90"
    assert "device-01" in payload["device_ids"]
    assert payload["max_per_txn_inr"] == 5000
    assert payload["max_per_month_inr"] == 15000
    assert payload["cooling_24h_inr"] == 5000
    assert payload["inactivity_revoke_days"] == 180


def test_upi_circle_device_id_not_allowed():
    """Request with device_id not in mandate's allowlist -> BREACH with
    verdict_reason="device_id_not_allowed" (OC-201B §3.7 Issuer Bank duty).
    """
    reset_upi_counters()
    m = _issue_upi_circle()
    verdict, payload = verify_mandate(
        m, 500, device_id="device-rogue-99", user_id="user-01"
    )
    assert verdict == MandateVerdict.BREACH
    assert payload["verdict_reason"] == "device_id_not_allowed"


def test_upi_circle_user_id_mismatch():
    """Per-txn user_id must match the mandate's user_id (OC-201B §3.3)."""
    reset_upi_counters()
    m = _issue_upi_circle()
    verdict, payload = verify_mandate(
        m, 500, device_id="device-01", user_id="wrong-user"
    )
    assert verdict == MandateVerdict.BREACH
    assert payload["verdict_reason"] == "user_id_mismatch"


def test_upi_circle_per_txn_cap_exceeded():
    """Amount > ₹5,000 (default cap) -> BREACH with
    verdict_reason="per_txn_cap_exceeded".
    """
    reset_upi_counters()
    m = _issue_upi_circle()
    verdict, payload = verify_mandate(
        m, 5001, device_id="device-01", user_id="user-01"
    )
    assert verdict == MandateVerdict.BREACH
    assert payload["verdict_reason"] == "per_txn_cap_exceeded"


def test_upi_circle_monthly_cap_exceeded():
    """Sum of txns > ₹15,000/month (default cap) -> BREACH with
    verdict_reason="monthly_cap_exceeded". The cumulative counter is per
    mandate_id (the salted customer_ref digest).

    We disable the 24h cooling gate for this test (set cooling_24h_inr
    high) so the cooling-period REVIEW doesn't fire before the monthly
    cap. Each individual txn stays within the ₹5,000 per-txn cap.
    """
    reset_upi_counters()
    m = _issue_upi_circle(cooling_24h_inr=99_999)  # disable cooling for this test
    # Three txns of ₹5,000 each: cumulative ₹15,000 (exactly at cap, VALID).
    for _ in range(3):
        v, _ = verify_mandate(m, 5000, device_id="device-01", user_id="user-01")
        assert v == MandateVerdict.VALID
    # 4th txn of ₹500: cumulative ₹15,500 > ₹15,000 cap -> BREACH.
    v4, p4 = verify_mandate(m, 500, device_id="device-01", user_id="user-01")
    assert v4 == MandateVerdict.BREACH
    assert p4["verdict_reason"] == "monthly_cap_exceeded"


def test_upi_circle_cooling_period():
    """Second txn within 24h after a >= ₹5,000 txn -> REVIEW (not REJECT)
    with verdict_reason="cooling_period_active". The cooling gate is a
    fraud-control circuit breaker, not a hard cap — the txn is permitted in
    principle, just routed to the review queue.
    """
    reset_upi_counters()
    # Lower the cooling threshold for the test so we don't need ₹5,000 to
    # trigger it (still within the ₹5,000 per-txn cap).
    m = _issue_upi_circle(cooling_24h_inr=1000)
    # First txn: ₹1,500 — at/above the ₹1,000 cooling threshold.
    v1, _ = verify_mandate(m, 1500, device_id="device-01", user_id="user-01")
    assert v1 == MandateVerdict.VALID
    # Second txn within 24h -> cooling gate fires.
    v2, p2 = verify_mandate(m, 500, device_id="device-01", user_id="user-01")
    assert v2 == MandateVerdict.REVIEW
    assert p2["verdict_reason"] == "cooling_period_active"


def test_upi_circle_inactivity_auto_revoke():
    """Mandate with last_activity > 180 days ago -> EXPIRED with
    verdict_reason="inactivity_auto_revoke" (OC-201B 6-month auto-revoke).
    Uses the ``simulate_inactivity`` test helper to backdate the per-mandate
    last_activity counter without sleeping 180 days.
    """
    reset_upi_counters()
    m = _issue_upi_circle(inactivity_revoke_days=180)
    # A freshly-issued mandate is active (iat=now).
    v0, _ = verify_mandate(m, 100, device_id="device-01", user_id="user-01")
    assert v0 == MandateVerdict.VALID
    # Simulate 181 days of inactivity.
    simulate_inactivity(m, days=181)
    v1, p1 = verify_mandate(m, 100, device_id="device-01", user_id="user-01")
    assert v1 == MandateVerdict.EXPIRED
    assert p1["verdict_reason"] == "inactivity_auto_revoke"


def test_upi_circle_more_than_5_devices_rejected_at_mint():
    """OC-201B: max 5 IoT devices/software per user. The mint function
    rejects a 6-device list at issue time (fail-loud per V3 §4 principle 3).
    """
    reset_upi_counters()
    with pytest.raises(ValueError, match="OC-201B"):
        issue_mandate(
            "CUST-UPI-X",
            max_amount_inr=15000,
            ttl_seconds=3600,
            scope="upi_circle",
            mandate_type="upi_circle_delegation",
            device_ids=["d1", "d2", "d3", "d4", "d5", "d6"],
            user_id="user-x",
        )


def test_upi_circle_bh_purpose_code_in_audit(client):
    """End-to-end: UPI Circle txn's audit record contains bh_purpose_code +
    mandate_type + device_id + user_id (OC-201B compliance telemetry).
    """
    reset_upi_counters()
    m = _issue_upi_circle()
    order = {
        **VALID,
        "order_id": "MND-UPI-AUDIT-1",
        "amount_inr": 1500,
    }
    r = client.post(
        "/risk/score",
        json=order,
        headers={
            **SCORER_H,
            "X-Mandate": m,
            "X-Device-Id": "device-01",
            "X-User-Id": "user-01",
        },
    )
    body = r.json()
    assert r.status_code == 200
    # Response body mandate section surfaces the OC-201B metadata.
    assert body["mandate"]["mandate_type"] == "upi_circle_delegation"
    assert body["mandate"]["bh_purpose_code"] == "90"
    assert body["mandate"]["verdict_reason"] == "ok"
    # Audit trail carries the same metadata into the hash chain.
    audit_id = body["audit_trail_url"].split("/")[-1]
    rec = client.get(f"/audit/{audit_id}", headers=ADMIN_H).json()
    assert rec["mandate_type"] == "upi_circle_delegation"
    assert rec["bh_purpose_code"] == "90"
    assert rec["device_id"] == "device-01"
    assert rec["user_id"] == "user-01"
    assert rec["mandate_verdict"] == "valid"
    assert rec["mandate_verdict_reason"] == "ok"


def test_upi_circle_device_id_not_allowed_in_routes(client):
    """End-to-end: rogue device_id at the API -> REJECT with
    verdict_reason="device_id_not_allowed" surfaced in the response body.
    """
    reset_upi_counters()
    m = _issue_upi_circle()
    order = {
        **VALID,
        "order_id": "MND-UPI-DEVICE-1",
        "amount_inr": 500,
    }
    r = client.post(
        "/risk/score",
        json=order,
        headers={
            **SCORER_H,
            "X-Mandate": m,
            "X-Device-Id": "device-rogue-99",
            "X-User-Id": "user-01",
        },
    ).json()
    assert r["decision"] == "REJECT"
    assert r["mandate"]["verdict"] == "breach"
    assert r["mandate"]["verdict_reason"] == "device_id_not_allowed"
    assert r["decision_source"] == "mandate_breach"


def test_upi_circle_cooling_period_routes_to_review(client):
    """End-to-end: 24h cooling-period gate -> REVIEW (not REJECT),
    decision_source="mandate_review_required", case created in the queue.
    """
    reset_upi_counters()
    m = _issue_upi_circle(cooling_24h_inr=1000)
    order1 = {**VALID, "order_id": "MND-UPI-COOL-1", "amount_inr": 1500}
    order2 = {**VALID, "order_id": "MND-UPI-COOL-2", "amount_inr": 500}
    headers = {
        **SCORER_H,
        "X-Mandate": m,
        "X-Device-Id": "device-01",
        "X-User-Id": "user-01",
    }
    r1 = client.post("/risk/score", json=order1, headers=headers).json()
    r2 = client.post("/risk/score", json=order2, headers=headers).json()
    assert r1["mandate"]["verdict"] == "valid"
    assert r2["decision"] == "REVIEW"
    assert r2["mandate"]["verdict"] == "review"
    assert r2["mandate"]["verdict_reason"] == "cooling_period_active"
    assert r2["decision_source"] == "mandate_review_required"
    assert r2["case_id"] is not None


def test_cod_order_mandate_still_works_after_upi_extension():
    """Backward-compat: the original cod_order 3-arg signature still issues
    + verifies a cod_order mandate after the UPI Circle extension landed.
    """
    reset_upi_counters()
    m = issue_mandate("CUST-LEGACY", max_amount_inr=2000, ttl_seconds=600)
    verdict, payload = verify_mandate(m, 1000)
    assert verdict == MandateVerdict.VALID
    assert payload["mandate_type"] == "cod_order"
    assert payload["verdict_reason"] == "ok"
    # cod_order mandates do not carry UPI Circle fields.
    assert "device_ids" not in payload
    assert "bh_purpose_code" not in payload


def test_decode_mandate_helper_round_trips():
    """decode_mandate exposes the mandate body for inspection without
    verifying the HMAC — useful for tests + dashboards.
    """
    m = _issue_upi_circle()
    payload = decode_mandate(m)
    assert payload["mandate_type"] == "upi_circle_delegation"
    assert payload["bh_purpose_code"] == "90"
    assert "device-01" in payload["device_ids"]
    assert payload["max_per_txn_inr"] == 5000

