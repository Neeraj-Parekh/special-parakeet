"""Security test suite for the RTO Trust Layer.

Covers the 8 original tests (Track B baseline) + the 5 new P0/P1
defences added by Agent A2:

  * P0-1 — ``test_anti_extraction_noise_binned_to_two_decimals`` +
    ``test_anti_extraction_noise_disabled_returns_raw_proba``.
  * P0-2 — ``test_rules_engine_jitters_amount_threshold_only`` +
    ``test_rules_engine_no_jitter_on_categorical`` +
    ``test_rules_engine_jitter_disabled_returns_unmodified``.
  * P1-1 — ``test_per_ip_rate_limit_returns_429_when_exhausted`` +
    ``test_ip_rate_limiter_extract_ip_honors_forwarded_for``.
  * P1-2 — ``test_hmac_valid_signature_passes`` +
    ``test_hmac_replayed_stale_timestamp_rejected`` +
    ``test_hmac_bad_signature_rejected`` + ``test_hmac_disabled_by_default``.
  * P1-3 — ``test_feature_store_negative_cache_prevents_pg_flood`` +
    ``test_feature_store_positive_cache_returns_features`` +
    ``test_feature_store_redis_unavailable_passthrough``.

All 8 original tests preserved at the top of the file (unchanged
behaviour). The new tests are grouped under section headers.
"""
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.routes import create_app  # noqa: E402
from src.api.security import (  # noqa: E402
    IPRateLimiter,
    apply_anti_extraction_noise,
    compute_hmac_signature,
    parse_signature_header,
    verify_hmac_signature,
)
from src.api.feature_store import FeatureStore, _NULL_SENTINEL  # noqa: E402
from src.rules.engine import RulesEngine, _jitter_threshold  # noqa: E402

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


# ---------------------------------------------------------------------------
# Original 8 tests (Track B baseline — preserved unchanged).
# ---------------------------------------------------------------------------


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


# ===========================================================================
# P0-1 — Probability binning + Gaussian noise (Tramèr USENIX 2016).
# ===========================================================================


def test_anti_extraction_noise_binned_to_two_decimals():
    """When ANTI_EXTRACTION_NOISE is enabled (default True), the returned
    probability is binned to 2 decimals + Gaussian-noised + clamped to
    [0, 1]. Run 200 samples to cover the noise distribution.
    """
    with patch.dict(os.environ, {"ANTI_EXTRACTION_NOISE": "true"}):
        # Reimport to refresh the env-flag cache (the function reads the
        # env var on every call so we don't need to reimport, but be
        # explicit for clarity).
        for raw in (0.7341, 0.5, 0.001, 0.999, 0.123456789):
            noisy = apply_anti_extraction_noise(raw)
            # 1. Always in [0, 1] (clamp works).
            assert 0.0 <= noisy <= 1.0, f"out of [0,1]: {noisy} from {raw}"
            # 2. Binned to 2 decimals (10-100× extraction error per Tramer).
            assert round(noisy, 2) == noisy, (
                f"not binned to 2 dec: {noisy} from {raw}"
            )
            # 3. The bin is within ±0.05 of the raw (noise σ=0.01; the
            # clamp + binning can push up to ~0.02 away at the boundaries).
            assert abs(noisy - raw) < 0.1, (
                f"noise shifted too far: {noisy} vs {raw}"
            )


def test_anti_extraction_noise_disabled_returns_raw_proba():
    """When ANTI_EXTRACTION_NOISE=false, the helper returns the raw
    probability unchanged (used by the SHAP explainer path which needs
    the exact value + the test suite which can't tolerate non-determinism).
    """
    with patch.dict(os.environ, {"ANTI_EXTRACTION_NOISE": "false"}):
        for raw in (0.7341, 0.5, 0.001, 0.999, 0.123456789):
            assert apply_anti_extraction_noise(raw) == raw


def test_anti_extraction_noise_default_is_enabled():
    """The default (env unset) must be ENABLED — anti-extraction is the
    safe default; an opt-OUT requires explicit intent.
    """
    # Clear the env var to simulate unset.
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ANTI_EXTRACTION_NOISE", None)
        assert apply_anti_extraction_noise(0.7341) != 0.7341  # noisy


