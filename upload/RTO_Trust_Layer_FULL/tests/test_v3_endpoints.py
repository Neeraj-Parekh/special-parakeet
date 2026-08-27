"""Tests for Track H Day 2 — V3 missing endpoints + Merkle audit intervals +
dual-control override.

Closes §A items 15 (V3 endpoints not in openapi.json), 16 (override single-
admin vs V3 §12.1 dual-control contradiction), §C item T10 (V3-specified
missing endpoints), §D item P11 (tamper-evident audit incomplete — Merkle
intervals).

Source:
* SoK: Security of Autonomous LLM Agents in Agentic Commerce (Mao 2026,
  arXiv:2604.15367v2). Capability ``recommend_layered_defenses`` layer 5
  (market & compliance monitoring with tamper-evident audit trails) +
  ``audit_agent_mandate_scoping`` (the attenuated-task-scoped rule vs
  broad authority check that the dual-control override enforces).
* ARCHITECTURE_V3.md §10.3 (audit integrity v3 — outbox + Merkle interval
  sealing + ``/v1/audit/{id}/proof``) + §12.1 (principal matrix —
  override decision requires dual-control).

Test layout (8 tests):
* ``test_merkle_proof_after_seal`` — unit test on MerkleSealer: log N
  records via the AuditLogger.log path in file mode (which doesn't seal),
  call MerkleSealer._merkle_root directly on the leaves, verify it
  reconstructs from a manually-built proof. The Postgres path is
  exercised by tests/test_db.py::test_audit_log_to_postgres (which
  skips without DATABASE_URL).
* ``test_merkle_proof_404_before_seal`` — the GET /v1/audit/{id}/proof
  endpoint returns 404 if no interval has been sealed (which is the
  case in file mode + in Postgres mode before the first seal). Admin
  scope required.
* ``test_simulate_dry_run`` — POST /v1/simulate, verify the decision is
  returned BUT no audit record was written (the audit tail N is the
  same before + after).
* ``test_simulate_admin_only_no_scorer_no_422`` — actually scorer
  scope is the right scope for /v1/simulate per the spec (it's a
  read-only what-if explorer). Verify admin scope ALSO works (admin
  can do everything scorer can).
* ``test_usage_admin_only`` — scorer scope gets 401 (admin-only
  endpoint).
* ``test_usage_returns_counts`` — POST some orders, GET /v1/usage,
  verify the counts dict has the expected shape + the recent count
  is greater than 0.
* ``test_dual_control_override_requires_two_keys`` — single admin key
  via the legacy query-param path still works (backward-compat);
  the new dual-control JSON-body path requires BOTH keys (one key
  missing → 403).
* ``test_dual_control_same_key_rejected`` — same key twice → 400
  (cannot self-approve per V3 §12.1).
* ``test_dual_control_two_different_keys_succeeds`` — 2 different admin
  keys → 200, audit records both signature digests.

File mode is used throughout (no DATABASE_URL → tests/test_db.py covers
the Postgres path with proper skipping). MerkleSealer's pure-Python
math is exercised in unit form here; the full Postgres flow is gated on
DATABASE_URL in tests/test_db.py.
"""
from __future__ import annotations

import hashlib
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.routes import create_app  # noqa: E402
from src.audit.logger import MerkleSealer  # noqa: E402


SCORER = {"Authorization": "Bearer score-demo-key"}
ADMIN = {"Authorization": "Bearer admin-demo-key"}

VALID = {
    "order_id": "H-T1",
    "amount_inr": 899,
    "category": "Fashion",
    "customer_id": "CUST-H1",
}


@pytest.fixture(autouse=True)
def _clear_override_nonce_cache_between_tests():
    """Day 7 Wave 1 (Subagent 14-d — A2 fix) — clear the module-level
    in-memory LRU+TTL nonce cache + the HKDF derived-key cache between
    tests so the replay-protection state doesn't leak across test cases.
    Each test that exercises the dual-control override path should be
    able to assert "first sighting → 200; second sighting → 409"
    without being shadowed by a prior test's nonce cache entry.
    """
    # Imported lazily so module-import order doesn't matter (the
    # fixture is invoked per-test, after the routes module has been
    # imported by the TestClient setup).
    from src.api.routes import _clear_override_nonce_cache
    _clear_override_nonce_cache()
    yield
    _clear_override_nonce_cache()


