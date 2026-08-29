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
from collections.abc import MutableMapping
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
#
# 15-b (Subagent 15-b — DO BADLY #3 cross-process state) — the prior dicts
# lost state across process restarts (every redeploy reset the cumulative
# monthly cap to 0, blowing the ₹15k ceiling; the cooling window + the
# inactivity timestamp also reset). The fix: ``_FileState`` wraps the 3
# sub-dicts (cumulative_monthly, cumulative_24h, last_activity) as ONE
# combined JSON file at ``$RTO_STATE_DIR/mandate_counters_state.json``
# (default ``out/``). The persist is throttled (max one disk write per
# 5 seconds) to avoid I/O thrash under burst traffic; ``force=True``
# bypasses the throttle for explicit flushes (test isolation + the
# ``reset_upi_counters`` clear). The load happens at ``_FileState``
# construction (module import time). The DB path (C8/C9/C10 fixes from
# 14-b) is UNAFFECTED — when ``DATABASE_URL`` is set, the
# ``mandate_counters`` / ``mandate_counter_events`` Postgres tables are
# the authoritative store; the file is only the file-mode fallback.
# ---------------------------------------------------------------------------
class _FileState:
    """File-backed combined dict-of-dicts for the mandate counter state.

    Three sub-dicts (``cumulative_monthly``, ``cumulative_24h``,
    ``last_activity``) are stored under ONE JSON file so a single atomic
    write captures a consistent snapshot of all mandate counters. The
    file path is configurable via the ``RTO_STATE_DIR`` env var
    (default ``out/``); the file name is passed at construction (e.g.
    ``mandate_counters_state.json``).

    Mutating operations on the sub-dicts trigger ``_persist_to_disk()``,
    which is throttled to ``max one disk write per 5 seconds`` to avoid
    I/O thrash under burst traffic. ``force=True`` bypasses the
    throttle for explicit flushes (test isolation, ``reset_upi_counters``,
    process shutdown hooks). The brief window during the 5-second I/O
    gap is the documented trade-off: if the process dies within 5 sec
    of the last persist, the in-memory state since the last persist is
    lost. Acceptable per the spec — the DB path is the authoritative
    production store; this file is only the file-mode / dev / test
    fallback.

    Atomic write (``tmp + os.replace``) so a crash mid-write never
    leaves a corrupt file. Best-effort — a write failure (read-only
    FS, full disk, permissions) degrades to "no persistence across
    restarts" which is the prior (pre-15-b) behaviour.
    """

    _SCHEMA: tuple[str, ...] = (
        "cumulative_monthly", "cumulative_24h", "last_activity",
    )
    _THROTTLE_SECONDS: float = 5.0

    def __init__(self, file_name: str) -> None:
        self._file_name = file_name
        self._data: dict[str, dict[str, Any]] = {k: {} for k in self._SCHEMA}
        self._lock = threading.Lock()
        self._last_persist: float = 0.0
        self._load_from_disk()

    def _persist_to_disk(self, *, force: bool = False) -> None:
        """Dump the combined state to JSON. Throttled (max once per
        5 sec) unless ``force=True``. Atomic write (``tmp + os.replace``)
        so a crash mid-write never leaves a corrupt file. Best-effort —
        a write failure degrades to "no persistence across restarts"
        (the prior behaviour).
        """
        now = time.time()
        if not force and now - self._last_persist < self._THROTTLE_SECONDS:
            return
        self._last_persist = now
        state_dir = (os.environ.get("RTO_STATE_DIR") or "out").strip() or "out"
        path = os.path.join(state_dir, self._file_name)
        try:
            os.makedirs(state_dir, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self._data, f)
            os.replace(tmp, path)
        except Exception:
            # File persistence is best-effort — the in-memory dict is
            # still the source of truth for the current process. A
            # write failure (read-only FS, full disk, permissions)
            # degrades to "no persistence across restarts" which is the
            # prior (pre-15-b) behaviour.
            pass

    def _load_from_disk(self) -> None:
        """Populate the sub-dicts from the JSON file if it exists.

        Best-effort — a read failure (file missing, corrupt JSON, read
        error) silently starts with empty sub-dicts (the prior
        behaviour; the cap is enforced within the current process
        regardless).
        """
        state_dir = (os.environ.get("RTO_STATE_DIR") or "out").strip() or "out"
        path = os.path.join(state_dir, self._file_name)
        try:
            with open(path, "r") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                for k in self._SCHEMA:
                    v = loaded.get(k)
                    if isinstance(v, dict):
                        self._data[k] = v
        except FileNotFoundError:
            pass  # fresh state — start empty (the common case at first boot)
        except Exception:
            # Corrupt JSON or read error — start empty (don't crash
            # the process at import time; the file can be repaired
            # later). The in-memory dicts remain the source of truth.
            pass

    def sub(self, key: str) -> "_SubStateView":
        """Return a ``MutableMapping`` view over a single sub-dict.
        Mutations trigger ``_persist_to_disk()`` (throttled)."""
        if key not in self._data:
            raise KeyError(key)
        return _SubStateView(self, key)


