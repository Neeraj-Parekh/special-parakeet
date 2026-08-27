"""Postgres-path tests for the Day 2 Track E dual-mode DB refactor.

These tests are SKIPPED unless ``DATABASE_URL`` points at a real Postgres
DSN (``postgresql://...``). In the sandbox the env var happens to be set
to a SQLite-style ``file:`` URL — the dual-mode switch in
``src/config.Settings.is_postgres`` filters that out so the file-mode
tests still pass; this file's tests skip the same way.

To run them locally:
  1. Start Postgres + apply migrations:
       docker compose up -d postgres
       docker compose run --rm api alembic upgrade head
  2. Point the env var at it + run pytest with the file selected:
       DATABASE_URL=postgresql://risk:risk@localhost:5432/riskdb \
         python3 -m pytest tests/test_db.py -v

The tests are pure-Python (psycopg v3 direct) — they do NOT depend on the
FastAPI app, so they don't pay the lifespan / model-training cost. They
exercise the same code paths the API uses at runtime:
  * ``AuditLogger`` Postgres mode (log → read → verify_chain)
  * ``CaseService`` Postgres mode (open_case → resolve → list_cases)
  * ``ModelRegistry`` Postgres mode (register_model → current_champion)
  * Idempotency ``idempotency_keys`` table (lookup → store → expiry)
  * SHA-256 hash-chain integrity across multiple log() calls

Each test is responsible for its own table cleanup (TRUNCATE) so they're
isolated + can be re-run in any order without residue.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Resolve the dual-mode switch ONCE at import time. If DATABASE_URL is
# unset OR points at a non-Postgres DSN (e.g. the host sandbox's
# ``file:/home/z/my-project/db/custom.db``), skip the entire file.
from src.config import get_settings  # noqa: E402

_SETTINGS = get_settings()
_POSTGRES_AVAILABLE = _SETTINGS.is_postgres


pytestmark = pytest.mark.skipif(
    not _POSTGRES_AVAILABLE,
    reason=(
        "DATABASE_URL not set to a Postgres DSN — skipping Postgres-path tests. "
        "Set DATABASE_URL=postgresql://risk:risk@localhost:5432/riskdb to run them."
    ),
)


@pytest.fixture(scope="module")
def db_conn():
    """A single psycopg connection for the whole test module. Module scope
    keeps the cost of psycopg.connect() amortized; per-test isolation comes
    from per-test TRUNCATE, not from per-test connections."""
    import psycopg

    conn = psycopg.connect(_SETTINGS.database_url, autocommit=False)
    # Apply migrations if the schema is empty (first run after a fresh
    # volume). Idempotent — alembic upgrade head is a no-op if already at
    # head. We do this in-fixture rather than in-conftest so this test file
    # is fully self-contained.
    try:
        import subprocess

        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=str(Path(__file__).resolve().parents[1]),
            check=True,
            env={**os.environ, "DATABASE_URL": _SETTINGS.database_url},
            capture_output=True,
            timeout=60,
        )
    except Exception:  # pragma: no cover — assume schema is already applied
        pass

    yield conn
    conn.close()


def _truncate(db_conn, *tables: str) -> None:
    """Per-test cleanup. TRUNCATE is faster than DELETE for small tables
    and resets the SERIAL id sequence so tests don't depend on prior rows."""
    with db_conn.cursor() as cur:
        cur.execute(f"TRUNCATE TABLE {', '.join(tables)} RESTART IDENTITY CASCADE")
        db_conn.commit()


def test_audit_log_to_postgres(db_conn):
    """AuditLogger.log → read → verify_chain all in Postgres mode."""
    from src.audit.logger import AuditLogger
    from src.config import get_settings

    get_settings.cache_clear()  # ensure the next AuditLogger sees DATABASE_URL
    _truncate(db_conn, "audit_records")

    log = AuditLogger(model_version="pytest-v1")
    audit_id = log.log(
        {
            "request": {"order_id": "DB-AUD-001"},
            "decision": "REVIEW",
            "mandate_type": "cod_order",
            "bh_purpose_code": None,
            "device_id": None,
            "user_id": None,
        }
    )
    assert audit_id.startswith("aud_")

    rec = log.read(audit_id)
    assert rec is not None
    assert rec["audit_id"] == audit_id
    assert rec["request"]["order_id"] == "DB-AUD-001"
    assert rec["model_version"] == "pytest-v1"
    # Track D mandate columns are surfaced as body JSONB keys too (per the
    # original audit payload contract — the typed columns are a fast-query
    # denormalization, not a replacement).
    assert rec["mandate_type"] == "cod_order"
    assert "raw_hash" in rec and "previous_hash" in rec

    ok, n_checked, first_bad = log.verify_chain()
    assert ok is True
    assert n_checked == 1
    assert first_bad == ""


