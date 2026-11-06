# Kaggle/Colab Training-Script PROMPT — Handoff Document

> **Purpose**: Hand this prompt to another AI agent (Claude / ChatGPT / Gemini / Copilot) OR a Kaggle notebook cell-generator. The agent should produce a **complete, runnable `train.py`** that trains a COD-RTO binary classifier and registers it with the RTO Trust Layer's model registry. Do NOT hand this to a human to write by hand — it is meant for an LLM code-generation session.
>
> **Why a separate agent**: The orchestrator's context window is reserved for the 9-fail-bug-fix wave (E14/C8/C9/C10/F17/F19/A1/A2/D13). The training script is self-contained and can be generated independently. Once you have the script, run it on Kaggle (GPU), then drop the resulting `model.pkl` + `priors.json` + `metrics.json` into `models/champion/` and re-register.

---

## COPY-PASTE THE BLOCK BELOW INTO THE OTHER AGENT

---

I need you to write a **complete, self-contained Python training script** (`train.py`) for a **Cash-on-Delivery Return-to-Origin (COD RTO) risk classifier**. This will run on **Kaggle Notebooks (GPU enabled)** or **Google Colab**. The script must produce a model artifact + priors JSON + metrics JSON that drop into a production FastAPI inference service.

### Background — the system this model serves

- **Domain**: Indian e-commerce Cash-on-Delivery (COD). A "return-to-origin" (RTO) happens when a customer refuses delivery → the seller eats round-trip shipping + the product is stuck in transit. We want to predict P(RTO=1) per order at checkout time so the cost-optimizer can pick the cheapest intervention (ship / OTP-verify / partial-COD / address-check / hold).
- **Inference path** (already built, DO NOT change): `POST /risk/score` → loads features → `model.predict_proba()` → `calibrate_probabilities(p, p_orig, p_und)` (Bahnsen Eq. 6 cost-sensitive resampling calibration) → `optimal_decision()` picks the 3-way BMR action → `optimal_intervention()` picks the 5-way intervention → audit + Merkle seal.
- **The calibration step is the critical hook**: `calibrate_probabilities(p, p_orig, p_und)` rescales the model's raw probability `p` using the original training-set positive rate `p_orig` and a target undersampling rate `p_und`. **If `p_orig`/`p_und` are not stored alongside the model, the calibration is a no-op and the entire cost-optimizer math is wrong.** This is bug **E14** — your script MUST fix it by emitting `priors.json` and wiring it into the registry.

### What the script must do (in order)