class _SubStateView(MutableMapping):
    """``MutableMapping`` view over a single sub-dict of a ``_FileState``.

    All mutating operations (``__setitem__``, ``__delitem__``,
    ``setdefault``, ``clear``, ``pop``, ``update``) trigger the parent
    ``_FileState._persist_to_disk()`` so changes are persisted (throttled).
    Read operations (``__getitem__``, ``__contains__``, ``get``,
    ``__iter__``, ``__len__``) do NOT trigger persist (they're cheap
    + don't change the on-disk state).

    ``setdefault`` is overridden so the persist fires when a NEW key is
    inserted (the default ``MutableMapping.setdefault`` calls
    ``__setitem__`` if missing — which would fire persist — but the
    returned object might then be mutated in place (e.g.
    ``d.setdefault(k, []).append(v)``) WITHOUT a subsequent
    ``__setitem__`` call, so the persist wouldn't capture the in-place
    mutation. The ``verify_mandate`` path was refactored to use a pure
    ``__setitem__`` instead of ``setdefault+append`` to ensure the
    persist fires after the in-place mutation — see line ~718 below.)
    """

    __slots__ = ("_parent", "_key")

    def __init__(self, parent: "_FileState", key: str) -> None:
        self._parent = parent
        self._key = key

    def _sub(self) -> dict[str, Any]:
        return self._parent._data[self._key]

    def __getitem__(self, k):
        return self._sub()[k]

    def __setitem__(self, k, v) -> None:
        with self._parent._lock:
            self._sub()[k] = v
            self._parent._persist_to_disk()

    def __delitem__(self, k) -> None:
        with self._parent._lock:
            del self._sub()[k]
            self._parent._persist_to_disk()

    def __iter__(self):
        return iter(self._sub())

    def __len__(self) -> int:
        return len(self._sub())

    def __contains__(self, k) -> bool:
        return k in self._sub()

    def get(self, k, default=None):
        return self._sub().get(k, default)

    def setdefault(self, k, default=None):  # type: ignore[override]
        with self._parent._lock:
            sub = self._sub()
            if k not in sub:
                sub[k] = default
                self._parent._persist_to_disk()
            return sub[k]

    def clear(self) -> None:  # type: ignore[override]
        with self._parent._lock:
            self._sub().clear()
            self._parent._persist_to_disk()

    def update(self, *args, **kwargs) -> None:  # type: ignore[override]
        with self._parent._lock:
            self._sub().update(*args, **kwargs)
            self._parent._persist_to_disk()

    def pop(self, k, *default_args):
        with self._parent._lock:
            sub = self._sub()
            if k in sub:
                v = sub.pop(k)
                self._parent._persist_to_disk()
                return v
            if default_args:
                return default_args[0]
            raise KeyError(k)

    def __repr__(self) -> str:
        return f"_SubStateView({self._key!r}, {self._sub()!r})"


# Module-level ``_FileState`` for the mandate counters (file-mode
# fallback). ONE JSON file captures a consistent snapshot of all 3
# sub-dicts. The ``_load_from_disk()`` call in ``__init__`` populates
# the sub-dicts from the prior process's persisted state (15-b fix for
# DO BADLY #3 — cross-process state).
_mandate_state: _FileState = _FileState("mandate_counters_state.json")
_cumulative_monthly = _mandate_state.sub("cumulative_monthly")
_cumulative_24h = _mandate_state.sub("cumulative_24h")
_last_activity = _mandate_state.sub("last_activity")


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