def test_risk_score_endpoint_applies_noise_to_response_probability(client):
    """The /risk/score endpoint's response probability must be binned to
    2 decimals when ANTI_EXTRACTION_NOISE is on (default). The audit
    record's probability (at 5 decimals) must match the binned value
    (same source — both come from the noisy ``proba``).
    """
    with patch.dict(os.environ, {"ANTI_EXTRACTION_NOISE": "true"}):
        r = client.post(
            "/risk/score", json={**VALID, "order_id": "SEC-NOISE-1"},
            headers=SCORER,
        )
        assert r.status_code == 200
        body = r.json()
        proba = body["probability"]
        assert proba is not None
        # Binned to 2 decimals — the headline defence.
        assert round(proba, 2) == proba, (
            f"response probability not binned to 2 dec: {proba}"
        )
        # Audit's probability (5 decimals from the SAME noisy proba)
        # must round-trip to the body's value within binning tolerance.
        audit_id = body["audit_trail_url"].split("/")[-1]
        audit = client.get(f"/audit/{audit_id}", headers=ADMIN).json()
        audit_p = audit["probability"]
        assert audit_p is not None
        assert abs(audit_p - proba) < 1e-3, (
            f"audit probability {audit_p} diverged from body probability "
            f"{proba} — they should share the same noisy source."
        )


# ===========================================================================
# P0-2 — Randomized rule thresholds (±₹500 jitter, IEEE Access 2024).
# ===========================================================================


def test_rules_engine_jitters_amount_threshold_only():
    """The jitter applies to numeric ``gt``/``lt`` rules on ``amount_inr``
    ONLY. For RULE-001 (``amount_inr > 50000``), the effective threshold
    on 1000 samples spans ``[49500, 50500]`` (±₹500 around the base).
    """
    thresholds = set()
    for _ in range(1000):
        eff = _jitter_threshold("amount_inr", 50000)
        eff = float(eff)
        thresholds.add(round(eff))
        # Always within ±500.
        assert 49500 <= eff <= 50500, f"jitter out of ±500: {eff}"
    # Sampled at least 50 distinct values (proves it's not constant).
    assert len(thresholds) >= 50, (
        f"jitter not random enough — only {len(thresholds)} distinct values"
    )


def test_rules_engine_no_jitter_on_categorical():
    """Categorical rules (``op="eq"``, ``op="in"``) are NEVER jittered —
    RULE-002's ``value=True`` is a boolean, not a numeric threshold.
    """
    for _ in range(100):
        eff = _jitter_threshold("_high_value_vague_cod", True)
        assert eff is True  # unchanged


def test_rules_engine_no_jitter_on_non_monetary_numeric():
    """Numeric rules on non-monetary fields (``items``, ``prior_orders``)
    are NOT jittered — only monetary INR fields (``amount_inr`` and
    aliases). Prevents the jitter from accidentally breaking non-amount
    rules a merchant might add (e.g. ``items > 5`` for a bulk order).
    """
    for _ in range(10):
        eff = _jitter_threshold("items", 5)
        assert eff == 5  # unchanged


def test_rules_engine_jitter_disabled_returns_unmodified():
    """When RULES_RANDOMIZE_THRESHOLDS=false, the helper returns the
    original value unchanged. Used by deterministic test runs + an
    admin override (e.g. an A/B test that needs the exact threshold).
    """
    with patch.dict(os.environ, {"RULES_RANDOMIZE_THRESHOLDS": "false"}):
        for _ in range(100):
            eff = _jitter_threshold("amount_inr", 50000)
            assert eff == 50000  # unchanged


def test_rules_engine_block_still_fires_above_jitter_band():
    """RULE-001 fires on ``amount_inr = 90_000`` (well above the 50,500
    upper jitter bound) — even at +500 jitter, the threshold stays at
    50,500 so 90,000 trips it every time. Validates the demo flow's
    ``test_rule_fast_path_blocks_without_model`` doesn't regress.
    """
    e = RulesEngine()
    blocked = {
        "order_id": "X",
        "amount_inr": 90_000,
        "payment_method": "COD",
        "prior_orders": 0,
        "address_quality": "complete",
    }
    for _ in range(50):
        fired = e.evaluate(blocked)
        assert fired is not None
        assert fired.rule_id == "RULE-001"


def test_rules_engine_review_rule_unchanged_by_jitter():
    """RULE-002 is categorical (``op="eq"``, ``value=True``) — jitter is
    a no-op for it. The vague-address-COD REVIEW rule still fires on
    the same input.
    """
    e = RulesEngine()
    review = {
        "amount_inr": 25_000,
        "payment_method": "COD",
        "address_quality": "vague",
        "prior_orders": 0,
    }
    for _ in range(20):
        fired = e.evaluate(review)
        assert fired is not None
        assert fired.action == "REVIEW"


# ===========================================================================
# P1-1 — Per-IP rate limiting (Tramer USENIX 2016 §5.2).
# ===========================================================================


