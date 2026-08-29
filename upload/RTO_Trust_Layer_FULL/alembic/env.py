"""Alembic environment — RTO Trust Layer.

Day 2 Track E. We do NOT use SQLAlchemy's MetaData reflection (V3 rejected
heavy ORMs per ``04-TECH-STACK-DECISIONS.md``). Migrations are raw SQL via
``op.execute("CREATE TABLE ...")``; this file just wires the connection
string from ``src.config.Settings().database_url`` so alembic can connect
online or generate offline SQL.

The ``DATABASE_URL`` env var (or ``.env``) is the single source of truth.
If it is unset, alembic refuses to run — running migrations against a
non-existent DB would fail anyway, so we surface a clear error rather than
letting alembic fall back to its empty ``sqlalchemy.url`` from ``alembic.ini``.
"""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from psycopg import connect

from alembic import context

# Make ``src.config`` importable regardless of CWD — alembic is run from the
# project root (``alembic upgrade head``) or via ``docker compose run --rm
# api alembic upgrade head``; both have the project root on sys.path[0] after
# this insert.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import get_settings  # noqa: E402

config = context.config

# Standard alembic logging setup — only if the .ini had [loggers] sections
# (it does — see alembic.ini). Failures here are non-fatal.
if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except Exception:  # pragma: no cover — defensive; logging is optional
        pass

# The single migration target — ``alembic/versions/`` is a flat directory
# of one-revision-per-file; no labels needed yet.
target_metadata = None


def _resolve_url() -> str:
    """Resolve the DSN from ``src.config.Settings().database_url``.

    Raises a clear, actionable error if unset — better than alembic's
    default "could not connect to ''" error.
    """
    s = get_settings()
    if not s.database_url:
        raise RuntimeError(
            "DATABASE_URL is not set. Set it before running alembic:\n"
            "  local : export DATABASE_URL=postgresql://risk:risk@localhost:5432/riskdb\n"
            "  docker: docker compose run --rm api alembic upgrade head\n"
            "  .env  : DATABASE_URL=postgresql://risk:risk@localhost:5432/riskdb"
        )
    return s.database_url


def run_migrations_offline() -> None:
    """Offline mode — emit SQL to stdout without connecting to the DB.

    Usage: ``alembic upgrade head --sql`` produces the DDL script. Useful
    for review (you can read the SQL before applying) and for environments
    where the migration runner has no DB access (CI generates the script,
    an operator with DB perms reviews + applies).
    """
    url = _resolve_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=False,  # we're not using ORM reflection
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Online mode — connect to the DB and apply migrations in a transaction.

    Uses raw ``psycopg`` (v3) — NOT a SQLAlchemy engine, per
    ``04-TECH-STACK-DECISIONS.md``: "V3 explicitly rejected heavy ORMs".
    Each migration's ``op.execute()`` call goes through this connection.

    psycopg v3 connection objects expose the DBAPI cursor interface that
    alembic's ``psycopg`` dialect expects, so this works without SQLAlchemy
    being on the runtime path.
    """
    url = _resolve_url()
    # ``connect`` with autocommit-style transaction handling; alembic wraps
    # the migration in BEGIN/COMMIT itself via ``begin_transaction()``.
    conn = connect(url, autocommit=False)
    try:
        context.configure(
            connection=conn,
            target_metadata=target_metadata,
            compare_type=False,
        )
        with context.begin_transaction():
            context.run_migrations()
    finally:
        conn.close()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
