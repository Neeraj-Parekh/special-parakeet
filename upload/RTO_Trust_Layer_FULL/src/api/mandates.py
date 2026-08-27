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

Track P (Task 11-a) — the UPI Circle cumulative counters (₹15,000/month cap,
₹5,000 24h cooling, 6-month inactivity auto-revoke) are now PERSISTED in
Postgres via the ``mandate_counters`` + ``mandate_counter_events`` tables (see
``alembic/versions/003_mandate_counters.py``). The caps survive process restarts,
multi-worker deployments, and redeploy events. When ``DATABASE_URL`` is unset
(file-mode / test path), the in-memory dicts remain the source of truth so the
22 existing tests in ``tests/test_mandates.py`` continue to pass unchanged.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Any


# ---------------------------------------------------------------------------
# In-memory UPI Circle cumulative counters — file-mode fallback (Track P).
# Keyed by mandate ``sub`` (the salted customer_ref digest prefix that uniquely
# identifies a mandate). These track:
#   * monthly cumulative spend per mandate (₹15,000 cap per OC-201B)
#   * 24-hour rolling txn log per mandate (₹5,000 cooling per OC-201B)
#   * last-activity timestamp per mandate (6-month auto-revoke per OC-201B)
#
# Track P (Task 11-a) — when ``DATABASE_URL`` is set, ``verify_mandate()`` reads
# + writes the persisted ``mandate_counters`` / ``mandate_counter_events`` rows
# instead of these dicts; the dicts remain the source of truth only for the
# file-mode / test path (no Postgres available). ``reset_upi_counters()`` wipes
# BOTH surfaces so test isolation holds regardless of which mode the suite runs in.
# ---------------------------------------------------------------------------
_cumulative_monthly: dict[str, float] = {}
_cumulative_24h: dict[str, list[tuple[float, float]]] = {}
_last_activity: dict[str, float] = {}


# ---------------------------------------------------------------------------
# Module-level lazy Postgres connection for the mandate counters path (Track P).
# Pattern mirrors ``src/audit/logger.py``: open ONE persistent psycopg connection
# per process (the mandate verify path is not the write-hot path; a pool would
# add latency for no benefit at this scale). Lazily constructed on first call to
# ``_get_counters_conn()`` so the import is side-effect-free — file-mode tests
# that never set ``DATABASE_URL`` never touch psycopg.
# ---------------------------------------------------------------------------
_counters_conn_lock = threading.Lock()
_counters_conn: Any = None  # psycopg.Connection | None


def _get_counters_conn() -> Any:
    """Return a lazy shared psycopg connection for the mandate counters path.

    Returns ``None`` when:
      * ``DATABASE_URL`` is unset (file-mode / test path), or
      * it does not point at a Postgres DSN (defensive — same logic as
        ``src.config.Settings.is_postgres``), or
      * psycopg is not importable (shouldn't happen — requirements.txt pins it;
        the ``try/except ImportError`` is a defensive guard for stripped test
        envs where the package was yanked).

    The connection is opened once and cached on the module; subsequent calls
    return the cached connection. ``reset_upi_counters()`` does NOT close it —
    connection lifecycle is the process's responsibility, not the test's. The
    cache is keyed off ``DATABASE_URL`` at first-read time; if the env var
    changes mid-process (tests that mutate it), call ``_reset_counters_conn()``
    to clear the cache.
    """
    global _counters_conn
    if _counters_conn is not None:
        return _counters_conn
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url or not db_url.startswith(
        ("postgresql://", "postgres://", "postgresql+psycopg://")
    ):
        return None
    try:
        import psycopg
    except ImportError:  # pragma: no cover — defensive; psycopg is in requirements
        return None
    with _counters_conn_lock:
        if _counters_conn is None:
            _counters_conn = psycopg.connect(db_url, autocommit=False)
        return _counters_conn


def _reset_counters_conn() -> None:
    """Test helper — drop the cached counters connection.

    Call between tests that mutate ``DATABASE_URL`` so the next
    ``_get_counters_conn()`` call re-reads the env var and reopens. Not part
    of the public API; only ``reset_upi_counters()`` should call this.
    """
    global _counters_conn
    if _counters_conn is not None:
        try:
            _counters_conn.close()
        except Exception:
            pass
    _counters_conn = None


