import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.breaker import CircuitBreaker  # noqa: E402
from src.api.routes import create_app  # noqa: E402
from src.audit.logger import AuditLogger  # noqa: E402
from src.business.cost_optimizer import optimal_decision  # noqa: E402
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
    finally:
        client.app.state.core["model"] = original
    assert client.app.state.core["breaker"].state in {"OPEN", "HALF_OPEN"}
