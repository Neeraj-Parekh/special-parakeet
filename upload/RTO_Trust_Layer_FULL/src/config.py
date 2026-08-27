"""Centralised, typed configuration for the RTO Trust Layer.

Day 2 Track E (Postgres + Alembic dual-mode refactor). Closes the ad-hoc
``os.environ.get`` reads scattered across ``src/api/security.py`` (the
``_keys("RTO_SCORER_KEYS")`` / ``_keys("RTO_ADMIN_KEYS")`` calls),
``src/audit/logger.py`` (the ``self_salt()`` reading ``RTO_AUDIT_SALT``), and
``src/api/mandates.py`` (the ``RTO_MANDATE_SECRET`` read) — everything now
goes through one ``pydantic-settings``-backed ``Settings`` object that reads
the ``.env`` file if present (and otherwise the process environment, the
default ``BaseSettings`` behaviour).

CRITICAL design principle: dual-mode. If ``database_url`` is set, the audit
logger / case service / model registry / idempotency cache all use Postgres
(raw psycopg v3 — NO SQLAlchemy ORM per ``04-TECH-STACK-DECISIONS.md``: "V3
explicitly rejected heavy ORMs"). If it is ``None`` (test runs, local dev
without ``docker compose --profile full up``), the existing file-based JSONL
/ JSON behaviour is preserved unchanged so the 63 existing tests still pass
without a Postgres fixture.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Single source of truth for runtime configuration.

    All defaults are demo-only — override via ``.env`` (auto-loaded by
    ``BaseSettings``), environment variables, or the docker-compose
    ``environment:`` block. Never bake real secrets into the Dockerfile
    (Track B already removed the baked ``ENV`` defaults; keep that pattern).
    """

    # Dual-mode switch. None → file-based JSONL/JSON fallback (tests + local
    # dev without Postgres). A real DSN → Postgres + Alembic mode (prod,
    # docker-compose --profile full). Read once at module import via the
    # cached ``get_settings()`` below — sub-processes / workers share.
    database_url: str | None = None  # e.g. "postgresql://risk:risk@postgres:5432/riskdb"

    # Redis Streams URL (Track F Day 2). None → no streaming path; the API
    # silently falls back to synchronous request/response (existing behaviour).
    redis_url: str | None = None

    # API-key scopes. CSV strings parsed by ``default_keys()`` in
    # ``src/api/security.py`` so existing call sites work unchanged.
    rto_scorer_keys: str = "score-demo-key"
    rto_admin_keys: str = "admin-demo-key"

    # HMAC mandate secret (Track D / ``src/api/mandates.py``).
    rto_mandate_secret: str = "dev-only-secret"

    # Salt for the ``redact_customer()`` digest prefix in
    # ``src/audit/logger.py`` (sha256 truncate-16 prefix).
    rto_audit_salt: str = "local-demo-salt"

    # File-mode fallback paths (used when database_url is None — i.e. tests
    # + any non-docker run without DATABASE_URL exported).
    audit_path: str = "out/audit.jsonl"
    cases_path: str = "out/cases.jsonl"
    model_registry_path: str = "out/model_registry.json"

    # Idempotency TTLCache settings (file-mode only — Postgres mode uses the
    # ``idempotency_keys`` table with ``expires_at``).
    idem_maxsize: int = 10_000
    idem_ttl_seconds: int = 3600

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # tolerate unknown env keys (e.g. Prometheus/Grafana)
    )

    @property
    def is_postgres(self) -> bool:
        """True iff ``database_url`` is a real Postgres DSN.

        We only enter Postgres mode for ``postgresql://`` / ``postgres://`` /
        ``postgresql+psycopg://`` schemes — anything else (incl. ``None`` or
        a SQLite-style ``file:`` URL leaked from a host sandbox env var) falls
        through to file mode so the 63 existing tests still pass without a
        Postgres fixture. This also makes the dual-mode switch robust to
        environments where ``DATABASE_URL`` is set to a non-Postgres DSN.
        """
        if not self.database_url:
            return False
        return self.database_url.startswith(
            ("postgresql://", "postgres://", "postgresql+psycopg://")
        )


@lru_cache
def get_settings() -> Settings:
    """Process-wide cached ``Settings`` instance.

    ``lru_cache`` makes this idempotent — repeated calls return the same
    object, and the env-var reads happen exactly once. Tests that mutate
    env vars between cases (e.g. ``test_mandates.py::test_expired_mandate_rejected``
    flips ``RTO_MANDATE_SECRET`` then restores it) call
    ``get_settings.cache_clear()`` to pick up the change — see the test
    fixtures. In prod (single long-lived worker) the cache is never cleared.
    """
    return Settings()
