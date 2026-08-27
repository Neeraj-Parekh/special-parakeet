"""mandate_counters — persistent UPI Circle cumulative counters (T1.4)

Revision ID: 003
Revises: 002
Create Date: 2026-08-27 02:00:00 UTC

Track P (Subagent 11-a) — RTO Trust Layer third migration. Closes gap T1.4
in the orchestrator gap-list: the UPI Circle (NPCI OC-201B) cumulative
counters — ``_cumulative_monthly`` (₹15,000/month cap), ``_cumulative_24h``
(₹5,000 24h cooling window) and ``_last_activity`` (6-month auto-revoke)
were module-level in-memory dicts in ``src/api/mandates.py``. They reset
on every process restart, so the ₹15k/month cap and 6-month auto-revoke
were real within a single process but UNENFORCED across redeploys.

This migration persists the cumulative state in Postgres so the caps
survive process restarts, multi-worker deployments, and redeploy events.
The 24h cooling window is reconstructed at read time by filtering rows
on ``created_at > now - 86400`` rather than persisted as a separate list
— the rolling-window semantics fall out of the timestamp column for free.

Tables created (additive — Track E's 001_initial.py + Track H's
002_merkle_intervals.py are intact):

1. ``mandate_counters`` — one row per mandate ``sub`` (the salted
   customer_ref digest prefix that uniquely identifies a mandate). The
   row holds the running monthly cumulative spend + last-activity epoch
   timestamp; the 24h cooling window is derived from the
   ``mandate_counter_events`` companion table (one row per txn) rather
   than mutated in place — that gives the rolling-window filter for free
   and a queryable history for compliance audit export.
2. ``mandate_counter_events`` — append-only 24h-window event log. One
   row per UPI Circle txn (amount + epoch). The verify path prunes rows
   older than 24h on read so the table stays bounded under steady-state
   traffic; the cleanup is probabilistic (1% of verifies) like the
   idempotency_keys cleanup so the hot path stays cheap.

Why two tables instead of one?
   ``mandate_counters`` is the per-mandate current state (single row,
   fast UPSERT) so the monthly + last_activity reads are O(1). The 24h
   cooling check is fundamentally a *windowed* query ("any txn in the
   last 86400s with amount >= cooling_24h_inr"), which is awkward to
   encode as a single cumulative counter (you'd have to store the
   rolling window as a JSONB array). The events table gives the
   windowed query naturally with a partial index; the prune job keeps
   it small. The split mirrors the dual nature of the OC-201B caps:
   monthly + inactivity are *monotone state*; the 24h cooling is a
   *time-windowed predicate*.

This migration is idempotent (``CREATE TABLE IF NOT EXISTS`` /
``CREATE INDEX IF NOT EXISTS``) — re-running after a partial-failure
recovery doesn't error. Downgrade drops the events table before the
counters table because events has a FK to counters.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: Union[str, Sequence[str], None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # mandate_counters — per-mandate cumulative state (monthly + last    #
    # activity). PK on mandate_sub so the verify path can UPSERT in O(1). #
    # ------------------------------------------------------------------ #
    # cumulative_monthly is NUMERIC(14,2) — Indian Rupee amounts have 2
    # decimal places (paise) and the monthly cap is ₹15,000 so 14 digits
    # of total precision is comfortable headroom. last_activity_ts is
    # BIGINT unix epoch seconds (not TIMESTAMPTZ) because the in-memory
    # variant used time.time() — keeping the same unit makes the
    # in-memory ↔ DB swap trivial + keeps the math out of tz-handling.
    # updated_at is TIMESTAMPTZ for ops visibility ("when was this row
    # last touched?") — not consulted by the verify path.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS mandate_counters (
            mandate_sub         TEXT        PRIMARY KEY,
            cumulative_monthly  NUMERIC(14,2) NOT NULL DEFAULT 0,
            last_activity_ts    BIGINT,
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    # ------------------------------------------------------------------ #
    # mandate_counter_events — append-only 24h cooling window log.       #
    # ------------------------------------------------------------------ #
    # One row per UPI Circle txn. (ts, amt) so the verify path can
    # reconstruct the rolling 24h window with a single range filter
    # (``WHERE mandate_sub = %s AND ts > now - 86400``). mandate_sub has
    # no FK to mandate_counters because we want the events to outlive a
    # counter row reset (the counter is UPSERTed on first activity; an
    # event for a fresh mandate should still be queryable even if the
    # counter row doesn't exist yet — the verify path upserts both in
    # the same transaction, but the no-FK design keeps the events table
    # append-only and cheap to prune).
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS mandate_counter_events (
            id              BIGSERIAL PRIMARY KEY,
            mandate_sub     TEXT        NOT NULL,
            ts              BIGINT      NOT NULL,
            amount_inr      NUMERIC(14,2) NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    # Hot paths:
    #   - 24h cooling check
    #     (``SELECT amount_inr FROM mandate_counter_events
    #        WHERE mandate_sub = %s AND ts > %s ORDER BY ts DESC``)
    #     → covered by the (mandate_sub, ts) composite index below.
    #   - probabilistic cleanup (``DELETE FROM mandate_counter_events
    #     WHERE ts < %s``) → the same composite index serves the range
    #     scan; no separate index needed.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_mandate_counter_events_sub_ts "
        "ON mandate_counter_events (mandate_sub, ts DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_mandate_counter_events_ts "
        "ON mandate_counter_events (ts)"
    )


def downgrade() -> None:
    # Reverse-order. The events table is dropped first (no FK to
    # counters, but IF EXISTS guards make this idempotent + safe to
    # re-run after a partial downgrade).
    op.execute("DROP INDEX IF EXISTS ix_mandate_counter_events_ts")
    op.execute("DROP INDEX IF EXISTS ix_mandate_counter_events_sub_ts")
    op.execute("DROP TABLE IF EXISTS mandate_counter_events")
    op.execute("DROP TABLE IF EXISTS mandate_counters")
