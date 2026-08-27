"""override_nonces — replay-nonce store for the dual-control override (A2)

Revision ID: 006_override_nonces
Revises: 005_gin_audit
Create Date: 2026-09-04 12:00:00 UTC

Wave 1 (Subagent 14-d) — closes gap A2 in the 25-question self-check:
the dual-control override request carried a timestamp but no server-side
replay-nonce store. Within the timestamp window (default 5 min), a
captured request could be replayed verbatim — admin1 + admin2's
co-signed override would fire twice, the audit trail would show two
records for one co-signing, and a malicious interceptor (TLS-terminating
proxy, sidecar logger, anyone with read access to a request log) could
re-execute the override until the timestamp window expired.

Fix design (per the task spec):
  * Add a ``nonce`` field (16-byte hex string = 32 chars) to the
    ``OverrideIn`` request body. The client MUST generate a fresh
    cryptographically-random nonce per request (uuid4().hex is fine —
    16 bytes of entropy is enough to make collisions astronomically
    unlikely at the override endpoint's traffic rate).
  * Store the SHA-256 HASH of the nonce (NOT the raw nonce) in a
    Postgres table so the table itself doesn't leak nonce values if
    the DB is compromised (defense in depth — same posture as
    ``redact_customer()`` in the audit logger).
  * INSERT on first sighting → 200 OK.
  * INSERT ON CONFLICT DO NOTHING → ``rowcount == 0`` means the nonce
    was already seen → 409 Conflict "replay detected".
  * Prune rows older than 1 day on every override so the table stays
    bounded (the timestamp window is 5 min — anything older than 1
    day is structurally useless; the timestamp check at the top of
    the handler rejects anything outside the window before this
    prune even runs).
  * File-mode fallback: when ``DATABASE_URL`` is unset (tests / single-
    process dev), the override handler uses a bounded in-memory LRU
    set of the last 10_000 nonce hashes + logs a warning that replay
    protection is in-memory only. The LRU cap protects against unbounded
    memory growth; in production, Postgres mode is authoritative.

Table created (additive — migrations 001/002/003 + Wave 1 peers 004/005
are intact):

1. ``override_nonces`` — one row per consumed nonce. PK on
   ``nonce_hash`` (SHA-256 hex of the raw nonce) so the INSERT ON
   CONFLICT path is a single index lookup. ``created_at`` carries the
   insertion timestamp; the prune job uses the
   ``idx_override_nonces_created_at`` index for the range-delete.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "006_override_nonces"
down_revision: Union[str, Sequence[str], None] = "005_gin_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # override_nonces — consumed replay-nonce store.                       #
    # ------------------------------------------------------------------ #
    # nonce_hash = SHA-256 hex of the raw 16-byte nonce value. We store
    # the HASH not the raw nonce so a DB compromise (read access to the
    # table via a SQL injection or a backup leak) does NOT reveal the
    # raw nonce values. The raw nonce is only meaningful in transit
    # (the client computed the dual-control HMAC chain over the raw
    # nonce? no — the nonce is NOT part of the HMAC chain per T1.1's
    # canonical_body = {prediction_id, decision, notes}; the nonce is
    # a separate one-shot replay-defense field). Defense in depth.
    #
    # PK on nonce_hash so the INSERT ON CONFLICT DO NOTHING path is a
    # single index probe + no separate unique-index lookup. The 0/1
    # rowcount after the INSERT tells the handler whether this is the
    # first sighting (200) or a replay (409).
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS override_nonces (
            nonce_hash  TEXT        PRIMARY KEY,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    # Hot path:
    #   - prune (``DELETE FROM override_nonces WHERE created_at < NOW()
    #     - INTERVAL '1 day'``) → covered by the created_at index.
    #   - INSERT ON CONFLICT DO NOTHING → covered by the PK on
    #     nonce_hash (no separate index needed).
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_override_nonces_created_at "
        "ON override_nonces (created_at)"
    )


def downgrade() -> None:
    # Drop the index first, then the table — IF EXISTS guards make
    # this idempotent + safe to re-run after a partial downgrade.
    op.execute("DROP INDEX IF EXISTS idx_override_nonces_created_at")
    op.execute("DROP TABLE IF EXISTS override_nonces")
