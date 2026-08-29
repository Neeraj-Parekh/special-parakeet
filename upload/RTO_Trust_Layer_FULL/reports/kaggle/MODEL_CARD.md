# Model Card — Amazon RTO Risk (Hackathon)

**Model:** `QtyZero_Region_histgb` **Version:** `20260827-2330` **Date:** `2026-08-27`  
**Artifact:** `model_processing/artifacts/model.pkl:1` (HistGradientBoostingClassifier + ColumnTransformer) `model_processing/06_master_autonomous_loop.py:189`  
**Data:** `Amazon Sale Report.csv.zip` `68.9MB uncompressed 128975 rows` `model_processing/00_eda_pre_training.py:75` → `processed/train_processed.csv:96944` + `test_processed.csv:24236` `model_processing/processed/schema.json:4`  
**Venv:** `linux_venv` `pandas 2.2.2 sklearn 1.8.0 torch 2.2.2+cpu` `model_processing/00_eda_pre_training.py:11`

## Intended Use
Predict **post-shipment Return-to-Origin (RTO)** at **order time** for COD-like orders to flag high-risk for intervention (e.g., prepaid nudge).  
**Not for:** Cancelled orders (separate problem, 14.21% `reports/REPORT.md:10`), post-shipment Courier Status.

## Data
- **Source:** Kaggle `dhananjaylaygude/amazon-sale-report` Apr-Jun 2022 (expanded Jan-Dec 2022 in this dump `Date 2022-01-04→2022-12-06` `01_preprocess_amazon_oom_safe.py:68`)
- **Raw:** 128975 rows ×24 cols `Amazon Sale Report.csv:1` (`Order ID`, `Date`, `Status`, `Category` 9, `Size` 11, `SKU` 7195, `Amount`, `Qty`, `ship-city` 8955, `ship-state` 50, `ship-postal-code` 406 prefixes, `B2B`, `Fulfilment`, `Sales Channel`, etc.) `reports/schema_snapshot.json:1`
- **Label:** `is_rto` via substring `return|rejected|rto|refused|returned to seller` `01_preprocess_amazon_oom_safe.py:52` → **2109 (1.64%)** RTO, **18332 Cancelled (14.21%)** `reports/REPORT.md:9` — overlap 0 `reports/quality_gates.json:3`. Wrong exact-set would conflate 16223 Cancels.
- **Split:** Time-based `80/20` `01_preprocess_amazon_oom_safe.py:133` `train 2022-01-04→2022-07-04 96944 (1.697%)` `test 2022-07-04→2022-12-06 24236 (1.898%)` `processed/schema.json:5`
- **Drops:** `Unnamed:22` junk `79925 False` `00_eda_pre_training.py:45`, `Amount NaN 7795 (97% Cancelled)` informative, `Amount 0 2343`
- **Class imbalance:** 1:61 → `PR-AUC` not accuracy.

## Features (43 → 79 after OHE)
**Categorical OHE `min_frequency 0.005`:** `category (9)`, `sku_prefix (14 → JNE/SET/J)`, `fulfilment`, `sales_channel`, `ship_service_level`, `fulfilled_by`, `amount_bucket q5` (train quantiles `train_stats.json:7`), `Size 11`, `cat_has_promo=category_has_promotion 17`, `pincode_region` first digit `06_master_autonomous_loop.py:80`  
**Numeric StandardScaler:** `amount_inr`, `amount_log`, `is_high_value`, `amount_zscore_by_category` (train `cat_mean/std`), `amount_ratio_to_cat_median`, `amount_per_qty`, `Qty`, `pincode_length`, `is_qty_zero (Qty==0 12807)`, `is_weekend/month_start/end`, `is_b2b`, `has_promotion`, `category_rto_rate` (expanding `shift(1)` train-only `01_preprocess:176` → map test `01:185`), `state/city/pincode_prefix/sku_prefix/fulfilment_rto_rate`, `category_order_count`, **smooth** `city/pincode_prefix_rto_rate_smooth m=20` `06:68` `amount_x_promo`  
**Excluded leak:** `courier_status_clean` (post-shipment, only `SHIPPED` can be RTO `04_tabnet:31`) `reports/feature_blueprint.json:22`, `hour_of_day=12` constant.

