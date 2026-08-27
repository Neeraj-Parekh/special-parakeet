"""api_keys table — merchant_id binding for multi-tenant isolation (F19)

Revision ID: 007_api_key_merchant
Revises: 006_override_nonces
Create Date: 2026-09-05 12:00:00 UTC

Wave 2 (Subagent 14-e) — closes gap F19 in the 25-question self-check:
the multi-tenant SaaS posture (multiple merchants on one RTO Trust
Layer instance) had NO merchant-tenant isolation. Any scorer-scope
API key could query ANY merchant's audit records + mandate counters.
Merchant A's scorer key could read merchant B's audit tail via
``/v1/compliance/audit-export`` or look up merchant B's prediction via
``/v1/audit/{audit_id}/proof`` — cross-tenant data leakage.

Fix design (per the task spec):
  * Bind each API key to a ``merchant_id`` claim at key-creation time.
    The authoritative store for the binding is the new ``api_keys``
    table (created here) — keyed by ``key_id`` (the SHA-256 hash of
    the raw API key string, so the table itself doesn't leak raw keys
    if the DB is compromised — same redaction posture as the override
    replay-nonce table from migration 006).
  * The ``scope`` column carries the key's authorized scope
    (``scorer`` / ``ops`` / ``admin``) — used by Subagent 14-e's D13
    fix (``enforce_agent_action`` in routes.py consults the key's
    scope to gate the requested ``X-Agent-Action`` against the
    scope→actions mapping in ``src/api.agent_allowlist``).
  * The ``merchant_id`` column is the multi-tenant isolation key.
    The new ``enforce_merchant_isolation`` Depends in routes.py
    reads the caller's ``merchant_id`` claim + injects it as a
    forced ``WHERE body->>'merchant_id' = %s`` filter on ALL data-
    access queries (audit tail lookup, override proof lookup, SHAP
    explain lookup, ``/v1/usage`` metering, ``/v1/cases`` queue).
    Cross-tenant queries → 403 Forbidden ("cross-tenant access
    denied").
  * File-mode fallback (DATABASE_URL unset): the binding is read
    from the ``RTO_KEY_MERCHANT_BINDINGS`` env var (CSV of
    ``key:merchant_id`` pairs). The file-mode path is what the new
    ``tests/test_tenant_isolation.py`` exercises end-to-end.

Table created (additive — migrations 001-006 are intact; this migration
is idempotent — IF NOT EXISTS guards make it safe to re-run after a
partial downgrade):

1. ``api_keys`` — one row per API key. PK on ``key_id`` (the SHA-256
   hex of the raw API key string) so a key-creation INSERT ON CONFLICT
   is a single index probe. ``scope`` defaults to ``scorer``; the
   merchant_id column is nullable (legacy pre-F19 keys that pre-date
   the merchant binding can be back-filled by an admin). ``revoked``
   column for forward-compat revocation without deletion (the
   ``enforce_merchant_isolation`` Depends will eventually check
   ``revoked=FALSE``). ``created_at`` is the audit timestamp.

The migration creates the table if it doesn't exist; if the table
already exists (forward-port from a future bootstrap), the IF NOT
EXISTS guard makes the migration idempotent + the merchant_id column
is added via ALTER TABLE IF NOT EXISTS (Postgres ≥9.6 supports
``ADD COLUMN IF NOT EXISTS``).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "007_api_key_merchant"
down_revision: Union[str, Sequence[str], None] = "006_override_nonces"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # api_keys — key_id (SHA-256 hex of raw key) → scope + merchant_id.   #
    # ------------------------------------------------------------------ #
    # The table is the authoritative source for "which merchant does
    # this API key belong to" + "what scope does this key carry".
    # ``enforce_merchant_isolation`` Depends in routes.py looks up the
    # caller's key_hash here (in Postgres mode) OR consults the env-var
    # fallback ``RTO_KEY_MERCHANT_BINDINGS`` (file mode). The key_id is
    # the SHA-256 hex of the raw key so a DB read access (SQL injection,
    # backup leak) doesn't reveal raw keys — defense in depth.
    #
    # ``scope`` is one of ``scorer`` / ``ops`` / ``admin``. Default
    # ``scorer`` (least-privilege). The D13 fix in
    # ``src.api.agent_allowlist`` consults the scope column to gate the
    # requested ``X-Agent-Action`` against the scope→actions mapping.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS api_keys (
            key_id       TEXT        PRIMARY KEY,
            key_hash     TEXT        NOT NULL UNIQUE,
            scope        TEXT        NOT NULL DEFAULT 'scorer',
            merchant_id  TEXT,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            revoked      BOOLEAN     NOT NULL DEFAULT FALSE
        )
        """
    )
    # The merchant_id index is the hot lookup path —
    # ``enforce_merchant_isolation`` reads it on every data-access call
    # to filter queries to the caller's merchant. IF NOT EXISTS makes
    # the migration idempotent.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_api_keys_merchant_id "
        "ON api_keys (merchant_id) WHERE merchant_id IS NOT NULL"
    )
    # The scope index is the D13 hot path — scope→action enforcement
    # reads it on every ``X-Agent-Action``-bearing request.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_api_keys_scope "
        "ON api_keys (scope)"
    )
    # Forward-compat: if the table existed pre-007 without the
    # merchant_id column (e.g. a partial bootstrap that created the
    # table without merchant isolation), ALTER TABLE ADD COLUMN IF NOT
    # EXISTS adds it idempotently. Postgres ≥9.6 supports this; older
    # Postgres would error (caught by the IF NOT EXISTS check).
    op.execute(
        "ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS merchant_id TEXT"
    )


def downgrade() -> None:
    # Drop the indexes first, then the table — IF EXISTS guards make
    # this idempotent + safe to re-run after a partial downgrade.
    # Pattern mirrors migrations 001-006.
    op.execute("DROP INDEX IF EXISTS ix_api_keys_scope")
    op.execute("DROP INDEX IF EXISTS ix_api_keys_merchant_id")
    op.execute("DROP TABLE IF EXISTS api_keys")
