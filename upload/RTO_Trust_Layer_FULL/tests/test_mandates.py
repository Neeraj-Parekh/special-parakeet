import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.mandates import (  # noqa: E402
    MandateVerdict,
    issue_mandate,
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
