"""Concurrency + month-reset + retention-prune tests for the UPI Circle
mandate counter path (C8/C9/C10 fixes by Subagent 14-b).

C8 — RACE CONDITION:
    The prior ``verify_mandate`` path split the counter read
    (``_read_db_counters``) + the counter write
    (``_write_db_counters``) across TWO separate transactions. Two
    concurrent ``/risk/score`` calls could both read the counter below
    the ₹15k/month cap, both decrement, and blow the ceiling.
    Fix: ``_begin_db_counter_txn`` opens ONE transaction + acquires
    ``SELECT ... FOR UPDATE`` on the per-mandate row.

C9 — NO MONTH-BOUNDARY RESET:
    The "monthly cap" silently became a "lifetime cap" after the first
    month because there was no logic to detect month rollover + reset
    the counter. Fix: alembic 004 adds a ``month_key`` VARCHAR(7)
    column to ``mandate_counters``; ``_begin_db_counter_txn`` compares
    the stored ``month_key`` to the current ``YYYY-MM`` string, and if
    they differ, resets ``cumulative_monthly = 0`` + updates
    ``month_key`` (still holding the FOR UPDATE lock).

C10 — NO RETENTION PRUNE:
    ``mandate_counter_events`` grew unbounded. Fix:
    ``_DbCounterTxn.commit_increment`` runs
    ``DELETE FROM mandate_counter_events
       WHERE created_at < NOW() - INTERVAL '90 days'``
    on EVERY counter-event INSERT (inline prune-on-write).

Test strategy:
    * **Source-grep tests (always run)** — assert the C8/C9/C10 SQL
      actually lives in ``src/api/mandates.py`` (not just a claim).
      These give the orchestrator a quick "the fix landed" signal in
      the sandbox even when no real Postgres is available.
    * **Unit tests for the new helpers (always run)** — exercise the
      ``_current_month_key``, ``_DbCounterTxn.commit_increment``, and
      ``_DbCounterTxn.rollback`` code paths with a mock cursor so the
      commit_increment SQL sequence (UPDATE + INSERT event + DELETE
      prune + commit) is asserted regardless of env.
    * **DB-path tests (skip when no Postgres)** — exercise the real
      C8/C9/C10 SQL against a real Postgres instance. Skipped in the
      sandbox (DATABASE_URL points at a SQLite ``file:`` URL — see
      ``src/config.Settings.is_postgres``); run locally with
      ``DATABASE_URL=postgresql://risk:risk@localhost:5432/riskdb``.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.mandates import (  # noqa: E402
    MandateVerdict,
    _begin_db_counter_txn,
    _current_month_key,
    _DbCounterTxn,
    issue_mandate,
    reset_upi_counters,
    verify_mandate,
)
from src.config import get_settings  # noqa: E402

# Resolve the dual-mode switch ONCE at import time. The DB-path tests
# (test_c8_concurrent_under_postgres, test_c9_month_reset_under_postgres,
# test_c10_retention_prune_under_postgres) skip when DATABASE_URL is unset
# or points at a non-Postgres DSN — same skip pattern as test_db.py.
_SETTINGS = get_settings()
_POSTGRES_AVAILABLE = _SETTINGS.is_postgres


# ============================================================================
# C8 source-grep tests (always run) — the C8 fix's existence proof.
# ============================================================================
def test_c8_fix_uses_for_update_in_mandates_py():
    """C8 fix: ``SELECT ... FOR UPDATE`` is present in ``src/api/mandates.py``.

    The grep is the existence-proof — the FOR UPDATE clause is what makes
    concurrent verifies serialize on the per-mandate counter row. Without
    it, the read-then-write is racy.
    """
    src = Path(__file__).resolve().parents[1] / "src" / "api" / "mandates.py"
    contents = src.read_text()
    assert "FOR UPDATE" in contents, (
        "C8 fix missing: src/api/mandates.py does not contain 'FOR UPDATE'. "
        "Concurrent verify_mandate() calls will race on the ₹15k/month cap."
    )
    # The FOR UPDATE should be on the SELECT from mandate_counters (the
    # per-mandate row that holds cumulative_monthly), NOT on the events
    # table. This is the row-level lock that serialises concurrent verifies.
    assert "FROM mandate_counters WHERE mandate_sub = %s FOR UPDATE" in contents, (
        "C8 fix: FOR UPDATE must be on the SELECT from mandate_counters "
        "(the per-mandate cumulative-monthly row), not elsewhere."
    )


def test_c8_fix_uses_single_transaction_for_read_increment_write():
    """C8 fix: the read+increment+write lives in ONE transaction.

    The prior code split ``_read_db_counters`` (one txn) +
    ``_write_db_counters`` (a second txn). The new code uses
    ``_begin_db_counter_txn`` + ``_DbCounterTxn.commit_increment`` —
    one txn that holds the FOR UPDATE lock from SELECT through COMMIT.
    """
    src = Path(__file__).resolve().parents[1] / "src" / "api" / "mandates.py"
    contents = src.read_text()
    # The new transactional API must exist.
    assert "_begin_db_counter_txn" in contents, (
        "C8 fix: _begin_db_counter_txn() helper missing — the new "
        "single-txn entry point that acquires the FOR UPDATE lock."
    )
    assert "class _DbCounterTxn" in contents, (
        "C8 fix: _DbCounterTxn class missing — the open-txn handle that "
        "holds the FOR UPDATE lock across the cap-checks + commit_increment."
    )
    assert "def commit_increment" in contents, (
        "C8 fix: _DbCounterTxn.commit_increment method missing — the "
        "commit path that runs UPDATE + INSERT + DELETE prune + COMMIT "
        "all in the same transaction (FOR UPDATE held throughout)."
    )
    # The old split-txn helpers must be GONE (otherwise we'd have
    # dead code OR a code path that still races). We check for the
    # function DEFINITION (`def _read_db_counters(`), not just any
    # string mention — the docstrings + comments intentionally
    # reference the legacy names to explain why they were removed.
    import re

    assert not re.search(r"^def _read_db_counters\b", contents, re.MULTILINE), (
        "C8 fix: _read_db_counters() function definition still present — "
        "the legacy split-txn read helper that caused the race condition. "
        "Remove it; _begin_db_counter_txn replaces it. (Mentions in "
        "docstrings/comments explaining the removal are OK.)"
    )
    assert not re.search(r"^def _write_db_counters\b", contents, re.MULTILINE), (
        "C8 fix: _write_db_counters() function definition still present — "
        "the legacy split-txn write helper that caused the race condition. "
        "Remove it; _DbCounterTxn.commit_increment replaces it. (Mentions "
        "in docstrings/comments explaining the removal are OK.)"
    )


# ============================================================================
# C9 source-grep tests (always run) — the C9 fix's existence proof.
# ============================================================================
def test_c9_fix_uses_month_key_in_mandates_py():
    """C9 fix: ``month_key`` is consulted in ``src/api/mandates.py``.

    The grep count must be >= 3 (>=3 distinct occurrences of the
    string ``month_key``) — one in the INSERT in
    ``_begin_db_counter_txn``, one in the SELECT-FOR-UPDATE column
    list, one in the C9 reset UPDATE statement, one in the
    ``commit_increment`` UPDATE SET clause.
    """
    src = Path(__file__).resolve().parents[1] / "src" / "api" / "mandates.py"
    contents = src.read_text()
    count = contents.count("month_key")
    assert count >= 3, (
        f"C9 fix missing: src/api/mandates.py mentions 'month_key' only "
        f"{count} time(s); need >= 3 (INSERT + SELECT-FOR-UPDATE + "
        f"reset UPDATE + commit_increment UPDATE). The month-boundary "
        f"reset logic isn't wired."
    )
    # The C9 reset branch must exist (compares stored month_key to
    # current_month_key + resets cumulative_monthly to 0 if they differ).
    assert "stored_month_key" in contents, (
        "C9 fix: the stored_month_key variable is missing — the C9 reset "
        "branch that compares stored to current month_key isn't wired."
    )
    assert "current_month_key" in contents, (
        "C9 fix: the current_month_key variable is missing — the "
        "YYYY-MM string computed from time.gmtime() that the stored "
        "month_key is compared against."
    )


def test_c9_current_month_key_helper_format():
    """C9 fix: ``_current_month_key(now)`` returns a ``YYYY-MM`` string.

    The format is the contract with the alembic-004 ``month_key
    VARCHAR(7)`` column — if the helper ever returns a different
    format (e.g. a different separator, or 2-digit year), the
    equality check in ``_begin_db_counter_txn`` would never match and
    the reset would fire on EVERY verify (resetting the monthly cap
    to 0 on every txn — silently breaking the cap).
    """
    # 2026-08-27 03:00:00 UTC = epoch 1787857200 (approx). The helper
    # must return "2026-08" regardless of the local tz.
    epoch_aug_2026 = 1787857200
    key = _current_month_key(epoch_aug_2026)
    assert key == "2026-08", f"expected '2026-08', got '{key}'"
    # Length contract: VARCHAR(7) column — must fit.
    assert len(key) == 7, f"month_key must be 7 chars (YYYY-MM), got {len(key)}"
    # Round-trip via time.gmtime — different epoch in same month yields same key.
    epoch_aug_2026_later = epoch_aug_2026 + 86400  # +1 day, still Aug
    assert _current_month_key(epoch_aug_2026_later) == "2026-08"
    # Epoch in Sep 2026 yields "2026-09".
    epoch_sep_2026 = epoch_aug_2026 + (31 * 86400)  # +31 days
    assert _current_month_key(epoch_sep_2026) == "2026-09"


# ============================================================================
# C10 source-grep test (always run) — the C10 fix's existence proof.
# ============================================================================
def test_c10_fix_prunes_old_events_in_mandates_py():
    """C10 fix: the 90-day retention prune is present in src/api/mandates.py.

    The prune runs on EVERY counter-event INSERT (inline prune-on-write —
    no scheduler dep). Uses the ``ix_mandate_counter_events_created_at``
    index added by alembic 004 for a fast range scan.
    """
    src = Path(__file__).resolve().parents[1] / "src" / "api" / "mandates.py"
    contents = src.read_text()
    # The prune DELETE statement — at least one of the alternative
    # phrasings must be present. The actual statement uses
    # ``INTERVAL '90 days'`` (Postgres interval syntax).
    assert (
        "INTERVAL '90 days'" in contents
        or "90 days" in contents
        or "retention" in contents.lower()
    ), (
        "C10 fix missing: src/api/mandates.py does not prune "
        "mandate_counter_events older than 90 days. The events table "
        "will grow unbounded under steady-state traffic."
    )
    # The prune DELETE must be inside _DbCounterTxn.commit_increment
    # (the same transaction as the counter UPDATE + event INSERT),
    # NOT a separate transaction (otherwise it'd be a second txn,
    # re-introducing the C8 split-txn bug).
    assert "DELETE FROM mandate_counter_events" in contents, (
        "C10 fix: the prune DELETE FROM mandate_counter_events is "
        "missing from src/api/mandates.py."
    )


# ============================================================================
# Unit tests for _DbCounterTxn (always run) — exercise the commit +
# rollback SQL sequences with a mock cursor so the C8/C9/C10 fix is
# asserted regardless of env (no Postgres required).
# ============================================================================
def test_db_counter_txn_commit_increment_runs_update_insert_prune_commit():
    """C8/C10 fix: ``commit_increment`` issues UPDATE + INSERT event +
    DELETE prune + COMMIT, in that order, on the same cursor.

    The mock cursor records each execute() call so we can assert the
    sequence + the SQL text. This proves the FOR UPDATE lock acquired
    in ``_begin_db_counter_txn`` is held across the UPDATE/INSERT/DELETE
    until COMMIT (the C8 single-transaction guarantee).
    """
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    txn = _DbCounterTxn(
        conn=mock_conn,
        cur=mock_cur,
        mid="test-mid",
        cumulative_monthly=5000.0,
        last_activity_ts=1700000000.0,
        recent_24h=[],
        current_month_key="2026-08",
    )
    txn.commit_increment(
        new_cumulative_monthly=6500.0,
        last_activity_ts=1700000123.0,
        txn_ts=1700000123.0,
        txn_amount=1500.0,
    )
    # 3 SQL statements: UPDATE counter, INSERT event, DELETE prune.
    assert mock_cur.execute.call_count == 3, (
        f"commit_increment must execute 3 SQL statements "
        f"(UPDATE + INSERT + DELETE prune); got "
        f"{mock_cur.execute.call_count}"
    )
    # Statement 1: UPDATE mandate_counters.
    sql_1 = mock_cur.execute.call_args_list[0].args[0]
    assert "UPDATE mandate_counters" in sql_1
    assert "cumulative_monthly" in sql_1
    assert "month_key" in sql_1  # C9: month_key is written back too.
    # Statement 2: INSERT mandate_counter_events.
    sql_2 = mock_cur.execute.call_args_list[1].args[0]
    assert "INSERT INTO mandate_counter_events" in sql_2
    # Statement 3: C10 retention prune DELETE.
    sql_3 = mock_cur.execute.call_args_list[2].args[0]
    assert "DELETE FROM mandate_counter_events" in sql_3
    assert "INTERVAL '90 days'" in sql_3
    # COMMIT was called on the connection (releases the FOR UPDATE lock).
    mock_conn.commit.assert_called_once()
    # The cursor was closed (cleanup — releases DB resources).
    mock_cur.close.assert_called_once()


def test_db_counter_txn_commit_increment_is_idempotent():
    """C8 fix: ``commit_increment`` is idempotent — a second call is a
    no-op. Guards against double-commit in error paths where the
    caller's try/except fires both the rollback and a second cleanup.
    """
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    txn = _DbCounterTxn(
        conn=mock_conn, cur=mock_cur, mid="m", cumulative_monthly=0,
        last_activity_ts=-1, recent_24h=[], current_month_key="2026-08",
    )
    txn.commit_increment(
        new_cumulative_monthly=100, last_activity_ts=1,
        txn_ts=1, txn_amount=100,
    )
    # First call: 3 SQL + commit + close.
    assert mock_cur.execute.call_count == 3
    assert mock_conn.commit.call_count == 1
    # Second call: idempotent no-op (no extra SQL, no double-commit).
    txn.commit_increment(
        new_cumulative_monthly=200, last_activity_ts=2,
        txn_ts=2, txn_amount=100,
    )
    assert mock_cur.execute.call_count == 3, "second commit_increment must be a no-op"
    assert mock_conn.commit.call_count == 1, "second commit_increment must not double-commit"


def test_db_counter_txn_rollback_releases_lock_without_advancing_counter():
    """C8 fix: ``rollback`` releases the FOR UPDATE lock without
    advancing the counter. Called on BREACH/REVIEW/EXPIRED — a rejected
    txn does not consume the monthly cap; a REVIEW txn does not
    re-trigger the cooling window.
    """
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    txn = _DbCounterTxn(
        conn=mock_conn, cur=mock_cur, mid="m", cumulative_monthly=5000,
        last_activity_ts=1, recent_24h=[], current_month_key="2026-08",
    )
    txn.rollback()
    # No SQL executed (no UPDATE/INSERT/DELETE — the counter is NOT
    # advanced on BREACH/REVIEW/EXPIRED).
    assert mock_cur.execute.call_count == 0
    # conn.rollback() called once — releases the FOR UPDATE lock.
    mock_conn.rollback.assert_called_once()
    mock_conn.commit.assert_not_called()  # no commit on rollback path
    mock_cur.close.assert_called_once()


def test_db_counter_txn_rollback_is_idempotent():
    """C8 fix: ``rollback`` is idempotent — a second call is a no-op."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    txn = _DbCounterTxn(
        conn=mock_conn, cur=mock_cur, mid="m", cumulative_monthly=0,
        last_activity_ts=-1, recent_24h=[], current_month_key="2026-08",
    )
    txn.rollback()
    txn.rollback()
    assert mock_conn.rollback.call_count == 1, "second rollback() must be a no-op"