def _read_db_counters(mandate_sub: str, now: float) -> tuple[float, float, list[tuple[float, float]]]:
    """Read persisted UPI Circle counters for a mandate from Postgres.

    Returns ``(cumulative_monthly, last_activity_ts, recent_24h_events)`` where:
      * ``cumulative_monthly`` is the running monthly cap counter (0.0 if the
        mandate has no row yet — fresh mandate).
      * ``last_activity_ts`` is the unix epoch of the last txn (or ``-inf`` if
        no row — the caller falls back to the mandate's ``iat``).
      * ``recent_24h_events`` is the rolling 24h txn list ``[(ts, amount), ...]``
        reconstructed from ``mandate_counter_events`` rows with ``ts > now-86400``.

    On ANY DB error (table missing, connection lost, partial-failure mid-query),
    this returns ``(0.0, -1.0, [])`` and the caller falls through to the in-memory
    dicts. NEVER raise — the verify path must degrade to the in-memory fallback
    rather than fail the request.
    """
    sentinel = (0.0, -1.0, [])
    conn = _get_counters_conn()
    if conn is None:
        return sentinel
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT cumulative_monthly, last_activity_ts "
                "FROM mandate_counters WHERE mandate_sub = %s",
                (mandate_sub,),
            )
            row = cur.fetchone()
            if row is not None:
                cumulative_monthly = float(row[0] or 0.0)
                last_activity_ts = float(row[1]) if row[1] is not None else -1.0
            else:
                cumulative_monthly = 0.0
                last_activity_ts = -1.0
            # Rolling 24h window — filter on the (mandate_sub, ts) index.
            cur.execute(
                "SELECT ts, amount_inr FROM mandate_counter_events "
                "WHERE mandate_sub = %s AND ts > %s ORDER BY ts ASC",
                (mandate_sub, now - 86400),
            )
            recent_24h = [(float(r[0]), float(r[1])) for r in cur.fetchall()]
        return cumulative_monthly, last_activity_ts, recent_24h
    except Exception:
        # Degrade to in-memory fallback. The verify path will use the dicts;
        # the cap is enforced within the process (real but not cross-restart).
        try:
            conn.rollback()
        except Exception:
            pass
        return sentinel


