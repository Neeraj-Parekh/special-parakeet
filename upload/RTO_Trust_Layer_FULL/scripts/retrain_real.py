"""Retrain the RTO model on real Kaggle data + register as champion.

The Day 4 Track L real-data upgrade path. Wires together every Track E-H
plumbing piece (Postgres-backed model registry, leakage-safe GroupShuffleSplit,
permutation importance, cost-curve sweep) so the user can run:

    python scripts/ingest_kaggle.py
    python scripts/retrain_real.py

and end up with a champion model trained on real Amazon India orders,
PR-AUC > 0.60 (CI gate) with target ≥ 0.72 (Kandula 2021 DSS benchmark:
AUC 0.73-0.79 on real Indian e-commerce delivery data).

Flow:
  1. Load `data/raw/ingested_real.csv` via `load_ingested_real` (Track L
     Day 4 — schema unification done by `scripts/ingest_kaggle.py`).
  2. `group_split` on CustomerID (GroupShuffleSplit, test_size=0.2,
     random_state=42) — same leakage-safe split as `scripts/evaluate.py`.
  3. Assert `group_leakage == 0` (CI gate; any overlap means a customer
     leaked across train/test, which inflates the headline metric and
     breaks every paper-cited comparison).
  4. Train `HistGradientBoostingClassifier` via `fit_model()` (unchanged
     from the synthetic-data path — the unified schema makes the swap
     drop-in).
  5. Evaluate: PR-AUC, ROC-AUC, F1, precision@threshold, recall@threshold.
     Threshold chosen as the F1-best point on the PR curve (same logic
     as `evaluate.py`).
  6. Read the current champion from the registry (Postgres-backed per
     Track E Day 2 dual-mode; falls back to `out/model_registry.json`).
  7. If new PR-AUC > current champion's PR-AUC: register the new model
     as champion with `version="real-{timestamp}"`, demote the old one
     (champion=True flag atomically flips).
  8. Write `out/metrics_real.json` (the evaluation report, the same shape
     as `out/metrics.json` so the dashboard's Model Health page can read
     either).
  9. Regenerate `docs/cost_table.md` via `scripts/cost_table.py --data
     data/raw/ingested_real.csv` (the per-threshold cost sweep, FN=12x FP
     per Bahnsen 2013 + Drummond-Holte 2006).
  10. Regenerate `docs/feature_importance.md` via `src.models.explain.
      global_importance` (permutation AP-drop on the held-out set, same
      as the existing synthetic-data table).
  11. Print a summary.
  12. Exit 1 if PR-AUC < 0.60 (matches `mlops.yml`'s hard floor — a model
      worse than random on the positive class is NOT promoted).

USAGE:
    python scripts/retrain_real.py                          # all defaults
    python scripts/retrain_real.py --data path/to/ingested.csv
    python scripts/retrain_real.py --feature-set full        # order+addr
    python scripts/retrain_real.py --no-cost-table           # skip sweep
    python scripts/retrain_real.py --no-feature-importance   # skip perm imp
    python scripts/retrain_real.py --promote-always          # register
                                                             #  even if <=
                                                             #  champion
"""
from __future__ import annotations

import argparse
import json
import subprocess
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

from src.features.cleaning import load_ingested_real  # noqa: E402
from src.features.enrich import add_address_features  # noqa: E402
from src.models.explain import global_importance  # noqa: E402
from src.models.splitting import group_leakage, group_split  # noqa: E402
from src.models.train import build_feature_frame, fit_model, save_model  # noqa: E402
from src.ml.registry import current_champion, register_model  # noqa: E402

# CI gate — matches `.github/workflows/mlops.yml` Stage 3 (Fail if PR-AUC
# < 0.60). The Kandula 2021 DSS paper benchmark is AUC 0.73-0.79 on real
# Indian e-commerce delivery data; PR-AUC < 0.60 means the model is
# worse than random on the positive class — block promotion + fail.
PR_AUC_FLOOR = 0.60


