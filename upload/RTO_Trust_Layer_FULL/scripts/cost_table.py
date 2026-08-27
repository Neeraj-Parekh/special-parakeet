"""E4: decision-threshold sweep + business cost table (false-positive cost bar)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.features.cleaning import load_orders  # noqa: E402
from src.features.enrich import add_address_features  # noqa: E402
from src.models.splitting import group_leakage, group_split  # noqa: E402
from src.models.train import build_feature_frame, fit_model  # noqa: E402

DEFAULT_FP_COST = 1.0
DEFAULT_FN_COST = 12.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/raw/cod_orders.csv")
    ap.add_argument(
        "--fp-cost", type=float, default=DEFAULT_FP_COST, help="cost per wrongly held order"
    )
    ap.add_argument(
        "--fn-cost",
        type=float,
        default=DEFAULT_FN_COST,
        help="cost per missed RTO (loss + reverse logistics)",
    )
    ap.add_argument("--out", default="docs/cost_table.md")
    args = ap.parse_args()

    df = add_address_features(load_orders(args.data))
    train_df, test_df = group_split(df)
    assert group_leakage(train_df, test_df) == 0
    X_tr, y_tr = build_feature_frame(train_df, "order+addr")
    X_te, y_te = build_feature_frame(test_df, "order+addr")
    model = fit_model(X_tr, y_tr)
    proba = model.predict_proba(X_te)[:, 1]

    rows = []
    for thr in [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60]:
        pred = (proba >= thr).astype(int)
        tp = int(((pred == 1) & (y_te == 1)).sum())
        fp = int(((pred == 1) & (y_te == 0)).sum())
        fn = int(((pred == 0) & (y_te == 1)).sum())
        precision = tp / max(tp + fp, 1)
        recall = tp / max(tp + fn, 1)
        cost = fp * args.fp_cost + fn * args.fn_cost
        rows.append(
            {
                "threshold": thr,
                "flagged": tp + fp,
                "precision": round(precision, 3),
                "recall": round(recall, 3),
                "fp": fp,
                "fn": fn,
                "cost_units": round(cost, 1),
            }
        )

    best = min(rows, key=lambda r: r["cost_units"])
    lines = [
        "# Decision-threshold cost analysis",
        "",
        f"Cost model: FP (good order held/manual review) = {args.fp_cost} unit, "
        f"FN (RTO missed, ships anyway) = {args.fn_cost} units "
        f"(industry: RTO loss + reverse logistics ~10-15x review cost).",
        "",
        "| threshold | flagged | precision | recall | FP | FN | total cost |",
        "|---|---|---|---|---|---|---|",
    ]
    lines += [
        "| {} | {} | {} | {} | {} | {} | {} |".format(
            r["threshold"],
            r["flagged"],
            r["precision"],
            r["recall"],
            r["fp"],
            r["fn"],
            r["cost_units"],
        )
        for r in rows
    ]
    lines += [
        "",
        f"**Cost-optimal threshold: {best['threshold']}** (cost {best['cost_units']} units)",
    ]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(lines) + "\n")
    print(json.dumps({"best_threshold": best["threshold"], "rows": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
