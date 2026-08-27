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
    p_orig: float | None = None,
    p_und: float | None = None,
    priors: dict | None = None,
) -> dict:
    """Register a model version. If ``champion`` is True, atomically demotes
    any existing champion to challenger in the same transaction.

    Postgres mode ignores ``registry_path`` (the table is the source of
    truth). File mode uses it.

    Parameters
    ----------
    p_orig, p_und : float | None
        Day 6 Track R — Bahnsen Eq.(6) post-resampling priors. ``p_orig`` is
        the minority (RTO) prior in the *original* training data BEFORE any
        resampling; ``p_und`` is the minority prior in the *resampled* training
        set (i.e. the prior the model was actually trained on). When both are
        supplied AND differ, the live decision path should call
        :func:`src.business.cost_optimizer.calibrate_probabilities` to undo
        the SMOTE / under-sampling inflation per Bahnsen 2013 Eq.(6). When
        equal (or both None), calibration is a no-op (the
        ``calibrate_probabilities`` fast path handles the equal case; the
        ``None`` case lets the caller skip calibration entirely).

        Both values are stored inside the ``metrics`` JSON column (file mode:
        inside the metrics dict on disk) so existing DB schema + file shape
        are unchanged. :func:`get_priors` reads them back out.

    priors : dict | None
        Day 8 Task E14 — full Bahnsen Eq.(6) priors dict (the
        :func:`src.models.train.compute_priors` shape)::

            {"p_orig": float, "p_und": float, "n_train": int,
             "n_pos_train": int, "calibration_method": "bahnsen_eq6",
             "created_at": "<iso8601>"}

        When provided, this is the **first-class** path: it is stored
        verbatim under the ``_priors`` key inside the model's metrics blob
        (file mode) / JSON column (Postgres mode), and the ``p_orig`` /
        ``p_und`` floats from the dict are ALSO folded into the metrics
        top-level so existing pre-E14 readers (the
        :func:`get_priors` legacy 2-key shape, the in-process lifespan
        registration in routes.py) continue to work unchanged. When
        ``None`` (the default), no priors are stored — the model is
        "pre-E14" and :func:`get_priors` returns the legacy
        ``{"p_orig": None, "p_und": None}`` shape. Mutually-compatible
        with the ``p_orig`` / ``p_und`` kwargs: if both are supplied,
        the dict's ``p_orig`` / ``p_und`` win (the dict is the
        first-class source).
    """
    metrics = dict(metrics) if isinstance(metrics, dict) else {}
    # Fold the priors dict (first-class path, E14) into the metrics blob
    # under the ``_priors`` key (read back by :func:`get_priors`) AND fold
    # p_orig/p_und to the top level so existing 2-key readers continue to
    # work. The kwargs (p_orig/p_und) are kept for backwards compatibility
    # with the Track-R lifespan registration path; the dict (if supplied)
    # is authoritative.
    if priors is not None:
        if not isinstance(priors, dict):
            raise TypeError(
                f"priors must be a dict or None (got {type(priors).__name__})"
            )
        # Defensive copy so callers can mutate their dict after registration
        # without leaking into the stored blob.
        priors_copy = dict(priors)
        # If the dict carries p_orig/p_und, those win over the kwargs.
        if "p_orig" in priors_copy:
            p_orig = priors_copy["p_orig"]
        if "p_und" in priors_copy:
            p_und = priors_copy["p_und"]
        metrics["_priors"] = priors_copy
    if p_orig is not None or p_und is not None:
        metrics["p_orig"] = p_orig
        metrics["p_und"] = p_und
    if _settings().is_postgres:
        return _register_model_postgres(version, model_path, metrics, champion)
    return _register_model_file(version, model_path, metrics, champion, registry_path)


