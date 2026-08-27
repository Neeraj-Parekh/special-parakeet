from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

ORDER_FEATURES = {
    "numeric": [
        "log_order_value",
        "discount_pct",
        "Items",
        "OrderDay",
        "OrderHour",
        "PriorOrders",
        "PriorReturns",
        "is_cod",
    ],
    "categorical": ["category", "device", "city_tier"],
}

ADDR_FEATURES = {"numeric": [], "categorical": ["address_quality"]}
GEO_FEATURES = {
    "numeric": ["state_offices", "state_delivery_share", "state_rural_bo_share"],
    "categorical": [],
}


def build_feature_frame(df: pd.DataFrame, feature_set: str) -> tuple[pd.DataFrame, pd.Series]:
    cols_num: list[str] = []
    cols_cat: list[str] = []

    def take(spec: dict[str, list[str]]) -> None:
        cols_num.extend([c for c in spec["numeric"] if c in df.columns])
        cols_cat.extend([c for c in spec["categorical"] if c in df.columns])

    take(ORDER_FEATURES)
    if feature_set in {"order+addr", "full"}:
        take(ADDR_FEATURES)
    if feature_set == "full":
        take(GEO_FEATURES)

    for c in cols_num:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in cols_cat:
        df[c] = df[c].astype("category")
    X = df[cols_num + cols_cat]
    if "is_returned" in df.columns:
        return X, df["is_returned"]
    return X, None


def fit_model(X: pd.DataFrame, y: pd.Series, seed: int = 42):
    model = HistGradientBoostingClassifier(
        max_iter=300,
        learning_rate=0.08,
        max_depth=6,
        categorical_features="from_dtype",
        l2_regularization=1.0,
        random_state=seed,
    )
    model.fit(X, y)
    return model


def save_model(model, path: str) -> None:
    import joblib

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path)


def load_model(path: str):
    import joblib

    return joblib.load(path)


# --------------------------------------------------------------------- #
# Day 8 Task E14 — Bahnsen Eq.(6) priors wiring                        #
# --------------------------------------------------------------------- #
# Self-check E14: "train.py does NOT pass priors to the model registry,
# which means calibrate_probabilities() has nothing to resample against →
# the entire cost-optimizer math is a no-op at inference."
#
# Fix: compute p_orig (the original training-set positive rate) + p_und
# (the positive rate AFTER any class-balancing; identity = p_orig when
# no resampling was done — recorded honestly so the live path's
# `p_orig == p_und` fast path in calibrate_probabilities is a no-op
# correctly) and pass them through register_model(priors=...) so the
# cost-optimizer's calibrate_probabilities([proba], p_orig, p_und) at
# routes.py:787 + 2464 has real numbers to resample against.
#
# Reference: Bahnsen et al. ICMLA 2013, DOI 10.1109/ICMLA.2013.68 Eq.(6):
#   P*(f|x) = P(f|x) · P_orig / P_und


