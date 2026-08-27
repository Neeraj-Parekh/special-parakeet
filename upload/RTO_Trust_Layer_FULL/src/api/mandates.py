"""Signed spending mandates: the only way an agent may transact within bounds.

Doctrine: agents hold ZERO ambient authority. A merchant backend (admin scope) issues
a short-lived, bounded mandate; agents present it; the server enforces bounds and
escalates any breach deterministically. Agents cannot mint, extend, or widen mandates.

Day 1 Track D (V3 §13 — mandate action-class expansion) adds UPI Circle / delegated-
payments mandates per NPCI OC-201B (8 Oct 2025). UPI Circle mandates are bounded by
the circular's hard caps: ₹5,000/txn, ₹15,000/month, ₹5,000 24-hour cooling, 5-device
cap, 6-month inactivity auto-revoke, BH purpose code tagging, per-txn device_id +
user_id validation by the issuer (this server). Source papers:

- "Addendum to NPCI/UPI/2024-25/OC 201 — Introduction of IoT devices & software on
  UPI Circle" (NPCI/UPI/OC-201B/2025-26, 8 Oct 2025)
- Walia, Gautam, Shrivastava (Khaitan & Co), Lexology, 21 Nov 2025 — interpretive
  analysis of OC-201B
- "SoK: Security of Autonomous LLM Agents in Agentic Commerce" (Mao 2026) — D2
  transaction-authorization dimension: design mandates as scoped, task-bound,
  attenuating credentials rather than standing broad authority.

Backward-compat: ``cod_order`` mandates (the original HMAC system agent 1-b built)
continue to work with the same 3-argument signature. UPI Circle mandates opt in via
``mandate_type="upi_circle_delegation"`` and the new keyword-only fields.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Any


# ---------------------------------------------------------------------------
# In-memory UPI Circle cumulative counters (Track E, Day 2 → Postgres).
# Keyed by mandate ``sub`` (the salted customer_ref digest prefix that uniquely
# identifies a mandate). These track:
#   * monthly cumulative spend per mandate (₹15,000 cap per OC-201B)
#   * 24-hour rolling txn log per mandate (₹5,000 cooling per OC-201B)
#   * last-activity timestamp per mandate (6-month auto-revoke per OC-201B)
# A real Postgres-backed implementation (Track E Day 2) would persist these in a
# ``mandate_counters`` table keyed by mandate_id, with TTL prune jobs for the 24h
# cooling window. The in-memory version is fine for demo + single-process test runs.
# ---------------------------------------------------------------------------
_cumulative_monthly: dict[str, float] = {}
_cumulative_24h: dict[str, list[tuple[float, float]]] = {}
_last_activity: dict[str, float] = {}


def _secret() -> bytes:
    return os.environ.get("RTO_MANDATE_SECRET", "dev-only-secret").encode()


def self_salt() -> str:
    return os.environ.get("RTO_AUDIT_SALT", "local-demo-salt")


def issue_mandate(
    customer_ref: str,
    max_amount_inr: float,
    ttl_seconds: int,
    scope: str = "cod_order",
    *,
    mandate_type: str | None = None,
    device_ids: list[str] | None = None,
    user_id: str | None = None,
    bh_purpose_code: str | None = None,
    max_per_txn_inr: float | None = None,
    max_per_month_inr: float | None = None,
    cooling_24h_inr: float | None = None,
    inactivity_revoke_days: int | None = None,
) -> str:
    """Mint a bounded, HMAC-signed mandate.

    Backward-compat: 3-positional-arg form (``customer_ref, max_amount_inr,
    ttl_seconds``) still issues a ``cod_order`` mandate — the original system
    agent 1-b built. No existing caller breaks.

    New keyword-only fields opt in to the UPI Circle / delegated-payments mandate
    type (NPCI OC-201B). When ``mandate_type="upi_circle_delegation"`` (or
    ``scope="upi_circle"`` for short), the payload carries the circular's hard
    caps + the device/user identity chain the issuer must validate per txn.
    """
    payload: dict[str, Any] = {
        "sub": hashlib.sha256(
            f"{customer_ref}:{self_salt()}".encode()
        ).hexdigest()[:16],
        "scope": scope,
        "max_amount_inr": round(float(max_amount_inr), 2),
        "exp": int(time.time()) + int(ttl_seconds),
        "iat": int(time.time()),
    }

    # Normalize mandate_type: if not provided, infer from scope (back-compat).
    if mandate_type is None:
        mandate_type = "upi_circle_delegation" if scope == "upi_circle" else "cod_order"
    payload["mandate_type"] = mandate_type

    if mandate_type == "upi_circle_delegation":
        # OC-201B: max 5 IoT devices/software per user. Reject at mint time
        # rather than at verify time — fail-loud doctrine (V3 §4 principle 3).
        if device_ids is None:
            device_ids = []
        if len(device_ids) > 5:
            raise ValueError("OC-201B: max 5 devices per delegation")
        payload["device_ids"] = list(device_ids)
        payload["user_id"] = user_id or ""
        # BH purpose code: "90" = commercial payment per NPCI BH list. The
        # ``bh_purpose_code_reconciliation`` skill surfaces this for raw-file
        # audit trail tagging (Track E Day 2 will persist + emit the
        # secondary-details raw file per OC-201B §3.8).
        payload["bh_purpose_code"] = bh_purpose_code or "90"
        payload["max_per_txn_inr"] = float(
            max_per_txn_inr if max_per_txn_inr is not None else 5000.0
        )
        payload["max_per_month_inr"] = float(
            max_per_month_inr if max_per_month_inr is not None else 15000.0
        )
        payload["cooling_24h_inr"] = float(
            cooling_24h_inr if cooling_24h_inr is not None else 5000.0
        )
        payload["inactivity_revoke_days"] = int(
            inactivity_revoke_days if inactivity_revoke_days is not None else 180
        )

    body = urlsafe_b64encode(
        json.dumps(payload, sort_keys=True).encode()
    ).decode().rstrip("=")
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


class MandateVerdict:
    """Outcome categories the mandate verifier can return.

    Backward-compat: VALID / TAMPERED / EXPIRED / BREACH are the original four
    (agent 1-b). REVIEW is new (Day 1 Track D) — UPI Circle 24h cooling period
    requires human approval but is not a hard REJECT (the txn is allowed in
    principle, just gated). REVIEW routes to the case queue like other REVIEW
    decisions; the difference is the audit carries ``verdict_reason=
    "cooling_period_active"`` for compliance traceability.
    """

    VALID = "valid"
    TAMPERED = "tampered"
    EXPIRED = "expired"
    BREACH = "breach"
    REVIEW = "review"


def verify_mandate(
    token: str | None,
    amount_inr: float,
    *,
    device_id: str | None = None,
    user_id: str | None = None,
) -> tuple[str, dict]:
    """Verify a mandate token + the per-txn request context.

    Returns ``(verdict, payload)`` where ``payload`` is the decoded mandate
    body (with the new ``verdict_reason`` key explaining non-VALID outcomes)
    or a minimal ``{"verdict_reason": ...}`` dict for TAMPERED tokens.

    Backward-compat: the original 2-arg signature ``verify_mandate(token,
    amount_inr)`` still works for ``cod_order`` mandates — ``device_id`` and
    ``user_id`` default to ``None`` and are simply not consulted.

    Enforcement precedence (consumer = ``src/api/routes.py``):
      * ``TAMPERED`` with header present → REJECT (mandate_invalid)
      * ``EXPIRED`` with header present   → REJECT (mandate_invalid) — covers
        both TTL expiry and OC-201B 6-month inactivity auto-revoke
      * ``BREACH``                          → REJECT (mandate_breach) — covers
        per-txn cap, monthly cap, device_id_not_allowed, user_id_mismatch
      * ``REVIEW``                          → REVIEW (mandate_review_required)
        — currently only the OC-201B 24h cooling-period gate
      * ``VALID``                           → fall through to the cost-optimizer
        decision path (Track C's optimal_decision / Bahnsen BMR)
    """
    empty: dict = {}
    if not token:
        return MandateVerdict.TAMPERED, {"verdict_reason": "missing_mandate"}
    try:
        body, sig = token.rsplit(".", 1)
        expected = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return MandateVerdict.TAMPERED, {"verdict_reason": "hmac_signature_mismatch"}
        pad = "=" * (-len(body) % 4)
        payload = json.loads(urlsafe_b64decode(body + pad))

        if payload.get("exp", 0) < time.time():
            return MandateVerdict.EXPIRED, {**payload, "verdict_reason": "expired_ttl"}

        mtype = payload.get("mandate_type", "cod_order")
        # Use ``sub`` (the salted customer_ref digest) as the per-mandate key
        # for cumulative counters. Stable across verifies for the same mandate.
        mid = payload.get("sub", "")
        now = time.time()

        if mtype == "upi_circle_delegation":
            # 1. OC-201B §3.8: 6-month inactivity auto-revoke. Baseline last
            #    activity is the mandate's iat (so a freshly-issued mandate
            #    passes); once a txn lands, _last_activity is updated.
            inactivity_days = int(payload.get("inactivity_revoke_days", 180))
            last_act = _last_activity.get(mid, payload.get("iat", now))
            if now - last_act > inactivity_days * 86400:
                return MandateVerdict.EXPIRED, {
                    **payload,
                    "verdict_reason": "inactivity_auto_revoke",
                }

            # 2. OC-201B: per-txn cap (default ₹5,000).
            max_per_txn = float(payload.get("max_per_txn_inr", 5000.0))
            if float(amount_inr) > max_per_txn:
                return MandateVerdict.BREACH, {
                    **payload,
                    "verdict_reason": "per_txn_cap_exceeded",
                }

            # 3. OC-201B: monthly cumulative cap (default ₹15,000).
            max_per_month = float(payload.get("max_per_month_inr", 15000.0))
            projected = _cumulative_monthly.get(mid, 0.0) + float(amount_inr)
            if projected > max_per_month:
                return MandateVerdict.BREACH, {
                    **payload,
                    "verdict_reason": "monthly_cap_exceeded",
                }

            # 4. OC-201B §3.7 (Issuer Bank duty): validate device_id before
            #    debiting. If the mandate enumerates device_ids and the
            #    request carries an X-Device-Id, it must be in the list.
            allowed_devices = payload.get("device_ids", [])
            if device_id is not None and allowed_devices and device_id not in allowed_devices:
                return MandateVerdict.BREACH, {
                    **payload,
                    "verdict_reason": "device_id_not_allowed",
                }

            # 5. OC-201B §3.3 (Secondary App/PSP duty): user_id captured at
            #    registration and validated per txn.
            expected_uid = payload.get("user_id", "")
            if user_id is not None and expected_uid and user_id != expected_uid:
                return MandateVerdict.BREACH, {
                    **payload,
                    "verdict_reason": "user_id_mismatch",
                }

            # 6. OC-201B: 24h cooling period. If a prior txn in the last 24h
            #    was >= cooling_24h_inr (default ₹5,000), require human
            #    approval — REVIEW, not REJECT. The txn is permitted in
            #    principle; the cooling gate is a fraud-control circuit
            #    breaker, not a hard cap.
            cooling_24h = float(payload.get("cooling_24h_inr", 5000.0))
            recent = _cumulative_24h.get(mid, [])
            # Prune txns older than 24h (rolling window).
            recent = [(ts, amt) for ts, amt in recent if now - ts < 86400]
            _cumulative_24h[mid] = recent
            for _ts, amt in recent:
                if amt >= cooling_24h:
                    return MandateVerdict.REVIEW, {
                        **payload,
                        "verdict_reason": "cooling_period_active",
                    }

            # All checks passed — record the txn for cumulative tracking.
            _cumulative_monthly[mid] = _cumulative_monthly.get(mid, 0.0) + float(amount_inr)
            _cumulative_24h.setdefault(mid, []).append((now, float(amount_inr)))
            _last_activity[mid] = now
            return MandateVerdict.VALID, {**payload, "verdict_reason": "ok"}

        # --- cod_order (legacy) path ---
        if float(amount_inr) > float(payload.get("max_amount_inr", 0)):
            return MandateVerdict.BREACH, {**payload, "verdict_reason": "amount_exceeds_max"}
        return MandateVerdict.VALID, {**payload, "verdict_reason": "ok"}
    except Exception:
        return MandateVerdict.TAMPERED, {"verdict_reason": "decode_error"}


def decode_mandate(token: str) -> dict:
    """Decode a mandate token's body without verifying the HMAC.

    Convenience helper for tests + dashboards that want to *inspect* a
    mandate's payload (e.g. read the ``mandate_type``, ``bh_purpose_code``,
    or ``device_ids``) without going through ``verify_mandate``'s
    enforcement path. **Never** use this for authorization decisions —
    always go through ``verify_mandate`` for those.
    """
    body = token.rsplit(".", 1)[0]
    pad = "=" * (-len(body) % 4)
    return json.loads(urlsafe_b64decode(body + pad))


def reset_upi_counters() -> None:
    """Test helper — wipe the in-memory UPI Circle cumulative counters.

    Call between tests so the monthly/cooling/inactivity state from one test
    doesn't bleed into the next. Track E Day 2 will replace this with a
    Postgres truncate + transactional rollback.
    """
    _cumulative_monthly.clear()
    _cumulative_24h.clear()
    _last_activity.clear()


def simulate_inactivity(token: str, days: int) -> None:
    """Test helper — backdate the per-mandate ``_last_activity`` by ``days``.

    Used to drive the OC-201B 6-month inactivity auto-revoke path without
    having to actually sleep 180 days. Mutates module state for the mandate
    identified by the token's ``sub``. No-op if the token is invalid.
    """
    try:
        payload = decode_mandate(token)
    except Exception:
        return
    mid = payload.get("sub", "")
    if not mid:
        return
    _last_activity[mid] = time.time() - (int(days) * 86400)