def set_priors(
    version: str,
    priors: dict,
    registry_path: str = "out/model_registry.json",
) -> None:
    """Update the Bahnsen Eq.(6) priors blob on an already-registered model.

    Day 8 Task E14 — first-class artifact path. The :func:`register_model`
    ``priors`` kwarg is preferred when the priors are known at registration
    time (the train.py path). This function exists for the rare case where
    priors are computed AFTER registration (e.g. the model was registered
    by the in-process lifespan path with no priors, then an external
    auditor recomputes them from the training data and wants to backfill
    the registry without re-registering).

    Parameters
    ----------
    version : str
        Version tag of the model to update. Must already exist in the
        registry (raises ``KeyError`` otherwise — use
        :func:`register_model` for first-time registration).
    priors : dict
        Priors blob (same shape as the :func:`register_model` ``priors``
        kwarg). Must contain at least ``p_orig`` and ``p_und``.
    registry_path : str
        File-mode registry path (ignored in Postgres mode).

    Side effects
    ------------
    Mutates the stored metrics blob: writes the dict under the
    ``_priors`` key (first-class path) AND folds ``p_orig`` / ``p_und``
    to the metrics top-level (legacy compat path) so existing
    :func:`get_priors` callers continue to work. In Postgres mode the
    ``metrics`` JSON column is UPDATEd in a single transaction.
    """
    if not isinstance(priors, dict):
        raise TypeError(f"priors must be a dict (got {type(priors).__name__})")
    priors_copy = dict(priors)
    if "p_orig" not in priors_copy or "p_und" not in priors_copy:
        raise ValueError(
            "priors dict must contain at least 'p_orig' and 'p_und' keys; "
            f"got {sorted(priors_copy.keys())}"
        )
    if _settings().is_postgres:
        _set_priors_postgres(version, priors_copy)
        return
    reg = load_registry(registry_path)
    found = False
    for m in reg.get("models", []):
        if m.get("version") == version:
            metrics = dict(m.get("metrics") or {})
            metrics["_priors"] = priors_copy
            metrics["p_orig"] = priors_copy["p_orig"]
            metrics["p_und"] = priors_copy["p_und"]
            m["metrics"] = metrics
            found = True
            break
    if not found:
        raise KeyError(
            f"model version {version!r} not found in registry {registry_path}"
        )
    Path(registry_path).parent.mkdir(parents=True, exist_ok=True)
    Path(registry_path).write_text(json.dumps(reg, indent=2))


def _set_priors_postgres(version: str, priors_copy: dict) -> None:
    """Postgres implementation of :func:`set_priors` — UPDATE the metrics
    JSON column in a single transaction. Reads the current row, merges the
    priors blob into the metrics dict (preserving all existing metrics),
    writes it back. No champion flip — this is a metadata-only update.
    """
    conn = _get_conn()
    with _conn_lock:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT metrics FROM model_registry WHERE version = %s LIMIT 1",
                (version,),
            )
            row = cur.fetchone()
            if row is None:
                raise KeyError(
                    f"model version {version!r} not found in model_registry table"
                )
            (metrics_raw,) = row
            metrics = (
                metrics_raw if isinstance(metrics_raw, dict)
                else json.loads(metrics_raw)
            )
            metrics = dict(metrics) if isinstance(metrics, dict) else {}
            metrics["_priors"] = priors_copy
            metrics["p_orig"] = priors_copy["p_orig"]
            metrics["p_und"] = priors_copy["p_und"]
            cur.execute(
                "UPDATE model_registry SET metrics = %s WHERE version = %s",
                (json.dumps(metrics), version),
            )
            conn.commit()