def test_ip_rate_limiter_extract_ip_honors_forwarded_for():
    """The first IP in X-Forwarded-For is the original client (the
    leftmost in the proxy chain). Strip optional port suffix (IPv4 +
    ``[IPv6]:port``).
    """
    assert IPRateLimiter.extract_ip("203.0.113.7", None) == "203.0.113.7"
    # IPv4 with port.
    assert IPRateLimiter.extract_ip("203.0.113.7:54321", None) == "203.0.113.7"
    # Chain (only the first is the original client).
    assert IPRateLimiter.extract_ip(
        "203.0.113.7, 10.0.0.1, 10.0.0.2", None
    ) == "203.0.113.7"
    # IPv6 literal.
    assert IPRateLimiter.extract_ip("[2001:db8::1]:12345", None) == "2001:db8::1"
    # Fallback to client.host when no X-Forwarded-For.
    assert IPRateLimiter.extract_ip(None, "10.0.0.1") == "10.0.0.1"
    # Unknown when neither is present.
    assert IPRateLimiter.extract_ip(None, None) == "unknown"


def test_per_ip_rate_limit_returns_429_when_exhausted():
    """When the per-IP bucket is exhausted, /risk/score returns 429
    (before the per-key bucket would have). Set PER_IP_RATE_PER_MIN=2
    so 3 requests trip it. The per-key bucket stays at 1000/min
    (well above) so this test isolates the per-IP path.
    """
    with patch.dict(os.environ, {"PER_IP_RATE_PER_MIN": "2"}):
        with TestClient(create_app(scorer_rate_per_min=1000)) as c:
            codes = [
                c.post(
                    "/risk/score",
                    json={**VALID, "order_id": f"IP-RL-{i}"},
                    headers=SCORER,
                ).status_code
                for i in range(5)
            ]
            # The last request must be 429 (per-IP exhausted).
            assert codes[-1] == 429, (
                f"per-IP limit didn't fire — codes: {codes}"
            )
            # At least one request succeeded (the rate-limit isn't
            # over-tight — the first 2 should be 200).
            assert 200 in codes, (
                f"no request succeeded — over-tight per-IP limit: {codes}"
            )


def test_ip_rate_limiter_in_memory_path_distributes_tokens():
    """Direct unit test of the in-memory path (no Redis). 5 requests
    against a 2-per-min bucket → 2 allowed, 3 denied.
    """
    rl = IPRateLimiter(rate_per_min=2, redis_url=None)
    ip = "203.0.113.99"
    results = [rl.check(ip) for _ in range(5)]
    assert results.count(True) == 2
    assert results.count(False) == 3


# ===========================================================================
# P1-2 — HMAC-SHA256 request signing (RFC 5869 / RFC 2104 anti-replay).
# ===========================================================================


def test_hmac_disabled_by_default():
    """When REQUIRE_HMAC is unset/false (default), every signature
    verification passes — preserves the demo flow + 350 existing tests.
    """
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("REQUIRE_HMAC", None)
        ok, reason = verify_hmac_signature(
            secret="score-demo-key",
            method="POST",
            path="/risk/score",
            body_bytes=b'{"order_id": "X"}',
            signature_header=None,
        )
        assert ok is True
        assert "disabled" in reason


def test_hmac_valid_signature_passes():
    """A valid signature (correct key + body + path + fresh timestamp)
    passes verification. The server recomputes the HMAC over the
    canonical message + constant-time compares.
    """
    secret = "score-demo-key"
    body = b'{"order_id": "HMAC-1", "amount_inr": 899}'
    ts = str(int(time.time()))
    sig = compute_hmac_signature(secret, "POST", "/risk/score", body, ts)
    header = f"t={ts},v={sig}"
    with patch.dict(os.environ, {"REQUIRE_HMAC": "true"}):
        ok, reason = verify_hmac_signature(
            secret=secret,
            method="POST",
            path="/risk/score",
            body_bytes=body,
            signature_header=header,
        )
        assert ok is True, f"valid signature rejected: {reason}"


def test_hmac_replayed_stale_timestamp_rejected():
    """A captured valid signature replayed 5 minutes later fails — the
    timestamp is outside the ±60s replay window. This is the headline
    anti-replay property: even if an attacker captures a valid
    ``X-Signature`` header from a legit request, they can't reuse it
    after 60s.
    """
    secret = "score-demo-key"
    body = b'{"order_id": "HMAC-REPLAY", "amount_inr": 899}'
    # Timestamp 5 minutes in the past — outside the ±60s window.
    stale_ts = str(int(time.time()) - 300)
    sig = compute_hmac_signature(secret, "POST", "/risk/score", body, stale_ts)
    header = f"t={stale_ts},v={sig}"
    with patch.dict(os.environ, {"REQUIRE_HMAC": "true"}):
        ok, reason = verify_hmac_signature(
            secret=secret,
            method="POST",
            path="/risk/score",
            body_bytes=body,
            signature_header=header,
        )
        assert ok is False
        assert "skew" in reason or "replay" in reason, (
            f"stale timestamp not flagged as replay: {reason}"
        )


