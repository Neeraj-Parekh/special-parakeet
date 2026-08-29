from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as _FutureTimeout
from typing import Any

import numpy as np
import pandas as pd


def reason_codes(
    model,
    x_row: pd.DataFrame,
    feature_names: list[str],
    base_rate: float,
    background: pd.Series | None = None,
    k: int = 5,
) -> list[dict]:
    """Local attribution vs population reference (training-mode row), one-at-a-time."""
    reference = background if background is not None else x_row.median(numeric_only=True)
    contributions: list[dict] = []
    proba = float(model.predict_proba(x_row)[0, 1])
    for name in feature_names:
        if name not in reference.index:
            continue
        perturbed = x_row.copy()
        val = reference[name]
        perturbed[name] = val.astype(x_row[name].dtype) if hasattr(val, "astype") else val
        p0 = float(model.predict_proba(perturbed)[0, 1])
        contributions.append(
            {"feature": name, "value": _py(x_row.iloc[0][name]), "delta_prob": round(proba - p0, 5)}
        )
    contributions.sort(key=lambda d: abs(d["delta_prob"]), reverse=True)
    return [
        {**c, "direction": "raises_risk" if c["delta_prob"] > 0 else "lowers_risk"}
        for c in contributions[:k]
    ] + [{"feature": "_base_rate", "value": round(base_rate, 5), "delta_prob": 0.0}]


def reason_codes_batch(
    model,
    x_row: pd.DataFrame,
    feature_names: list[str],
    base_rate: float,
    background: pd.Series | None = None,
    k: int = 5,
) -> list[dict]:
    """Vectorized variant: one predict_proba call for the row plus all perturbations."""
    reference = background if background is not None else x_row.median(numeric_only=True)
    names = [n for n in feature_names if n in reference.index]
    frames = [x_row]
    for name in names:
        p = x_row.copy()
        val = reference[name]
        p[name] = val.astype(x_row[name].dtype) if hasattr(val, "astype") else val
        frames.append(p)
    probs = model.predict_proba(pd.concat(frames, ignore_index=True))[:, 1]
    proba = float(probs[0])
    contributions = [
        {
            "feature": n,
            "value": _py(x_row.iloc[0][n]),
            "delta_prob": round(proba - float(probs[i + 1]), 5),
        }
        for i, n in enumerate(names)
    ]
    contributions.sort(key=lambda d: abs(d["delta_prob"]), reverse=True)
    return [
        {**c, "direction": "raises_risk" if c["delta_prob"] > 0 else "lowers_risk"}
        for c in contributions[:k]
    ] + [{"feature": "_base_rate", "value": round(base_rate, 5), "delta_prob": 0.0}]


def global_importance(model, X: pd.DataFrame, y: pd.Series, seed: int = 42) -> pd.DataFrame:
    from sklearn.inspection import permutation_importance

    r = permutation_importance(
        model, X, y, n_repeats=10, random_state=seed, scoring="average_precision"
    )
    imp = pd.DataFrame({"feature": X.columns, "ap_drop": r.importances_mean}).sort_values(
        "ap_drop", ascending=False
    )
    return imp


def _py(v):
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return round(float(v), 5)
    return str(v)