def get_priors(
    model_version: str | None = None,
    registry_path: str = "out/model_registry.json",
) -> dict:
    """Return the Bahnsen Eq.(6) priors for the model (champion by default).

    Day 6 Track R (T2.2-helper) — exposes the per-model ``p_orig`` /
    ``p_und`` stored at registration time so the live decision path
    (routes.py:566) can call
    :func:`src.business.cost_optimizer.calibrate_probabilities` to undo
    SMOTE / under-sampling's inflated minority prior BEFORE the cost-optimal
    ACCEPT/REVIEW/REJECT fires. This is Bahnsen et al. (ICMLA 2013, DOI
    10.1109/ICMLA.2013.68) Eq.(6): ``P*(f|x) = P(f|x) · P_orig / P_und``.

    Parameters
    ----------
    model_version : str | None
        Optional version tag. If provided, looks up that specific model's
        stored priors; if None (default), reads from the current champion.
    registry_path : str
        File-mode registry path (ignored in Postgres mode — the table is
        the source of truth). Mirrors the ``registry_path`` parameter on
        :func:`current_champion` so tests can pass a tmp_path.

    Returns
    -------
    dict
        Day 8 Task E14 — when the model was registered via the first-class
        ``priors`` kwarg (the train.py / retrain_real.py path), returns the
        full priors blob (``p_orig``, ``p_und``, ``n_train``,
        ``n_pos_train``, ``calibration_method``, ``created_at``) verbatim.
        The ``p_orig`` / ``p_und`` keys remain at the top level so existing
        pre-E14 readers (the routes.py ``_priors.get("p_orig")`` pattern
        at lines 787 + 2464) continue to work without modification.

        Falls back to the legacy 2-key shape ``{"p_orig": float | None,
        "p_und": float | None}`` when the model was registered via the
        older ``p_orig`` / ``p_und`` kwargs (the Track-R lifespan path) OR
        when no priors were stored at all (the pre-Track-R path — both
        keys ``None``). Callers should treat both-None as a no-op signal
        — the live path skips calibration (the un-calibrated probability
        is used as-is, same as Track C's behaviour — correct when no
        resampling was applied).

    Interface contract for 11-routes
    --------------------------------
        priors = get_priors()  # or get_priors(champ["version"])
        if priors.get("p_orig") is not None and priors.get("p_und") is not None:
            proba = calibrate_probabilities([proba], priors["p_orig"], priors["p_und"])[0]
        decision, costs = optimal_decision(proba, amount_inr=order.amount_inr, **DEFAULT_COST_WEIGHTS)
    """
    if model_version is not None:
        m = _get_model_by_version(model_version, registry_path)
    else:
        m = current_champion(registry_path)
    if m is None:
        return {"p_orig": None, "p_und": None}
    metrics = m.get("metrics") or {}
    if not isinstance(metrics, dict):
        return {"p_orig": None, "p_und": None}
    # Day 8 Task E14 — first-class priors blob path. Returned verbatim
    # (it carries p_orig/p_und at the top level so all existing 2-key
    # readers keep working — routes.py:787, routes.py:2464, test_ship.py
    # assertions, the in-process lifespan registration path).
    if "_priors" in metrics and isinstance(metrics["_priors"], dict):
        return dict(metrics["_priors"])
    p_orig = metrics.get("p_orig")
    p_und = metrics.get("p_und")
    return {"p_orig": p_orig, "p_und": p_und}


def _get_model_by_version(
    version: str, registry_path: str = "out/model_registry.json"
) -> dict | None:
    """Look up a single model entry by its version tag.

    Postgres mode: SELECT on the model_registry table. File mode: linear
    scan through the registry JSON. Returns None when the version isn't
    found.
    """
    if _settings().is_postgres:
        return _get_model_postgres(version)
    reg = load_registry(registry_path)
    for m in reg.get("models", []):
        if m.get("version") == version:
            return m
    return None


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


def _get_model_postgres(version: str) -> dict | None:
    """Look up a single model entry by version tag (Postgres mode).

    Day 6 Track R (T2.2-helper) — used by :func:`get_priors` to read the
    stored ``p_orig`` / ``p_und`` priors when the caller asks for a
    specific version rather than the current champion.
    """
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT version, model_path, metrics, is_champion, is_challenger,
                   traffic_split, drift_status, deployed_at, promoted_at
              FROM model_registry
             WHERE version = %s
             LIMIT 1
            """,
            (version,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    (
        ver,
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
        "version": ver,
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