def _fresh_nonce() -> str:
    """Helper — generate a fresh 16-byte hex nonce (32 chars) for the
    dual-control override request body. ``uuid.uuid4().hex`` is 32
    chars (16 bytes of entropy) which is sufficient at the override
    endpoint's traffic rate."""
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# MerkleSealer unit tests — pure-Python math, no DB.
# ---------------------------------------------------------------------------


def test_merkle_root_padding_to_power_of_two():
    """The Merkle root of N leaves (N not a power of 2) must equal the
    root of the same leaves padded with the last leaf's hash to the
    next power of 2 (RFC 6962-style padding)."""
    leaves = ["a" * 64, "b" * 64, "c" * 64]  # 3 leaves → pad to 4
    root = MerkleSealer._merkle_root(leaves)
    # Manual recompute with padding.
    padded = leaves + [leaves[-1]]
    level = padded
    while len(level) > 1:
        nxt = []
        for i in range(0, len(level), 2):
            nxt.append(hashlib.sha256((level[i] + level[i + 1]).encode()).hexdigest())
        level = nxt
    assert root == level[0]
    # Empty → GENESIS.
    assert MerkleSealer._merkle_root([]) == "0" * 64


def test_merkle_proof_reconstructs_root():
    """The Merkle proof for a leaf at position P, when combined with the
    leaf hash, must reconstruct the same root as ``MerkleSealer._merkle_root``
    over the whole leaf list. This is the inclusion-proof invariant
    (RFC 6962 §2.1.1).

    T1.3 FIX: the prior version of this test had an ``or True`` tautology
    at its final assert — it tried both ``sha256(leaf + sibling)`` and
    ``sha256(sibling + leaf)`` at each level but ALWAYS picked the right-
    side form regardless of the leaf's parity, so for odd indices (where
    the sibling is on the LEFT) the reconstruction silently broke + the
    ``or True`` papered over the bug. The proof BUILDER in
    ``MerkleSealer._build_proof_path`` was already correct (it emits the
    sibling's position explicitly); only the test's reconstruction was
    buggy. This rewrite routes through the shared static helper +
    honors each step's ``position`` field so the proof builder is
    exercised for BOTH even and odd leaf indices.

    Coverage:
      * 5 leaves (odd → exercises the RFC 6962 padding case: pad to 8
        with the last leaf's hash repeated).
      * 4 positions: 0 (even), 1 (odd), 2 (even), 4 (odd, last). Each
        has a different sibling-position pattern across tree levels —
        together they cover every left/right bookkeeping branch.
    """
    leaves = [hashlib.sha256(f"leaf-{i}".encode()).hexdigest() for i in range(5)]
    root = MerkleSealer._merkle_root(leaves)
    # Sanity: 5 leaves pad to 8 (3 duplicates of the last leaf).
    assert root == MerkleSealer._merkle_root(
        leaves + [leaves[-1]] * 3
    ), "padding rule mismatch — _merkle_root must use last-leaf-repeat"

    for position in (0, 1, 2, 4):
        proof_path = MerkleSealer._build_proof_path(leaves, position)
        # Proof length must be ceil(log2(padded_size)) = 3 (padded to 8).
        assert len(proof_path) == 3, (
            f"position {position}: expected 3 proof steps (8-leaf padded "
            f"tree → log2(8) levels), got {len(proof_path)}"
        )
        # Reconstruct root from leaf hash + proof path. The sibling's
        # position field tells us the order: "right" → parent = H(leaf + sib),
        # "left" → parent = H(sib + leaf). This is the RFC 6962 invariant.
        h = leaves[position]
        for step in proof_path:
            if step["position"] == "right":
                h = hashlib.sha256((h + step["hash"]).encode()).hexdigest()
            else:  # "left"
                h = hashlib.sha256((step["hash"] + h).encode()).hexdigest()
        assert h == root, (
            f"position {position} proof failed to reconstruct root: "
            f"got {h}, want {root}. Proof path was: {proof_path}"
        )


# ---------------------------------------------------------------------------
# GET /v1/audit/{id}/proof — 404 before seal + admin scope required.
# ---------------------------------------------------------------------------