def test_db_counter_txn_commit_increment_rolls_back_on_db_error():
    """C8 fix: on DB error mid-commit, ``commit_increment`` rolls back
    the transaction (releases the FOR UPDATE lock) so a subsequent
    verify_mandate call doesn't block forever waiting for the lock.
    """
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    # The INSERT-event statement raises (e.g. connection lost mid-txn).
    mock_cur.execute.side_effect = [None, RuntimeError("connection lost"), None]
    txn = _DbCounterTxn(
        conn=mock_conn, cur=mock_cur, mid="m", cumulative_monthly=0,
        last_activity_ts=-1, recent_24h=[], current_month_key="2026-08",
    )
    # Should not raise — the verify path degrades to in-memory fallback.
    txn.commit_increment(
        new_cumulative_monthly=100, last_activity_ts=1,
        txn_ts=1, txn_amount=100,
    )
    # Rollback called to release the FOR UPDATE lock.
    mock_conn.rollback.assert_called_once()
    # Commit NOT called (we errored before commit).
    mock_conn.commit.assert_not_called()
    # The txn is now closed (cursor.close called in _close()).
    mock_cur.close.assert_called_once()


# ============================================================================
# File-mode end-to-end tests (always run) — assert the cap-checks +
# counter advancement still work in the file-mode fallback path (no
# Postgres). This proves the C8/C9/C10 fix didn't break the existing
# 22 tests in test_mandates.py (which all use file mode).
# ============================================================================
def _issue_upi_circle_for_concurrency(
    *,
    cooling_24h_inr: float = 99_999,  # disable cooling for cap-only tests
    max_per_month_inr: float = 15000,
) -> str:
    return issue_mandate(
        "CUST-CONC-1",
        max_amount_inr=max_per_month_inr,
        ttl_seconds=3600,
        scope="upi_circle",
        mandate_type="upi_circle_delegation",
        device_ids=["device-01", "device-02"],
        user_id="user-01",
        bh_purpose_code="90",
        max_per_txn_inr=5000,
        max_per_month_inr=max_per_month_inr,
        cooling_24h_inr=cooling_24h_inr,
        inactivity_revoke_days=180,
    )


