# Model Card — RTO Trust Layer scorer

> Format per Google Model Cards spec (Mitchell et al., "Model Cards for
> Model Reporting," FAT\* 2019, DOI 10.1145/3287560.3287596). Covers
> the model that powers `POST /risk/score` and the cost-optimal BMR
> decision layer (Bahnsen 2013, ICMLA).
>
> Machine-readable twin at `GET /v1/compliance/model-card` (scorer
> scope). Generated from `current_champion()` in the live model
> registry; falls back to the lifespan-pinned artifact if no champion
> is registered.

---

## 1. Model details

| Field | Value |
|---|---|
| Model name | RTO Trust Layer scorer |
| Model type | `sklearn.ensemble.HistGradientBoostingClassifier` |
| Library | scikit-learn 1.8 (per `requirements.txt` `>=1.8`) |
| Hyperparameters | `max_iter=300`, `learning_rate=0.08`, `max_depth=6`, `l2=1.0`, `categorical_features=from_dtype` (native categorical handling — no manual encoding) |
| Model size | ~0.6 MB joblib (vs ~712 MB for an equivalent RandomForest; the Kandula 2021 DSS paper makes the same lightweight-model argument) |
| Decision layer | `optimal_decision(p, weights)` — Bahnsen Bayes Minimum Risk per-order cost argmin (ICMLA 2013, DOI 10.1109/ICMLA.2013.68) |
| Threshold manager | Cost-optimal sweep — Drummond & Holte 2006 (DOI 10.1007/s10994-006-8199-5), `/v1/policy/cost-curves` endpoint |
| Drift detectors | PSI (existing, batch) + DDM + ADWIN (Track G Day 2, Gama 2014 §3.2/§3.3) |
| Explainability | Permutation AP-drop on held-out set (current). SHAP KernelExplainer planned Day 4 (TreeExplainer doesn't support HistGB per prompt-razor line 1737) |
| Registry | Lightweight Postgres-backed (V3 rejected MLflow-server as cargo-cult). Champion/challenger promotion gated on `auc` + `cost_weighted_error` + per-slice metrics (TFX §6 pattern) |
| Owner | Neeraj Parekh, ENTC TY, MITAOE |
| Date | August 2026 |
| License | Same as repo (Apache 2.0 implied by V3 license hygiene) |
| Citation | Bahnsen et al. ICMLA 2013 (DOI 10.1109/ICMLA.2013.68) for the decision layer; Drummond & Holte 2006 for cost curves; Gama et al. 2014 (DOI 10.1145/2523813) for drift |

---

## 2. Intended use

### Primary intended use

Score cash-on-delivery (COD) orders for return-to-origin (RTO) risk
at address level for Indian e-commerce merchants. Returns a
probability `p(RTO) ∈ [0, 1]`, a 3-way decision
(`ACCEPT / REVIEW / REJECT`) computed via the Bahnsen BMR cost
argmin, and per-prediction reason codes (top-5 features ranked by
AP drop).

### Primary intended users

- **Merchant order-management systems** integrating via REST
  (`POST /risk/score`) at the dispatch-decision point.
- **Merchant risk analysts** tuning thresholds, reading their own
  audit trail, exporting CSV for compliance via the dashboard.
- **Razorpay judges** evaluating the platform as a Track 02 AI Risk
  Manager submission.

### Out-of-scope uses

- **Non-COD orders.** The model is trained on COD orders; prepaid
  orders don't have an RTO concept (payment already settled).
  `OrderIn.payment_method` accepts `Prepaid` for completeness but the
  model's output is undefined for prepaid.
- **Non-Indian addresses.** The `city_tier` feature
  (`tier_1|tier_2|tier_3`) is calibrated for the Indian Tier-1/2/3
  city classification. Non-Indian cities have no equivalent.
- **Real-time fraud detection.** The model is a batch RTO scorer
  trained on historical order outcomes with chargeback-style label
  delay (3-21 days post-dispatch). It is NOT a real-time fraud
  classifier — the `order_hour` feature has negative permutation
  importance (`-0.0027`), confirming it carries no signal.
- **Autonomous decision authority.** The model informs; it never
  authorizes. Per V3 §4 principle 6: "Determinism where money moves,
  probability where it doesn't." Rules + mandates + gates are
  deterministic; the model's probability is an input to the
  cost-optimal BMR argmin, never the final word.

---

## 3. Training data

### Synthetic (current)

| Property | Value |
|---|---|
| Dataset | CODScore synthetic-but-realistic COD orders CSV |
| Rows | 7,235 |
| Features | 10 (is_cod, PriorReturns, PriorOrders, city_tier, address_quality, log_order_value, Items, discount_pct, OrderDay, OrderHour) |
| Positive rate | ~23% (RTO labels) |
| Label definition | `DeliveryStatus == "Returned" → is_returned=1` |
| Schema compatibility | Mirrors real Indian e-commerce orders; swapping in a real labeled dataset is drop-in (per `scripts/ingest_kaggle.py`) |
| Leakage control | `GroupShuffleSplit` on `CustomerID` — repeat buyers cannot leak across train/test. `group_leakage()` assertion = 0 every build (CI gate). |
| Split | 80/20 customer-grouped holdout; PSI reference sample taken from training set for drift monitoring |

### Real-data upgrade path (Day 4 Track L)

The Track L Day 4 pipeline (`scripts/ingest_kaggle.py` →
`scripts/retrain_real.py`) ships the drop-in real-data upgrade. After
the user downloads the Amazon India Sale Report CSV (~129k orders) from
Kaggle into `data/raw/amazon_sale_report.csv`, both scripts run end-to-end
with no source-file changes: ingestion maps columns to the unified
schema, the retrain loads the unified CSV via `load_ingested_real()`,
trains HistGB on the leakage-safe CustomerID-grouped split, evaluates
PR-AUC + ROC-AUC + F1 + precision/recall@threshold, registers as champion
if PR-AUC beats the synthetic incumbent, and regenerates
`docs/cost_table.md` + `docs/feature_importance.md` on real data. CI
gate (`.github/workflows/mlops.yml` Stage 3 + `scripts/retrain_real.py`
exit code): PR-AUC < 0.60 blocks promotion.

| Dataset | Source | Size | Expected lift |
|---|---|---|---|
| Amazon India Sale Report | Kaggle (user-uploaded, public) | ~129,000 orders | **DEPLOYED as default champion** (`?dataset=amazon`) — measured PR-AUC **0.1027** (6.05× baseline 0.0170); honest for 1.7% prevalence. No `user_id` history → `user_rto_rate` / `merchant_id_rto_rate` are inert. Ceiling for any model on this data is ~0.12. Target after Shiprocket NDA data: ≥ 0.72 (Kandula 2021 DSS vol. 147, DOI 10.1016/j.dss.2021.113584) |
| **Olist Brazilian e-commerce** | Kagglehub `olistbr/brazilian-ecommerce` | 99,441 orders (19,784 boleto subset) | **DEPLOYED as `?dataset=olist` alternate champion** — measured PR-AUC **0.3950** (32× baseline, **3.8× the Amazon champion**), ROC-AUC 0.7676, Brier 0.0439. Real `user_id`/`merchant_id` history (494 repeat users in the boleto subset) → expanding-window `user_id_rto_rate` / `merchant_id_rto_rate` features actually fire here. Honest caveat: `boleto` ≠ Indian COD (canceled/unavailable ≠ true RTO); 1.24% positive rate vs Indian 25–60%. Closest public proxy on Earth. |
| Indian E-commerce Dataset | Kaggle | ~50,000 orders | Smaller but cleaner; explicit COD flag + return status |
| Online Retail Dataset | UCI ML Repo / Kaggle | ~541,000 transactions | UK-based; no RTO labels but useful for RFM feature engineering patterns |

Plus geocoding layer (deferred): India Post pincode directory (free,
official) for pincode existence validation + Here Technologies POI
data (12 amenity types × 9 radii = 108 features compressed to 12
via robust autoencoder, per Kandula 2021).

### The `is_cod` tautology — honest documentation

Permutation AP-drop puts `is_cod` at **0.1796** — the highest feature
importance. This is near-tautological: the whole problem is COD RTO
risk, so a feature flag that says "this is a COD order" trivially
separates the positive class.

**Reframe (per `00-MASTER-PLAN.md` Tier 3 Q6 + V3 §21 claims ledger):**

> `is_cod` gates model invocation. The model runs only on COD orders
> (prepaid orders don't have an RTO concept). `is_cod` is a pass-through
> for logging — it tells the audit trail "this was a COD order" but
> carries no predictive signal beyond "the model was invoked."

The **real** signal lives in the next four features: `PriorReturns`
(0.1150), `PriorOrders` (0.0700), `city_tier` (0.0505),
`address_quality` (0.0393). These are the features a merchant can
actually move — by tightening customer-history thresholds, by
geographic filtering, or by adding address-validation UI at checkout.

The `OrderDay` (-0.0011) and `OrderHour` (-0.0027) features have
**negative** permutation importance — they're noise. They were kept
in the model for forward-compat with real data (where time-of-day
may carry signal in dense urban areas) but documented as cut candidates.

Full feature importance table: [`feature_importance.md`](feature_importance.md).

---

## 4. Evaluation

### Metrics on synthetic CODScore holdout (legacy — NOT deployed)

The numbers below are the **synthetic-data baseline** from the
7,235-row `data/raw/cod_orders.csv` placeholder (23% positive rate).
The live `/risk/score` endpoint serves the **real** Kaggle Amazon
champion (PR-AUC 0.1027 — see §1) and the Olist champion (PR-AUC
0.3950 — `?dataset=olist`). These synthetic numbers are preserved
here for traceability of the E1/E2/E3 experiments ladder; they are
NOT the model in production.

| Metric | Value | Notes |
|---|---|---|
| PR-AUC (E2 features, synthetic) | **0.5495** | Primary metric — 23% positive rate makes accuracy meaningless |
| ROC-AUC (E2, synthetic) | **0.808** | Reported for parity with Kandula 2021 (AUC 73-79%) |
| Recall @ cost-optimal threshold (0.15) | 0.789 | The fraction of true RTO orders we catch |
| Precision @ cost-optimal threshold (0.15) | 0.406 | The fraction of flagged orders that are actually RTO |
| FP share of flagged @ 0.15 | 52.4% | The cost of the wide-net strategy — accepted because FN costs 12x FP |
| Leakage (group_overlap) | **0** | Asserted every build; CI gate |

### Experiments ladder

| Experiment | Features | PR-AUC | ROC-AUC | FP share of flagged | Verdict |
|---|---|---|---|---|---|
| E1 | order + customer only | 0.524 | 0.794 | 56.2% | baseline |
| **E2** | + address quality | **0.550** | **0.808** | **52.4%** | **kept** |
| E3 | + state infra aggregates | 0.545 | 0.808 | 56.4% | no lift, cut |
| E4 | threshold × cost model | — | — | — | optimal thr = 0.15 |

### Cost-optimal threshold math

Per Bahnsen ICMLA 2013 Eq.(5): the BMR rule for each order is
`cost_accept = p · C_FN`, `cost_review = C_OTP + (1-p) · C_FP +
p · (1 - otp_eff) · C_FN`, `cost_reject = (1-p) · C_BLOCK`. Decision =
`argmin` over the three.

Default weights (in `src/business/cost_optimizer.py` and
`src/api/routes.py:DEFAULT_COST_WEIGHTS`):

| Weight | Value | Meaning |
|---|---|---|
| `c_fp` | ₹50 | false-positive (good order held) admin / review fee |
| `c_fn` | ₹600 | false-negative (missed RTO) reverse-logistics + refund (12x C_FP, per industry RTO economics) |
| `c_otp` | ₹5 | REVIEW-gate selective-OTP verification cost |
| `c_block` | ₹1,000 | false-block (good order blocked) goodwill / churn cost |
| `otp_effectiveness` | 0.82 | published selective-OTP RTO-catch rate (industry-reported 0.78-0.84; V3 §21 marks UNVERIFIED-industry until primary source is found) |

At the cost-optimal threshold (0.15) on the E2 model, the policy is a
wide review net: recall 79%, precision 41%, applied through cheap
interventions (selective OTP, partial-COD). This matches published
selective-OTP results (78-84% fraud reduction at 4-7% conversion
cost — Pragma 2025, see [`RESEARCH.md`](RESEARCH.md)).

Full cost table: [`cost_table.md`](cost_table.md). Cost-curve explorer
endpoint: `/v1/policy/cost-curves` (Drummond-Holte sweep, ≥500
bootstrap CIs preserving row marginals — Track C Day 1).

---

## 5. Explainability

### Current (E2)

Permutation AP-drop on the held-out set, one-feature-at-a-time
perturbation vs population reference (NOT SHAP TreeExplainer — it
doesn't support HistGradientBoostingClassifier per prompt-razor
line 1737). Top-5 per-prediction reason codes are surfaced in the
`POST /risk/score` response's `explanation` field, ranked by
absolute `delta_prob`, with a `direction` field
(`raises_risk` / `lowers_risk`).

Sample reason panel (from `scripts/demo_agent.py` order #2):

```json
"explanation": [
  {"feature": "city_tier",       "value": "tier_3",         "delta_prob": 0.419, "direction": "raises_risk"},
  {"feature": "log_order_value", "value": 9.43,             "delta_prob": 0.268, "direction": "raises_risk"},
  {"feature": "is_cod",          "value": 1,                "delta_prob": 0.180, "direction": "raises_risk"},
  {"feature": "PriorReturns",    "value": 0,                "delta_prob": 0.115, "direction": "raises_risk"},
  {"feature": "PriorOrders",     "value": 0,                "delta_prob": 0.070, "direction": "raises_risk"}
]
```

### Planned (Day 4 Track L retraining on real data)

SHAP KernelExplainer (works on any model — the hybrid-multistage
paper's perturbation-based explainer is the alternative if KernelExplainer
is too slow at scale). Top-feature benchmark from Hu et al. (ACM 2025,
DOI 10.1145/3779475.3779510): `Shipping mode_Standard Class mean|SHAP| = 0.101` — replicate on our RTO model. Use **F2-score** (not F1)
when FN cost > FP cost per the metric-asymmetry-advisor capability.

Until SHAP is wired, `scripts/retrain_real.py` regenerates
`docs/feature_importance.md` via `src.models.explain.global_importance`
(`sklearn.inspection.permutation_importance` with `n_repeats=10`,
`scoring="average_precision"`) on every retrain — both the synthetic
and real-data paths produce the same AP-drop table.

---

## 6. Limitations

1. **Synthetic data + Amazon-as-ceiling.** The 7,235-row CODScore
dataset is a synthetic-but-realistic placeholder (kept for the
cost-curve precompute). The **deployed** model is the real Kaggle
Amazon India Sale Report champion (PR-AUC 0.1027 — honestly low
for 1.7% prevalence; ceiling ~0.12 because Amazon has no `user_id`
history so `user_rto_rate` is inert). The Olist boleto champion
(PR-AUC 0.3950, 3.8× Amazon) is the closest public-proxy benchmark
with real user history — wired as `?dataset=olist` so the lift is
visible live. Indian real-COD PR-AUC of 0.60+ needs NDA-gated
Shiprocket/Delhivery data. Day 4 Track L closes the synthetic-to-real
gap via `scripts/ingest_kaggle.py` + `scripts/retrain_real.py`;
the user runs them after downloading the Amazon Sale Report from
Kaggle.
2. **Single model, no ensemble.** V3 §11.6 prescribes a hybrid
   multi-stage ensemble (per Alsagri 2025 IEEE Access, DOI
   10.1109/ACCESS.2025.3565612 — Layer 1: 6 models, Layer 2: Linear
   SVM meta-learner). The current model is a single HistGB. The
   ensemble is a Day 4+ stretch goal.
3. **No concept-drift adaptation beyond DDM/ADWIN.** The drift
   detectors (Track G, Gama 2014 §3.2/§3.3) flag DRIFT and fire a
   shadow-retrain trigger, but there's no ensemble reweighting (DWM
   / SEA / DDD per Gama 2014 §4) — the model is retrained wholesale
   on a rolling 90-day window. The DWM-style incremental ensemble
   is a deferred enhancement.
4. **No adversarial robustness testing.** The `EC-M1` edge case
   (adversarial padding — gaming `address_quality` to `'complete'`)
   is documented in V3 §14 but no adversarial probe suite exists.
   Tramèr model-extraction mitigation (rate-limit + capped top-k
   reasons + per-key explanation quota) is in place; watermarking
   scores with low-bit jitter keyed per tenant is documented but not
   implemented.
5. **Permutation importance, not SHAP.** See §5 above — the
   explainability is one-at-a-time perturbation vs population
   reference. SHAP KernelExplainer swap is planned Day 4.
6. **In-sample cost-curve data.** The `/v1/policy/cost-curves`
   endpoint uses the training set for the cost sweep (documented in
   the response's `data_source: "train_df_in_sample"`). Track E +
   G Day 2 deferred the swap to the held-out test slice + delayed-label
   feedback. At synthetic-data scale (7k rows) the overfit risk is
   small; at real-data scale (129k rows) it matters.
7. **Single-region (India).** The `city_tier` feature is calibrated
   for Indian Tier-1/2/3. Multi-region deployment requires retraining
   + a region-specific feature column.
8. **Single-tenant demo.** The schema (Track E migration 001) has a
   `merchant_id` JSONB column ready for multi-tenancy, but the
   `X-Merchant-Id` header is not yet wired into `/risk/score`. All
   audit counts in `/v1/usage` are aggregate.

---

## 7. Bias analysis

### Identified bias risks

1. **`city_tier` may bias against Tier-3 cities (rural India).** The
   feature has 0.0505 permutation importance — Tier-3 cities are
   scored higher-risk on average. If the synthetic data reflects
   real-world delivery infrastructure gaps (poor roads, sparse
   courier coverage, longer delivery times), this is signal; if it
   reflects a class imbalance (fewer Tier-3 orders in training, hence
   higher variance on their predictions), it's bias. Without real
   Indian data we can't disambiguate.

2. **`PriorOrders = 0` penalizes new customers.** A new customer with
   no order history is scored higher-risk by definition. This biases
   against customer acquisition — a merchant using RTO Trust Layer
   strictly might suppress all first-time COD orders.

3. **`address_quality` may penalize non-English addresses.** The
   `complete|partial|vague` classification is heuristic; addresses
   in regional scripts (Devanagari, Tamil, Bengali) with
   non-standardized formatting may be classified `vague` even when
   they're complete in their native form.

### Mitigations

1. **Rules engine (the primary mitigation).** A merchant can override
   any model bias by adding a rule (e.g., "Tier-3 + COD + PriorOrders
   ≥ 5 → REVIEW, not REJECT"). The rules fast-path short-circuits the
   model — `decision_source = rules_engine_block` — so the merchant
   has the final word on any class of order.
2. **Dual-control override (V3 §12.1, Track H).** Two admins must
   co-sign any override of a model decision. The override is recorded
   in the audit hash chain with both admin-key digests — a verifier
   can prove accountability without retaining raw secrets.
3. **Merchant-configurable thresholds.** The cost-optimal BMR weights
   (`c_fp`, `c_fn`, `c_otp`, `c_block`, `otp_effectiveness`) are
   module-level defaults in `src/api/routes.py:DEFAULT_COST_WEIGHTS`
   but the dashboard exposes them as a tunable cost-curve explorer
   (Track C Day 1) — a high-FN-cost merchant (jewelry, electronics)
   sets `c_fn` high; a high-FP-cost merchant (fast-fashion, low-AOV)
   sets `c_fp` high.
4. **Per-slice metrics in the model registry (TFX §6 pattern,
   deferred).** The champion-promotion gate (planned Track E + H Day
   2 follow-on) checks `auc` + `cost_weighted_error` + per-slice
   metrics on `merchant_category`, `cod_vs_prepaid`, `pin_code_tier`
   — a model that improves aggregate AUC while degrading Tier-3
   precision is blocked from promotion.
5. **Audit trail + right-to-explanation.** Every decision is logged
   with the exact request, model version, probability, decision, and
   ranked reason codes. A customer who is REJECTed can request the
   explanation bundle (CSV export from `/v1/compliance/audit-export`).
   Per Goodman & Flaxman (AI Magazine 2017), this is the GDPR
   right-to-explanation posture.

---

## 8. Ethical considerations

- **RTO scoring can discriminate against new customers** (low
  `PriorOrders`) and against Tier-3 cities. The first biases against
  customer acquisition; the second biases against rural India. Both
  are mitigated by the rules engine + dual-control override +
  merchant-configurable thresholds + audit trail (see §7).
- **The agent layer must not become a discriminatory auto-reject
  bot.** Per V3 §13, the bounded agent has zero ambient authority —
  high-cost actions (`block_order`, `upi_circle_delegated_pay`)
  require dual-control human approval. The agent can suggest; it
  cannot execute.
- **Label poisoning prevention.** The `/v1/feedback/ingest` endpoint
  (Track G) is admin-scope only — merchants cannot self-report
  `is_returned` outcomes to suppress retrain triggers. A 403 is
  returned for scorer-scope keys with the detail `"feedback ingestion
  requires admin scope (label poisoning prevention)"`.
- **PII redaction.** `customer_id` is salted+hashed into the audit
  log (`redact_customer()` in `src/audit/logger.py`); the digest
  `cust_<sha256-truncate-16>` is what's stored, never the raw ID.
  Per V3 §10.4, Zone-0/Zone-1/Zone-2 PII zoning is enforced by
  contract tests (planned: hypothesis property-based fuzzing that no
  Zone-0 substring ever appears in logs/audit/events).
- **Crypto-shredding for DPDP erasure.** V3 §10.3 step 6 prescribes
   per-tenant/per-customer DEK envelope encryption so the
   append-only-vs-deletion paradox is resolved honestly: destroy the
   DEK, the record structure + hashes remain verifiable, the
   plaintext is unrecoverable.

---

## 9. Caveats and open questions

- **Headline PR-AUC truth.** Three honest numbers, all measured from
  committed artifacts: (a) synthetic CODScore baseline = 0.5495
  (NOT deployed — see §4 above); (b) real Kaggle Amazon India
  champion = **0.1027** (deployed as default `/risk/score`); (c) real
  Olist boleto champion = **0.3950** (deployed as `?dataset=olist`,
  3.8× Amazon because it carries real `user_id`/`merchant_id`
  history). Indian real-COD true rate is 0.25–0.60 (Shiprocket /
  Delhivery NDA data) — we report the best public-proxy metrics, not
  aspirational ones. The 0.72+ Kandula 2021 benchmark requires the
  NDA dataset.
- **The `otp_effectiveness = 0.82` weight is industry-reported, not
  measured.** Per V3 §21 claims ledger, the selective-OTP
  effectiveness range (0.78-0.84) is UNVERIFIED-industry until a
  primary source (logistics whitepaper) is found. The
  Pragma 2025 industry brief (see [`RESEARCH.md`](RESEARCH.md))
  reports 78-84% fraud reduction at 4-7% conversion cost — same
  range, same UNVERIFIED-industry status.
- **No live A/B test.** The platform has not been deployed against
  real merchant traffic; all numbers are from the offline
  customer-grouped holdout. The `POST /v1/simulate` endpoint (Track H
  Day 2) is the policy explorer — a merchant can replay a
  historical slice through a proposed threshold / rule / model-version
  and see the cost-curve + confusion + affected-order sample before
  flipping a live policy. This is the demo moment that separates us
  from "we trained XGBoost" teams (V3 §11.5).
- **Multi-process drift state.** The DDM/ADWIN detectors
  (`src/feedback/label_service.py`) hold in-memory state per worker.
  In a multi-worker uvicorn deployment, each worker sees a different
  slice of the label stream — the drift signal is partial. Single-worker
  deployment is fine for the demo. Multi-process shared detector state
  via Redis HINCRBY + a Lua script for the σ_min comparison is the
  documented upgrade path (V3 §11.4).
