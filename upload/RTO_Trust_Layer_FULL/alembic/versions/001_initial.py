"""initial schema — audit_records, cases, model_registry, idempotency_keys, psi_reference

Revision ID: 001
Revises:
Create Date: 2026-08-27 00:00:00 UTC

Day 2 Track E — RTO Trust Layer first migration. Raw SQL via ``op.execute()``
per ``04-TECH-STACK-DECISIONS.md`` (V3 rejected heavy ORMs; we use psycopg v3
directly, not SQLAlchemy ORM, for all queries).

Tables created:
1. ``audit_records``   — replaces ``out/audit.jsonl``. The SHA-256 hash chain
   audit log (``src/audit/logger.py``). Columns cover Track D's new payload
   fields (mandate_type / bh_purpose_code / device_id / user_id) so OC-201B
   compliance metadata is queryable, not just JSONB-blobbed.
2. ``cases``            — replaces ``out/cases.jsonl`` (``src/cases/service.py``).
   Human-in-the-loop review queue. Separate table (not reusing audit_records)
   per the task spec — cleaner data model, proper indexes for the case queue
   query shape (filter by status, lookup by prediction_id).
3. ``model_registry``   — replaces ``out/model_registry.json`` (``src/ml/registry.py``).
   Champion/challenger metadata. Closes gap #5 (TFX-style registry) + §A item
   4 (register_model dead in prod — Track E wires it into the lifespan).
4. ``idempotency_keys`` — replaces the unbounded in-process dict
   ``state["idem"]`` (§A item 2 — memory leak). TTL via ``expires_at``; index
   for cleanup (DELETE WHERE expires_at < now()).
5. ``psi_reference``    — population reference distributions for the PSI drift
   metric. Currently the in-process ``state["psi_sample"]`` dict is recomputed
   on every worker boot — moving it to Postgres means drift can be computed
   cross-worker + cross-restart.

All tables use TIMESTAMPTZ (UTC) for created_at / resolved_at / deployed_at /
promoted_at — the audit trail must be timezone-stable across worker
restarts + cross-region deployments (per RBI PA data-localization note in
04-TECH-STACK-DECISIONS.md §compliance-posture).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1. audit_records — replaces out/audit.jsonl                        #
    # ------------------------------------------------------------------ #
    # raw_hash = sha256(canonical(body) + prev_hash). body is the full
    # audit payload (request, decision, reason_codes, features_used, etc.)
    # stored as JSONB so consumers can query arbitrary paths (e.g.
    # ``body->>'mandate_verdict_reason'``) without a schema migration.
    # Track D's mandate fields (mandate_type, bh_purpose_code, device_id,
    # user_id) are first-class columns so the compliance / audit-export
    # endpoints can do indexed queries ("all UPI Circle decisions for
    # device X in last 24h") without a JSONB expression index.
    op.execute(
        """
        CREATE TABLE audit_records (
            id              SERIAL PRIMARY KEY,
            audit_id        TEXT        NOT NULL UNIQUE,
            body            JSONB       NOT NULL,
            raw_hash        TEXT        NOT NULL,
            prev_hash       TEXT        NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            model_version   TEXT        NOT NULL DEFAULT 'dev',
            -- Track D (V3 §13) mandate compliance columns — nullable because
            -- cod_order mandates + rule-only / degraded decisions don't carry
            -- them. Indexed below.
            mandate_type       TEXT,
            bh_purpose_code    TEXT,
            device_id          TEXT,
            user_id            TEXT
        )
        """
    )
    # Primary query patterns:
    #   - by audit_id (the /audit/{id} endpoint) → UNIQUE index above suffices
    #   - by mandate_type / device_id / user_id (compliance audit-export)
    #     → covered by the columns; add composite index if hot path emerges
    #   - tail N records (drift endpoint / audit-export CSV) → PK id is enough
    op.execute(
        "CREATE INDEX ix_audit_records_created_at ON audit_records (created_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_audit_records_mandate_type_device_id "
        "ON audit_records (mandate_type, device_id) "
        "WHERE mandate_type IS NOT NULL"
    )

    # ------------------------------------------------------------------ #
    # 2. cases — replaces out/cases.jsonl                               #
    # ------------------------------------------------------------------ #
    # The file-mode logger was a JSONL of events (OPENED + RESOLVED), and
    # list_cases merged them in Python. The table is one row per case so
    # the merge goes away — a case is OPENED once and UPDATEd on resolve.
    # Status values: OPENED, UNDER_REVIEW, APPROVED, REJECTED, ESCALATED.
    op.execute(
        """
        CREATE TABLE cases (
            case_id          TEXT        PRIMARY KEY,
            prediction_id    TEXT,
            order_id         TEXT,
            merchant_id      TEXT,
            status           TEXT        NOT NULL DEFAULT 'OPENED',
            priority         TEXT        NOT NULL DEFAULT 'MEDIUM',
            assigned_to      TEXT,
            reason           TEXT,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            resolved_at      TIMESTAMPTZ,
            resolution_notes TEXT,
            resolution_by    TEXT,
            resolution_decision TEXT
        )
        """
    )
    # Hot paths: filter by status (case queue), lookup by prediction_id (the
    # /risk/score → case_id → resolve flow).
    op.execute("CREATE INDEX ix_cases_status ON cases (status)")
    op.execute("CREATE INDEX ix_cases_prediction_id ON cases (prediction_id)")
    op.execute("CREATE INDEX ix_cases_created_at ON cases (created_at DESC)")

    # ------------------------------------------------------------------ #
    # 3. model_registry — replaces out/model_registry.json               #
    # ------------------------------------------------------------------ #
    # Champion / challenger metadata. TFX-style canary gate (V3 §audit
    # rejected MLflow-server; the lightweight Postgres-backed registry is
    # the chosen approach per 04-TECH-STACK-DECISIONS.md §ML registry).
    # Only one is_champion row exists at a time — enforced by a partial
    # unique index (so a champion promotion flips the prior champion's
    # is_champion to FALSE in the same UPDATE).
    op.execute(
        """
        CREATE TABLE model_registry (
            version         TEXT        PRIMARY KEY,
            model_path      TEXT        NOT NULL,
            metrics         JSONB       NOT NULL DEFAULT '{}'::jsonb,
            is_champion     BOOLEAN     NOT NULL DEFAULT FALSE,
            is_challenger   BOOLEAN     NOT NULL DEFAULT FALSE,
            traffic_split   DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            drift_status    TEXT        NOT NULL DEFAULT 'unknown',
            deployed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            promoted_at     TIMESTAMPTZ
        )
        """
    )
    # At most one champion at a time. The partial index makes INSERT/UPDATE
    # of a second is_champion=TRUE row fail with a unique-violation — this
    # is the cheap race-condition-free promotion gate.
    op.execute(
        "CREATE UNIQUE INDEX ix_model_registry_single_champion "
        "ON model_registry (is_champion) WHERE is_champion = TRUE"
    )
    op.execute(
        "CREATE INDEX ix_model_registry_is_champion ON model_registry (is_champion)"
    )

    # ------------------------------------------------------------------ #
    # 4. idempotency_keys — replaces the unbounded state["idem"] dict   #
    # ------------------------------------------------------------------ #
    # §A item 2 (memory leak). TTL via expires_at; the /risk/score handler
    # does a probabilistic 1%-per-request cleanup (DELETE WHERE expires_at
    # < now()) so the table doesn't grow forever even under burst traffic.
    op.execute(
        """
        CREATE TABLE idempotency_keys (
            key            TEXT        PRIMARY KEY,
            request_body   TEXT        NOT NULL,
            response_body  TEXT        NOT NULL,
            status_code    INTEGER     NOT NULL DEFAULT 200,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at     TIMESTAMPTZ NOT NULL
        )
        """
    )
    # The cleanup query scans expires_at; the replay query reads by PK
    # (key) + filters expires_at. Both hot paths covered.
    op.execute(
        "CREATE INDEX ix_idempotency_keys_expires_at ON idempotency_keys (expires_at)"
    )

    # ------------------------------------------------------------------ #
    # 5. psi_reference — population reference for drift                 #
    # ------------------------------------------------------------------ #
    # Currently ``state["psi_sample"]`` is recomputed at every worker
    # boot from the training set mode. Moving it to Postgres means drift
    # is comparable cross-worker + cross-restart (the reference doesn't
    # shift every time the API redeploys). Track G (Day 2) will write here
    # after model promotion; the drift endpoint will read from here.
    op.execute(
        """
        CREATE TABLE psi_reference (
            id                  SERIAL PRIMARY KEY,
            feature_name        TEXT        NOT NULL,
            expected_distribution JSONB     NOT NULL,
            n_bins              INTEGER     NOT NULL DEFAULT 10,
            model_version       TEXT        NOT NULL DEFAULT 'dev',
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_psi_reference_feature ON psi_reference (feature_name, model_version)"
    )


def downgrade() -> None:
    # Reverse-order DROP. The IF EXISTS guard makes downgrade idempotent —
    # safe to run even if a previous downgrade partially failed.
    op.execute("DROP TABLE IF EXISTS psi_reference")
    op.execute("DROP TABLE IF EXISTS idempotency_keys")
    op.execute("DROP TABLE IF EXISTS model_registry")
    op.execute("DROP TABLE IF EXISTS cases")
    op.execute("DROP TABLE IF EXISTS audit_records")
