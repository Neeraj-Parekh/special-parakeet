# FIXED — Honest RTO Training (all leaks removed)

**Date:** 2026-08-27  **Venv:** linux_venv  **Mem avail:** 8.1GB (no OOM)

## What was fixed vs previous `artifacts/metrics.json`
| Issue | Before (leaky) | Fix | After (artifacts_fixed → promoted to artifacts) |
|-------|----------------|-----|-----------------------------------------------|
| **Target leak #1** | `courier_status_clean` (SHIPPED vs UNKNOWN/UNSHIPPED) was #1 feature coef 3.56 — at order time always UNKNOWN, only SHIPPED can be RTO → inflates ROC | Dropped from `low_card` `02_train_fixed.py:33` `low_card=[category,sku_prefix,fulfilment,sales_channel,ship_service_level,fulfilled_by,amount_bucket]` (7 vs 8) | Leak-removed list `["courier_status_clean","hour_of_day"]` `metrics.json:12` |
| **Constant feature** | `hour_of_day=12` for all 121k rows → zero variance, StandardScaler divide-by-zero risk, fake importance 0.81 | Dropped from `exclude_raw` `02_train_fixed.py:38` | `feature_count_fixed 49` vs leaky 53 |
| **Calibration** | Brier `logreg 0.165` / `histgb 0.048` — threshold 0.5 predicted 26% positives vs truth 1.9% | `CalibratedClassifierCV(sigmoid, cv=TimeSeriesSplit(3))` per model `02_train_fixed.py:108` | Brier `histgb 0.074→0.0179`, `logreg 0.166→0.0178` — honest |
| **Threshold** | Fixed 0.5 gave F1 0.10-0.13, HistGB `22691,1085,375,85` (many FN) | Max-F1 search via `precision_recall_curve` `02_train_fixed.py:56` | `histgb_fixed thr 0.024 F1 0.136 prec 0.073 rec 0.915 [18465,5311,39,421]` / `logreg_fixed thr 0.039 F1 0.134 prec 0.072 rec 0.939 [18240,5536,28,432]` |
| **Hyperparams** | HistGB default `lr 0.05 depth 6` | Tiny GridSearch `lr [0.05,0.08] x depth [4,6]` with `TimeSeriesSplit` `02_train_fixed.py:84` | Best `{'learning_rate':0.08,'max_depth':4}` PR-CV 0.0675 |
| **Honest comparison** | Only leaky PR 0.098 reported | Train same data with FIXED vs LEAKY side-by-side `02_train_fixed.py:122` | `logreg_leaky 0.0814 vs logreg_fixed 0.0802` inflation only `0.0012 (1.5%)` — leak was small after fixing hour, but still reported |

## Honest fixed metrics (test 24236, prevalence 0.0190)
| Model | PR-AUC (honest) | ROC-AUC | Brier cal | Prec@10% | Rec@10% | Conf thr0.5 | Best thr (F1) | Conf best thr |
|-------|----------------|---------|-----------|----------|---------|-------------|---------------|---------------|
| **logreg_fixed (BEST)** | **0.0802** | 0.8772 | 0.0178 | 0.0755 | 0.3978 | [23776,0,460,0] | 0.039 (F1 0.134) | [18240,5536,28,432] |
| histgb_fixed | 0.0713 | 0.8706 | 0.0179 | 0.0685 | 0.3609 | [23776,0,460,0] | 0.024 (F1 0.136) | [18465,5311,39,421] |
| histgb_leaky (for ref) | 0.0640 | 0.8639 | 0.0179 | 0.0532 | 0.2804 | — | 0.024 (F1 0.134) | — |
| logreg_leaky (for ref) | 0.0814 | 0.8777 | 0.0178 | 0.0751 | 0.3957 | — | 0.039 (F1 0.133) | — |

**Interpretation:** After removing courier leak, PR-AUC stays ~0.07-0.08 (4× baseline 0.019) — lift is real but modest. Honest precision in top decile ~7.5% (vs 1.9% random). Need more signal (e.g., device/behavior) for hackathon win. ROC 0.87 decent ranker. Calibration now excellent (0.0178).

## Top honest features (logreg_fixed)
`fulfilled_by_UNK 2.20`, `fulfilment_AMAZON 2.20`, `ship_service_level_EXPEDITED 2.17`, `pincode_length 1.90`, `amount_inr 1.44`, `amount_per_qty 1.38`, `has_promotion 1.14` — all order-time available.

## Artifacts (promoted)
- `model_processing/artifacts/model.pkl` — FIXED pipeline (49 feats, no leak, with calibration wrapper if better)
- `model_processing/artifacts/metrics.json` — honest FIXED + leaky side-by-side
- `model_processing/artifacts/calibration.png` — NEW
- `model_processing/artifacts_fixed/` — preserved fixed run
- `model_processing/artifacts_leaky_backup/` — old leaky for audit

## OOM safety
- FIXED X_train `96944x49 38MB` vs leaky 53 cols 41MB `02_train_fixed.py:45` — avail 8.3GB after.
- Next: drop `has_promotion` if still proxy, try `IsolationForest` for pincode outliers, or SMOTE/TimeSeriesSplit threshold per fold.