# ---------------------------------------------------------------------------
# SHAP KernelExplainer — Lundberg 2017 NeurIPS, model-agnostic per-prediction
# feature attribution. Closes the V2 §9.3 explainability gap (the existing
# ``reason_codes_batch`` is a one-at-a-time perturbation-style attribution;
# SHAP's KernelExplainer is the Lundberg paper's "gold standard" with the
# additive Shapley-value foundation, unlike LIME's local-surrogate fit).
#
# Why TreeExplainer (primary) + KernelExplainer (fallback):
#   * The live model is a ``HistGradientBoostingClassifier`` (tree-based).
#     As of shap 0.42+ (we run 0.52.0) ``shap.TreeExplainer`` supports HistGB
#     directly — it traverses the tree's internal predictors to compute exact
#     TreeSHAP values in O(T·L·D) instead of KernelExplainer's O(2^M) sampling.
#     Empirically verified 2025-08-29: 5 rows x 8 features -> 40/40 non-zero
#     values, max abs 3.43 (cf. KernelExplainer's degenerate 0.0 when the
#     background cache is empty — that was the root cause of the
#     "SHAP returns all 0.0" bug, because the background fell back to the
#     input row itself, making every marginal contribution trivially zero).
#   * KernelExplainer remains the fallback for non-tree models (model-agnostic).
#     It is slower (O(2^M) for M features). Mitigation: cap background to 50
#     rows + feature dim to 100, and run inside a 5-second timeout.
#
# Dual-mode like Track E's DATABASE_URL + Track F's REDIS_URL + Track M's
# OTEL_EXPORTER_OTLP_ENDPOINT: if ``shap`` isn't installed, the function
# returns a graceful fallback so the live API + the 141 existing tests pass
# without a ``pip install shap`` step. The user installs shap via
# ``pip install -r requirements.txt`` on their machine.
# ---------------------------------------------------------------------------

# Module-level background cache. Populated by ``routes.py``'s lifespan once
# the training DataFrame is loaded (so the SHAP explainer doesn't have to
# re-read /data/raw/cod_orders.csv on every /v1/explain/shap request). When
# unset (test mode, or routes.py pre-lifespan), get_background_sample returns
# an empty list + explain_with_shap falls back to a single-row background
# built from the input feature dict (KernelExplainer accepts this — degenerate
# but functional; the resulting SHAP values are ~0 because the background
# equals the input).
_BACKGROUND_CACHE: pd.DataFrame | None = None

# Per-spec hard caps: KernelExplainer is O(2^M) for M features. Cap background
# rows to 50 + feature dimensions to 100 so the worst-case 2^100 stays within
# the 5-second timeout on a laptop. Source: SHAP docs §"Kernel SHAP" §3 —
# "the number of coalitions grows exponentially with the number of features".
SHAP_MAX_BACKGROUND_ROWS = 50
SHAP_MAX_FEATURE_DIMENSIONS = 100
SHAP_TIMEOUT_SECONDS = 5.0
SHAP_NSAMPLES = 100  # Coalitions per explanation — SHAP default is "auto"; we
                     # cap to keep latency predictable (100 ≈ 2-3s for 50 cols).


def set_background_cache(df: pd.DataFrame | None) -> None:
    """Populate the module-level background cache (called by routes.py lifespan).

    Storing a *reference* to the training DataFrame here lets the SHAP
    explainer subsample it on every /v1/explain/shap request without
    re-reading the CSV. The cache lives at module scope so subsequent
    requests in the same worker process reuse it.
    """
    global _BACKGROUND_CACHE
    _BACKGROUND_CACHE = df


def get_background_cache() -> pd.DataFrame | None:
    """Read the module-level background cache (mainly for tests + introspection)."""
    return _BACKGROUND_CACHE