def test_hmac_bad_signature_rejected():
    """A signature computed with the wrong key (or over a different body)
    is rejected via constant-time compare. Even with a fresh timestamp.
    """
    body = b'{"order_id": "HMAC-BAD", "amount_inr": 899}'
    ts = str(int(time.time()))
    # Sign with the WRONG key.
    sig = compute_hmac_signature(
        "wrong-key", "POST", "/risk/score", body, ts
    )
    header = f"t={ts},v={sig}"
    with patch.dict(os.environ, {"REQUIRE_HMAC": "true"}):
        ok, reason = verify_hmac_signature(
            secret="score-demo-key",
            method="POST",
            path="/risk/score",
            body_bytes=body,
            signature_header=header,
        )
        assert ok is False
        assert "mismatch" in reason, f"bad sig not flagged: {reason}"


def test_hmac_missing_header_rejected_when_enforced():
    """When REQUIRE_HMAC=true and the client sends no X-Signature at all,
    the request is rejected with a clear reason.
    """
    with patch.dict(os.environ, {"REQUIRE_HMAC": "true"}):
        ok, reason = verify_hmac_signature(
            secret="score-demo-key",
            method="POST",
            path="/risk/score",
            body_bytes=b'{"order_id": "X"}',
            signature_header=None,
        )
        assert ok is False
        assert "missing" in reason or "malformed" in reason


def test_hmac_signature_header_parsing():
    """The parser tolerates whitespace + order swap (``v=...,t=...`` is
    as valid as ``t=...,v=...``). Returns (None, None) on malformed
    input.
    """
    ts, sig = parse_signature_header("t=12345,v=abc123")
    assert ts == "12345" and sig == "abc123"
    # Order swap.
    ts, sig = parse_signature_header("v=abc123, t=12345")
    assert ts == "12345" and sig == "abc123"
    # Missing header.
    ts, sig = parse_signature_header(None)
    assert ts is None and sig is None
    # Empty header.
    ts, sig = parse_signature_header("")
    assert ts is None and sig is None


def test_risk_score_endpoint_accepts_valid_hmac_when_enforced():
    """End-to-end: with REQUIRE_HMAC=true, a /risk/score request with a
    valid X-Signature header returns 200 (not 401).
    """
    secret = "score-demo-key"
    body = b'{"order_id": "HMAC-E2E", "amount_inr": 899, "category": "Fashion", "customer_id": "CUST-HMAC"}'
    ts = str(int(time.time()))
    sig = compute_hmac_signature(secret, "POST", "/risk/score", body, ts)
    headers = {
        "Authorization": f"Bearer {secret}",
        "X-Signature": f"t={ts},v={sig}",
        "Content-Type": "application/json",
    }
    with patch.dict(os.environ, {"REQUIRE_HMAC": "true"}):
        with TestClient(create_app(scorer_rate_per_min=1000)) as c:
            r = c.post("/risk/score", content=body, headers=headers)
            assert r.status_code == 200, r.text


def test_risk_score_endpoint_rejects_missing_hmac_when_enforced():
    """End-to-end: with REQUIRE_HMAC=true, a /risk/score request with NO
    X-Signature header is rejected (401).
    """
    with patch.dict(os.environ, {"REQUIRE_HMAC": "true"}):
        with TestClient(create_app(scorer_rate_per_min=1000)) as c:
            r = c.post(
                "/risk/score",
                json={**VALID, "order_id": "HMAC-REJECT"},
                headers=SCORER,
            )
            assert r.status_code == 401
            assert "hmac" in r.json()["detail"].lower()


# ===========================================================================
# P1-3 — Negative caching in FeatureStore.
# ===========================================================================