1. **Load a CSV** from a Kaggle Dataset path (configurable via `--data` arg, default `/kaggle/input/<dataset>/orders.csv`). Expected columns (rename if the source CSV differs — make column-mapping a dict at the top of the script so it's easy to edit):
   - `order_id` (string) — unique order id
   - `amount_inr` (float) — order value in INR (this is the per-txn false-negative cost per Bahnsen Eq. 5; the cost-optimizer uses `c_fn = amount_inr`)
   - `pincode` (string/int) — delivery pincode
   - `user_id` (string) — customer id (for historical features)
   - `device_id` (string) — device fingerprint
   - `merchant_id` (string) — seller id
   - `payment_mode` (string) — expect "COD" (filter to COD only)
   - `order_status` (string) — "DELIVERED" / "RETURNED" / "RTO" → derive the binary label: `rto = 1 if status in {"RETURNED","RTO"} else 0`
   - `created_at` (datetime) — for time-based train/test split
   - optional: `category`, `city`, `state`, `tentative_delivery_days`, `shipping_days`
2. **Feature engineering** (use sklearn Pipeline + FunctionTransformer so the SAME pipeline runs at inference):
   - `amount_log = log1p(amount_inr)`
   - `amount_bucket` = pd.qcut into 5 buckets → string
   - `pincode_prefix` = pincode.astype(str).str[:3] (first 3 digits = sub-region)
   - `user_order_count` = groupby user_id cumcount+1 (hist order count)
   - `user_rto_rate` = expanding mean of rto per user, shifted by 1 to avoid leakage (use `groupby().shift().expanding().mean()`)
   - `merchant_rto_rate` = same for merchant_id
   - `pincode_rto_rate` = same for pincode
   - `is_high_value` = amount_inr > 5000
   - `delivery_speed` = tentative_delivery_days / shipping_days (ratio)
   - OHE for `category`, `state`, `pincode_prefix`, `amount_bucket` (use sklearn `OneHotEncoder(handle_unknown='ignore', min_frequency=0.001)` to cap cardinality)
   - Drop raw `user_id`, `device_id`, `merchant_id`, `pincode` (don't one-hot these — cardinality too high; their signal is captured via the rate features above)
3. **Time-based split**: sort by `created_at`, last 20% = test set (no shuffle — respects temporal leakage). Within train, do 5-fold TimeSeriesSplit for hyperparameter search.
4. **Model**: `XGBClassifier` (or `lightgbm.LGBMClassifier` if xgboost not available on Kaggle — make it a flag). Suggested starting hyperparams:
   - `n_estimators=600, max_depth=6, learning_rate=0.05, subsample=0.8, colsample_bytree=0.7, min_child_weight=10, reg_lambda=10, tree_method='hist'` (Kaggle GPU: `tree_method='gpu_hist', device='cuda'` if `--gpu` flag passed)
   - `objective='binary:logistic'`, `eval_metric='aucpr'`
   - `early_stopping_rounds=50` on the test PR-AUC
5. **Metrics** (write to `metrics.json`):
   - `pr_auc` (PRIMARY — must be ≥ 0.60, enforced by the MLOps CI gate; if below, the script should `sys.exit(1)`)
   - `roc_auc`, `brier_score`, `precision@10%` (top-10% highest-risk orders), `recall@10%`, `expected_cost_savings_inr` (sum over test set of `amount_inr * (p_orig*rto - p_calibrated*rto)` — this is the Bahnsen cost-saving proxy)
   - `confusion_matrix` (flat list), `classification_report` (dict)
   - `feature_importances` (top 20)
6. **Priors** (write to `priors.json`) — THIS IS THE E14 FIX:
   - `p_orig` = the positive rate in the ORIGINAL training set (before any undersampling) = `train_df['rto'].mean()`
   - `p_und` = the positive rate AFTER any undersampling/balancing you apply (if you don't undersample, `p_und = p_orig` and the calibration is identity — that's fine, but record it honestly)
   - `n_train`, `n_test`, `n_pos_train`, `n_pos_test`
   - `calibration_method` = "bahnsen_eq6" (string tag so the registry knows)
   - `created_at` = ISO timestamp
7. **Artifact export**:
   - `model.pkl` = pickle of a dict `{"model": xgb_model, "feature_pipeline": pipeline, "feature_names": [...], "version": "YYYYMMDD-HHMM", "model_type": "xgboost"}` — the inference service does `model.predict_proba(pipeline.transform(row))`
   - `priors.json`, `metrics.json` (pretty-printed)
   - Write all three to `--out-dir` (default `/kaggle/working/`)
8. **Registry wiring** (optional but preferred — if the RTO Trust Layer repo is cloned into the Kaggle env at `/kaggle/working/RTO_Trust_Layer_FULL`):
   - After export, call `from src.ml.registry import register_model; register_model(name="rto_xgb", artifact_path=out_dir, priors={"p_orig":..,"p_und":..}, metrics=metrics_dict)`
   - If the import fails (repo not cloned), just print "MANUAL REGISTRATION REQUIRED" + the exact CLI command to register from the inference host.

### CLI interface (use argparse)

```
python train.py --data /kaggle/input/dataset/orders.csv \
               --out-dir /kaggle/working/ \
               --model xgboost \
               --gpu \
               --pr-auc-gate 0.60 \
               --register
```

Flags: `--data`, `--out-dir`, `--model {xgboost,lightgbm}`, `--gpu`, `--pr-auc-gate` (float, default 0.60, sys.exit(1) if pr_auc below), `--register` (boolean, attempt registry import), `--seed` (default 42), `--test-size` (default 0.2).

### Hard requirements

- **No data leakage**: time-split, expanding-window rate features shifted by 1, no `fit_transform` on the full dataset (fit on train only, transform test).
- **Reproducible**: `np.random.seed(args.seed)` + `xgb.set_config(seed=args.seed)` + write the seed into `metrics.json`.
- **Self-contained**: single file, only stdlib + pandas + numpy + scikit-learn + xgboost (or lightgbm). No internal repo imports except the OPTIONAL `register_model` call wrapped in try/except.
- **Robust to schema drift**: if a column is missing, log a warning and skip its feature (don't crash — Kaggle datasets are messy).
- **Kaggle-friendly**: print progress to stdout (Kaggle captures stdout), use `tqdm` if available, write artifacts to `/kaggle/working/` by default.
- **E14 compliance**: `priors.json` MUST be written and the `p_orig` + `p_und` values MUST be printed to stdout at the end so the user can verify the calibration is no longer dead.

### Output the script as

A single Python file, ~250-400 lines, fully commented (one-line docstrings on each function), ready to `python train.py --data ... --gpu --register` on Kaggle. Do NOT write tests. Do NOT write a notebook — write a `.py` script (the user can paste it into a Kaggle notebook code cell).

---

## END OF PASTE BLOCK

---

## How the user should use this

1. Copy the block above into a fresh Claude/ChatGPT/Gemini session.
2. The agent will produce `train.py` (~300 lines).
3. Upload `train.py` + your Amazon CSV to a Kaggle Dataset.
4. Create a Kaggle Notebook (GPU on), add the dataset, run `python train.py --data /kaggle/input/<your-dataset>/orders.csv --gpu --register`.
5. Download the 3 artifacts (`model.pkl`, `priors.json`, `metrics.json`) from `/kaggle/working/`.
6. Drop them into the RTO Trust Layer at `models/champion/` and re-register on the inference host.
7. Verify the E14 fix: `curl /v1/models/current` should show `priors: {p_orig: <float>, p_und: <float>}` (not null) AND `curl /risk/score` with a test order should produce a probability that differs from the raw model output (proving calibration is live).

## Why this fixes E14

E14 = "train.py doesn't pass priors → calibration dead". The root cause: the current training pipeline (whatever produced the existing `model.pkl`) didn't compute/store `p_orig` + `p_und`. This prompt forces the new training script to emit `priors.json` as a first-class artifact AND (optionally) call `register_model(priors=...)` so the registry's `get_priors()` returns real values. Once that lands, `calibrate_probabilities(p, p_orig, p_und)` in `registry.py:157` actually rescales the probability, and the cost-optimizer's Bahnsen Eq. 5/6 math becomes live.