# ---------------------------------------------------------------------------
# C8/C9/C10 fix (Subagent 14-b) — the prior code split the counter read
# (``_read_db_counters``) and the counter write (``_write_db_counters``)
# across TWO separate transactions. Two concurrent ``/risk/score`` calls
# could both read the counter below the ₹15k/month cap, both decrement,
# and blow the ceiling. The new ``_begin_db_counter_txn`` opens a single
# transaction, takes a per-mandate row lock with ``SELECT ... FOR UPDATE``,
# does the C9 month-boundary reset (if needed), reads the 24h cooling
# events, and returns a ``_DbCounterTxn`` handle holding the open txn.
# The caller then runs the cap-checks while the lock is held; on VALID
# it calls ``commit_increment`` (UPDATE + INSERT event + C10 90-day
# prune + COMMIT all in the same txn); on any non-VALID verdict it calls
# ``rollback`` to release the lock. Concurrent verifies serialize on the
# row lock — the second caller blocks at the FOR UPDATE until the first
# commits/rolls back.
# ---------------------------------------------------------------------------
class _DbCounterTxn:
    """Open Postgres transaction holding a FOR UPDATE lock on a mandate counter row.

    Constructed by ``_begin_db_counter_txn``; the caller runs the cap-checks
    (inactivity, per-txn, monthly, device_id, user_id, cooling 24h) while
    the lock is held, then either:

      * ``commit_increment(new_cumulative_monthly=..., ...)`` on VALID —
        UPDATEs the counter row, INSERTs the 24h event, runs the C10
        90-day prune DELETE, and COMMITs. Closes the transaction.
      * ``rollback()`` on BREACH/REVIEW/EXPIRED — releases the FOR UPDATE
        lock without advancing the counter (a rejected txn does not
        consume the monthly cap; a REVIEW txn does not re-trigger the
        cooling window). Closes the transaction.

    Both methods are idempotent — a second call is a no-op (the txn is
    already closed). This guards against double-commit in error paths
    where the caller's try/except fires both the rollback and a second
    cleanup.
    """

    __slots__ = (
        "conn", "cur", "mid", "cumulative_monthly", "last_activity_ts",
        "recent_24h", "current_month_key", "_closed",
    )

    def __init__(
        self,
        *,
        conn: Any,
        cur: Any,
        mid: str,
        cumulative_monthly: float,
        last_activity_ts: float,
        recent_24h: list[tuple[float, float]],
        current_month_key: str,
    ) -> None:
        self.conn = conn
        self.cur = cur
        self.mid = mid
        self.cumulative_monthly = cumulative_monthly
        self.last_activity_ts = last_activity_ts
        self.recent_24h = recent_24h
        self.current_month_key = current_month_key
        self._closed = False

    def commit_increment(
        self,
        *,
        new_cumulative_monthly: float,
        last_activity_ts: float,
        txn_ts: float,
        txn_amount: float,
    ) -> None:
        """UPDATE counter + INSERT 24h event + C10 90-day prune + COMMIT.

        All four statements run in the SAME transaction (the FOR UPDATE
        lock is held throughout). On ANY DB error this rolls back — the
        verify path has ALREADY updated the in-memory dicts by the time
        this is called (so the request succeeds even if the persistence
        layer fails; the cap is enforced within the process). A future
        improvement is to surface the failure to the audit logger so
        ops sees it (the silent-fall-back is the safe-but-debuggable
        trade-off — same as the prior code's behaviour).
        """
        if self._closed:
            return
        try:
            # C8 — UPDATE the counter while still holding the FOR UPDATE
            # lock (acquired in _begin_db_counter_txn). The
            # ``month_key`` is written back too so the C9 reset branch
            # fires only once per month rollover (subsequent verifies in
            # the same month see the matching month_key and skip the
            # reset).
            self.cur.execute(
                "UPDATE mandate_counters "
                "SET cumulative_monthly = %s, "
                "    last_activity_ts = %s, "
                "    month_key = %s, "
                "    updated_at = NOW() "
                "WHERE mandate_sub = %s",
                (
                    new_cumulative_monthly,
                    last_activity_ts,
                    self.current_month_key,
                    self.mid,
                ),
            )
            self.cur.execute(
                "INSERT INTO mandate_counter_events "
                "(mandate_sub, ts, amount_inr, created_at) "
                "VALUES (%s, %s, %s, NOW())",
                (self.mid, txn_ts, txn_amount),
            )
            # C10 — retention prune. Keep only the last 90 days of
            # mandate_counter_events so the table stays bounded under
            # steady-state traffic. The 24h cooling window only needs
            # the last 24h; 90 days is generous headroom for compliance
            # audit export. Runs on EVERY counter-event INSERT (inline
            # prune-on-write — no scheduler dep). Uses the
            # ``ix_mandate_counter_events_created_at`` index added by
            # alembic migration 004 for a fast range scan.
            self.cur.execute(
                "DELETE FROM mandate_counter_events "
                "WHERE created_at < NOW() - INTERVAL '90 days'"
            )
            self.conn.commit()
        except Exception:
            try:
                self.conn.rollback()
            except Exception:
                pass
        finally:
            self._close()

    def rollback(self) -> None:
        """Release the FOR UPDATE lock without advancing the counter.

        Called on BREACH/REVIEW/EXPIRED — a rejected txn does not consume
        the monthly cap; a REVIEW txn does not re-trigger the cooling
        window. Idempotent — a second call (e.g. from a try/finally
        after the cap-check already returned) is a no-op.
        """
        if self._closed:
            return
        try:
            self.conn.rollback()
        except Exception:
            pass
        finally:
            self._close()

    def _close(self) -> None:
        self._closed = True
        try:
            self.cur.close()
        except Exception:
            pass