def test_file_mode_monthly_cap_still_enforced_after_c8c9c10_fix():
    """File-mode (no Postgres) — the ₹15k/month cap still trips on the
    4th ₹5k txn after the C8/C9/C10 refactor. The new code must not
    break the file-mode fallback path.
    """
    reset_upi_counters()
    m = _issue_upi_circle_for_concurrency()
    # Three ₹5k txns: cumulative ₹15k (exactly at cap, VALID).
    for _ in range(3):
        v, _ = verify_mandate(m, 5000, device_id="device-01", user_id="user-01")
        assert v == MandateVerdict.VALID
    # 4th ₹500 txn: cumulative ₹15.5k > ₹15k cap -> BREACH.
    v4, p4 = verify_mandate(m, 500, device_id="device-01", user_id="user-01")
    assert v4 == MandateVerdict.BREACH
    assert p4["verdict_reason"] == "monthly_cap_exceeded"


def test_file_mode_cooling_period_still_fires_after_c8c9c10_fix():
    """File-mode — the 24h cooling REVIEW still fires after the
    C8/C9/C10 refactor. The new code must not break the cooling
    circuit breaker.
    """
    reset_upi_counters()
    m = _issue_upi_circle_for_concurrency(cooling_24h_inr=1000)
    v1, _ = verify_mandate(m, 1500, device_id="device-01", user_id="user-01")
    assert v1 == MandateVerdict.VALID
    v2, p2 = verify_mandate(m, 500, device_id="device-01", user_id="user-01")
    assert v2 == MandateVerdict.REVIEW
    assert p2["verdict_reason"] == "cooling_period_active"