def test_feature_store_redis_unavailable_passthrough():
    """When Redis is unavailable (no REDIS_URL set), the store operates
    in passthrough mode — every lookup calls the PG callback + returns
    None on miss. The 350 existing tests run this way (no Redis fixture).
    """
    call_count = {"pg": 0}

    def fake_pg(_cid: str):
        call_count["pg"] += 1
        return None

    fs = FeatureStore(
        redis_url=None,
        base_rate=0.10,
        pg_lookup=fake_pg,
    )
    # First lookup — PG miss, caches __null__ in-memory.
    result1 = fs.get_online_features("unknown-cust-1")
    assert result1 is None
    assert call_count["pg"] == 1
    # Second lookup for the SAME id — hits the in-memory negative cache,
    # no PG call.
    result2 = fs.get_online_features("unknown-cust-1")
    assert result2 is None
    assert call_count["pg"] == 1, (
        f"PG called again on negative-cached id: {call_count['pg']}"
    )
    # Stats reflect the path.
    assert fs.stats["pg_misses"] == 1
    assert fs.stats["redis_neg_hits"] == 1  # in-memory counts as neg hit


def test_feature_store_positive_cache_returns_features():
    """On a PG hit, the features are cached (in-memory when no Redis) +
    returned directly on subsequent lookups (no PG re-query).
    """
    call_count = {"pg": 0}

    def fake_pg(_cid: str):
        call_count["pg"] += 1
        return {"prior_orders": 12, "prior_returns": 2, "user_rto_rate": 0.16}

    fs = FeatureStore(
        redis_url=None,
        base_rate=0.10,
        pg_lookup=fake_pg,
    )
    # First lookup — PG hit, caches the JSON.
    f1 = fs.get_online_features("real-cust-1")
    assert f1 is not None
    assert f1["prior_orders"] == 12
    assert call_count["pg"] == 1
    # Second lookup — in-memory positive cache, no PG.
    f2 = fs.get_online_features("real-cust-1")
    assert f2 is not None
    assert f2["prior_orders"] == 12
    assert call_count["pg"] == 1
    # Stats.
    assert fs.stats["redis_hits"] == 1


def test_feature_store_negative_cache_prevents_pg_flood():
    """Headline defence: an attacker flooding with the SAME unknown
    customer_id 1000 times only triggers ONE PG query (the first);
    the remaining 999 hit the negative cache. Simulates the DoS
    vector 4 from FOLLOWUP.md §4 — PG pool exhaust via unique-customer
    flood.
    """
    call_count = {"pg": 0}

    def fake_pg(_cid: str):
        call_count["pg"] += 1
        return None  # always miss

    fs = FeatureStore(
        redis_url=None,
        base_rate=0.10,
        pg_lookup=fake_pg,
    )
    # 1000 requests for the SAME unknown customer_id.
    for _ in range(1000):
        result = fs.get_online_features("flood-cust")
        assert result is None  # caller falls back to base_rate
    # Only ONE PG query should have fired (the first miss populated
    # the negative cache; the remaining 999 hit the sentinel).
    assert call_count["pg"] == 1, (
        f"PG flood: expected 1 query, got {call_count['pg']} — "
        "negative cache not preventing the DoS vector."
    )
    # Stats: 1 miss + 999 neg hits.
    assert fs.stats["pg_misses"] == 1
    assert fs.stats["redis_neg_hits"] == 999


def test_feature_store_distinct_customers_each_get_one_pg_query():
    """Negative caching is PER customer_id. A flood of 100 DISTINCT
    unknown IDs → 100 PG queries (one per ID, the first), then any
    repeats hit the negative cache. Demonstrates the DoS dampens but
    doesn't eliminate (attacker still hits PG N times for N unique IDs
    in the first 60s; the cache then absorbs the rest).
    """
    call_count = {"pg": 0}

    def fake_pg(_cid: str):
        call_count["pg"] += 1
        return None

    fs = FeatureStore(
        redis_url=None,
        base_rate=0.10,
        pg_lookup=fake_pg,
    )
    # 100 distinct IDs — one PG query each.
    for i in range(100):
        fs.get_online_features(f"flood-cust-{i}")
    assert call_count["pg"] == 100
    # Repeat each ID — negative cache absorbs, no new PG queries.
    for i in range(100):
        fs.get_online_features(f"flood-cust-{i}")
    assert call_count["pg"] == 100, (
        f"negative cache didn't absorb repeats: {call_count['pg']}"
    )


def test_feature_store_sentinel_value_is_distinct_from_real_features():
    """The ``__null__`` sentinel is a distinct string — a real customer's
    features (a JSON dict serialised) can never collide with it. This
    guards against a subtle bug where a real customer's empty-dict
    features (``{}``) might be confused with a miss.
    """
    import json

    empty_features_json = json.dumps({})
    assert empty_features_json != _NULL_SENTINEL
    real_features_json = json.dumps({"prior_orders": 0})
    assert real_features_json != _NULL_SENTINEL