def _current_month_key(now: float) -> str:
    """Return the current UTC month as a ``YYYY-MM`` string.

    Centralised here so ``_begin_db_counter_txn`` (C9 reset) + the test
    that asserts the C9 reset branch uses the same key computation.
    Uses ``time.gmtime`` rather than ``datetime.utcnow`` so we don't
    pull a ``datetime`` import into the verify hot path (the rest of
    the file uses ``time.time()`` consistently).
    """
    return time.strftime("%Y-%m", time.gmtime(now))


def _begin_db_counter_txn(
    mandate_sub: str,
    now: float,
    current_month_key: str | None = None,
) -> "_DbCounterTxn | None":
    """Open a transaction + acquire FOR UPDATE on the per-mandate counter row.

    Sequence (all inside ONE transaction — the FOR UPDATE lock is held
    throughout):
      1. ``INSERT ... ON CONFLICT DO NOTHING`` to ensure the row exists
         (race-safe against a concurrent verifier that just inserted
         the same ``mandate_sub``).
      2. ``SELECT cumulative_monthly, last_activity_ts, month_key ...
         FOR UPDATE`` — takes the per-mandate row lock. Concurrent
         verifies block here until the first commits/rolls back.
      3. **C9 month-boundary reset** — if the stored ``month_key``
         differs from the current ``YYYY-MM`` string, the monthly cap
         has rolled over: ``UPDATE mandate_counters SET
         cumulative_monthly = 0, month_key = %s`` (still holding the
         FOR UPDATE lock). If ``month_key`` is empty (the migration
         just landed + the row hasn't been touched since), back-fill
         the current month_key without resetting (the counter is
         already the legacy cumulative value — preserving it would
         be wrong, but resetting would lose prior-month spend; the
         pragmatic choice is to back-fill month_key + let the next
         month rollover trigger the reset on its own). Tests cover
         both branches.
      4. ``SELECT ts, amount_inr FROM mandate_counter_events WHERE
         mandate_sub = %s AND ts > now - 86400`` — the 24h cooling
         window, read while holding the lock so the cooling check
         sees a consistent snapshot.

    Returns a ``_DbCounterTxn`` handle (open transaction, lock held) on
    success. Returns ``None`` on ANY failure (no DB connection, table
    missing, query error) — the caller falls back to the in-memory
    dicts in that case (file-mode / test path). NEVER raise — the
    verify path must degrade to the in-memory fallback rather than
    fail the request.
    """
    conn = _get_counters_conn()
    if conn is None:
        return None
    if current_month_key is None:
        current_month_key = _current_month_key(now)
    try:
        cur = conn.cursor()
        # Step 1 — ensure the row exists (race-safe upsert; ON CONFLICT
        # DO NOTHING so a concurrent verifier that just inserted the
        # same mandate_sub doesn't trip a PK violation).
        cur.execute(
            "INSERT INTO mandate_counters "
            "(mandate_sub, cumulative_monthly, last_activity_ts, month_key, updated_at) "
            "VALUES (%s, 0, NULL, %s, NOW()) "
            "ON CONFLICT (mandate_sub) DO NOTHING",
            (mandate_sub, current_month_key),
        )
        # Step 2 — SELECT FOR UPDATE (C8). Concurrent verifies block
        # here until the first commits/rolls back. The lock is held
        # until commit_increment/rollback is called by the caller.
        cur.execute(
            "SELECT cumulative_monthly, last_activity_ts, month_key "
            "FROM mandate_counters WHERE mandate_sub = %s FOR UPDATE",
            (mandate_sub,),
        )
        row = cur.fetchone()
        if row is None:
            # Defensive — INSERT ON CONFLICT DO NOTHING should have
            # materialised the row above. Roll back + fall back.
            try:
                conn.rollback()
            except Exception:
                pass
            return None
        cumulative_monthly = float(row[0] or 0.0)
        last_activity_ts = float(row[1]) if row[1] is not None else -1.0
        stored_month_key = row[2] or ""

        # Step 3 — C9 month-boundary reset. If the stored month_key is
        # non-empty AND differs from the current YYYY-MM, the monthly
        # cap has rolled over. Reset cumulative_monthly to 0 + update
        # month_key to the current month. If stored_month_key is empty
        # (the migration just landed; the row hasn't been touched
        # since), back-fill the current month_key WITHOUT resetting
        # (pragmatic choice — see docstring above).
        if stored_month_key and stored_month_key != current_month_key:
            cur.execute(
                "UPDATE mandate_counters "
                "SET cumulative_monthly = 0, month_key = %s, updated_at = NOW() "
                "WHERE mandate_sub = %s",
                (current_month_key, mandate_sub),
            )
            cumulative_monthly = 0.0
        elif not stored_month_key:
            cur.execute(
                "UPDATE mandate_counters "
                "SET month_key = %s, updated_at = NOW() "
                "WHERE mandate_sub = %s",
                (current_month_key, mandate_sub),
            )

        # Step 4 — 24h cooling window, read while holding the lock so
        # the cooling check sees a consistent snapshot.
        cur.execute(
            "SELECT ts, amount_inr FROM mandate_counter_events "
            "WHERE mandate_sub = %s AND ts > %s ORDER BY ts ASC",
            (mandate_sub, now - 86400),
        )
        recent_24h = [(float(r[0]), float(r[1])) for r in cur.fetchall()]

        return _DbCounterTxn(
            conn=conn,
            cur=cur,
            mid=mandate_sub,
            cumulative_monthly=cumulative_monthly,
            last_activity_ts=last_activity_ts,
            recent_24h=recent_24h,
            current_month_key=current_month_key,
        )
    except Exception:
        # Degrade to in-memory fallback. The verify path will use the
        # dicts; the cap is enforced within the process (real but not
        # cross-restart). Same trade-off as the prior code.
        try:
            conn.rollback()
        except Exception:
            pass
        return None


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
            # --- Track P (Task 11-a) — read persisted counters from DB    ---
            # --- when available; fall back to in-memory dicts in file mode.---
            # C8/C9/C10 fix (Subagent 14-b): the prior code split the
            # read (``_read_db_counters``) + write (``_write_db_counters``)
            # across two transactions — concurrent verifies could both read
            # below the cap + both decrement, blowing the ₹15k ceiling.
            # The new ``_begin_db_counter_txn`` opens ONE transaction,
            # acquires ``SELECT ... FOR UPDATE`` on the per-mandate row
            # (serialising concurrent verifies), does the C9 month-boundary
            # reset (if stored ``month_key != current YYYY-MM``), reads the
            # 24h cooling events, and returns a ``_DbCounterTxn`` handle
            # holding the open txn. The cap-checks run with the lock held;
            # on VALID we call ``commit_increment`` (UPDATE counter +
            # INSERT event + C10 90-day prune DELETE + COMMIT, all in the
            # same txn); on BREACH/REVIEW/EXPIRED we call ``rollback`` to
            # release the lock without advancing the counter.
            #
            # ``_begin_db_counter_txn`` returns None when no Postgres
            # connection is configured OR when the query fails — the
            # sentinel ``last_activity_ts = -1.0`` from the legacy code is
            # replaced by the None return; the caller falls back to the
            # in-memory dicts (file mode / test path).
            current_month_key = _current_month_key(now)
            db_txn = _begin_db_counter_txn(mid, now, current_month_key)
            if db_txn is not None:
                # DB is the source of truth in prod. The C9 reset has
                # already fired inside ``_begin_db_counter_txn`` if needed
                # (the FOR UPDATE lock was held throughout), so
                # ``db_txn.cumulative_monthly`` is the post-reset value.
                cumulative_monthly = db_txn.cumulative_monthly
                # If the row exists but last_activity_ts is NULL (mandate
                # never spent), fall back to the mandate's iat — same
                # baseline as the in-memory path.
                last_act = (
                    db_txn.last_activity_ts
                    if db_txn.last_activity_ts > 0
                    else payload.get("iat", now)
                )
                # ``db_txn.recent_24h`` is already filtered to ts >
                # now-86400 by the DB query inside the txn — no need to
                # re-prune.
                recent = db_txn.recent_24h
                # Mirror to in-memory dicts so ops introspection + the
                # file-mode fallback path see the same values.
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
                # Release the FOR UPDATE lock on the non-VALID path — a
                # rejected txn does not consume the monthly cap.
                if db_txn is not None:
                    db_txn.rollback()
                return MandateVerdict.EXPIRED, {
                    **payload,
                    "verdict_reason": "inactivity_auto_revoke",
                }

            # 2. OC-201B: per-txn cap (default ₹5,000).
            max_per_txn = float(payload.get("max_per_txn_inr", 5000.0))
            if float(amount_inr) > max_per_txn:
                if db_txn is not None:
                    db_txn.rollback()
                return MandateVerdict.BREACH, {
                    **payload,
                    "verdict_reason": "per_txn_cap_exceeded",
                }

            # 3. OC-201B: monthly cumulative cap (default ₹15,000).
            # C8 fix: the cap-check uses the post-FOR-UPDATE-LOCK
            # cumulative_monthly value (read inside the txn while the
            # row was locked). A concurrent verifier that just landed a
            # ₹15k txn will have committed before our SELECT FOR UPDATE
            # acquired the lock, so we'll see its committed value + trip
            # the cap ourselves — the cap is now race-safe.
            max_per_month = float(payload.get("max_per_month_inr", 15000.0))
            projected = cumulative_monthly + float(amount_inr)
            if projected > max_per_month:
                if db_txn is not None:
                    db_txn.rollback()
                return MandateVerdict.BREACH, {
                    **payload,
                    "verdict_reason": "monthly_cap_exceeded",
                }

            # 4. OC-201B §3.7 (Issuer Bank duty): validate device_id before
            #    debiting. If the mandate enumerates device_ids and the
            #    request carries an X-Device-Id, it must be in the list.
            allowed_devices = payload.get("device_ids", [])
            if device_id is not None and allowed_devices and device_id not in allowed_devices:
                if db_txn is not None:
                    db_txn.rollback()
                return MandateVerdict.BREACH, {
                    **payload,
                    "verdict_reason": "device_id_not_allowed",
                }

            # 5. OC-201B §3.3 (Secondary App/PSP duty): user_id captured at
            #    registration and validated per txn.
            expected_uid = payload.get("user_id", "")
            if user_id is not None and expected_uid and user_id != expected_uid:
                if db_txn is not None:
                    db_txn.rollback()
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
                    if db_txn is not None:
                        db_txn.rollback()
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
            # 15-b — refactor from ``setdefault(mid, []).append(...)`` (which
            # mutated the stored list in place WITHOUT triggering the
            # ``_FileState._persist_to_disk()`` hook — the in-memory state
            # would be advanced but the file would be stale). The new form
            # uses a pure ``__setitem__`` so the persist fires after the
            # in-place append, capturing the new event on disk.
            prior_events = _cumulative_24h.get(mid, [])
            _cumulative_24h[mid] = prior_events + [(now, float(amount_inr))]
            _last_activity[mid] = now
            if db_txn is not None:
                # C8/C9/C10 — the FOR UPDATE lock has been held since
                # ``_begin_db_counter_txn``; this UPDATE + INSERT event +
                # C10 90-day prune DELETE + COMMIT all run in the SAME
                # transaction, so concurrent verifies serialize.
                db_txn.commit_increment(
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
    # 15-b — force-flush the combined cleared state. The 3 individual
    # ``.clear()`` calls each trigger a throttled persist; the throttle
    # would skip the 2nd + 3rd writes (within 5 sec of the first), so
    # the file would have a STALE partial-cleared state (monthly
    # cleared, 24h + last_activity still populated). The force=True
    # bypass ensures the file reflects the post-clear empty state.
    _mandate_state._persist_to_disk(force=True)
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
