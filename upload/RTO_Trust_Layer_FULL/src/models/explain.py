from __future__ import annotations

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
