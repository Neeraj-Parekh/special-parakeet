import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.routes import create_app  # noqa: E402
from src.ml.registry import current_champion, psi, register_model  # noqa: E402

SCORER_H = {"Authorization": "Bearer score-demo-key"}
ADMIN_H = {"Authorization": "Bearer admin-demo-key"}
VALID = {
    "order_id": "PLAT-001",
    "amount_inr": 899,
    "category": "Fashion",
    "customer_id": "CUST-P1",
}


@pytest.fixture(scope="module")
def client():
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        with TestClient(create_app(audit_path=f"{td}/audit.jsonl")) as c:
            yield c


def test_metrics_endpoint_prometheus_format(client):
    client.post("/risk/score", json=VALID, headers=SCORER_H)
    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.text
    assert "risk_decisions_total{" in body and "decision=" in body
    assert "rto_circuit_state" in body
    assert "rto_score_latency_seconds_count" in body


def test_review_decision_opens_case_and_admin_resolves(client):
    scored = client.post(
        "/risk/score",
        json={**VALID, "order_id": "PLAT-CASE", "amount_inr": 2999,
              "category": "Health", "address_quality": "partial",
              "payment_method": "COD", "prior_orders": 4, "prior_returns": 3},
        headers=SCORER_H,
    ).json()
    assert scored["decision"] == "REVIEW"
    case_id = scored["case_id"]
    assert case_id and case_id.startswith("CASE-")
    cases = client.get("/v1/cases", headers=ADMIN_H).json()["cases"]
    assert client.get("/v1/cases", headers=SCORER_H).status_code == 401
    assert any(c["case_id"] == case_id for c in cases)
    r = client.post(
        f"/v1/cases/{case_id}/resolve?decision=APPROVED&notes=fine",
        headers=SCORER_H,
    )
    assert r.status_code == 403
    r = client.post(
        f"/v1/cases/{case_id}/resolve?decision=APPROVED&notes=fine",
        headers=ADMIN_H,
    )
    assert r.status_code == 200 and r.json()["status"] == "APPROVED"


def test_registry_register_and_champion(tmp_path):
    reg = str(tmp_path / "reg.json")
    register_model("v1", "a.pkl", {"pr_auc": 0.52}, champion=True, registry_path=reg)
    register_model("v2", "b.pkl", {"pr_auc": 0.55}, champion=True, registry_path=reg)
    champ = current_champion(reg)
    assert champ["version"] == "v2"
    others = [m for m in __import__("json").loads(Path(reg).read_text())["models"]]
    assert [m["is_champion"] for m in others] == [False, True]


def test_psi_detects_shift():
    import random

    random.seed(1)
    ref = [random.gauss(0, 1) for _ in range(5000)]
    same = [random.gauss(0, 1) for _ in range(2000)]
    shifted = [random.gauss(3, 1) for _ in range(2000)]
    assert psi(ref, same) < 0.05
    assert psi(ref, shifted) > 0.25


def test_models_current_endpoint(client):
    r = client.get("/v1/models/current", headers=SCORER_H)
    assert r.status_code == 200
    assert "champion" in r.json()


def test_drift_endpoint_shape(client):
    r = client.get("/v1/models/drift", headers=ADMIN_H)
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") in {"OK", "WARNING", "CRITICAL", "insufficient_data"}
    assert client.get("/v1/models/drift", headers=SCORER_H).status_code == 401


def test_compliance_export_csv(client):
    client.post("/risk/score", json={**VALID, "order_id": "PLAT-EXP"}, headers=SCORER_H)
    r = client.get("/v1/compliance/audit-export", headers=ADMIN_H)
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "audit_id" in r.text.splitlines()[0]
    assert client.get("/v1/compliance/audit-export", headers=SCORER_H).status_code == 401


def test_model_card(client):
    r = client.get("/v1/compliance/model-card", headers=SCORER_H)
    body = r.json()
    assert r.status_code == 200
    assert "limitations" in body and "intended_use" in body


def test_latency_ms_in_response(client):
    body = client.post("/risk/score", json=VALID, headers=SCORER_H).json()
    assert isinstance(body.get("latency_ms"), float) and body["latency_ms"] > 0
