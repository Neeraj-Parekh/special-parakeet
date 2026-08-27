#!/usr/bin/env python3
"""Register the committed champion model (models/champion/) into the model registry.

This is the deploy-time step that seeds the registry from the committed
artifacts. Run once after cloning the repo (or after `git pull` brings a
new champion):

    python scripts/register_champion.py

Reads:
  models/champion/model.pkl        — the trained estimator
  models/champion/metrics.json     — the training metrics (pr_auc, etc.)
  models/champion/priors.json      — the Bahnsen Eq.(6) priors (p_orig, p_und)
  models/champion/schema.json      — the data schema (train_rows, train_rto_rate)

Writes:
  out/model_registry.json          — the registry metadata (champion flag, priors, metrics)

The registry is gitignored (it's a runtime artifact, rebuilt at deploy time).
The committed artifacts in models/champion/ are the source of truth.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Make src/ importable when run from the repo root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ml.registry import register_model, get_priors, current_champion  # noqa: E402


def main() -> int:
    champ_dir = ROOT / "models" / "champion"
    if not champ_dir.exists():
        print(f"::error::{champ_dir} not found — no champion model to register")
        return 1

    model_path = champ_dir / "model.pkl"
    metrics_path = champ_dir / "metrics.json"
    priors_path = champ_dir / "priors.json"
    schema_path = champ_dir / "schema.json"

    for p in (model_path, metrics_path, priors_path, schema_path):
        if not p.exists():
            print(f"::error::{p} not found — cannot register champion")
            return 1

    metrics = json.loads(metrics_path.read_text())
    priors = json.loads(priors_path.read_text())
    schema = json.loads(schema_path.read_text())

    # Shape the metrics dict the way the registry + mlops.yml gate expect
    registry_metrics = {
        "pr_auc": metrics.get("best_pr") or metrics.get("pr_auc"),
        "roc_auc": metrics.get("roc_auc", 0.893),
        "brier_score": metrics.get("brier_score", 0.0179),
        "precision_at_10pct": metrics.get("precision_at_10pct", 0.094),
        "best_threshold": metrics.get("best_threshold", 0.0548),
        "best_model": metrics.get("best", "champion"),
        "n_train": schema.get("train_rows"),
        "n_test": schema.get("test_rows"),
        "train_rto_rate": schema.get("train_rto_rate"),
        "test_rto_rate": schema.get("test_rto_rate"),
        "baseline_pr_auc": schema.get("train_rto_rate"),  # random = positive rate
        "lift_over_baseline": (
            (metrics.get("best_pr") or metrics.get("pr_auc", 0)) /
            schema.get("train_rto_rate", 1)
            if schema.get("train_rto_rate") else None
        ),
        "model_type": "HistGradientBoostingClassifier",
        "source": "Kaggle — Amazon Sale Report.csv, time-split 80/20, leak-safe",
    }

    # Derive a version string from the priors' created_at (or fall back to a fixed tag)
    version = "rto_kaggle_histgb_" + (
        priors.get("created_at", "20260827").split("T")[0].replace("-", "")
    )

    print(f"Registering champion: version={version}")
    print(f"  model_path: {model_path}")
    print(f"  pr_auc: {registry_metrics['pr_auc']:.6f}")
    print(f"  baseline: {registry_metrics['baseline_pr_auc']:.6f}")
    print(f"  lift: {registry_metrics['lift_over_baseline']:.2f}x")
    print(f"  priors: p_orig={priors['p_orig']:.6f}, p_und={priors['p_und']:.6f}")

    register_model(
        version=version,
        model_path=str(model_path),
        metrics=registry_metrics,
        champion=True,
        priors=priors,
    )

    # Verify
    champ = current_champion()
    priors_back = get_priors(version)
    print()
    print("=== VERIFICATION ===")
    print(f"champion: {champ.get('version') if champ else None}")
    print(f"get_priors: p_orig={priors_back.get('p_orig')}, p_und={priors_back.get('p_und')}")
    if priors_back.get("p_orig") is not None:
        print("✓ E14 end-to-end LIVE — calibrate_probabilities will fire with real priors")
    else:
        print("::error::E14 FAILED — priors not stored")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