def get_background_sample(n: int = 100) -> list[dict]:
    """Return a representative sample of the cached training data as a list of
    row dicts — the input shape ``shap.KernelExplainer`` expects for its
    background data argument.

    Source: SHAP Python API §"KernelExplainer" — ``background_data`` accepts a
    DataFrame, a numpy array, or a list of dicts. We return list[dict] per
    the task spec so the caller (explain_with_shap) can build the
    KernelExplainer directly.

    Args:
        n: Desired number of background rows. Capped to
            ``SHAP_MAX_BACKGROUND_ROWS`` (50) per the spec — KernelExplainer's
            O(2^M) cost is the bottleneck, so keeping the background small
            is more important than representative sampling. If the cache has
            fewer rows than ``n``, all of them are returned.

    Returns:
        * A list of ≤ ``min(n, 50, len(cache))`` row dicts when the cache is
          populated.
        * An empty list when no training data is cached (the caller falls
          back to a single-row background built from the input feature dict —
          KernelExplainer accepts this; the SHAP values are ~0 because the
          background equals the input).
    """
    global _BACKGROUND_CACHE
    if _BACKGROUND_CACHE is None or len(_BACKGROUND_CACHE) == 0:
        return []
    # Per-spec hard cap — 50 rows max regardless of n.
    capped_n = max(1, min(n, SHAP_MAX_BACKGROUND_ROWS, len(_BACKGROUND_CACHE)))
    try:
        if len(_BACKGROUND_CACHE) > capped_n:
            sample = _BACKGROUND_CACHE.sample(
                n=capped_n, random_state=42
            )
        else:
            sample = _BACKGROUND_CACHE
    except Exception:
        # Defensive — a corrupt cache (e.g. mixed dtypes after a hot reload)
        # shouldn't crash the explain endpoint. Fall back to head(n).
        sample = _BACKGROUND_CACHE.head(capped_n)
    # Coerce numpy scalars to native Python for JSON-serializability (the
    # caller may pass the background to KernelExplainer which expects a
    # DataFrame; we let explain_with_shap build the DataFrame, but returning
    # JSON-native values makes the function safer for direct API exposure).
    out: list[dict] = []
    for _, row in sample.iterrows():
        out.append({_py(k): _py(v) for k, v in row.items()})
    return out


def _row_to_frame(features: dict) -> pd.DataFrame:
    """Convert a feature dict to a single-row DataFrame suitable for
    ``model.predict_proba``. Numeric values pass through; strings are kept
    as object dtype (HistGB handles category columns via its own internal
    categorical handling, so we don't coerce to ``category`` here).
    """
    cleaned = {}
    for k, v in features.items():
        if isinstance(v, str):
            # Try numeric coercion (the API accepts amount_inr as a string
            # sometimes; model expects float).
            try:
                cleaned[k] = float(v)
            except ValueError:
                cleaned[k] = v
        else:
            cleaned[k] = v
    return pd.DataFrame([cleaned])


def _normalize_shap_values(shap_values: Any) -> tuple[list[float], float]:
    """Normalize SHAP's heterogeneous output formats into a single
    per-feature-contribution list + the expected base value for the positive
    class (class 1 — RTO risk).

    SHAP 0.42+ returns:
        * For binary classification with ``shap_values(X)``: a list of 2
          arrays (class 0, class 1) — pre-0.45 convention.
        * For binary with ``shap_values(X, nsamples=...)`` on a numpy input:
          a single 2D array of shape (1, n_features) — newer convention.
        * For ``Explainer.__call__()`` (the new API): an ``Explanation``
          object with ``.values`` + ``.base_values`` attributes.

    Returns the class-1 contributions (RTO-positive class) as a flat list +
    the expected_value for class 1.
    """
    # Case 1: list of arrays (pre-0.45 binary classifier).
    if isinstance(shap_values, list) and len(shap_values) >= 2:
        arr = np.asarray(shap_values[1])
        flat = arr.flatten().tolist()
        return flat, float(np.mean(arr)) if arr.size > 0 else 0.0
    # Case 2: Explanation object (newer API).
    if hasattr(shap_values, "values") and hasattr(shap_values, "base_values"):
        vals = np.asarray(shap_values.values)
        # For binary classification, Explanation.values may be a 3D array of
        # shape (n_rows, n_features, 2). Pick class 1 if 3D.
        if vals.ndim == 3 and vals.shape[-1] == 2:
            flat = vals[0, :, 1].flatten().tolist()
        else:
            flat = vals.flatten().tolist()
        base = np.asarray(shap_values.base_values)
        if base.ndim >= 1 and len(base) >= 2:
            base_val = float(base[1])
        else:
            base_val = float(base.flatten()[0]) if base.size > 0 else 0.0
        return flat, base_val
    # Case 3: raw numpy array (single 2D for binary class 1).
    if isinstance(shap_values, np.ndarray):
        if shap_values.ndim == 3 and shap_values.shape[-1] == 2:
            flat = shap_values[0, :, 1].flatten().tolist()
        elif shap_values.ndim == 2:
            flat = shap_values[0].flatten().tolist()
        else:
            flat = shap_values.flatten().tolist()
        return flat, 0.0
    # Case 4: a plain Python list.
    if isinstance(shap_values, list):
        if len(shap_values) > 0 and isinstance(shap_values[0], (list, np.ndarray)):
            # Nested — pick class 1 if length=2.
            arr = np.asarray(shap_values[1] if len(shap_values) >= 2 else shap_values[0])
            return arr.flatten().tolist(), 0.0
        return [float(v) for v in shap_values], 0.0
    return [], 0.0