def test_case_lifecycle_postgres(db_conn):
    """CaseService.open_case → resolve → list_cases (with status filter)."""
    from src.cases.service import CaseService
    from src.config import get_settings

    get_settings.cache_clear()
    _truncate(db_conn, "cases")

    cs = CaseService()
    cid = cs.open_case(
        prediction_id="pred-001", order_id="ORD-001", reason="db_test", actor="pytest"
    )
    assert cid.startswith("CASE-")

    out = cs.resolve(cid, decision="APPROVED", notes="ok", actor="admin")
    assert out["status"] == "APPROVED"

    open_cases = cs.list_cases(status="OPENED")
    approved_cases = cs.list_cases(status="APPROVED")
    assert not any(c["case_id"] == cid for c in open_cases)
    assert any(c["case_id"] == cid and c["status"] == "APPROVED" for c in approved_cases)


def test_model_registry_postgres(db_conn):
    """register_model demotes prior champion; current_champion returns the latest."""
    from src.config import get_settings
    from src.ml.registry import current_champion, register_model

    get_settings.cache_clear()
    _truncate(db_conn, "model_registry")

    # Register v1 as champion, then v2 as champion — v1 must demote.
    register_model("reg-v1", "/tmp/m1.pkl", {"pr_auc": 0.60}, champion=True)
    register_model("reg-v2", "/tmp/m2.pkl", {"pr_auc": 0.72}, champion=True)

    champ = current_champion()
    assert champ is not None
    assert champ["version"] == "reg-v2"
    assert champ["is_champion"] is True

    # The partial-unique index on is_champion=TRUE should enforce a single
    # champion — verify by counting.
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM model_registry WHERE is_champion = TRUE"
        )
        n = cur.fetchone()[0]
    assert n == 1


def test_idempotency_postgres(db_conn):
    """Direct table test of the idempotency_keys insert + lookup + expiry."""
    import json
    from datetime import datetime, timedelta, timezone

    from src.api.routes import _idem_lookup_postgres, _idem_store_postgres
    from src.config import get_settings

    get_settings.cache_clear()
    _truncate(db_conn, "idempotency_keys")

    state = {"settings": _SETTINGS}

    # Store a cached response.
    _idem_store_postgres(
        state,
        key="idem-test-1",
        request_body='{"order_id":"X"}',
        response_body={"prediction_id": "pred-1", "decision": "ACCEPT"},
        status_code=200,
    )
    # Lookup must return it.
    cached = _idem_lookup_postgres(state, "idem-test-1")
    assert cached is not None
    assert cached["prediction_id"] == "pred-1"
    assert cached["decision"] == "ACCEPT"

    # Manually expire the row + verify the lookup now misses.
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE idempotency_keys SET expires_at = %s WHERE key = %s",
            (datetime.now(timezone.utc) - timedelta(hours=1), "idem-test-1"),
        )
        db_conn.commit()
    assert _idem_lookup_postgres(state, "idem-test-1") is None


def test_hash_chain_postgres(db_conn):
    """Hash-chain integrity across 3 records + tamper-detection.

    AuditLogger.verify_chain recomputes the entire chain — if any record's
    body or hash is mutated, the chain breaks at the next record's
    prev_hash comparison.
    """
    from src.audit.logger import AuditLogger
    from src.config import get_settings

    get_settings.cache_clear()
    _truncate(db_conn, "audit_records")

    log = AuditLogger()
    id1 = log.log({"request": {"i": 1}, "decision": "ACCEPT"})
    id2 = log.log({"request": {"i": 2}, "decision": "REJECT"})
    id3 = log.log({"request": {"i": 3}, "decision": "REVIEW"})
    assert id1 != id2 != id3

    ok, n, bad = log.verify_chain()
    assert ok is True and n == 3 and bad == ""

    # Tamper with record 2's body — the chain should break at record 3
    # (because record 3's prev_hash was computed from record 2's original
    # raw_hash, not the mutated one).
    with db_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE audit_records
               SET body = '{"audit_id": "%s", "request": {"i": 999}, "decision": "TAMPERED"}'::jsonb
             WHERE audit_id = %s
            """,
            (id2, id2),
        )
        db_conn.commit()

    ok, n, bad = log.verify_chain()
    assert ok is False
    assert bad == id3  # the first record whose prev_hash no longer matches


def test_audit_tail_postgres(db_conn):
    """AuditLogger.tail(limit) returns the most-recent N records in
    chronological order — used by /v1/models/drift + audit-export CSV."""
    from src.audit.logger import AuditLogger
    from src.config import get_settings

    get_settings.cache_clear()
    _truncate(db_conn, "audit_records")

    log = AuditLogger()
    ids = [log.log({"request": {"i": i}, "decision": "ACCEPT"}) for i in range(5)]
    tail = log.tail(limit=3)
    assert len(tail) == 3
    # Most recent last (chronological order — the audit_export CSV expects
    # this so the last row in the CSV is the latest decision).
    assert tail[-1]["audit_id"] == ids[-1]
    assert tail[0]["audit_id"] == ids[-3]