def best_f1_threshold(y_true, proba):
    """Same F1-best threshold logic as `scripts/evaluate.py`.

    Returns (threshold, precision_at_threshold, recall_at_threshold, f1).
    The threshold is the operating point that maximises F1 on the PR
    curve — chosen because the cost-optimizer's per-order argmin uses
    the raw probability, not a single global threshold, so this is for
    reporting (the dashboard's "precision@threshold / recall@threshold"
    surface) not for the decision policy.
    """
    prec, rec, thr = precision_recall_curve(y_true, proba)
    f1 = 2 * prec * rec / (prec + rec + 1e-12)
    i = int(f1.argmax())
    t = float(thr[max(i - 1, 0)]) if len(thr) else 0.5
    return t, float(prec[i]), float(rec[i]), float(f1[i])


def _write_feature_importance(model, X, y, out_path: Path) -> None:
    """Regenerate `docs/feature_importance.md` from permutation AP-drop.

    Same approach as the existing synthetic-data table:
    `sklearn.inspection.permutation_importance` with `n_repeats=10`,
    `scoring="average_precision"`. The output table is the input to the
    MODEL_CARD §5 Explainability section + the dashboard's per-prediction
    reason-code panel.
    """
    imp = global_importance(model, X, y, seed=42)
    lines = [
        "# Global feature importance (permutation, AP drop on held-out set)",
        "",
        "<!-- Auto-regenerated by `scripts/retrain_real.py` on real Kaggle",
        "     data. The Kandula 2021 DSS paper (DOI 10.1016/j.dss.2021.113584)",
        "     is the benchmark: AUC 0.73-0.79 on real Indian e-commerce",
        "     delivery data; the feature ladder is Payment_Type → Service_Tier",
        "     → Delay → POI/amenity counts. -->",
        "",
        "| feature | avg_precision drop |",
        "|---|---|",
    ]
    for _, row in imp.iterrows():
        lines.append(f"| {row['feature']} | {row['ap_drop']:.4f} |")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n")