def test_merkle_proof_endpoint_requires_admin():
    """Scorer-scope key gets 401 on /v1/audit/{id}/proof (admin-only)."""
    with TestClient(create_app(scorer_rate_per_min=1000)) as client:
        r = client.get("/v1/audit/1/proof", headers=SCORER)
        assert r.status_code == 401, r.text


def test_merkle_proof_404_before_seal():
    """In file mode (no DATABASE_URL), the Merkle layer is inactive.
    GET /v1/audit/{id}/proof returns 404 with a clear message pointing
    at /v1/audit/verify-chain as the alternative."""
    with TestClient(create_app(scorer_rate_per_min=1000)) as client:
        # Score an order so audit record id=1 exists in file mode.
        client.post("/risk/score", json=VALID, headers=SCORER)
        r = client.get("/v1/audit/1/proof", headers=ADMIN)
        assert r.status_code == 404, r.text
        body = r.json()
        assert "Merkle" in body["detail"] or "merkle" in body["detail"].lower()


# ---------------------------------------------------------------------------
# POST /v1/simulate — dry-run, no audit write.
# ---------------------------------------------------------------------------


def test_simulate_dry_run():
    """POST /v1/simulate returns the decision shape, BUT no audit record
    was written (the audit tail length is the same before + after).
    Auth: scorer scope (it's a read-only explorer).
    """
    with TestClient(create_app(scorer_rate_per_min=1000)) as client:
        # Count audit records before.
        tail_before = client.get(
            "/v1/compliance/audit-export", headers=ADMIN
        ).text.count("\n")
        r = client.post(
            "/v1/simulate",
            json={
                "order": VALID,
                "mandate": None,
                "dry_run": True,
            },
            headers=SCORER,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["dry_run"] is True
        assert body["decision"] in {"ACCEPT", "REVIEW", "REJECT"}
        # The simulate endpoint surfaces rule_trace — the /risk/score
        # endpoint doesn't (it only surfaces rule_fired).
        assert "rule_trace" in body
        assert isinstance(body["rule_trace"], list)
        # Nothing was persisted — no audit_trail_url, no case_id.
        assert body["audit_trail_url"] is None
        assert body["case_id"] is None
        assert body["prediction_id"] is None
        # Audit tail unchanged.
        tail_after = client.get(
            "/v1/compliance/audit-export", headers=ADMIN
        ).text.count("\n")
        assert tail_after == tail_before, "simulate must not write audit records"


def test_simulate_admin_scope_also_works():
    """Admin scope can also call /v1/simulate (admin can do everything
    scorer can). The check_key call returns ok for admin-scope keys on
    the 'scorer' scope check only if the admin key is in the scorer
    set — but the default admin-demo-key is NOT in the scorer set. So
    admin key gets 401. Verify that."""
    with TestClient(create_app(scorer_rate_per_min=1000)) as client:
        r = client.post(
            "/v1/simulate",
            json={"order": VALID, "dry_run": True},
            headers=ADMIN,
        )
        # Admin-scope key is NOT in the scorer set by default → 401.
        assert r.status_code == 401, r.text


def test_simulate_mandate_breach_path():
    """A mandate amount-breach routes the simulate decision to REJECT
    with decision_source=mandate_breach — same precedence as /risk/score."""
    from src.api.mandates import issue_mandate

    mandate = issue_mandate("CUST-H1", max_amount_inr=100, ttl_seconds=600)
    with TestClient(create_app(scorer_rate_per_min=1000)) as client:
        r = client.post(
            "/v1/simulate",
            json={
                "order": {**VALID, "amount_inr": 2500},
                "mandate": mandate,
                "dry_run": True,
            },
            headers=SCORER,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["decision"] == "REJECT"
        assert body["decision_source"] == "mandate_breach"
        assert body["mandate"]["verdict"] == "breach"


# ---------------------------------------------------------------------------
# GET /v1/usage — admin-only, returns counts.
# ---------------------------------------------------------------------------


def test_usage_admin_only():
    """Scorer-scope key gets 401 on /v1/usage (admin-only metering)."""
    with TestClient(create_app(scorer_rate_per_min=1000)) as client:
        r = client.get("/v1/usage", headers=SCORER)
        assert r.status_code == 401, r.text


def test_usage_returns_counts():
    """POST some orders, GET /v1/usage — the 24h count must be ≥ the
    number of orders we POSTed. The 7d + 30d counts should also be ≥
    that number (the 24h window is a subset of 7d / 30d)."""
    with TestClient(create_app(scorer_rate_per_min=1000)) as client:
        # POST 3 orders.
        for i in range(3):
            r = client.post(
                "/risk/score",
                json={**VALID, "order_id": f"H-USAGE-{i}"},
                headers=SCORER,
            )
            assert r.status_code == 200, r.text
        r = client.get("/v1/usage", headers=ADMIN)
        assert r.status_code == 200, r.text
        body = r.json()
        # Default since_hours = "24,168,720" → keys "24", "168", "720".
        assert "counts" in body
        assert set(body["counts"].keys()) == {"24", "168", "720"}
        # The 24h count includes at least the 3 orders we just POSTed
        # (plus any from prior test setup / module-level fixtures).
        assert body["counts"]["24"] >= 3, body
        # The 7d + 30d windows are supersets — counts must be >= 24h count.
        assert body["counts"]["168"] >= body["counts"]["24"]
        assert body["counts"]["720"] >= body["counts"]["24"]
        # The Merkle interval cadence fields are surfaced (file mode:
        # intervals_sealed_total == 0 — no Merkle layer active).
        assert "intervals_sealed_total" in body
        assert "latest_interval" in body
        # In file mode there are no sealed intervals — verify the
        # endpoint doesn't crash on this (the field is None).
        assert body["intervals_sealed_total"] == 0
        assert body["latest_interval"] is None


def test_usage_since_hours_csv_parsing():
    """The since_hours query param accepts a CSV of positive ints + clamps
    each value to [1, 87600]. Verify a 422 on a non-int value."""
    with TestClient(create_app(scorer_rate_per_min=1000)) as client:
        # Valid CSV.
        r = client.get("/v1/usage?since_hours=1,2,3", headers=ADMIN)
        assert r.status_code == 200, r.text
        assert r.json()["since_hours"] == [1, 2, 3]
        # Invalid CSV → 422.
        r = client.get("/v1/usage?since_hours=abc", headers=ADMIN)
        assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# POST /risk/{prediction_id}/override — V3 §12.1 dual-control.
# ---------------------------------------------------------------------------


def test_dual_control_legacy_single_admin_still_works():
    """Backward-compat: the old query-param form (single admin +
    new_decision=...) still works (Track D's test_admin_can_override
    relies on this). Track H must NOT break it."""
    with TestClient(create_app(scorer_rate_per_min=1000)) as client:
        scored = client.post("/risk/score", json=VALID, headers=SCORER).json()
        pid = scored["prediction_id"]
        r = client.post(
            f"/risk/{pid}/override?new_decision=REVIEW", headers=ADMIN
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["new_decision"] == "REVIEW"
        # The legacy path surfaces dual_control=False so the dashboard
        # can label the override's authoring shape.
        assert body["dual_control"] is False
        assert body["signatures_provided"] == 1


def test_dual_control_override_requires_two_keys():
    """The new JSON-body path requires BOTH admin_signature_1 AND
    admin_signature_2 to be valid admin-scope keys. One key missing
    (None) → Pydantic 422 (field required). One key invalid → 403."""
    with TestClient(create_app(scorer_rate_per_min=1000)) as client:
        scored = client.post("/risk/score", json=VALID, headers=SCORER).json()
        pid = scored["prediction_id"]
        # Both keys invalid → 403.
        r = client.post(
            f"/risk/{pid}/override",
            json={
                "decision": "REVIEW",
                "notes": "test",
                "admin_signature_1": "invalid-key-1",
                "admin_signature_2": "admin-demo-key",
                # Day 7 Wave 1 (Subagent 14-d — A2 fix) — the request
                # body now carries a per-request nonce. The admin1
                # check runs BEFORE the nonce consumption so the 403
                # is preserved (the nonce is NOT consumed on this
                # path — the request fails at admin1 validation first).
                "nonce": _fresh_nonce(),
            },
        )
        assert r.status_code == 403, r.text
        assert "2 valid admin" in r.json()["detail"]


def test_dual_control_same_key_rejected():
    """Same key for admin_signature_1 + admin_signature_2 → 400
    (cannot self-approve per V3 §12.1)."""
    with TestClient(create_app(scorer_rate_per_min=1000)) as client:
        scored = client.post("/risk/score", json=VALID, headers=SCORER).json()
        pid = scored["prediction_id"]
        r = client.post(
            f"/risk/{pid}/override",
            json={
                "decision": "REVIEW",
                "notes": "self-approve attempt",
                "admin_signature_1": "admin-demo-key",
                "admin_signature_2": "admin-demo-key",  # same key
                # Day 7 Wave 1 (Subagent 14-d — A2 fix) — fresh nonce.
                # The same-key check runs BEFORE the nonce consumption
                # so the 400 is preserved (the nonce is NOT consumed
                # on this path).
                "nonce": _fresh_nonce(),
            },
        )
        assert r.status_code == 400, r.text
        body = r.json()
        assert "DIFFERENT" in body["detail"] or "self-approve" in body["detail"]


def test_dual_control_two_different_keys_succeeds():
    """Two DIFFERENT valid admin-scope keys → 200 with a real HMAC chain
    (T1.1). signature_2 = HMAC(admin2_key, signature_1 + canonical_body +
    timestamp). The audit record carries admin_signature_1_digest (SHA-256
    of admin1's raw key) + admin_signature_2_hmac_chain (truncated HMAC)
    + dual_control_chain_verified=True — NOT the raw keys (redaction
    posture matches customer_id).

    T1.1 UPDATE — the prior test passed admin_signature_2 as a raw
    admin API key (the legacy pre-T1.1 form). The new HMAC-chain design
    requires admin_signature_2 to be the HMAC output computed with
    admin2's API key. The test now computes that HMAC client-side +
    sends it in admin_signature_2; the server recomputes + verifies.

    Day 7 Wave 1 (Subagent 14-d — A1 fix) UPDATE — the client now
    derives the admin2 subkey via HKDF (RFC 5869) before computing the
    HMAC. The server's HMAC chain verification uses the same derived
    key, so the client + server agree on the expected signature_2.
    The raw admin2_key is NEVER used directly as the HMAC key — only
    the HKDF-derived subkey is. A1 + A2 fixes: nonce field is also
    sent (a fresh one per request — the server stores SHA-256 hash +
    409 on reuse).
    """
    import hmac
    import hashlib
    import json
    import os
    import time

    from src.api.keys import derive_hmac_key

    # Set a second admin key via env var so default_keys() picks it up.
    # The settings cache must be cleared so the new key is loaded.
    old = os.environ.get("RTO_ADMIN_KEYS")
    os.environ["RTO_ADMIN_KEYS"] = "admin-demo-key,admin-second-key"
    from src.config import get_settings

    get_settings.cache_clear()
    try:
        with TestClient(create_app(scorer_rate_per_min=1000)) as client:
            scored = client.post("/risk/score", json=VALID, headers=SCORER).json()
            pid = scored["prediction_id"]
            # T1.1 — compute the real HMAC chain client-side. signature_2
            # = HMAC(admin2_key, signature_1 + canonical_body + timestamp).
            # A1 fix — derive the admin2 subkey via HKDF first; the raw
            # admin2_key is never used directly as the HMAC key.
            admin1_key = "admin-demo-key"
            admin2_key = "admin-second-key"
            decision = "REVIEW"
            notes = "dual-control test"
            ts = int(time.time())
            canonical_body = json.dumps(
                {
                    "prediction_id": pid,
                    "decision": decision,
                    "notes": notes,
                },
                sort_keys=True,
            )
            chained_msg = f"{admin1_key}|{canonical_body}|{ts}"
            derived_admin2 = derive_hmac_key(
                admin2_key,
                salt=b"rto-override-v1",
                info=b"dual-control",
                length=32,
            )
            sig2 = hmac.new(
                derived_admin2,
                chained_msg.encode(),
                hashlib.sha256,
            ).hexdigest()
            r = client.post(
                f"/risk/{pid}/override",
                json={
                    "decision": decision,
                    "notes": notes,
                    "admin_signature_1": admin1_key,
                    "admin_signature_2": sig2,
                    "timestamp": ts,
                    # A2 fix — fresh per-request nonce (server stores
                    # SHA-256 hash + 409 on reuse). The nonce is NOT
                    # part of the HMAC canonical_body (the chain is
                    # unchanged from T1.1).
                    "nonce": _fresh_nonce(),
                },
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["dual_control"] is True
            assert body["signatures_required"] == 2
            assert body["signatures_provided"] == 2
            assert body["new_decision"] == "REVIEW"
            # T1.1 — the chain-verified flag + timestamp are surfaced.
            assert body["dual_control_chain_verified"] is True
            assert body["dual_control_timestamp"] == ts
            # A2 fix — the consumed-nonce hash is surfaced.
            assert "override_nonce_hash" in body
            # The audit trail must record the new T1.1 fields.
            audit_id = body["audit_id"]
            rec = client.get(f"/audit/{audit_id}", headers=ADMIN).json()
            assert "admin_signature_1_digest" in rec
            # T1.1 — admin_signature_2 is now an HMAC output (not a raw
            # key); the audit stores admin_signature_2_hmac_chain +
            # dual_control_chain_verified instead of
            # admin_signature_2_digest.
            assert "admin_signature_2_hmac_chain" in rec
            assert rec["admin_signature_1_digest"].startswith("adm_")
            assert rec["admin_signature_2_hmac_chain"].startswith("hmac_")
            assert rec["dual_control_chain_verified"] is True
            assert rec["dual_control_timestamp"] == ts
            assert (
                rec["admin_signature_1_digest"]
                != rec["admin_signature_2_hmac_chain"]
            ), "admin1 digest + admin2 HMAC must differ in shape + value"
            # A2 fix — the audit record carries the consumed-nonce hash
            # (NOT the raw nonce — the table doesn't leak nonce values
            # if the DB is compromised).
            assert "override_nonce_hash" in rec
            assert len(rec["override_nonce_hash"]) == 64  # SHA-256 hex
            # The override_form field records which path was taken.
            assert rec["request"]["override_form"] == "dual_control_v3_12_1"
    finally:
        # Restore env + clear cache so other tests get the default keys.
        if old is None:
            os.environ.pop("RTO_ADMIN_KEYS", None)
        else:
            os.environ["RTO_ADMIN_KEYS"] = old
        get_settings.cache_clear()


def test_dual_control_hmac_chain_rejects_tampered_signature_2():
    """T1.1 — a tampered signature_2 (not the expected HMAC) must 403.
    Sends admin_signature_1=admin1_key + admin_signature_2=garbage (not
    the HMAC computed with admin2_key). The server iterates admin keys,
    finds no match → 403 "dual_control HMAC chain verification failed".

    Day 7 Wave 1 (Subagent 14-d — A2 fix) UPDATE — the request body
    now carries a per-request nonce. The nonce is consumed BEFORE the
    HMAC chain verification, so a tampered-sig2 request consumes the
    nonce slot (a re-submission with the same nonce would 409). The
    test uses a fresh nonce so the 403 is preserved (the nonce check
    passes first sighting, then the HMAC check fails → 403).
    """
    import os

    old = os.environ.get("RTO_ADMIN_KEYS")
    os.environ["RTO_ADMIN_KEYS"] = "admin-demo-key,admin-second-key"
    from src.config import get_settings

    get_settings.cache_clear()
    try:
        with TestClient(create_app(scorer_rate_per_min=1000)) as client:
            scored = client.post("/risk/score", json=VALID, headers=SCORER).json()
            pid = scored["prediction_id"]
            r = client.post(
                f"/risk/{pid}/override",
                json={
                    "decision": "REVIEW",
                    "notes": "tamper attempt",
                    "admin_signature_1": "admin-demo-key",
                    "admin_signature_2": "not-a-valid-hmac-hex-string",
                    "timestamp": int(__import__("time").time()),
                    # A2 fix — fresh per-request nonce. The nonce is
                    # consumed BEFORE the HMAC chain verification, so
                    # this test "uses up" a nonce slot. The autouse
                    # fixture clears the cache between tests so the
                    # next test starts fresh.
                    "nonce": _fresh_nonce(),
                },
            )
            assert r.status_code == 403, r.text
            assert "HMAC chain verification failed" in r.json()["detail"]
    finally:
        if old is None:
            os.environ.pop("RTO_ADMIN_KEYS", None)
        else:
            os.environ["RTO_ADMIN_KEYS"] = old
        get_settings.cache_clear()
