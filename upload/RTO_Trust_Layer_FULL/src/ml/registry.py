"""Model registry (champion/challenger metadata) + PSI drift metric.

Dual-mode (Day 2 Track E):
  * Postgres (DATABASE_URL set): ``model_registry`` table (one row per
    registered version; ``is_champion`` partial-unique index enforces
    single champion).
  * File fallback: the original JSON file at ``out/model_registry.json``.

Closes §A item 4 — ``register_model`` was previously dead in prod (only
called from tests; champion always None at runtime). Track E wires it into
the ``src/api/routes.py`` lifespan so every worker boot registers its
in-process HistGB with PR-AUC + ROC-AUC as metrics.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import numpy as np

# Module-level connection cache (Postgres mode only). Lazily opened on first
# register_model / current_champion call; the lifespan closes it on shutdown.
_conn = None
_conn_lock = threading.Lock()


def _settings():
    from src.config import get_settings

    return get_settings()


def _get_conn():
    global _conn
    if _conn is not None:
        return _conn
    with _conn_lock:
        if _conn is None:
            import psycopg

            _conn = psycopg.connect(_settings().database_url, autocommit=False)
    return _conn


def _close_conn() -> None:
    """Called by the lifespan shutdown handler (Track E wiring)."""
    global _conn
    with _conn_lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None


def load_registry(path: str | None = None) -> dict:
    """File mode only — kept for tests (test_platform.py::test_registry_register_and_champion
    uses a tmp_path registry) and for the cost-curve crossover lookup (which
    reads non-champion models for the challenger comparison).
    """
    p = Path(path or _settings().model_registry_path)
    if not p.exists():
        return {"models": []}
    return json.loads(p.read_text())


def register_model(
    version: str,
    model_path: str,
    metrics: dict,
    champion: bool = True,
    registry_path: str = "out/model_registry.json",
) -> dict:
    """Register a model version. If ``champion`` is True, atomically demotes
    any existing champion to challenger in the same transaction.

    Postgres mode ignores ``registry_path`` (the table is the source of
    truth). File mode uses it.
    """
    if _settings().is_postgres:
        return _register_model_postgres(version, model_path, metrics, champion)
    return _register_model_file(version, model_path, metrics, champion, registry_path)


def current_champion(registry_path: str = "out/model_registry.json") -> dict | None:
    if _settings().is_postgres:
        return _current_champion_postgres()
    return _current_champion_file(registry_path)


def psi(expected: list[float], actual: list[float], bins: int = 10) -> float:
    """Population Stability Index. <0.1 stable, 0.1-0.25 shift, >0.25 retrain.

    Pure numpy — unchanged from the original implementation. Not tied to
    the storage backend (PSI is computed over an in-memory reference +
    observed array).
    """
    e, a = np.asarray(expected, dtype=float), np.asarray(actual, dtype=float)
    e, a = e[~np.isnan(e)], a[~np.isnan(a)]
    if len(e) == 0 or len(a) == 0:
        return 0.0
    edges = np.unique(np.quantile(e, np.linspace(0, 1, bins + 1)))
    if len(edges) < 2:
        return 0.0
    ep = np.histogram(e, edges)[0] / len(e)
    ap = np.histogram(a, edges)[0] / len(a)
    eps = 1e-6
    return float(np.sum((ap - ep) * np.log((ap + eps) / (ep + eps))))


# --------------------------------------------------------------------- #
# Postgres mode                                                        #
# --------------------------------------------------------------------- #

def _register_model_postgres(
    version: str, model_path: str, metrics: dict, champion: bool
) -> dict:
    conn = _get_conn()
    with _conn_lock:
        with conn.cursor() as cur:
            # Single transaction: demote prior champion (if any) + INSERT.
            if champion:
                cur.execute(
                    """
                    UPDATE model_registry
                       SET is_champion = FALSE, promoted_at = NULL
                     WHERE is_champion = TRUE AND version <> %s
                    """,
                    (version,),
                )
            # UPSERT on version PK so re-registering the same version (e.g.
            # the lifespan re-registering v1234567890 after a worker restart)
            # doesn't fail.
            cur.execute(
                """
                INSERT INTO model_registry
                  (version, model_path, metrics, is_champion, is_challenger,
                   traffic_split, drift_status, deployed_at, promoted_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (version) DO UPDATE SET
                  model_path     = EXCLUDED.model_path,
                  metrics        = EXCLUDED.metrics,
                  is_champion    = EXCLUDED.is_champion,
                  is_challenger  = EXCLUDED.is_challenger,
                  traffic_split  = EXCLUDED.traffic_split,
                  drift_status   = EXCLUDED.drift_status,
                  deployed_at    = EXCLUDED.deployed_at,
                  promoted_at    = EXCLUDED.promoted_at
                """,
                (
                    version,
                    model_path,
                    json.dumps(metrics),
                    champion,
                    not champion,
                    0.0 if champion else 0.5,  # challenger gets a 50% slice
                    "unknown",
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    if champion
                    else None,
                ),
            )
            conn.commit()
    return {
        "version": version,
        "model_path": model_path,
        "metrics": metrics,
        "is_champion": champion,
        "deployed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _current_champion_postgres() -> dict | None:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT version, model_path, metrics, is_champion, is_challenger,
                   traffic_split, drift_status, deployed_at, promoted_at
              FROM model_registry
             WHERE is_champion = TRUE
             ORDER BY deployed_at DESC, version DESC
             LIMIT 1
            """
        )
        row = cur.fetchone()
    if row is None:
        return None
    (
        version,
        model_path,
        metrics_raw,
        is_champ,
        is_chall,
        traffic,
        drift,
        deployed,
        promoted,
    ) = row
    metrics = metrics_raw if isinstance(metrics_raw, dict) else json.loads(metrics_raw)
    return {
        "version": version,
        "model_path": model_path,
        "metrics": metrics,
        "is_champion": is_champ,
        "is_challenger": is_chall,
        "traffic_split": float(traffic),
        "drift_status": drift,
        "deployed_at": deployed.isoformat() if deployed else None,
        "promoted_at": promoted.isoformat() if promoted else None,
    }


# --------------------------------------------------------------------- #
# File mode (unchanged behaviour from the original implementation)       #
# --------------------------------------------------------------------- #

def _register_model_file(
    version: str,
    model_path: str,
    metrics: dict,
    champion: bool,
    registry_path: str,
) -> dict:
    reg = load_registry(registry_path)
    if champion:
        for m in reg["models"]:
            m["is_champion"] = False
    entry = {
        "version": version,
        "model_path": model_path,
        "metrics": metrics,
        "is_champion": champion,
        "deployed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    reg["models"].append(entry)
    Path(registry_path).parent.mkdir(parents=True, exist_ok=True)
    Path(registry_path).write_text(json.dumps(reg, indent=2))
    return entry


def _current_champion_file(registry_path: str) -> dict | None:
    reg = load_registry(registry_path)
    champions = [m for m in reg["models"] if m.get("is_champion")]
    return champions[-1] if champions else None