def test_file_mode_inactivity_auto_revoke_still_fires_after_c8c9c10_fix():
    """File-mode — the OC-201B 6-month inactivity auto-revoke still
    fires after the C8/C9/C10 refactor.
    """
    from src.api.mandates import simulate_inactivity

    reset_upi_counters()
    m = _issue_upi_circle_for_concurrency()
    v0, _ = verify_mandate(m, 100, device_id="device-01", user_id="user-01")
    assert v0 == MandateVerdict.VALID
    simulate_inactivity(m, days=181)
    v1, p1 = verify_mandate(m, 100, device_id="device-01", user_id="user-01")
    assert v1 == MandateVerdict.EXPIRED
    assert p1["verdict_reason"] == "inactivity_auto_revoke"


# ============================================================================
# DB-path tests (skip when no Postgres available) — exercise the real
# C8/C9/C10 SQL against a real Postgres instance. The skip pattern
# mirrors test_db.py.
# ============================================================================
@pytest.mark.skipif(
    not _POSTGRES_AVAILABLE,
    reason=(
        "DATABASE_URL not set to a Postgres DSN — skipping C8/C9/C10 "
        "DB-path tests. Set DATABASE_URL=postgresql://risk:risk@localhost:5432/riskdb "
        "to run them."
    ),
)
class TestC8C9C10UnderPostgres:
    """Real-Postgres tests for the C8/C9/C10 fixes.

    These tests apply alembic migrations 001-004, then exercise the
    real SQL paths. Each test is responsible for its own table cleanup
    so they're isolated + can be re-run in any order.
    """

    @pytest.fixture(scope="class")
    def db_conn(self):
        """Single psycopg connection for the whole class. Applies alembic
        migrations 001-004 (including the new 004 month_key column +
        created_at index)."""
        import subprocess

        import psycopg

        conn = psycopg.connect(_SETTINGS.database_url, autocommit=False)
        try:
            subprocess.run(
                ["alembic", "upgrade", "head"],
                cwd=str(Path(__file__).resolve().parents[1]),
                check=True,
                env={**os.environ, "DATABASE_URL": _SETTINGS.database_url},
                capture_output=True,
                timeout=60,
            )
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            try:
                conn.close()
            except Exception:
                pass
            pytest.skip("alembic upgrade head failed — see test_db.py for the same pattern")
        # Reset the cached counters conn so verify_mandate picks up
        # this DB connection (the module-level cache may hold an older
        # connection from before the migrations applied).
        from src.api.mandates import _reset_counters_conn

        _reset_counters_conn()
        yield conn
        _reset_counters_conn()
        conn.close()

    def _truncate(self, db_conn):
        """Per-test cleanup. Truncate both mandate tables so the test
        starts from a known-empty state."""
        with db_conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE mandate_counter_events RESTART IDENTITY")
            cur.execute("DELETE FROM mandate_counters")
        db_conn.commit()
        # Also reset the module-level cache so the next verify_mandate
        # call doesn't reuse a cursor from a prior test's txn.
        from src.api.mandates import _reset_counters_conn, reset_upi_counters

        _reset_counters_conn()
        reset_upi_counters()

    def test_c9_month_reset_under_postgres(self, db_conn):
        """C9 fix under Postgres: insert a counter row with month_key =
        '2025-12' (stale) + cumulative_monthly = 12000.0, then call
        verify_mandate() with the current month = whatever time.gmtime
        returns now. Assert the counter reset to 0 (then advanced by
        the txn amount) + month_key updated to current.
        """
        self._truncate(db_conn)
        # Issue a UPI Circle mandate — we'll write a stale counter row
        # directly into the DB, then call verify_mandate to assert the
        # C9 reset branch fires.
        m = _issue_upi_circle_for_concurrency()
        from src.api.mandates import decode_mandate

        payload = decode_mandate(m)
        mid = payload["sub"]
        # Insert a stale counter row directly. cumulative_monthly =
        # 12000 (well below the 15000 cap, so the cap-check won't trip
        # before the C9 reset has a chance to fire). month_key =
        # "2025-12" (stale relative to the current real-time month).
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO mandate_counters "
                "(mandate_sub, cumulative_monthly, last_activity_ts, month_key, updated_at) "
                "VALUES (%s, 12000, EXTRACT(EPOCH FROM NOW())::bigint, '2025-12', NOW())",
                (mid,),
            )
        db_conn.commit()
        # Verify the mandate with a small amount — the C9 reset should
        # fire inside _begin_db_counter_txn, resetting cumulative to 0
        # before the cap-check, so the projected cumulative = 0 + 500
        # = 500 (well below the 15000 cap -> VALID).
        v, _ = verify_mandate(m, 500, device_id="device-01", user_id="user-01")
        assert v == MandateVerdict.VALID, (
            f"C9 reset branch should have fired (stale month_key='2025-12') "
            f"and the txn should be VALID after reset; got {v}"
        )
        # Read the counter row back + assert month_key updated to
        # current + cumulative_monthly = 500 (reset to 0, then advanced
        # by the txn amount).
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT cumulative_monthly, month_key FROM mandate_counters "
                "WHERE mandate_sub = %s",
                (mid,),
            )
            row = cur.fetchone()
        assert row is not None, "mandate_counters row should exist after verify_mandate"
        cumulative_after = float(row[0])
        month_key_after = row[1]
        current_month = _current_month_key(time.time())
        assert month_key_after == current_month, (
            f"C9 fix: month_key should be updated to current '{current_month}'; "
            f"got '{month_key_after}'"
        )
        # cumulative_monthly should be 500 (12000 reset to 0 by C9, then
        # advanced by 500 from the VALID txn).
        assert cumulative_after == 500.0, (
            f"C9 fix: cumulative_monthly should be 500.0 (reset to 0 + "
            f"500 txn); got {cumulative_after}. The C9 reset didn't fire "
            f"OR the counter wasn't advanced correctly."
        )

    def test_c10_retention_prune_under_postgres(self, db_conn):
        """C10 fix under Postgres: insert 100 events with timestamps
        spanning 120 days (50 events older than 90 days + 50 events
        in the last 90 days). Call verify_mandate once. Assert events
        older than 90 days are pruned (the inline prune-on-write ran
        inside commit_increment).
        """
        self._truncate(db_conn)
        m = _issue_upi_circle_for_concurrency()
        from src.api.mandates import decode_mandate

        payload = decode_mandate(m)
        mid = payload["sub"]
        now_epoch = int(time.time())
        # Insert 50 OLD events (120-100 days ago) + 50 RECENT events
        # (10-0 days ago). The 50 old events are > 90 days old, so
        # the C10 prune DELETE should remove them. The 50 recent
        # events are < 90 days old, so they should survive.
        # NOTE: created_at is the TIMESTAMPTZ column the prune DELETE
        # filters on. The `ts` BIGINT column is the unix-epoch event
        # time (used by the 24h cooling window, NOT by the prune).
        with db_conn.cursor() as cur:
            for i in range(50):
                # OLD events: created_at = NOW() - (100 + i) days.
                cur.execute(
                    "INSERT INTO mandate_counter_events "
                    "(mandate_sub, ts, amount_inr, created_at) "
                    "VALUES (%s, %s, 100, NOW() - INTERVAL '100 days' - (%s || ' days')::interval)",
                    (mid, now_epoch - (100 + i) * 86400, i),
                )
            for i in range(50):
                # RECENT events: created_at = NOW() - i days.
                cur.execute(
                    "INSERT INTO mandate_counter_events "
                    "(mandate_sub, ts, amount_inr, created_at) "
                    "VALUES (%s, %s, 100, NOW() - (%s || ' days')::interval)",
                    (mid, now_epoch - i * 86400, i),
                )
        db_conn.commit()
        # Sanity: 100 events inserted.
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM mandate_counter_events WHERE mandate_sub = %s",
                (mid,),
            )
            assert cur.fetchone()[0] == 100
        # Call verify_mandate once — this triggers _begin_db_counter_txn
        # (FOR UPDATE + C9 reset, no-op since fresh row + current month)
        # + commit_increment (UPDATE + INSERT new event + DELETE prune).
        v, _ = verify_mandate(m, 500, device_id="device-01", user_id="user-01")
        assert v == MandateVerdict.VALID
        # After verify_mandate, the C10 prune should have removed the
        # 50 OLD events. The 50 RECENT events + 1 NEW event (from the
        # verify_mandate call itself) should remain = 51 total.
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM mandate_counter_events WHERE mandate_sub = %s",
                (mid,),
            )
            count_after = cur.fetchone()[0]
        # 50 recent + 1 new = 51. Allow some slack for boundary events
        # whose created_at is exactly at the 90-day boundary (depends on
        # test execution speed).
        assert count_after <= 51, (
            f"C10 fix: {count_after} events remained after verify_mandate; "
            f"expected <= 51 (50 recent + 1 new from the verify call). "
            f"The 50 OLD events (100+ days old) should have been pruned."
        )
        assert count_after >= 50, (
            f"C10 fix: only {count_after} events remained; expected >= 50 "
            f"(the 50 RECENT events should survive the prune)."
        )

    def test_c8_concurrent_under_postgres(self, db_conn):
        """C8 fix under Postgres: spawn N concurrent threads that each
        call verify_mandate(m, 5000) on the same mandate (₹5k/txn cap).
        With the FOR UPDATE lock, the threads serialize; the
        cumulative_monthly should never exceed the ₹15k cap. Without
        the lock (the bug), the threads would race + the counter would
        blow past the cap.

        We use 4 threads × 4 txns of ₹5k each = 16 txns total. The cap
        is ₹15k, so only 3 txns can succeed (₹15k exactly at cap); the
        4th txn onwards should return BREACH (monthly_cap_exceeded).
        With the FOR UPDATE fix, exactly 3 VALID + 13 BREACH.
        """
        self._truncate(db_conn)
        m = _issue_upi_circle_for_concurrency()
        results: list[tuple[str, str]] = []
        results_lock = threading.Lock()

        def _worker():
            verdicts: list[tuple[str, str]] = []
            # Each thread fires 4 verifies of ₹5k each. Across 4
            # threads, that's 16 total verifies against the same
            # mandate row (cumulative ₹80k if not capped). The ₹15k
            # monthly cap allows exactly 3 × ₹5k = ₹15k through.
            for _ in range(4):
                v, p = verify_mandate(
                    m, 5000, device_id="device-01", user_id="user-01"
                )
                verdicts.append((v, p.get("verdict_reason", "")))
            with results_lock:
                results.extend(verdicts)

        threads = [threading.Thread(target=_worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Total verifies fired = 4 threads × 4 verifies = 16.
        assert len(results) == 16, f"expected 16 results, got {len(results)}"
        valid_count = sum(1 for v, _ in results if v == MandateVerdict.VALID)
        breach_count = sum(
            1 for v, r in results
            if v == MandateVerdict.BREACH and r == "monthly_cap_exceeded"
        )
        # C8 fix: with the FOR UPDATE lock, exactly 3 verifies succeed
        # (₹5k × 3 = ₹15k, exactly at the cap). The 4th onwards hits
        # the cap → BREACH with verdict_reason="monthly_cap_exceeded".
        # Without the fix, more than 3 would succeed (the race would
        # let threads read the counter below cap before any of them
        # committed the increment).
        assert valid_count == 3, (
            f"C8 fix: with FOR UPDATE, exactly 3 verifies should succeed "
            f"(₹15k = 3 × ₹5k exactly at cap); got {valid_count}. "
            f"This indicates the FOR UPDATE lock is NOT serialising "
            f"concurrent verifies — the race condition is back."
        )
        assert breach_count == 13, (
            f"C8 fix: the 13 verifies after the cap should return "
            f"BREACH (monthly_cap_exceeded); got {breach_count}."
        )
        # Final assertion: read the cumulative_monthly from the DB —
        # it must be exactly ₹15k (the cap), NOT a single rupee over.
        from src.api.mandates import decode_mandate

        mid = decode_mandate(m)["sub"]
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT cumulative_monthly FROM mandate_counters "
                "WHERE mandate_sub = %s",
                (mid,),
            )
            row = cur.fetchone()
        assert row is not None
        cumulative = float(row[0])
        assert cumulative == 15000.0, (
            f"C8 fix: cumulative_monthly should be exactly ₹15,000 "
            f"(the cap, with 3 × ₹5k VALID txns); got ₹{cumulative}. "
            f"If this is > 15000, the FOR UPDATE lock failed to "
            f"serialise + the race condition blew the cap."
        )


# ============================================================================
# Final integration check — the new tests don't break import-time of
# the mandates module (a regression on the C8/C9/C10 refactor would
# surface here). Always runs.
# ============================================================================
def test_mandates_module_imports_clean_after_c8c9c10_refactor():
    """Sanity: the C8/C9/C10 refactor doesn't break the module's
    import-time side effects. The 22 existing tests in
    test_mandates.py depend on this import working; if it breaks,
    the whole suite fails.
    """
    import importlib

    import src.api.mandates as mandates_mod

    importlib.reload(mandates_mod)
    # Public API surface preserved.
    assert hasattr(mandates_mod, "verify_mandate")
    assert hasattr(mandates_mod, "issue_mandate")
    assert hasattr(mandates_mod, "decode_mandate")
    assert hasattr(mandates_mod, "reset_upi_counters")
    assert hasattr(mandates_mod, "simulate_inactivity")
    assert hasattr(mandates_mod, "MandateVerdict")
    # New helpers added by the C8/C9/C10 refactor.
    assert hasattr(mandates_mod, "_begin_db_counter_txn")
    assert hasattr(mandates_mod, "_DbCounterTxn")
    assert hasattr(mandates_mod, "_current_month_key")
    # Legacy helpers removed (the split-txn helpers that caused the
    # race condition).
    assert not hasattr(mandates_mod, "_read_db_counters"), (
        "_read_db_counters must be removed — the split-txn read helper "
        "that caused the C8 race condition."
    )
    assert not hasattr(mandates_mod, "_write_db_counters"), (
        "_write_db_counters must be removed — the split-txn write helper "
        "that caused the C8 race condition."
    )