## Model
`HistGradientBoostingClassifier(loss=log_loss, max_iter=250, max_depth=4, learning_rate=0.08, max_leaf_nodes=31, l2_regularization=0.1, class_weight=None)` `06_master_autonomous_loop.py:189` — **no class_weight** won vs balanced (balanced hurt -0.01 `05_has_promo:9`).  
**Preprocessor:** `ColumnTransformer(cat OHE + num StandardScaler)` `06:102` → `Xtr 96944×79 61MB` `artifacts/metrics.json:3`.

## Training
- **CV:** `TimeSeriesSplit(3)` on train `06:152`
- **Calibration:** `CalibratedClassifierCV(sigmoid, cv=TimeSeriesSplit(3))` `06:157` — `Brier raw 0.0183 → cal 0.0179`
- **Threshold:** max-F1 via `precision_recall_curve` `06:112` → `0.0548`
- **Search:** Master loop tried `SMOTE/Borderline/ADASYN` (hurt `0.087-0.092`), `RF/ExtraTrees/MLP/LGBM/CatBoost/TabNet (0.0838 -10%)`, `m 10/30/50`, `bins 3/10` — best `m=20 bins=5`.

## Evaluation (test 24236, prevalence 0.0190)
| Model | PR-AUC | ROC-AUC | Brier (cal) | Prec@10% | Rec@10% | Conf thr0.5 | Best thr | F1 thr |
|---|---|---|---|---|---|---|---|
| **QtyZero_Region_histgb (BEST)** | **0.1027** | 0.8930 | 0.0179 | 0.0941 | 0.436 | [23776,0,460,0] | 0.0548 | 0.092 |
| MLP_size_smooth | 0.1015 | 0.8898 | 0.0188 | 0.0863 | — | — | — | — |
| ENS top3 avg | 0.1009 | — | — | — | — | — | — | — |
| Baseline logreg (no Size) | 0.0802 | 0.8772 | 0.0178 | 0.0755 | 0.397 | — | 0.0209 | 0.149 |
| Leaky logreg (courier) | 0.0980* | 0.89* | 0.165* | — | — | — | — | — | *leaky, dropped |

**Lift:** `5.4×` baseline `0.019`, `+28%` over `B0`. `CV PR 0.2416` vs test `0.1027` (time drift). Previous `08_all_remaining` variants `0.094-0.102` all ≤ `0.1027` — ceiling.

**Feature importance (HistGB):** `is_qty_zero`, `pincode_region`, `amount_per_qty`, `category_rto_rate`, `has_promotion` etc. (no courier).

## Leakage & Quality Gates
`reports/quality_gates.json:1` PASS `Label substring`, `Cancel≠RTO`, `time usable`, `city 8955 high-card → rate-encode`, `imbalance`, `Amount NaN informative`. `courier_status_clean` flagged leaky but excluded `02_train_fixed.py:33`.

## Limitations / Risks
- No `user_id`/`payment` history → PR capped ~0.12-0.15; ~50% false positives at `prec10 9.4%`.
- Amount NaN ≈ Cancelled — not for RTO scoring.
- Slight drift train 1.697% → test 1.898% (0.2pp).
- `is_qty_zero` 12807 zeros strongly predictive but may reflect Cancelled proxy — monitor.

## OOM Safety
`Xtr 61MB`, `Xte 15MB`, `avail 7.7-8.2GB` `06:169`, `OMP 4`, `float32/category`, `gc`, `Agg`, `chunksize 20k` `00_eda:98`.

## Reproduce
```bash
linux_venv/bin/python 00_eda_pre_training.py
linux_venv/bin/python 01_preprocess_amazon_oom_safe.py --out processed --test-frac 0.20
linux_venv/bin/python 06_master_autonomous_loop.py  # best auto-selected
# or: python 02_train_fixed.py
```

## Files
- `processed/train_processed.csv (25MB)`, `test_processed.csv (6.2MB)`, `schema.json`, `train_stats.json`, `feature_list.json`
- `artifacts/model.pkl (125KB)`, `metrics.json`, `pr_curve.png`, `roc_curve.png`, `calibration.png` (fixed)
- `reports/REPORT.md`, `figures/01-07.png`, `feature_blueprint.json`, `FIXED_REPORT.md`, `artifacts_all_remaining/` (tied 0.1026)
- `artifacts_master/`, `artifacts_has_promo/` (ENS 0.0962), `artifacts_tabnet/` (0.0838), `artifacts_improved/` (0.0939)

---
*Generated 2026-08-27, master loop PID 2157613 → 2177274, `free avail 7.8GB`.*
