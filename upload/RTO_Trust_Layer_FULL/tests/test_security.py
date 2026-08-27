import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.routes import create_app  # noqa: E402

VALID = {
    "order_id": "SEC-T1",
    "amount_inr": 899,
    "category": "Fashion",
    "customer_id": "CUST-9",
}
SCORER = {"Authorization": "Bearer score-demo-key"}
ADMIN = {"Authorization": "Bearer admin-demo-key"}


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app(scorer_rate_per_min=1000)) as c:
        yield c


def test_rejects_missing_credentials(client):
    assert client.post("/risk/score", json=VALID).status_code == 401


def test_rejects_wrong_key(client):
    r = client.post("/risk/score", json=VALID, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_audit_requires_admin_scope(client):
    scored = client.post("/risk/score", json=VALID, headers=SCORER).json()
    audit_id = scored["audit_trail_url"].split("/")[-1]
    assert client.get(f"/audit/{audit_id}", headers=SCORER).status_code == 401
    assert client.get(f"/audit/{audit_id}", headers=ADMIN).status_code == 200


def test_audit_redacts_customer_identity(client):
    scored = client.post("/risk/score", json=VALID, headers=SCORER).json()
    rec = client.get(scored["audit_trail_url"], headers=ADMIN).json()
    assert rec["request"]["customer_id"].startswith("cust_")
    assert "CUST-9" not in str(rec)


def test_bounded_inputs_rejected(client):
    r1 = client.post("/risk/score", json={**VALID, "amount_inr": 1e15}, headers=SCORER)
    r2 = client.post("/risk/score", json={**VALID, "address_quality": "shady"}, headers=SCORER)
    assert r1.status_code == 422 and r2.status_code == 422


def test_idempotency_replay_same_prediction(client):
    h = {**SCORER, "Idempotency-Key": "ord-77"}
    a = client.post("/risk/score", json=VALID, headers=h).json()
    b = client.post("/risk/score", json=VALID, headers=h).json()
    assert a["prediction_id"] == b["prediction_id"] and b["replayed"] is True


def test_rate_limit_returns_429():
    with TestClient(create_app(scorer_rate_per_min=2)) as c:
        codes = [
            c.post(
                "/risk/score",
                json={**VALID, "order_id": f"RATE-{i}"},
                headers=SCORER,
            ).status_code
            for i in range(5)
        ]
        assert codes[-1] == 429 and 200 in codes


def test_no_internal_error_leakage(client):
    """Model failure must degrade gracefully - never leak internals, never 500."""
    class ExplodingModel:
        def predict_proba(self, _):
            raise RuntimeError("secret internal path /etc/passwd")

    original = client.app.state.core["model"]
    client.app.state.core["model"] = ExplodingModel()
    try:
        r = client.post("/risk/score", json=VALID, headers=SCORER)
    finally:
        client.app.state.core["model"] = original
    body = str(r.json())
    assert r.status_code == 200
    assert r.json()["degraded"] is True
    assert "passwd" not in body and "/etc/" not in body