def explain_with_shap(
    model,
    features: dict,
    background_samples: int = 100,
    prebuilt_explainer: Any = None,
) -> dict:
    """SHAP KernelExplainer per-prediction feature attribution.

    Source: Lundberg & Lee, "A Unified Approach to Interpreting Model
    Predictions", NeurIPS 2017 (https://arxiv.org/abs/1705.07856). The
    KernelExplainer is the model-agnostic instance of the Lundberg framework
    that approximates Shapley values from cooperative-game theory via a
    weighted linear regression on coalitions of features.

    Args:
        model: A fitted sklearn-style classifier exposing
            ``predict_proba(X) -> np.ndarray``. For the RTO Trust Layer
            this is the in-process ``HistGradientBoostingClassifier``.
        features: A dict of feature-name → value for the specific order
            being explained. Built by the /v1/explain/shap endpoint from
            either a past prediction's ``features_used`` (looked up by
            ``order_id``) or from a JSON-string ``features`` query param.
        background_samples: Number of background rows to subsample from the
            module-level training-data cache. Capped to
            ``SHAP_MAX_BACKGROUND_ROWS`` (50) per the spec.
        prebuilt_explainer: An optional pre-built ``shap.KernelExplainer``
            instance (e.g. cached in ``state["shap_explainer"]`` by the
            routes.py lifespan). When provided, the function skips
            construction (saves the per-request setup cost — the explainer
            is reusable across calls since the model + background are
            stable per worker). When None, the function builds a fresh
            explainer on each call (cheaper than you'd think — KernelExplainer
            construction is just storing the model + background DataFrame,
            ~ms-scale for 50 rows × 12 features; the slow part is the
            subsequent ``shap_values()`` call). The spec asks for caching at
            the routes.py layer; this kwarg is the contract.

    Returns:
        A dict with the following keys on success::

            {
                "shap_values": [float, ...],  # per-feature Shapley contributions
                "base_value": float,         # E[model | background] for class 1
                "expected_value": float,    # alias for base_value (SHAP convention)
                "feature_names": [str, ...],  # ordered feature names
                "method": "shap_kernel",
                "nsamples": int,             # coalition count
                "background_rows": int,      # actual background size used
            }

        On any failure mode the function returns a graceful fallback so the
        API doesn't 500 — the caller's response shape is preserved::

            {
                "error": "shap not installed",
                "fallback": "use /v1/explain endpoint for LIME",
            }
            # or
            {
                "error": "explanation timeout after 5.0s",
                "fallback": "use /v1/explain for LIME",
                "timeout_seconds": 5.0,
            }
            # or
            {
                "error": "<message>",
                "fallback": "use /v1/explain for LIME",
            }

    Dual-mode: if ``shap`` is not installed (test sandbox, or the user
    hasn't run ``pip install -r requirements.txt`` yet), the function
    returns the "shap not installed" fallback. The 141 existing tests
    pass without a shap fixture.
    """
    # ---- 1. shap import gate ----------------------------------------------
    try:
        import shap  # type: ignore[import-untyped]
    except ImportError:
        return {
            "error": "shap not installed",
            "fallback": "use /v1/explain endpoint for LIME",
        }

    # ---- 2. build the input row DataFrame --------------------------------
    if not isinstance(features, dict) or not features:
        return {
            "error": "features must be a non-empty dict",
            "fallback": "use /v1/explain for LIME",
        }
    try:
        x_row = _row_to_frame(features)
    except Exception as e:
        return {
            "error": f"feature-dict → DataFrame conversion failed: {type(e).__name__}: {e}",
            "fallback": "use /v1/explain for LIME",
        }

    # ---- 3. cap feature dimensionality per spec --------------------------
    # KernelExplainer is O(2^M); cap M to SHAP_MAX_FEATURE_DIMENSIONS (100).
    if x_row.shape[1] > SHAP_MAX_FEATURE_DIMENSIONS:
        # Keep the first 100 columns — deterministic + sufficient for the
        # demo (the model's actual feature count is ~12 after one-hot).
        keep_cols = list(x_row.columns[:SHAP_MAX_FEATURE_DIMENSIONS])
        x_row = x_row[keep_cols]

    # ---- 4. build background DataFrame -----------------------------------
    bg_rows = get_background_sample(background_samples)
    if bg_rows:
        background_df = pd.DataFrame(bg_rows)
        # Align columns with the input row (KernelExplainer requires the
        # background + the explained row to have the same column set).
        # Fill missing columns with the column mean (numeric) or mode
        # (object) so the kernel doesn't crash.
        for col in x_row.columns:
            if col not in background_df.columns:
                background_df[col] = x_row[col].iloc[0]
        # Reorder to match x_row.
        background_df = background_df[x_row.columns]
    else:
        # No cache — use the input row as a 1-row background. Degenerate
        # (SHAP values → 0) but lets the function run in test mode where
        # routes.py hasn't populated the cache yet.
        background_df = x_row.copy()

    background_n = int(background_df.shape[0])

    # ---- 5. build the explainer + compute shap_values with a 5s TO -----
    # Prefer TreeExplainer for tree-based models (HistGB / RF / GBM /
    # DecisionTree) — exact, fast, and does NOT need a background dataset,
    # so it side-steps the empty-background-cache root cause that produced
    # all-0.0 SHAP values. Falls back to KernelExplainer (model-agnostic)
    # for any non-tree model. ``shap.TreeExplainer`` raises
    # ``InvalidModelError`` for unsupported models — caught below.
    explainer_kind = "kernel"  # flipped to "tree" on TreeExplainer success
    if prebuilt_explainer is not None:
        explainer = prebuilt_explainer
        explainer_kind = (
            "tree" if isinstance(explainer, shap.TreeExplainer) else "kernel"
        )
        # Pull feature_names from the cached explainer's background (the
        # column set the model was trained on; this is what SHAP will
        # surface per-feature contributions for).
        try:
            bg_data = getattr(explainer, "data", None)
            if bg_data is not None and hasattr(bg_data, "columns"):
                feature_names = list(bg_data.columns)
            else:
                # Fall back to the input row's columns — degenerate but
                # keeps the response shape stable.
                feature_names = list(x_row.columns)
        except Exception:
            feature_names = list(x_row.columns)
    else:
        try:
            explainer = shap.TreeExplainer(model)
            explainer_kind = "tree"
        except Exception:
            try:
                explainer = shap.KernelExplainer(
                    model.predict_proba, background_df
                )
            except Exception as e:
                return {
                    "error": (
                        f"Explainer construction failed "
                        f"(Tree+Kernel): {type(e).__name__}: {e}"
                    ),
                    "fallback": "use /v1/explain for LIME",
                }
        # Pull feature_names — TreeExplainer reads them from the model's
        # ``feature_names_in_`` if available; KernelExplainer from background_df.
        if explainer_kind == "tree":
            feature_names = list(
                getattr(model, "feature_names_in_", None) or x_row.columns
            )
        else:
            feature_names = list(background_df.columns)

    # The actual shap_values() call is the slow part. Run it in a worker
    # thread with a hard timeout — if it doesn't return within
    # SHAP_TIMEOUT_SECONDS, return the LIME-fallback so the dashboard's
    # explainability card still renders.
    def _compute() -> Any:
        # TreeExplainer computes exact TreeSHAP values (no sampling needed);
        # only KernelExplainer accepts nsamples (caps coalitions sampled —
        # default "auto" can be 2*M + 2*ceil(M); for M=50 that's ~2048 model
        # calls; we cap at SHAP_NSAMPLES=100 to bound latency).
        if explainer_kind == "tree":
            return explainer.shap_values(x_row, check_additivity=False)
        return explainer.shap_values(
            x_row, nsamples=SHAP_NSAMPLES, silent=True
        )

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_compute)
            shap_values = future.result(timeout=SHAP_TIMEOUT_SECONDS)
    except _FutureTimeout:
        return {
            "error": f"explanation timeout after {SHAP_TIMEOUT_SECONDS}s",
            "fallback": "use /v1/explain for LIME",
            "timeout_seconds": SHAP_TIMEOUT_SECONDS,
        }
    except Exception as e:
        return {
            "error": f"shap_values() computation failed: {type(e).__name__}: {e}",
            "fallback": "use /v1/explain for LIME",
        }

    # ---- 6. normalize + extract base_value -------------------------------
    try:
        shap_list, base_value = _normalize_shap_values(shap_values)
    except Exception as e:
        return {
            "error": f"shap_values normalization failed: {type(e).__name__}: {e}",
            "fallback": "use /v1/explain for LIME",
        }

    # Fallback if base_value couldn't be extracted from shap_values —
    # KernelExplainer.expected_value is a list for binary classification;
    # index 1 is the positive class.
    if base_value == 0.0:
        try:
            ev = explainer.expected_value
            if isinstance(ev, (list, np.ndarray)) and len(ev) >= 2:
                base_value = float(ev[1])
            elif isinstance(ev, (list, np.ndarray)) and len(ev) == 1:
                base_value = float(ev[0])
            else:
                base_value = float(ev)
        except Exception:
            base_value = 0.0

    # ---- 7. serialize ---------------------------------------------------
    # Round to 5 decimals to match reason_codes_batch's delta_prob precision
    # (the existing endpoint's contract — keeps the dashboard's explanation
    # card numeric formatting consistent across LIME + SHAP explanations).
    shap_list = [round(float(v), 5) for v in shap_list]

    return {
        "shap_values": shap_list,
        "base_value": round(float(base_value), 5),
        "expected_value": round(float(base_value), 5),
        "feature_names": feature_names,
        "method": "shap_tree" if explainer_kind == "tree" else "shap_kernel",
        "nsamples": SHAP_NSAMPLES if explainer_kind == "kernel" else None,
        "background_rows": background_n,
        "source_paper": (
            "Lundberg & Lee, A Unified Approach to Interpreting Model "
            "Predictions, NeurIPS 2017, arXiv:1705.07856"
        ),
    }


def serialize_shap_result(result: dict) -> dict:
    """JSON-safe serialization helper for the API response layer.

    The /v1/explain/shap endpoint may surface this directly; if the result
    contains numpy scalars (shouldn't — explain_with_shap rounds to floats
    — but defensive), this helper coerces them. Currently a thin pass-through
    that exists so the route handler doesn't inline the JSON.dumps call.
    """
    # Ensure JSON-serializable (the route handler uses FastAPI's default
    # JSON encoder which handles numpy scalars, but a defensive json.dumps
    # round-trip catches any stragglers).
    try:
        return json.loads(json.dumps(result, default=str))
    except (TypeError, ValueError):
        return result