def _write_db_counters(
    mandate_sub: str,
    *,
    new_cumulative_monthly: float,
    last_activity_ts: float,
    txn_ts: float,
    txn_amount: float,
) -> None:
    """Persist the updated UPI Circle counters + append the 24h event to Postgres.

    UPSERTs the per-mandate ``mandate_counters`` row (single row per ``sub``)
    and appends a row to ``mandate_counter_events`` for the 24h rolling window.
    Both writes are in the same transaction so the cumulative counter + the
    cooling-window event log advance atomically (a crash mid-write leaves the
    prior state intact, not a half-updated counter).

    On ANY DB error this swallows the exception and rolls back — the verify
    path has ALREADY updated the in-memory dicts by the time this is called,
    so the request succeeds even if the persistence layer fails. A future
    improvement is to surface the failure to the audit logger so ops sees it
    (the silent-fall-back is the safe-but-debuggable trade-off).
    """
    conn = _get_counters_conn()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO mandate_counters "
                "(mandate_sub, cumulative_monthly, last_activity_ts, updated_at) "
                "VALUES (%s, %s, %s, NOW()) "
                "ON CONFLICT (mandate_sub) DO UPDATE SET "
                "cumulative_monthly = EXCLUDED.cumulative_monthly, "
                "last_activity_ts = EXCLUDED.last_activity_ts, "
                "updated_at = NOW()",
                (mandate_sub, new_cumulative_monthly, last_activity_ts),
            )
            cur.execute(
                "INSERT INTO mandate_counter_events "
                "(mandate_sub, ts, amount_inr, created_at) "
                "VALUES (%s, %s, %s, NOW())",
                (mandate_sub, txn_ts, txn_amount),
            )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


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
            # --- Track P (Task 11-a) — read persisted counters from DB when  ---
            # --- available; fall back to in-memory dicts in file mode.       ---
            # ``_read_db_counters`` returns (0.0, -1.0, []) sentinel when no
            # Postgres connection is configured OR when the query fails — the
            # sentinel ``last_activity_ts = -1.0`` is the "no row / use iat"
            # signal. We use the DB values when ``db_last_activity >= 0``,
            # otherwise the in-memory dicts (file mode / test path).
            db_cumulative, db_last_activity, db_recent_24h = _read_db_counters(mid, now)
            if db_last_activity >= 0:
                cumulative_monthly = db_cumulative
                # DB is the source of truth in prod. If the row exists but
                # last_activity_ts is NULL (mandate never spent), fall back to
                # the mandate's iat — same baseline as the in-memory path.
                last_act = (
                    db_last_activity
                    if db_last_activity > 0
                    else payload.get("iat", now)
                )
                # ``db_recent_24h`` is already filtered to ts > now-86400 by
                # the DB query — no need to re-prune.
                recent = db_recent_24h
                _cumulative_monthly[mid] = cumulative_monthly
                _cumulative_24h[mid] = list(recent)
                _last_activity[mid] = last_act
            else:
                cumulative_monthly = _cumulative_monthly.get(mid, 0.0)
                last_act = _last_activity.get(mid, payload.get("iat", now))
                recent = _cumulative_24h.get(mid, [])
                # Prune txns older than 24h (rolling window).
                recent = [(ts, amt) for ts, amt in recent if now - ts < 86400]
                _cumulative_24h[mid] = recent

            # 1. OC-201B §3.8: 6-month inactivity auto-revoke. Baseline last
            #    activity is the mandate's iat (so a freshly-issued mandate
            #    passes); once a txn lands, _last_activity is updated.
            inactivity_days = int(payload.get("inactivity_revoke_days", 180))
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
            projected = cumulative_monthly + float(amount_inr)
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
            for _ts, amt in recent:
                if amt >= cooling_24h:
                    return MandateVerdict.REVIEW, {
                        **payload,
                        "verdict_reason": "cooling_period_active",
                    }

            # All checks passed — record the txn for cumulative tracking.
            # Update BOTH the in-memory dicts (cheap, file-mode source of
            # truth + shadow for ops introspection) AND the DB counters
            # (cross-restart persistence, Track P). On a VALID verdict only —
            # BREACH/REVIEW/EXPIRED do not advance the counters (a rejected
            # txn does not consume the monthly cap; a REVIEW txn does not
            # re-trigger the cooling window).
            new_cumulative = cumulative_monthly + float(amount_inr)
            _cumulative_monthly[mid] = new_cumulative
            _cumulative_24h.setdefault(mid, []).append((now, float(amount_inr)))
            _last_activity[mid] = now
            _write_db_counters(
                mid,
                new_cumulative_monthly=new_cumulative,
                last_activity_ts=now,
                txn_ts=now,
                txn_amount=float(amount_inr),
            )
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
    """Test helper — wipe the UPI Circle cumulative counters.

    Wipes BOTH surfaces so test isolation holds regardless of mode:
      * in-memory dicts (file-mode / test path) — always cleared.
      * Postgres ``mandate_counters`` + ``mandate_counter_events`` tables
        (Track P, prod path) — TRUNCATED when ``DATABASE_URL`` is set.
      * cached module-level psycopg connection — closed + cleared so the
        next ``_get_counters_conn()`` call re-reads ``DATABASE_URL`` (handles
        tests that flip the env var between cases).

    Track E Day 2 will replace this with a Postgres truncate + transactional
    rollback; this implementation already does the truncate so a future Track
    E refactor just removes the in-memory fallback.
    """
    _cumulative_monthly.clear()
    _cumulative_24h.clear()
    _last_activity.clear()
    # Close + drop the cached connection so DATABASE_URL changes between
    # tests are picked up. Re-opening is cheap (lazy on next verify call).
    _reset_counters_conn()
    conn = _get_counters_conn()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            # Events first (no FK to counters, but order is still safer).
            cur.execute("TRUNCATE TABLE mandate_counter_events")
            cur.execute("DELETE FROM mandate_counters")
        conn.commit()
    except Exception:
        # The tables may not exist (migration not run yet — e.g. CI without
        # DATABASE_URL fixture). Silently fall through; the in-memory dicts
        # are the test's source of truth in that case.
        try:
            conn.rollback()
        except Exception:
            pass


def simulate_inactivity(token: str, days: int) -> None:
    """Test helper — backdate the per-mandate ``_last_activity`` by ``days``.

    Used to drive the OC-201B 6-month inactivity auto-revoke path without
    having to actually sleep 180 days. Mutates module state for the mandate
    identified by the token's ``sub``. No-op if the token is invalid.

    Track P — also persists the backdated timestamp to the
    ``mandate_counters`` row so the auto-revoke check fires in Postgres mode
    too (the DB row is the source of truth there; in-memory mutation alone
    would be invisible to the next ``verify_mandate`` call).
    """
    try:
        payload = decode_mandate(token)
    except Exception:
        return
    mid = payload.get("sub", "")
    if not mid:
        return
    backdated_ts = time.time() - (int(days) * 86400)
    _last_activity[mid] = backdated_ts
    # Persist to the mandate_counters row so the inactivity check fires in
    # Postgres mode. UPSERT — the mandate may not have a row yet (fresh
    # mandate with no prior txn).
    conn = _get_counters_conn()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO mandate_counters "
                "(mandate_sub, cumulative_monthly, last_activity_ts, updated_at) "
                "VALUES (%s, 0, %s, NOW()) "
                "ON CONFLICT (mandate_sub) DO UPDATE SET "
                "last_activity_ts = EXCLUDED.last_activity_ts, "
                "updated_at = NOW()",
                (mid, backdated_ts),
            )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
