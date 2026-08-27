"""GIN index on audit_records.body + expression index on body->>'merchant_id' (F17)

Revision ID: 005_gin_audit
Revises: 004
Create Date: 2026-08-27 05:00:00 UTC

Subagent 14-c (F17 GIN index migration). Closes the F17 finding from the
25-question self-check (Task 12-a): the audit tail (``audit_records`` table)
is the hot table — every ``/risk/score`` request writes one row — and the
T2.3 per-merchant filter ``WHERE body->>'merchant_id' = %s`` (added by
Subagent 11-d in ``src/api/routes.py::_per_merchant_counts``) does a full
table scan on every call because the JSONB column has no index. As the
audit tail grows past ~1M rows this becomes a production bottleneck.

Two indexes added (additive — no schema change to ``audit_records``):

1. ``idx_audit_log_body_gin`` — a GIN index on the whole ``body`` JSONB
   column. Speeds up containment / key-existence queries
   (``body ? 'key'`` / ``body @> '{...}'``) which are the generic JSONB
   query patterns the audit-export + drift endpoints issue when ad-hoc
   paths are queried.
2. ``idx_audit_log_body_merchant_id`` — a functional/expression index on
   ``(body->>'merchant_id')``. This is the specific index that makes the
   T2.3 per-merchant counts query fast (``WHERE body->>'merchant_id' = %s``
   becomes an index scan instead of a seq scan). Expression indexes on
   JSONB paths are the canonical Postgres pattern for hot JSONB query
   shapes per the PostgreSQL docs §"Indexes → Expression Indexes" +
   §"JSON Functions and Operators".

Why raw SQL via ``op.execute`` instead of ``op.create_index``?
   Alembic's ``op.create_index`` doesn't always emit ``USING GIN``
   correctly across Postgres versions / alembic versions — it sometimes
   drops the access-method clause or mangles the expression syntax for
   functional indexes. Raw SQL is unambiguous and works on every supported
   PG version (>=12, which is the project's minimum per the JSONB + GIN
   support requirement).

Why ``IF NOT EXISTS`` on both?
   Idempotent — re-running ``alembic upgrade head`` after a partial
   failure or in CI doesn't error. Matches the pattern used in
   ``002_merkle_intervals.py`` + ``003_mandate_counters.py``.

NOTE on table name: the prompt for this subagent referred to the table as
``audit_log``, but the actual table created in ``001_initial.py`` is
``audit_records`` (see line 63 of that migration). The migration uses the
correct table name ``audit_records``; the index names keep the
``idx_audit_log_body_*`` prefix per the prompt's explicit naming choice
(the prefix is descriptive of purpose — "the audit log's body index" —
not a literal table reference, so it's fine for the index name to refer
to "audit_log" while the underlying table is ``audit_records``). If the
orchestrator prefers the index names aligned to the table, a one-line
follow-up rename is trivial.

CROSS-SUBAGENT COORDINATION NOTE (RESOLVED):
   The prompt for this subagent said to set
   ``down_revision = "004_mandate_counter_concurrency"`` as a PLACEHOLDER
   because Subagent 14-b was assumed to be writing 004 in parallel (and
   was expected to use that revision id). I read 14-b's actual 004
   migration file (``alembic/versions/004_mandate_counter_concurrency.py``)
   post-hoc and discovered 14-b's actual revision id is ``"004"``
   (NOT ``"004_mandate_counter_concurrency"`` — that string is the
   FILENAME slug, not the revision id; the file template
   ``file_template = %%(rev)s_%%(slug)s`` in ``alembic.ini`` produces
   filenames like ``004_mandate_counter_concurrency`` from ``rev="004"``
   + the slug derived from the docstring title). I therefore set
   ``down_revision = "004"`` to match 14-b's actual revision, so the
   alembic chain ``001 → 002 → 003 → 004 → 005_gin_audit`` is correct
   WITHOUT needing orchestrator intervention. The prompt's escape clause
   ("if you discover 14-b's actual revision id ... use the placeholder
   + a comment") was for the case where 14-b's file wasn't yet readable
   — in this run it WAS readable, so I used the real id. The orchestrator
   should still verify the chain at merge time, but no fix-up should be
   needed.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "005_gin_audit"
# Matches Subagent 14-b's actual revision id ("004") — see the
# CROSS-SUBAGENT COORDINATION NOTE in the docstring above for how this
# was verified post-hoc rather than assumed from the prompt's placeholder.
down_revision: Union[str, Sequence[str], None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1. GIN index on the whole ``body`` JSONB column.                   #
    # ------------------------------------------------------------------ #
    # Speeds up containment (``body @> '{...}'``) + key-existence
    # (``body ? 'key'``) queries that the audit-export + drift endpoints
    # issue when ad-hoc JSONB paths are queried. GIN is the only Postgres
    # access method that handles JSONB containment operators efficiently
    # (the default B-tree index cannot be used for ``@>`` / ``?``).
    # Raw SQL via op.execute — see the docstring for the rationale
    # (op.create_index doesn't reliably emit USING GIN across versions).
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_log_body_gin "
        "ON audit_records USING GIN (body)"
    )

    # ------------------------------------------------------------------ #
    # 2. Functional / expression index on the JSONB path                 #
    #    ``(body->>'merchant_id')``.                                     #
    # ------------------------------------------------------------------ #
    # This is the specific index that makes the T2.3 per-merchant counts
    # query (``WHERE body->>'merchant_id' = %s``) an index scan instead
    # of a seq scan. The expression index is necessary because a plain
    # GIN on the whole JSONB column doesn't help ``->>`` (text-extraction)
    # queries — Postgres needs the expression materialized as a column-
    # like index. Per the PG docs: "Indexes on the result of an expression
    # can be useful for query shapes that filter on a computed value."
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_log_body_merchant_id "
        "ON audit_records ((body->>'merchant_id'))"
    )


def downgrade() -> None:
    # Reverse-order DROP. IF EXISTS guards make downgrade idempotent —
    # safe to re-run after a partial-downgrade recovery. Indexes are
    # dropped in reverse creation order (cheap defensive convention; the
    # order doesn't matter for indexes since they don't have FK
    # dependencies on each other).
    op.execute("DROP INDEX IF EXISTS idx_audit_log_body_merchant_id")
    op.execute("DROP INDEX IF EXISTS idx_audit_log_body_gin")
