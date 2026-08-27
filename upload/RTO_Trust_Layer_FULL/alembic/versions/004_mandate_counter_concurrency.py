"""mandate_counter concurrency + month reset + retention prune (C8/C9/C10)

Revision ID: 004
Revises: 003
Create Date: 2026-08-27 03:00:00 UTC

Phase 4 self-check fixes (Subagent 14-b). Closes three mandate-counter SQL
bugs found by the 25-question self-check (worklog Task 12-a):

  * **C8 (RACE CONDITION)** — the prior verify path split the counter
    read (``_read_db_counters``) and the counter write
    (``_write_db_counters``) across TWO separate transactions. Two
    concurrent ``/risk/score`` calls could both read the counter below
    the ₹15k/month cap, both decrement, and blow the ceiling. Fix: the
    new ``_begin_db_counter_txn`` in ``src/api/mandates.py`` wraps the
    read-increment-write in a single transaction with
    ``SELECT ... FOR UPDATE`` so concurrent verifies serialize on the
    per-mandate counter row.
  * **C9 (NO MONTH-BOUNDARY RESET)** — the "monthly cap" silently
    became a "lifetime cap" after the first month because there was no
    logic to detect month rollover + reset the counter. Fix: this
    migration adds a ``month_key`` ``VARCHAR(7)`` column
    (``YYYY-MM`` string) to ``mandate_counters``; the new
    ``_begin_db_counter_txn`` compares the stored ``month_key`` to the
    current ``time.strftime("%Y-%m")`` value, and if they differ,
    resets ``cumulative_monthly = 0`` + updates ``month_key`` to the
    current month — still holding the FOR UPDATE lock — before the
    increment proceeds.
  * **C10 (NO RETENTION PRUNE)** — ``mandate_counter_events`` grew
    unbounded. The original migration (003) said "the verify path prunes
    rows older than 24h on read so the table stays bounded under
    steady-state traffic" but that prune was NEVER actually implemented
    (a code-only aspiration, not a SQL execution). Fix: the new
    ``_DbCounterTxn.commit_increment`` in ``src/api/mandates.py`` runs
    ``DELETE FROM mandate_counter_events WHERE created_at <
    NOW() - INTERVAL '90 days'`` on EVERY counter-event INSERT, in the
    same transaction. This keeps the table bounded at ~90 days of
    events (the 24h cooling window only needs the last 24h, so 90 days
    is generous headroom for compliance audit export). The new index on
    ``created_at`` (below) makes the prune a fast range scan.

The migration is idempotent (``ALTER TABLE ... ADD COLUMN IF NOT
EXISTS`` + ``CREATE INDEX IF NOT EXISTS``) so a partial-failure recovery
re-run is safe. Downgrade drops the index + column.

Why an index on ``mandate_counter_events.created_at`` (separate from
the existing ``ix_mandate_counter_events_ts`` index on the ``ts``
column)?
   The existing ``ix_mandate_counter_events_ts`` indexes the
   high-precision unix-epoch ``ts`` column (used by the 24h cooling
   range filter ``WHERE ts > now - 86400``). The C10 prune filters on
   the TIMESTAMPTZ ``created_at`` column (``WHERE created_at < NOW() -
   INTERVAL '90 days'``), which is a different column. Postgres can't
   use the ``ts`` index for a ``created_at`` predicate, so a separate
   index is required. The two indexes serve different hot paths.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: Union[str, Sequence[str], None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # C9 — month_key column on mandate_counters.                          #
    # ------------------------------------------------------------------ #
    # VARCHAR(7) because the format is "YYYY-MM" (7 chars). NOT NULL with
    # DEFAULT '' so the migration is safe to apply to existing rows that
    # don't yet have a month_key set — the first verify call after this
    # migration back-fills the month_key (via the C9 reset branch in
    # _begin_db_counter_txn). DEFAULT '' rather than NULL so the column
    # can be NOT NULL without a backfill ALTER (Postgres evaluates the
    # DEFAULT for any row that doesn't have an explicit value during the
    # ADD COLUMN — fast for any table size).
    op.execute(
        "ALTER TABLE mandate_counters "
        "ADD COLUMN IF NOT EXISTS month_key VARCHAR(7) NOT NULL DEFAULT ''"
    )

    # ------------------------------------------------------------------ #
    # C10 — index on mandate_counter_events.created_at for the prune.    #
    # ------------------------------------------------------------------ #
    # The prune DELETE filters on ``created_at < NOW() - INTERVAL '90
    # days'``. Without an index on created_at, Postgres would seq-scan
    # the events table on every verify call (unbounded growth). The index
    # turns the prune into a fast range scan that touches only the rows
    # being deleted. Partial index isn't worth it here (the prune touches
    # ~1/365 of the table per day under steady-state — a partial index
    # would add complexity for no measurable gain).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_mandate_counter_events_created_at "
        "ON mandate_counter_events (created_at)"
    )


def downgrade() -> None:
    # Reverse-order. The index is dropped first (no dependency, but
    # IF EXISTS guards make this idempotent + safe to re-run after a
    # partial downgrade). The column is dropped second — this requires
    # the column to exist (which the IF EXISTS guard makes safe).
    op.execute("DROP INDEX IF EXISTS ix_mandate_counter_events_created_at")
    op.execute("ALTER TABLE mandate_counters DROP COLUMN IF EXISTS month_key")
