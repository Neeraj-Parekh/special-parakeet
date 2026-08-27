"""merkle intervals for tamper-evident audit (V3 §10.3)

Revision ID: 002
Revises: 001
Create Date: 2026-08-27 01:00:00 UTC

Day 2 Track H — RTO Trust Layer second migration. Closes §D item P11
(tamper-evident audit incomplete — Merkle intervals) per
05-PAPER-SKILLS-MAP.md gap #11 (source: SoK Mao 2026, capability
``recommend_layered_defenses`` layer 5: market & compliance monitoring
with tamper-evident audit trails; capability
``audit_agent_mandate_scoping`` for the dual-control override that
consumes this tamper-evidence layer).

What this migration adds (additive — Track E's 001_initial.py is intact):
1. ``audit_merkle_intervals`` — coarse Merkle interval sealing layer.
   Every N records (default 1000) or T seconds (default 3600), the
   ``MerkleSealer`` in ``src/audit/logger.py`` computes the Merkle root
   of the interval's ``raw_hash`` leaves, chains it to the previous
   interval's root, and inserts a row here.
2. ``audit_records.interval_id`` + ``audit_records.interval_position``
   — per-record back-reference so the ``GET /v1/audit/{id}/proof``
   endpoint can locate the leaf's interval + position in O(1) and
   construct the inclusion proof in O(log N) tree descent.

Why a SECOND layer on top of Track E's per-record hash chain?
   Track E's hash chain (``raw_hash = sha256(canonical(body) + prev_hash)``)
   is tamper-evident but full-chain verification is O(N) — for a 10M-row
   audit table that's a 10M-record recompute on every compliance audit.
   The Merkle interval layer amortizes: O(log N) inclusion proof per
   record, O(M) re-seal per interval where M = interval_size (default
   1000). The interval roots are themselves chained (``prev_interval_root``)
   so cross-interval tampering is detected the same way cross-record
   tampering is detected within one interval.

The transactional-outbox half of V3 §10.3 is deferred — Track F's
fire-and-forget Redis Streams publish is the pragmatic hackathon pattern;
the full outbox (audit row + outbox row in the same transaction, drained
by a worker that XADDs to Redis + DELETEs the outbox row) is a Day-3+
enhancement. The Merkle intervals here work in both modes — the sealer
is called from ``AuditLogger.log()`` after the audit INSERT.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, Sequence[str], None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # audit_merkle_intervals — coarse Merkle interval sealing layer     #
    # ------------------------------------------------------------------ #
    # One row per sealed interval. start_record_id / end_record_id cover
    # the [id, id] range of audit_records in this interval. merkle_root is
    # the root of the Merkle tree built from raw_hash leaves.
    # prev_interval_root chains intervals together (same tamper-evidence
    # model as Track E's per-record prev_hash — mutate any historical
    # interval and every subsequent interval's prev_interval_root breaks).
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_merkle_intervals (
            interval_id        SERIAL PRIMARY KEY,
            start_record_id    BIGINT      NOT NULL,
            end_record_id      BIGINT      NOT NULL,
            merkle_root        TEXT        NOT NULL,
            prev_interval_root TEXT        NOT NULL,
            leaf_count         INTEGER     NOT NULL,
            sealed_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    # Hot paths:
    #   - seal-time lookup of prev interval's root
    #     (``SELECT merkle_root ... ORDER BY interval_id DESC LIMIT 1``)
    #     → PK interval_id DESC scan suffices (small table, ~1k rows/day)
    #   - court-friendly export by sealed_at range (compliance audit
    #     "give me all intervals sealed in Q1 2026")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_merkle_intervals_sealed_at "
        "ON audit_merkle_intervals (sealed_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_merkle_intervals_root "
        "ON audit_merkle_intervals (merkle_root)"
    )

    # ------------------------------------------------------------------ #
    # Per-record back-reference                                          #
    # ------------------------------------------------------------------ #
    # interval_id points to the sealed interval this record belongs to
    # (NULL until the sealer reaches its threshold + flushes — at most
    # ``interval_size`` records are pending at any time). interval_position
    # is the leaf index within the interval (0-based) so the proof builder
    # knows which sibling to descend.
    # IF NOT EXISTS so this migration is idempotent — re-running after a
    # partial-failure recovery doesn't error.
    op.execute(
        "ALTER TABLE audit_records "
        "ADD COLUMN IF NOT EXISTS interval_id INT "
        "REFERENCES audit_merkle_intervals(interval_id)"
    )
    op.execute(
        "ALTER TABLE audit_records "
        "ADD COLUMN IF NOT EXISTS interval_position INT"
    )
    # Speed up the proof-builder query:
    #   ``SELECT raw_hash FROM audit_records WHERE interval_id = %s
    #      ORDER BY interval_position``
    # Without this index the query falls back to a full scan + sort —
    # fine at 1000 leaves per interval, but the index keeps it O(M).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_records_interval "
        "ON audit_records (interval_id, interval_position)"
    )


def downgrade() -> None:
    # Reverse-order. The per-record columns go first because they
    # reference audit_merkle_intervals (FK); dropping the table first
    # would cascade-fail without the IF EXISTS guard on each statement.
    op.execute("DROP INDEX IF EXISTS ix_audit_records_interval")
    op.execute("ALTER TABLE audit_records DROP COLUMN IF EXISTS interval_position")
    op.execute("ALTER TABLE audit_records DROP COLUMN IF EXISTS interval_id")
    op.execute("DROP TABLE IF EXISTS audit_merkle_intervals")