def compute_priors(
    y_train: pd.Series,
    y_und: pd.Series | None = None,
    calibration_method: str = "bahnsen_eq6",
) -> dict:
    """Compute the Bahnsen Eq.(6) priors blob for the model registry.

    Parameters
    ----------
    y_train : pd.Series
        The original training-set labels (the ``is_returned`` column
        BEFORE any class balancing). ``p_orig`` is the positive (RTO)
        rate of this vector.
    y_und : pd.Series | None
        The training-set labels AFTER any under-sampling / SMOTE. When
        ``None`` (the default — the train.py path does NO balancing), the
        resampled prior is identical to the original prior; we record
        ``p_und = p_orig`` honestly so the live decision path's
        ``calibrate_probabilities`` fast path (no-op when priors are
        equal) fires correctly. If you implement SMOTE / under-sampling
        in a future train.py revision, pass the post-balancing labels
        here and the calibration ratio will become non-trivial.
    calibration_method : str
        Recorded for audit — defaults to ``"bahnsen_eq6"`` so an
        operator reading the priors blob knows the calibration formula
        the live path will apply.

    Returns
    -------
    dict
        The full priors blob shape consumed by
        :func:`src.ml.registry.register_model` ``priors`` kwarg::

            {"p_orig": float, "p_und": float, "n_train": int,
             "n_pos_train": int, "calibration_method": str,
             "created_at": "<iso8601>"}
    """
    y_train = pd.Series(y_train).astype(int)
    n_train = int(len(y_train))
    n_pos_train = int(y_train.sum())
    p_orig = float(y_train.mean()) if n_train else 0.0
    if y_und is not None:
        y_und = pd.Series(y_und).astype(int)
        p_und = float(y_und.mean()) if len(y_und) else p_orig
    else:
        # Identity calibration — no balancing was applied. Recording
        # honestly (p_und = p_orig) so the live path's fast-path check
        # `p_orig == p_und` correctly skips calibration (the un-balanced
        # model's probabilities are already on the natural-prior scale).
        p_und = p_orig
    return {
        "p_orig": p_orig,
        "p_und": p_und,
        "n_train": n_train,
        "n_pos_train": n_pos_train,
        "calibration_method": calibration_method,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def write_priors_artifact(priors: dict, model_path: str) -> Path:
    """Write ``priors.json`` next to the model artifact on disk.

    Day 8 Task E14 — first-class artifact path. The priors blob lives
    in the model registry's metrics JSON (Postgres ``model_registry``
    table / file-mode ``out/model_registry.json``), but ALSO on disk
    next to the model artifact so an operator auditing a model can read
    the priors without a DB round-trip. Path: sibling of ``model_path``
    with ``.priors.json`` suffix appended (e.g.
    ``out/model_real.joblib`` → ``out/model_real.joblib.priors.json``).

    Returns
    -------
    Path
        The path to the written ``priors.json`` file (for the caller to
        print/log).
    """
    p = Path(str(model_path) + ".priors.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(priors, indent=2))
    return p


def main(argv: list[str] | None = None) -> int:
    """End-to-end training pipeline that wires Bahnsen Eq.(6) priors.

    Day 8 Task E14 — the production retraining entrypoint. Loads data
    via the Track L dispatcher (``load_data`` — prefers real Kaggle
    data, falls back to the synthetic CODScore CSV), splits
    leakage-safe on CustomerID, fits HistGB, computes the priors blob
    BEFORE ``register_model`` is called, writes ``priors.json`` next
    to the model artifact, registers the model with priors, prints
    ``p_orig`` + ``p_und`` so the user can verify the calibration is
    no longer dead.

    USAGE::

        python -m src.models.train                       # all defaults
        python -m src.models.train --data path/to/x.csv  # explicit CSV
        python -m src.models.train --feature-set full
        python -m src.models.train --model-out out/m.joblib

    NOTE: ``scripts/retrain_real.py`` is the heavyweight variant
    (adds cost-table sweep, permutation importance, PR-AUC gate) and
    ALSO wires priors via the same ``compute_priors`` +
    ``write_priors_artifact`` helpers. This ``main()`` is the lean
    variant for environments (e.g. Kaggle notebooks) where the
    dashboard regeneration isn't needed.

    Parameters
    ----------
    argv : list[str] | None
        Optional argv override (defaults to ``sys.argv[1:]`` — the
        argparse convention). Used by the test suite to drive main()
        with a fixed argv without monkeypatching ``sys.argv``.
    """
    # Late imports so ``import src.models.train`` is cheap (the test
    # suite + dashboard import this module for build_feature_frame /
    # fit_model / compute_priors without needing the data loaders or
    # the registry's Postgres dependency chain).
    from src.features.cleaning import load_data  # noqa: E402
    from src.features.enrich import add_address_features  # noqa: E402
    from src.models.splitting import group_leakage, group_split  # noqa: E402
    from src.ml.registry import current_champion, register_model  # noqa: E402

    ap = argparse.ArgumentParser(
        description="Train the RTO model + register as champion with Bahnsen Eq.(6) priors.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument(
        "--data",
        default=None,
        help="Path to CSV. None = load_data() dispatcher (prefers "
        "data/raw/ingested_real.csv, falls back to data/raw/cod_orders.csv).",
    )
    ap.add_argument(
        "--feature-set",
        choices=["order", "order+addr", "full"],
        default="order+addr",
    )
    ap.add_argument(
        "--model-out",
        default="out/model.joblib",
        help="Where to save the retrained model artifact.",
    )
    ap.add_argument(
        "--registry-path",
        default="out/model_registry.json",
        help="File-mode registry path (ignored in Postgres mode).",
    )
    ap.add_argument(
        "--version",
        default=None,
        help="Version tag. None = 'train-{timestamp}'.",
    )
    ap.add_argument(
        "--no-promote",
        action="store_true",
        help="Register as challenger (is_champion=False), not champion.",
    )
    args = ap.parse_args(argv)

    # 1. Load + enrich (Track L unified schema — synthetic + real both work).
    print(f"[load] reading {args.data or 'dispatcher default'} ...")
    df = load_data(args.data)
    n_rows = len(df)
    n_returned = int(df["is_returned"].sum())
    base_rate = float(df["is_returned"].mean())
    print(
        f"[load] {n_rows:,} rows | {n_returned:,} returned ({base_rate:.2%} "
        f"base rate) | {df['CustomerID'].nunique():,} unique customers"
    )
    if n_returned == 0:
        print("ERROR: 0 returned rows — cannot train (no positive class).",
              file=sys.stderr)
        return 1

    if args.feature_set in {"order+addr", "full"}:
        df = add_address_features(df)

    # 2. Leakage-safe split on CustomerID.
    train_df, test_df = group_split(df)
    leakage = group_leakage(train_df, test_df)
    print(f"[split] train={len(train_df):,} test={len(test_df):,} leakage={leakage}")
    assert leakage == 0, f"CustomerID leakage = {leakage} (must be 0)"

    X_tr, y_tr = build_feature_frame(train_df, args.feature_set)
    X_te, y_te = build_feature_frame(test_df, args.feature_set)
    print(f"[features] X_tr={X_tr.shape} X_te={X_te.shape} feature_set={args.feature_set}")

    # 3. Train.
    print("[train] fitting HistGradientBoostingClassifier (max_iter=300)...")
    model = fit_model(X_tr, y_tr)
    save_model(model, args.model_out)
    print(f"[train] model saved → {args.model_out}")

    # 4. *** E14 fix — compute priors BEFORE register_model is called. ***
    #    No class balancing in this path → p_und = p_orig (identity
    #    calibration; the live path's fast-path check correctly skips
    #    calibration). If a future revision adds SMOTE / under-sampling,
    #    pass the post-balancing labels via the y_und arg.
    priors = compute_priors(y_tr)
    priors_path = write_priors_artifact(priors, args.model_out)
    print(f"[priors] written → {priors_path}")
    print(
        f"[priors] p_orig={priors['p_orig']:.6f} p_und={priors['p_und']:.6f} "
        f"n_train={priors['n_train']} n_pos_train={priors['n_pos_train']} "
        f"calibration_method={priors['calibration_method']}"
    )

    # 5. Evaluate (lightweight — for the registration metrics blob).
    from sklearn.metrics import average_precision_score, roc_auc_score
    proba = model.predict_proba(X_te)[:, 1]
    pr_auc = float(average_precision_score(y_te, proba))
    roc = float(roc_auc_score(y_te, proba))
    print(f"[eval] PR-AUC={pr_auc:.4f} ROC-AUC={roc:.4f}")

    # 6. Register as champion (or challenger) WITH priors — E14 fix.
    champion = current_champion(args.registry_path)
    champ_version = champion.get("version", "(none)") if champion else "(none)"
    version = args.version or f"train-{int(time.time())}"
    entry = register_model(
        version=version,
        model_path=args.model_out,
        metrics={
            "pr_auc": round(pr_auc, 4),
            "roc_auc": round(roc, 4),
            "feature_set": args.feature_set,
            "training_data": str(args.data) if args.data else "dispatcher",
            "n_train": int(len(X_tr)),
            "n_test": int(len(X_te)),
            "n_rows": n_rows,
            "base_return_rate": round(base_rate, 4),
            "registered_by": "src.models.train:main",
        },
        champion=not args.no_promote,
        registry_path=args.registry_path,
        priors=priors,
    )
    print(
        f"[registry] registered {entry.get('version', version)} "
        f"(champion={not args.no_promote}; prior champion: {champ_version})"
    )

    # 7. Summary — single legible block.
    print("\n" + "=" * 72)
    print(
        f"Trained on {n_rows:,} orders. Champion: {version}. "
        f"PR-AUC={pr_auc:.4f} ROC-AUC={roc:.4f}. "
        f"p_orig={priors['p_orig']:.6f} p_und={priors['p_und']:.6f} "
        f"(calibration {'IDENTITY — no resampling applied' if priors['p_orig'] == priors['p_und'] else 'NON-TRIVIAL — resampling was applied'})."
    )
    print("=" * 72 + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