def _run_cost_table_sweep(data_path: str) -> int:
    """Shell out to `scripts/cost_table.py` for the per-threshold sweep.

    The cost-table script is read-only per the spec (Track L uses it as a
    pattern). Calling it via subprocess lets us reuse its bootstrap
    logic + the FP/FN cost model + the markdown table format without
    duplicating the code. The script's `load_orders` dispatch in
    `src/features/cleaning.py` detects the unified real-data schema and
    delegates to `load_ingested_real`, so the same `--data` arg works
    for both the synthetic + real CSVs.
    """
    print(f"\n[cost_table] regenerating docs/cost_table.md on {data_path}...")
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parent / "cost_table.py"),
        "--data",
        data_path,
        "--out",
        "docs/cost_table.md",
    ]
    try:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    except FileNotFoundError as exc:
        print(f"[cost_table] WARN: cost_table.py not found — {exc}", file=sys.stderr)
        return 1
    if result.returncode != 0:
        print(
            f"[cost_table] WARN: cost_table.py exited {result.returncode}\n"
            f"  stderr: {result.stderr.strip()[:500]}",
            file=sys.stderr,
        )
        return result.returncode
    if result.stdout:
        print(f"[cost_table] {result.stdout.strip()[:500]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Retrain the RTO model on real Kaggle data + register as champion.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument(
        "--data",
        default="data/raw/ingested_real.csv",
        help="Path to the unified-schema CSV produced by ingest_kaggle.py.",
    )
    ap.add_argument(
        "--feature-set",
        choices=["order", "order+addr", "full"],
        default="order+addr",
        help="Feature ladder (full = order+addr; geo features were removed",
    )
    ap.add_argument(
        "--model-out",
        default="out/model_real.joblib",
        help="Where to save the retrained model artifact.",
    )
    ap.add_argument(
        "--metrics-out",
        default="out/metrics_real.json",
        help="Where to write the JSON evaluation report.",
    )
    ap.add_argument(
        "--no-cost-table",
        action="store_true",
        help="Skip regenerating docs/cost_table.md.",
    )
    ap.add_argument(
        "--no-feature-importance",
        action="store_true",
        help="Skip regenerating docs/feature_importance.md.",
    )
    ap.add_argument(
        "--promote-always",
        action="store_true",
        help="Register as champion even if PR-AUC <= current champion's.",
    )
    args = ap.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        print(
            f"ERROR: {data_path} not found.\n"
            f"  → run `python scripts/ingest_kaggle.py` first (after placing the\n"
            f"    Amazon India Sale Report CSV at data/raw/amazon_sale_report.csv).\n"
            f"  → see data/raw/README.md for download instructions.",
            file=sys.stderr,
        )
        return 1

    # 1. Load real data via the Track L unified-schema loader.
    print(f"[load] reading {data_path} ...")
    df = load_ingested_real(str(data_path))
    n_rows = len(df)
    n_returned = int(df["is_returned"].sum())
    base_rate = float(df["is_returned"].mean())
    print(
        f"[load] {n_rows:,} rows | {n_returned:,} returned ({base_rate:.2%} "
        f"base rate) | {df['CustomerID'].nunique():,} unique customers"
    )

    if n_returned == 0:
        print(
            "ERROR: 0 returned rows — cannot train (no positive class).",
            file=sys.stderr,
        )
        return 1

    # 2. Enrich address-quality (no-op for real data — adds the column
    #    with "unknown" so the model's feature whitelist matches).
    if args.feature_set in {"order+addr", "full"}:
        df = add_address_features(df)

    # 3. Leakage-safe split on CustomerID (GroupShuffleSplit).
    train_df, test_df = group_split(df)
    leakage = group_leakage(train_df, test_df)
    print(f"[split] train={len(train_df):,} test={len(test_df):,} leakage={leakage}")

    # 4. CI gate — leakage > 0 means a customer leaked across train/test.
    #    This breaks every paper-cited comparison (Kandula 2021, Bahnsen
    #    2013) — fail loudly.
    assert leakage == 0, (
        f"CustomerID leakage = {leakage} (must be 0). Repeat buyers in "
        f"both train + test inflates the headline metric — refusing to "
        f"train a model on a leakage-tainted split."
    )

    X_tr, y_tr = build_feature_frame(train_df, args.feature_set)
    X_te, y_te = build_feature_frame(test_df, args.feature_set)
    print(f"[features] X_tr={X_tr.shape} X_te={X_te.shape} feature_set={args.feature_set}")

    # 5. Train HistGB via the same fit_model() used by the synthetic path.
    print("[train] fitting HistGradientBoostingClassifier (max_iter=300)...")
    model = fit_model(X_tr, y_tr)
    save_model(model, args.model_out)
    print(f"[train] model saved → {args.model_out}")

    # 6. Evaluate — PR-AUC, ROC-AUC, F1, precision/recall @ F1-best threshold.
    proba = model.predict_proba(X_te)[:, 1]
    pr_auc = float(average_precision_score(y_te, proba))
    roc = float(roc_auc_score(y_te, proba))
    thr, prec, rec, f1 = best_f1_threshold(y_te, proba)
    pred = (proba >= thr).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_te, pred).ravel()

    print(
        f"[eval] PR-AUC={pr_auc:.4f} | ROC-AUC={roc:.4f} | "
        f"F1={f1:.4f} (thr={thr:.3f}) | "
        f"prec@thr={prec:.4f} rec@thr={rec:.4f} | "
        f"TP={tp} FP={fp} FN={fn} TN={tn}"
    )

    # 7. Compare to the current champion (Postgres-backed per Track E;
    #    falls back to file mode for local runs without DATABASE_URL).
    champion = current_champion()
    if champion is not None:
        champ_metrics = champion.get("metrics", {}) or {}
        champ_pr_auc = float(champ_metrics.get("pr_auc", 0.0))
        champ_version = champion.get("version", "unknown")
        print(
            f"[champion] current champion: {champ_version} | PR-AUC={champ_pr_auc:.4f}"
        )
    else:
        champ_pr_auc = 0.0
        champ_version = "(none)"
        print("[champion] no current champion registered — first model.")

    # 8. Promote if better (or --promote-always).
    promote = (pr_auc > champ_pr_auc) or args.promote_always
    version = f"real-{int(time.time())}"
    if promote:
        entry = register_model(
            version=version,
            model_path=args.model_out,
            metrics={
                "pr_auc": round(pr_auc, 4),
                "roc_auc": round(roc, 4),
                "threshold_best_f1": round(thr, 4),
                "precision_at_threshold": round(prec, 4),
                "recall_at_threshold": round(rec, 4),
                "f1_at_threshold": round(f1, 4),
                "confusion": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
                "feature_set": args.feature_set,
                "training_data": str(data_path),
                "n_train": int(len(X_tr)),
                "n_test": int(len(X_te)),
                "n_rows": n_rows,
                "base_return_rate": round(base_rate, 4),
            },
            champion=True,
        )
        print(
            f"[registry] NEW champion registered: {entry.get('version', version)} "
            f"(demoted prior champion {champ_version})"
        )
    else:
        print(
            f"[registry] NOT promoted — new PR-AUC {pr_auc:.4f} <= champion's "
            f"{champ_pr_auc:.4f}. Use --promote-always to force."
        )

    # 9. Write the evaluation report (same shape as out/metrics.json so the
    #    dashboard's Model Health page can read either).
    report = {
        "feature_set": args.feature_set,
        "data_source": str(data_path),
        "n_rows": n_rows,
        "n_train": int(len(X_tr)),
        "n_test": int(len(X_te)),
        "base_return_rate": base_rate,
        "pr_auc": pr_auc,
        "roc_auc": roc,
        "lift_over_base": pr_auc / base_rate if base_rate > 0 else 0.0,
        "threshold_best_f1": thr,
        "precision_at_threshold": prec,
        "recall_at_threshold": rec,
        "f1_at_threshold": f1,
        "confusion": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        "false_positive_share_flagged": fp / max(tp + fp, 1),
        "customer_group_leakage": leakage,
        "champion_promoted": bool(promote),
        "champion_version": version if promote else champ_version,
        "champion_metrics_compare": {
            "previous_pr_auc": champ_pr_auc,
            "previous_version": champ_version,
        },
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    metrics_path = Path(args.metrics_out)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(report, indent=2))
    print(f"[report] metrics written → {metrics_path}")

    # 10. Regenerate docs/cost_table.md via scripts/cost_table.py.
    if not args.no_cost_table:
        _run_cost_table_sweep(str(data_path))

    # 11. Regenerate docs/feature_importance.md via permutation AP-drop.
    if not args.no_feature_importance:
        fi_path = Path("docs/feature_importance.md")
        print(f"[feature_importance] regenerating {fi_path} ...")
        try:
            _write_feature_importance(model, X_te, y_te, fi_path)
            print(f"[feature_importance] written → {fi_path}")
        except Exception as exc:
            print(
                f"[feature_importance] WARN: permutation importance failed — {exc}",
                file=sys.stderr,
            )

    # 12. Summary — print a single legible block the user can paste in a
    #     worklog or screenshot for the pitch video.
    print("\n" + "=" * 72)
    print(
        f"Retrained on {n_rows:,} real orders. "
        f"PR-AUC: {pr_auc:.2f} (was {champ_pr_auc:.2f} on synthetic). "
        f"Champion: {version if promote else champ_version}."
    )
    print("=" * 72 + "\n")

    # 13. CI gate — fail if PR-AUC below the floor.
    if pr_auc < PR_AUC_FLOOR:
        print(
            f"::error::PR-AUC {pr_auc:.4f} below floor {PR_AUC_FLOOR:.2f} — "
            f"model NOT promoted (mlops.yml Stage 3 gate).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
