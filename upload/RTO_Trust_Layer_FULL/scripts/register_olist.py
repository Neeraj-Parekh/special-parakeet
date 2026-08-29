#!/usr/bin/env python3
"""Register the Olist champion model (data/olist/artifacts/) into the model registry.

Task ID 2-b (Wave — Olist wiring, G1 fix). Mirrors ``scripts/register_champion.py``
but registers the Olist champion as a NON-default model (``champion=False``)
so the Amazon Kaggle champion remains the default ``/risk/score`` champion
and the Olist champion is selectable via ``?dataset=olist``.

Run once after cloning the repo (or after `git pull` brings a new Olist
champion):

    python scripts/register_olist.py

Reads:
  data/olist/artifacts/model.pkl      — the trained Olist estimator (dict with
                                        model, preprocessor, feature_names)
  data/olist/artifacts/metrics.json  — the Olist training metrics (pr_auc,
                                        roc_auc, brier, train_rto, etc.)

Writes:
  out/model_registry.json             — the registry metadata (NON-champion
                                        entry alongside the Amazon champion)

The registry is gitignored (it's a runtime artifact, rebuilt at deploy time).
The committed artifacts in data/olist/artifacts/ are the source of truth.

HONEST priors convention (per E14 fix + the task spec):
  * ``p_orig = train_rto = 0.013647564288873443`` (from metrics.json) —
    the minority prior in the ORIGINAL training data BEFORE any
    resampling.
  * ``p_und = p_orig`` (identity calibration) — the Olist champion was
    trained with ``HistGradientBoostingClassifier(class_weight='balanced')``
    which reweights the *loss function*, NOT the prior. There was no
    under-sampling or SMOTE. Per the E14 convention, identity calibration
    is the correct honest recording: ``calibrate_probabilities`` is a
    no-op when ``p_orig == p_und`` (the live decision path's
    ``if _priors['p_orig'] != _priors['p_und']:`` guard skips calibration).
    The ``note`` field records this honestly so an auditor reading the
    priors blob knows why the calibration ratio is 1.0.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Make src/ importable when run from the repo root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ml.registry import _get_model_by_version, get_priors, register_model  # noqa: E402

# The Olist champion's version tag — mirrors the Amazon champion's tag
# style (``rto_<dataset>_<model_type>_<yyyymmdd>``). The Olist training
# run produced metrics.json's created_at = "2026-08-28T00:45:37" → 20260828.
OLIST_VERSION = "rto_olist_histgb_20260828"


def main() -> int:
    olist_dir = ROOT / "data" / "olist" / "artifacts"
    if not olist_dir.exists():
        print(f"::error::{olist_dir} not found — no Olist champion to register")
        return 1

    model_path = olist_dir / "model.pkl"
    metrics_path = olist_dir / "metrics.json"

    for p in (model_path, metrics_path):
        if not p.exists():
            print(f"::error::{p} not found — cannot register Olist champion")
            return 1

    metrics = json.loads(metrics_path.read_text())

    # The Olist champion's training-set positive rate. This is the
    # ORIGINAL (pre-resampling) minority prior — there was no
    # under-sampling or SMOTE; class_weight='balanced' only reweights
    # the loss function, not the prior. Identity calibration per E14.
    p_orig = float(metrics.get("train_rto", 0.013647564288873443))
    p_und = p_orig  # identity — class_weight != resampling

    priors = {
        "p_orig": p_orig,
        "p_und": p_und,
        "n_train": int(metrics.get("train_rows", 15827)),
        "n_pos_train": int(round(p_orig * int(metrics.get("train_rows", 15827)))),
        "n_test": int(metrics.get("test_rows", 3957)),
        "n_pos_test": int(round(float(metrics.get("test_rto", 0.007328784432650998))
                                 * int(metrics.get("test_rows", 3957)))),
        "calibration_method": "bahnsen_eq6",
        "note": (
            "p_und == p_orig because HistGradientBoostingClassifier was "
            "trained with class_weight='balanced' (reweights the LOSS, "
            "NOT the prior — no under-sampling or SMOTE was applied). "
            "Identity calibration per E14 convention — "
            "calibrate_probabilities is a no-op when p_orig == p_und."
        ),
        "created_at": metrics.get("created_at", "2026-08-28T00:45:37"),
        "source": (
            "Olist Brazilian e-commerce — boleto subset, 19,784 rows, "
            "245 RTO positives (1.24%), time-split 80/20 → 15,827 train / "
            "3,957 test. RTO label: order_status IN {canceled, unavailable}."
        ),
    }

    # Shape the metrics dict the way the registry + mlops.yml gate expect
    # (mirrors scripts/register_champion.py's shape so the model-card /
    # drift endpoints can read Olist metrics uniformly).
    pr_auc = float(metrics.get("pr_auc", 0.3950047863348404))
    train_rto = float(metrics.get("train_rto", p_orig))
    registry_metrics = {
        "pr_auc": pr_auc,
        "roc_auc": float(metrics.get("roc_auc", 0.7676188636842475)),
        "brier_score": float(metrics.get("brier", 0.0438925593212936)),
        "best_model": metrics.get("best_model", "histgb"),
        "n_train": int(metrics.get("train_rows", 15827)),
        "n_test": int(metrics.get("test_rows", 3957)),
        "train_rto_rate": train_rto,
        "test_rto_rate": float(metrics.get("test_rto", 0.007328784432650998)),
        "baseline_pr_auc": train_rto,  # random = positive rate
        "lift_over_baseline": (
            pr_auc / train_rto if train_rto > 0 else None
        ),
        "model_type": "HistGradientBoostingClassifier",
        "source": (
            "Olist Brazilian e-commerce — boleto subset, "
            "time-split 80/20, leak-safe expanding-window rate features. "
            "Real user_id / merchant_id history (the lift driver that's "
            "inert on the Amazon Kaggle champion)."
        ),
        "dataset": "olist",
        "honest_caveats": (
            "boleto != Indian COD; order_status canceled/unavailable != "
            "true RTO; 1.24% positive rate vs Indian real-COD 25-60%. "
            "Closest public-proxy benchmark on Earth — Indian production "
            "model lives in models/champion/ (Amazon Kaggle, PR-AUC 0.1027)."
        ),
    }

    print(f"Registering Olist champion: version={OLIST_VERSION}")
    print(f"  model_path: {model_path}")
    print(f"  pr_auc: {registry_metrics['pr_auc']:.6f}")
    print(f"  baseline: {registry_metrics['baseline_pr_auc']:.6f}")
    print(f"  lift: {registry_metrics['lift_over_baseline']:.2f}x (3.8x the Amazon champion)")
    print(f"  priors: p_orig={priors['p_orig']:.6f}, p_und={priors['p_und']:.6f} (identity)")
    print("  champion=False (Amazon stays default; Olist is ?dataset=olist)")

    # Idempotency check: if the Olist version is already registered,
    # skip the re-registration (mirrors _seed_champion_registry's guard
    # so repeat runs don't bloat the registry JSON with duplicates).
    existing = None
    try:
        existing = _get_model_by_version(OLIST_VERSION)
    except Exception:
        pass

    if existing is not None:
        print("  already registered (idempotent) — skipping re-registration")
    else:
        register_model(
            version=OLIST_VERSION,
            model_path=str(model_path),
            metrics=registry_metrics,
            champion=False,  # Olist is NOT the default; Amazon stays champion
            priors=priors,
        )

    # Verify
    olist_entry = _get_model_by_version(OLIST_VERSION)
    priors_back = get_priors(OLIST_VERSION)
    print()
    print("=== VERIFICATION ===")
    if olist_entry is None:
        print("::error::Olist model not found in registry after registration")
        return 1
    print(f"olist entry: version={olist_entry.get('version')}, "
          f"is_champion={olist_entry.get('is_champion')}")
    print(f"get_priors: p_orig={priors_back.get('p_orig')}, "
          f"p_und={priors_back.get('p_und')}")
    if priors_back.get("p_orig") is not None:
        print("✓ Olist priors stored end-to-end — calibrate_probabilities "
              "will fire with identity calibration (no-op since p_orig == p_und)")
    else:
        print("::error::Olist priors NOT stored")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
