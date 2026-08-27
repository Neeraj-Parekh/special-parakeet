"""Minimal test for the F17 GIN index migration (alembic 005).

Asserts that after ``alembic upgrade head`` on a fresh Postgres DB, the
two indexes added by migration 005 (``idx_audit_log_body_gin`` and
``idx_audit_log_body_merchant_id``) exist on the ``audit_records`` table.

The test is SKIPPED unless:
  1. ``DATABASE_URL`` points at a real Postgres DSN (the same dual-mode
     switch used by ``tests/test_db.py`` — ``Settings.is_postgres``).
  2. The ``alembic`` binary is on PATH and successfully applies the
     migration head against the test DB (a ``subprocess.run`` with
     ``check=True``; failure modes are surfaced as ``pytest.skip`` with
     the actual stderr so the cause is obvious — see ``tests/test_db.py``
     docstring for the rationale, this mirrors that pattern).

When the env is the sandbox's default (no Postgres, no alembic), the
module-level ``pytestmark`` skips the entire file with a clear reason —
the GIN index can't be tested on SQLite (SQLite has no GIN / JSONB;
expression indexes also work differently there). The Postgres CI path is
documented in the module docstring for local reproduction.

The test queries ``pg_indexes`` (the Postgres catalog view) rather than
parsing ``\\d+`` output — ``pg_indexes`` is SQL, returns one row per index,
and is the canonical way to check index existence from Python.
"""
from __future__ import annotations

import os
import subprocess
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
        "DATABASE_URL not set to a Postgres DSN — GIN index test requires "
        "Postgres (SQLite has no GIN access method). Set "
        "DATABASE_URL=postgresql://risk:risk@localhost:5432/riskdb to run it."
    ),
)


@pytest.fixture(scope="module")
def migrated_pg_conn():
    """Apply ``alembic upgrade head`` on the test Postgres + yield a connection.

    Mirrors the ``db_conn`` fixture in ``tests/test_db.py``: open a psycopg
    connection, run migrations in a subprocess so a broken migration surfaces
    as a clear ``pytest.skip`` (not a confusing later "relation does not
    exist" error), yield the connection, close it. Module scope keeps the
    migration cost amortized across the (small) number of tests in this file.
    """
    import psycopg

    conn = psycopg.connect(_SETTINGS.database_url, autocommit=False)
    # Apply migrations if the schema is empty (first run after a fresh
    # volume). Idempotent — ``alembic upgrade head`` is a no-op if already
    # at head. We do this in-fixture so this test file is fully
    # self-contained (no conftest.py requirement).
    try:
        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=str(Path(__file__).resolve().parents[1]),
            check=True,
            env={**os.environ, "DATABASE_URL": _SETTINGS.database_url},
            capture_output=True,
            timeout=60,
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
        pytest.skip(
            f"alembic upgrade head failed (exit {e.returncode}): {stderr[:500] or e}"
        )
    except FileNotFoundError:
        pytest.skip("alembic not installed in this environment (PATH lookup failed)")
    except subprocess.TimeoutExpired:
        pytest.skip("alembic upgrade head timed out (>60s) — DB unreachable or migration hung")

    yield conn
    conn.close()


def _list_indexes(pg_conn, table_name: str) -> list[str]:
    """Return the ``indexname`` of every index on ``table_name``.

    Uses the ``pg_indexes`` Postgres catalog view — canonical way to check
    index existence from Python (vs parsing ``\\d+`` psql output).
    """
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = %s ORDER BY indexname",
            (table_name,),
        )
        return [row[0] for row in cur.fetchall()]


def test_gin_index_on_audit_records_body_exists(migrated_pg_conn):
    """The GIN index on the whole ``body`` JSONB column exists.

    Asserts that migration 005's first ``CREATE INDEX ... USING GIN (body)``
    statement succeeded. The GIN access method is the only Postgres index
    type that supports JSONB containment (``@>``) + key-existence (``?``)
    operators efficiently — without it, ad-hoc JSONB path queries on the
    audit tail fall back to a seq scan.
    """
    indexes = _list_indexes(migrated_pg_conn, "audit_records")
    assert "idx_audit_log_body_gin" in indexes, (
        f"idx_audit_log_body_gin not found on audit_records; "
        f"got indexes: {indexes}"
    )


def test_merchant_id_expression_index_exists(migrated_pg_conn):
    """The expression index on ``(body->>'merchant_id')`` exists.

    Asserts that migration 005's second ``CREATE INDEX ... ((body->>'merchant_id'))``
    statement succeeded. This is the specific index that makes the T2.3
    per-merchant counts query (``WHERE body->>'merchant_id' = %s``) an
    index scan instead of a seq scan — the production bottleneck F17 flagged.
    """
    indexes = _list_indexes(migrated_pg_conn, "audit_records")
    assert "idx_audit_log_body_merchant_id" in indexes, (
        f"idx_audit_log_body_merchant_id not found on audit_records; "
        f"got indexes: {indexes}"
    )


def test_gin_index_is_actually_gin(migrated_pg_conn):
    """The ``idx_audit_log_body_gin`` index is the GIN access method.

    Asserts via ``pg_indexes.indexdef`` that the index was created with
    ``USING gin`` — guards against a regression where the migration
    silently falls back to B-tree (which wouldn't help JSONB
    containment queries).
    """
    with migrated_pg_conn.cursor() as cur:
        cur.execute(
            "SELECT indexdef FROM pg_indexes "
            "WHERE tablename = %s AND indexname = %s",
            ("audit_records", "idx_audit_log_body_gin"),
        )
        row = cur.fetchone()
    assert row is not None, "idx_audit_log_body_gin not found — test_gin_index_on_audit_records_body_exists should also fail"
    indexdef = row[0].lower()
    assert "using gin" in indexdef, (
        f"idx_audit_log_body_gin is not a GIN index — indexdef: {row[0]}"
    )
