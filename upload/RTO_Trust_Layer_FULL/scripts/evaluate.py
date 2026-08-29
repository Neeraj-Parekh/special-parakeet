"""Train + evaluate RTO risk model. Prints JSON metrics; writes model + report."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    precision_recall_curve,
    roc_auc_score,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.features.cleaning import load_orders  # noqa: E402
from src.features.enrich import add_address_features  # noqa: E402

# NOTE: `add_geo_features` was removed from `src/features/enrich.py` as dead
# code (no pincodes CSV in the repo, never called from the API lifespan).
# `--feature-set full` now silently trains on order+addr features only; the
# `--pincodes` arg below is a no-op kept for backward-compat.
from src.models.splitting import group_leakage, group_split  # noqa: E402
from src.models.train import build_feature_frame, fit_model, save_model  # noqa: E402


def best_f1_threshold(y_true, proba):
    prec, rec, thr = precision_recall_curve(y_true, proba)
    f1 = 2 * prec * rec / (prec + rec + 1e-12)
    i = int(f1.argmax())
    t = float(thr[max(i - 1, 0)]) if len(thr) else 0.5
    return t, float(prec[i]), float(rec[i]), float(f1[i])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feature-set", choices=["order", "order+addr", "full"], default="order")
    ap.add_argument("--data", default="data/raw/cod_orders.csv")
    ap.add_argument("--pincodes", default="data/raw/pincodes_india.csv")
    ap.add_argument("--model-out", default="out/model.joblib")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    df = load_orders(args.data)
    if args.feature_set in {"order+addr", "full"}:
        df = add_address_features(df)
    # Geo features (add_geo_features) were removed as dead code — see
    # `src/features/enrich.py`. --feature-set "full" now trains on
    # order+addr features only; the `--pincodes` arg is a no-op.

    train_df, test_df = group_split(df)
    leakage = group_leakage(train_df, test_df)

    X_tr, y_tr = build_feature_frame(train_df, args.feature_set)
    X_te, y_te = build_feature_frame(test_df, args.feature_set)

    model = fit_model(X_tr, y_tr)
    proba = model.predict_proba(X_te)[:, 1]

    pr_auc = float(average_precision_score(y_te, proba))
    roc = float(roc_auc_score(y_te, proba))
    base_rate = float(y_te.mean())
    thr, prec, rec, f1 = best_f1_threshold(y_te, proba)
    pred = (proba >= thr).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_te, pred).ravel()

    save_model(model, args.model_out)
    from src.ml.registry import register_model

    register_model(
        version=f"histgb-{args.feature_set}-{time.strftime('%Y%m%d%H%M%S')}",
        model_path=args.model_out,
        metrics={"pr_auc": round(pr_auc, 4), "roc_auc": round(roc, 4)},
    )
    report = {
        "feature_set": args.feature_set,
        "n_train": int(len(X_tr)),
        "n_test": int(len(X_te)),
        "base_return_rate": base_rate,
        "pr_auc": pr_auc,
        "roc_auc": roc,
        "lift_over_base": pr_auc / base_rate,
        "threshold_best_f1": thr,
        "precision_at_threshold": prec,
        "recall_at_threshold": rec,
        "f1_at_threshold": f1,
        "confusion": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        "false_positive_share_flagged": fp / max(tp + fp, 1),
        "customer_group_leakage": leakage,
    }
    print(json.dumps(report, indent=2))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, indent=2))
    return 1 if leakage > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
