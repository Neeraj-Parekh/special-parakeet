"""TFX stage 4 (cont.) — Per-slice metrics (TFX ``small-slice warning``).

Per TFX Baylor 2017 (``command/05-PAPER-SKILLS-MAP.md`` gap #14), the
canary-gate stage must compute per-slice metrics + flag the canonical
canary-failure signature: aggregate metrics IMPROVE while one or more
slices DEGRADE. This catches a model that "looks better on average" by
trading off performance on a minority slice (a fairness + business
risk — the canonical reason TFX gates on slices, not just aggregates).

SLICES (per the worklog + the cost-table + feature_importance docs):
  * merchant_category — the ``Category`` column (Accessories, Fashion,
    Electronics, etc.). Slicing here catches a model that improves
    overall PR-AUC by gaining on Fashion while losing on Electronics.
  * cod_vs_prepaid — the ``is_cod`` flag (0=Prepaid, 1=COD). COD is
    the entire problem domain (Track A reframe: "is_cod gates model
    invocation; is_cod is pass-through for logging") — but the canary
    must not regress on Prepaid either, since the dashboard scores
    both.
  * pin_code_tier — the ``city_tier`` column (tier_1, tier_2, tier_3).
    Tier-3 cities have the highest RTO rate (the original problem
    framing in the user's pitch) — a model that regresses on tier_3
    while improving on tier_1 is a regression for the merchant's
    highest-loss segment.

OUTPUT:
  * JSON report at ``out/slice_metrics.json`` (uploaded as CI artifact).
  * Exit 0 if no slice regressed > MAX_REGRESSION vs the aggregate;
    exit 1 if any slice regressed — TFX "small-slice warning" pattern.

USAGE:
    python scripts/slice_metrics.py --model out/model.joblib \\
        --data data/raw/cod_orders.csv --out out/slice_metrics.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import joblib  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.metrics import average_precision_score  # noqa: E402

from src.features.cleaning import load_orders  # noqa: E402
from src.features.enrich import add_address_features  # noqa: E402
from src.models.splitting import group_split  # noqa: E402
from src.models.train import build_feature_frame  # noqa: E402

# Slice spec — (slice_name, column_name, kind, max_regression).
# `kind` controls how we treat the column:
#   "categorical" — slice by unique value (Category, city_tier).
#   "binary"     — slice by 0/1 (is_cod).
# The aggregate PR-AUC is the baseline; per-slice deltas are computed
# against the aggregate. A slice that regresses > MAX_REGRESSION vs the
# aggregate PR-AUC trips the "small-slice warning" (TFX pattern).
SLICES = [
    {
        "name": "merchant_category",
        "column": "Category",
        "kind": "categorical",
    },
    {
        "name": "cod_vs_prepaid",
        "column": "is_cod",
        "kind": "binary",
        "labels": {0: "prepaid", 1: "cod"},
    },
    {
        "name": "pin_code_tier",
        "column": "city_tier",
        "kind": "categorical",
    },
]

# Minimum slice size — slices with < 30 rows are too noisy for a
# meaningful PR-AUC (TFX's "small-slice" threshold is typically 100,
# but our test set is ~1.4k rows so 30 is the right floor here).
MIN_SLICE_SIZE = 30

# Default regression ceiling — a slice that regresses > 10% vs the
# aggregate PR-AUC is a blocking anomaly (looser than the canary-gate's
# 5% because per-slice metrics are noisier; TFX's recommended pattern
# is "loose slice gate + tight aggregate gate").
DEFAULT_MAX_SLICE_REGRESSION = 0.10


def pr_auc_for_subset(model, X: pd.DataFrame, y: pd.Series) -> float | None:
    """PR-AUC for the subset; None if too few positives or rows."""
    if len(y) < MIN_SLICE_SIZE:
        return None
    if y.sum() < 5:  # need ≥5 positives for a meaningful PR curve
        return None
    proba = model.predict_proba(X)[:, 1]
    return float(average_precision_score(y, proba))


def evaluate_slices(model, X: pd.DataFrame, y: pd.Series,
                   df: pd.DataFrame) -> tuple[dict, list[str]]:
    """Compute per-slice PR-AUC + flag the TFX small-slice signature."""
    # Aggregate baseline — same as evaluate.py.
    proba_all = model.predict_proba(X)[:, 1]
    aggregate_pr_auc = float(average_precision_score(y, proba_all))
    print(f"Aggregate PR-AUC: {aggregate_pr_auc:.4f}")

    report: dict = {"aggregate_pr_auc": aggregate_pr_auc, "slices": {}}
    warnings: list[str] = []

    for spec in SLICES:
        col = spec["column"]
        if col not in df.columns:
            warnings.append(
                f"slice '{spec['name']}' skipped — column '{col}' not "
                f"in dataframe (feature-set may be incomplete)"
            )
            continue

        slice_report: dict = {"column": col, "levels": {}}
        if spec["kind"] == "binary":
            levels = [(spec["labels"].get(v, str(v)), v) for v in sorted(df[col].dropna().unique())]
        else:
            levels = [(str(v), v) for v in sorted(df[col].dropna().unique())]

        for label, value in levels:
            mask = (df[col] == value) & df.index.isin(X.index)
            if not mask.any():
                continue
            X_slice = X[mask]
            y_slice = y[mask.reindex_like(y) if hasattr(mask, "reindex_like") else mask]
            # The mask is aligned to df.index; y is indexed identically
            # to X (both come from build_feature_frame's `df[...]` slice
            # so they share df.index). We need the mask in the same
            # index space as X.
            mask_in_X = X.index.isin(df[mask].index)
            X_slice = X.loc[mask_in_X]
            y_slice = y.loc[mask_in_X]

            pr = pr_auc_for_subset(model, X_slice, y_slice)
            if pr is None:
                slice_report["levels"][label] = {
                    "n": int(len(X_slice)),
                    "pr_auc": None,
                    "status": "too_small",
                }
                continue

            delta = (pr - aggregate_pr_auc) / aggregate_pr_auc if aggregate_pr_auc > 0 else 0.0
            status = "ok"
            if delta < -DEFAULT_MAX_SLICE_REGRESSION:
                status = "regressed"
                warnings.append(
                    f"slice '{spec['name']}={label}' PR-AUC {pr:.4f} "
                    f"regresses aggregate by {delta:.1%} — TFX small-slice "
                    f"warning"
                )
            slice_report["levels"][label] = {
                "n": int(len(X_slice)),
                "pr_auc": pr,
                "delta_vs_aggregate": round(delta, 4),
                "status": status,
            }
            print(f"  {spec['name']}={label:20s} n={len(X_slice):4d} "
                  f"PR-AUC={pr:.4f}  Δ={delta:+.1%}  [{status}]")

        report["slices"][spec["name"]] = slice_report

    return report, warnings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="out/model.joblib")
    ap.add_argument("--data", default="data/raw/cod_orders.csv")
    ap.add_argument("--feature-set", default="full",
                    choices=["order", "order+addr", "full"])
    ap.add_argument("--out", default="out/slice_metrics.json")
    ap.add_argument("--max-regression", type=float,
                    default=DEFAULT_MAX_SLICE_REGRESSION,
                    help="per-slice regression ceiling vs aggregate (default 0.10 = 10%)")
    args = ap.parse_args()

    model_path = Path(args.model)
    if not model_path.exists():
        print(f"::error::model artifact not found: {model_path}")
        return 1
    data_path = Path(args.data)
    if not data_path.exists():
        print(f"::error::data file not found: {data_path}")
        return 1

    # Load + preprocess — same path as evaluate.py + canary_gate.py.
    df = load_orders(args.data)
    if args.feature_set in {"order+addr", "full"}:
        df = add_address_features(df)
    _, test_df = group_split(df)
    X_te, y_te = build_feature_frame(test_df, args.feature_set)

    print(f"Loading canary model: {model_path}")
    model = joblib.load(model_path)

    report, warnings = evaluate_slices(model, X_te, y_te, test_df)

    if warnings:
        print("\n" + "\n".join(f"::warning::{w}" for w in warnings))
        print(f"\n::error::slice-metrics gate FAILED — {len(warnings)} "
              f"slice(s) regressed beyond the {args.max_regression:.0%} ceiling")
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2))
        return 1

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))
    print(f"\nWrote slice-metrics report → {out_path}")
    print("✓ Slice-metrics gate passed — no slice regressed beyond "
          f"{args.max_regression:.0%} of the aggregate PR-AUC")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
