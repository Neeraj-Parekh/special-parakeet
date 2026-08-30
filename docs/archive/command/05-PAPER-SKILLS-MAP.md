# RTO Trust Layer — Paper Skills → Code Gaps Map
## The bridge between the 40-paper knowledge base and the code improvements

> Source: agent 2-knowledge synthesis (see `/home/z/my-project/worklog.md` Task ID 2-knowledge).
> Full per-paper deep dives in `/home/z/my-project/upload/RTO_Trust_Layer_FULL/paper studied/<slug>/`.

---

## Knowledge base summary

- **40 papers** distilled (from 54 source PDFs; 9 exact-dupes folded, 5 version-pairs folded)
- **5 files per paper**: `summary.md`, `skill.yaml`, `research_requests.md`, `metadata.json`, `figures.md` (where present)
- **3 most-central papers** (highest in-degree in `knowledge-graph.md`):
  1. **AI Agents in Payments: Applications, Risks and Regulations** (Cambridge UP, 2026) — 9 inbound edges
  2. **SoK: Security of Autonomous LLM Agents in Agentic Commerce** (arXiv 2604.15367, 2026) — 8 inbound edges
  3. **Agentic Proxies: Governance, Accountability, and the Architecture of a Trustworthy AI Economy** (SSRN, 2026) — 5+ inbound edges
- **5 thematic clusters** (from `paper studied/index.md`):
  - Agentic Commerce & Payments (29 papers)
  - Trust, Governance & Liability (28 papers)
  - Risk Modelling — RTO/Fraud/Delay (15 papers)
  - Production ML & Infrastructure (12 papers)
  - Supply Chain & Retail Ops (13 papers)
- **Top tags**: `governance` (22), `trust` (21), `agentic-payments` (15), `agentic-commerce` (15), `consent` (15), `security` (11), `fraud` (10), `explainability` (10), `deployment` (10), `ecommerce` (10), `regulation` (9), `cost-optimization` (8), `mlops` (8), `imbalanced-learning` (7), `liability` (7)

---

## 14-row skills → code gaps map (THE KEY DELIVERABLE)

| # | Code gap | Paper | Skill | Capability | How to apply |
|---|---|---|---|---|---|
| 1 | **Cost optimizer not wired into decision** — `cost_optimizer.py` exists but only as `policy_hint` field; `routes.py:36, 194` uses static `0.15/0.60` thresholds | Cost-Sensitive Bayes Minimum Risk (Bahnsen 2013) | `cost-sensitive-bayes-minimum-risk-fraud-decisions` | `bayes_minimum_risk_decision_layer` + `post_resampling_probability_calibration` | Replace static-threshold block with `optimal_decision()`: `cost_accept = p*C_FN`, `cost_review = C_OTP + (1-p)*C_FP + p*(1-0.82)*C_FN`, `cost_reject = (1-p)*C_BLOCK`, return min. Apply Eqs.(6)-(7) recalibration if any under-sampling used in training. **Day 1 Track C.** |
| 2 | **Mandate action-class expansion V3 §13** — currently 4 bounded actions; V3 §13 wants UPI Circle / delegated payments | NPCI OC-201B UPI Circle IoT circular + UPI Delegated Payments Lexology (Khaitan & Co) | `upi-circle-iot-delegation-compliance` + `upi-oc201b-legal-interpretation` | `delegation_limit_validation` (₹15k/month, ₹5k/txn, 24h ₹5k cooling, 5-device cap, 6-mo inactivity auto-revoke) + `consent_chain_audit` (2FA at linking, mobile+OTP, per-txn device/user-ID validation) + `agentic_payments_positioning` | Extend `ALLOWED_ACTIONS` dict with: `upi_circle_delegated_pay` (cost 5, requires_approval=True, hard cap ₹5000/txn ₹15000/month), `validate_device_id` (cost 1), `revoke_delegation_on_inactivity` (cost 2, auto at 6-mo). Wire per-txn `device_id` + `user_id` validation in `mandates.py` HMAC chain. Tag BH purpose code in audit records. **Day 1 Track D.** |
| 3 | **Feedback loop missing** — no `is_returned` ground-truth ingestion; no drift trigger from labels | Survey on Concept Drift Adaptation (Gama 2014) | `concept-drift-monitoring-adaptation` | `wrap_model_with_drift_detector` + `plan_adaptation_strategy` | Build `LabelFeedbackService` that consumes delayed `is_returned` labels (chargeback-style delay), replays them through DDM detector (`p+sigma >= p_min + 2*sigma_min` WARNING, `+3*sigma_min` DRIFT). On DRIFT, trigger shadow-retraining on rolling 90-day window. Track detection-delay + false-alarm-run-length as Prometheus metrics. **Day 2 Track G.** |
| 4 | **Concept drift detection — PSI is there but is it the best?** | Survey on Concept Drift Adaptation (Gama 2014) | `concept-drift-monitoring-adaptation` | `localize_change_with_adwin` | PSI (in `ml/registry.py`) monitors per-feature distribution shift (good for batch), but not the right tool for **online error-stream monitoring**. Add **DDM** (per-record error indicator, O(1) memory) for production-scoring error monitoring + **ADWIN** (variable sliding window, O(log W), Hoeffding bound `ε_cut = √((1/2m)ln(4\|W\|/δ))`) for change-point localization. Surface both as `/v1/models/{version}/drift` Prometheus gauges. Use McNemar/Nemenyi tests when A/B-ing retrained vs incumbent. **Day 2 Track G.** |
| 5 | **ML registry dead in prod** — `register_model` only called from tests; `current_champion()` returns None; model-card endpoint hardcodes "dev" | TFX Production ML Platform (Baylor 2017) | `tfx-style-pipeline-builder` | `gate_model_promotion` + `plan_warm_started_retraining` | Replace in-memory `register_model` call in `lifespan` with real Model Registry (Postgres-backed): store `model_version`, `model_path` (MinIO/S3 URI), `metrics` (pr_auc/roc_auc/f1 JSON), `is_champion`, `is_challenger`, `traffic_split`, `drift_status`. Add `POST /v1/models/{version}/promote` that gates on (a) fixed thresholds AND (b) head-to-head vs incumbent on `auc` + `cost_weighted_error` + per-slice metrics (merchant_category, cod_vs_prepaid, pin_code_tier). Block promotion on failure. Wire warm-starting for daily refreshes. **Day 2 Track E + H.** |
| 6 | **Feature store absent** — `features/enrich.py` called inline; no online/offline parity; no Feast | Prescriptive Analytics E-commerce Order Delivery (Kandula 2021) + TFX | `ecommerce-delivery-success-prediction-scheduling` + `tfx-style-pipeline-builder` | `predict_delivery_success_profile` + `export_transforms_with_model` | Build 3-component feature store per V2 §3.3: Online (Redis <5ms), Offline (Postgres+Parquet point-in-time correct), Registry (Feast). Critical: ship preprocessing inside model artifact so train/serve transformations are identical (TFX `export_transforms_with_model` — the +2% Google Play installs win). Engineer Kandula paper's feature set: Payment_Type (COD vs prepaid), Service_Tier, Delay, Pretermission dates, Value, Category, Weekday + address-level POI/amenity counts (compressed via robust autoencoder over 9 radii = 108 → 12 features). **Day 2 Track E + F.** |
| 7 | **Streaming transformations absent** — Microsoft Eventhouse equivalent; current pipeline is REST-only | TFX + MLOps-DevOps Integration (IJIEE 2021) | `tfx-style-pipeline-builder` + `mlops-devops-integration-planner` | `generate_data_statistics` (streaming stats via HyperLogLog) | Add a stream-processor step (Faust/Bytewax equivalent) consuming the event bus (Redis Streams now → NATS → Kafka per V3 §9.3) and running normalize/filter/aggregate as a separate continuous stage. TFX's Data Analysis uses distributed streaming approximation (HyperLogLog cited) — port this pattern to whylogs/pandas-profiler for online stats on the stream. Stats → ADWIN detector (gap #4) → shadow retrain trigger. **Day 2 Track F.** |
| 8 | **Declarative rule routing absent** — Microsoft Activator equivalent; current rules are Python module + DEFAULT_RULES list, no DSL | TFX + Challenges in Deploying ML (Paleyes 2022) | `tfx-style-pipeline-builder` + `ml-deployment-challenge-auditor` | `build_and_apply_schema` (actionable anomaly descriptions) + `audit_deployment_pipeline` | Build a YAML rule DSL per V2 §3.2: `rule_id`, `name`, `condition` (operator AND/OR + clauses), `action`, `priority`, `is_active`, `merchant_scope`. Expose `POST /v1/rules` (admin only), `PUT /v1/rules/{id}` (soft delete). Auto-actioning: rule fires → BLOCK/REVIEW/ACCEPT + notify merchant + create case. Treat rule anomalies as tracked bugs (TFX principle). Schema-validation produces actionable errors ("rule RULE-001 has unknown field 'prior_orders' — did you mean 'prior_order_count'?"). **Day 2 Track H.** |
| 9 | **Case management stub** — `CaseService._latest()` returns None always | Challenges in Deploying ML (Paleyes 2022) | `ml-deployment-challenge-auditor` | `recommend_business_metrics` + `plan_three_axis_cicd` | Wire CaseService to real Postgres table per V2 §3.6: `cases` (case_id PK, prediction_id FK, order_id, merchant_id, status OPEN/UNDER_REVIEW/APPROVED/REJECTED/ESCALATED, assigned_to, priority, created_at, resolved_at, resolution_notes, resolution_by). Expose `GET /v1/cases` (filter by status/merchant/priority), `POST /v1/cases/{id}/resolve`, `POST /v1/cases/{id}/escalate`, `GET /v1/cases/metrics` (avg resolution time, backlog, FP rate). Add SLA timers per Microsoft Activator equivalent: cases auto-escalate if `now - created_at > SLA_threshold` (LOW 24h, MEDIUM 8h, HIGH 2h, CRITICAL 30min). **Day 2 Track E + H.** |
| 10 | **Multi-channel ingest absent** — Microsoft designs for mobile banking + ATM + e-commerce + call center; user has only `ingest_kaggle.py` | Prescriptive Analytics E-commerce Order Delivery (Kandula 2021) + TFX | `ecommerce-delivery-success-prediction-scheduling` + `tfx-style-pipeline-builder` | Both — feature-parity across channels | Build 4 ingest adapters writing to same feature store: (a) E-commerce (current REST `/v1/risk/score` — keep), (b) Mobile banking (Kafka topic `mobile.orders` consumer), (c) ATM (batch CSV ingest from ATM switch logs, daily), (d) Call center (webhook from CRM). Each adapter normalizes to unified `OrderIn` schema. TFX `generate_data_statistics` runs on each channel to detect channel-specific distribution shift. Kandula's `Payment_Type` feature becomes the channel discriminator. **Day 4 Track M (cut if time short).** |
| 11 | **Tamper-evident audit incomplete** — V3 §10.3 specifies Merkle intervals + `/v1/audit/{id}/proof`; openapi.json has no such endpoint | SoK: Security of Autonomous LLM Agents in Agentic Commerce (Mao 2026) | `agentic-commerce-security-threat-model` | `recommend_layered_defenses` (layer 5: market & compliance monitoring) + `audit_agent_mandate_scoping` | Current `AuditLogger.verify_chain` is single-replica hash chain — V3 §10.3 says this is "RPO=0 lie". Upgrade to **outbox + Merkle audit v3**: write audit records to Postgres transactional outbox, periodically (every N records or T seconds) compute Merkle root, publish root to tamper-evident external anchor. Add `GET /v1/audit/{id}/proof` returning Merkle path from record to last published root. Per SoK paper D5: log the LLM reasoning behind each agent-initiated transaction (intent artifact → model decision → payment), not just the final action. **Day 2 Track H.** |
| 12 | **Model interpretability — currently LOO; should it be SHAP?** | ML-Based Prediction and Interpretability Analysis of Logistics Delay Risks in E-commerce (Hu 2025, ICCBD) | `logistics-delay-risk-prediction-shap-interpretability` | `shap_feature_importance_report` (mean \|SHAP\| + signed top-k) + `metric_asymmetry_advisor` (F2 vs F1) | Current `src/models/explain.py` uses LOO + permutation importance (agent 1-b notes `shap` is dead dep — never imported). **Switch to SHAP** (TreeExplainer doesn't support HistGB per prompt-razor line 1737; use **KernelExplainer** or perturbation-based SHAP, OR the hybrid-multistage paper's perturbation-based explainer which works on any model). Hu's paper top-feature: `Shipping mode_Standard Class mean\|SHAP\| 0.101164` — replicate on our RTO model. Use **F2-score** (not F1) when FN cost > FP cost — metric asymmetry. **Day 3 (model retraining on real data).** |
| 13 | **Cost-sensitive threshold sweep — Drummond & Holte cost curves** | Cost Curves: An Improved Method for Visualizing Classifier Performance (Drummond & Holte 2006) | `cost-curve-threshold-analysis` | `plot_cost_curve` + `find_model_crossover` + `bootstrap_performance_ci` + `select_threshold_cost_minimally` | Current `docs/cost_table.md` is 8 rows × FN=12×FP, optimal=0.15 — simplistic. Replace with **Drummond-Holte cost curves**: for each threshold sweep point, plot cost line from FP at `PC(+)=0` to FN at `PC(+)=1`, compute lower envelope across all candidate models. Find exact `PC(+)` value where challenger beats incumbent. Bootstrap CIs (≥500 resamples, 90% level) preserving row marginals. Surface as `/v1/policy/cost-curves` endpoint returning JSON for dashboard to render. Dashboard cost-curve explorer currently uses hardcoded `COSTS=[[0.15,1258],...]` — wire it to this endpoint. **Day 1 Track C.** |
| 14 | **Production ML deployment patterns — no CI/CD, no canary, no slice metrics** | TFX (Baylor 2017) + Challenges in Deploying ML (Paleyes 2022) + MLOps-DevOps Integration (IJIEE 2021) | `tfx-style-pipeline-builder` + `ml-deployment-challenge-auditor` + `mlops-devops-integration-planner` | `gate_model_promotion` (canary) + `audit_deployment_pipeline` (4-stage gap) + `plan_three_axis_cicd` (code+model+data CD) | Build `.github/workflows/mlops.yml` with 7 stages: CI quality (ruff+pytest+mypy), CI data validation (schema+Evidently drift), CT model training (DVC pull → train → evaluate → log to MLflow, fail if PR-AUC<0.60), CT model registry (Staging → auto-promote to Production if integration tests pass), CD container build (Docker + push to GHCR + Trivy scan), CD deploy (blue-green to staging → k6 load test → promote to prod), Monitor (Prometheus → Grafana → PagerDuty, auto-rollback if error>1%). Implement 3-axis CD per Challenges paper: code (GitHub Actions), model (MLflow + canary), data (DVC). Add slice metrics per TFX (merchant_category, cod_vs_prepaid, pin_code_tier). **Day 3 Track J.** |

---

## 7 priority papers — quick reference

### C1. Cost-Sensitive Credit Card Fraud Detection using Bayes Minimum Risk
- **Citation**: Correa Bahnsen, Stojanovic, Aouada, Ottersten (SnT, U. Luxembourg). ICMLA 2013, pp. 333-338. **DOI 10.1109/ICMLA.2013.68**
- **Method**: Bayes minimum risk (BMR) decision layer wrapping any calibrated classifier. Per-transaction FN cost = transaction amount; FP cost = administrative fee Ca. Recalibrate probabilities after under-sampling via Eq.(6): `P*(f|x) = P(f|x)·P_orig/P_und`.
- **Key results**: On 750k European card txns (3,500 frauds, €148,562 test fraud loss): RF-MR A costs €36,634 at Ca=€2.50 vs RF's €47,669 → **23% savings**. F1-best model saves almost nothing; S50 under-sampling saves 76% despite low F1 → **F1-best ≠ money-best**.
- **Capabilities**: `monetary_cost_evaluation`, `bayes_minimum_risk_decision_layer`, `post_resampling_probability_calibration`, `model_selection_by_cost_not_f1`
- **Pseudo-code**:
```python
probs = rf.predict_proba(X_test)[:,1]
probs_adj = probs * p_orig / p_under      # Eq.(6) recalibration
flags = [Ca*Pf + Ca*(1-Pf) <= amount*Pf   # Eq.(5) BMR rule
         for Pf, amount in zip(probs_adj, amounts)]
cost = sum(y*(flag*Ca + (1-flag)*amt) + (1-y)*flag*Ca ...)
# leaderboard sorted by cost, not by F1
```

### C2. Hybrid Machine Learning-Based Multi-Stage Framework for Detection of Credit Card Anomalies and Fraud
- **Citation**: Hatoon S. Alsagri (IMSIU, Riyadh). IEEE Access vol. 13, 2025, pp. 77039-77048. **DOI 10.1109/ACCESS.2025.3565612**
- **Method**: Two-layer stack — Layer 1 runs 6 models (LR, SVM, XGBoost, RF, KNN, DNN); Layer 2 is Linear SVM meta-learner on first-layer class+probability (not raw features). Distribution-based resampling: KNN-classified boundary points vs k-means-clustered mass points — avoids SMOTE's synthetic-noise FP problem.
- **Key results**: Kaggle MLG-ULB (284,807 txns, 492 frauds): **fraud recall 0.901, legitimate recall 0.995, model cost ratio 0.421**.
- **Capabilities**: `boundary_aware_resampling`, `probability_aware_stacking`, `bank_cost_kpi_evaluation`, `imbalanced_fraud_benchmark_reproduction`

### C3. SoK: Security of Autonomous LLM Agents in Agentic Commerce
- **Citation**: Mao, Wang, Liu, Zhu, Ma, Yan (Nanjing U + SUSTech + CityU HK). arXiv 2604.15367v2, 2026.
- **Method**: Systematization of Knowledge — 142 works synthesized into 5-dimensional threat taxonomy (D1 Agent Integrity, D2 Transaction Authorization, D3 Inter-Agent Trust, D4 Market Manipulation, D5 Regulatory Compliance) + 12 cross-layer attack vectors + 5-layer defense architecture.
- **Key results**: 12 vectors include P2T (prompt-to-transaction), T2R (tool-to-reasoning), T2T (tool-to-transaction), P2K (prompt-to-key). **No single agent-payment protocol covers all 5 dimensions.** MPP (Stripe+Tempo) judged most operationally mature.
- **Capabilities**: `threat_model_agent_payment_flow`, `recommend_layered_defenses`, `audit_agent_mandate_scoping`, `assess_protocol_coverage_gaps`
- **Use for**: mandate action-class expansion (gap #2), Merkle audit (gap #11), dual-control override (Day 2 Track H)

### C4. A prescriptive analytics framework for efficient E-commerce order delivery
- **Citation**: Kandula, Krishnamoorthy, Roy (IIM Ahmedabad + Erasmus Rotterdam). Decision Support Systems vol. 147, 2021. **DOI 10.1016/j.dss.2021.113584**
- **Method**: Two-stage — Stage 1: XGBoost + cost-sensitive weighted-loss classifier predicts P(delivery success) per order per attempt-time → "Order Success Profile" (OSP). Stage 2: OSP→slot inference → VRPTW solved via priority-ordered insertion heuristic + 2-opt* ILS.
- **Key results**: Real Indian e-commerce data (Flipkart acknowledged). Hub-A AUC **73.65%**, Hub-B AUC **79.12%**. **Delivery-cost savings 7.2% Hub-A, 10.2% Hub-B** vs baseline. Model size: 0.59 MB XGBoost vs 712 MB RF — argues for lightweight in production APIs.
- **Capabilities**: `predict_delivery_success_profile`, `generate_risk_aware_delivery_schedule`, `evaluate_imbalanced_classifier_protocol`, `simulate_prescriptive_policy_savings`
- **Use for**: real-data benchmark (PR-AUC target > 0.70), feature engineering (Payment_Type, Service_Tier, Delay, POI counts)

### C5. A Survey on Concept Drift Adaptation
- **Citation**: Gama, Žliobaitė, Bifet, Pechenizkiy, Bouchachia. **ACM Computing Surveys 46(4), March 2014. DOI 10.1145/2523813**
- **Method**: Unified taxonomy of adaptive learning systems around 4 combinable modules: memory, change detection (SPRT/CUSUM/Page-Hinkley, SPC/DDM 2σ-warning 3σ-drift, ADWIN with Hoeffding bound, contextual), learning (retrain vs incremental vs streaming VFDT; blind vs informed; ensembles DWM/SEA/DDD), loss estimation.
- **Key results** (conceptual): real concept drift = change in `P(y|X)` (real) vs `P(X)` (virtual). Outliers must NOT trigger adaptation. Detection delay + false-alarm run length are detector-quality SLOs. Prequential test-then-train + controlled permutations required (cross-validation breaks temporal order).
- **Capabilities**: `wrap_model_with_drift_detector`, `localize_change_with_adwin`, `plan_adaptation_strategy`, `evaluate_streaming_model`
- **Use for**: feedback loop (gap #3), drift detection (gap #4)

### C6. Challenges in Deploying Machine Learning: a Survey of Case Studies
- **Citation**: Paleyes, Urma, Lawrence (Cambridge). **ACM Computing Surveys 2022. DOI 10.1145/3533378** (arXiv 2011.09926)
- **Method**: Qualitative survey mapping practitioner-reported challenges onto Ashmore 4-stage ML deployment workflow (Data management, Model learning, Model verification, Model deployment) + cross-cutting ethics/law/trust/security.
- **Key results** (selected): Airbnb shipped single-hidden-layer NN with 32 ReLU units after complex DL failed (simplicity wins). Booking.com: proxy metrics like clicks fail to convert to business metrics. Tramèr model-stealing: 650-4,013 queries replicates production models. **Data poisoning via feedback loops: 8% poisoned samples caused wrong dosage for half of patients**. Microsoft Tay corrupted in 16 hours. Concept drift after 2008 financial crisis = discrete change. Continuous delivery for ML must handle **3 axes: code, model, data**.
- **Capabilities**: `audit_deployment_pipeline`, `recommend_business_metrics`, `plan_three_axis_cicd`, `assess_adversarial_exposure`
- **Use for**: case management (gap #9), production ML patterns (gap #14), CI/CD (Day 3 Track J)

### C7. TFX: A TensorFlow-Based Production-Scale Machine Learning Platform
- **Citation**: Baylor et al. (Google). **KDD'17 Applied Data Science. DOI 10.1145/3097983.3098021**
- **Method**: 6-stage pipeline: (1) Data Analysis (per-feature stats, HyperLogLog at scale); (2) Data Transformation (vocab generation; **transformations exported as part of the trained model** — eliminates train/serve skew); (3) Data Validation (versioned schema, actionable anomaly descriptions); (4) Trainer (warm-starting selectively initializes sparse-feature embeddings); (5) Model Evaluation & Validation (canary vs prod baseline + slice-level metrics); (6) Serving (TensorFlow Serving, multitenancy, dedicated threadpool size 1-2, specialized lazy protobuf parser).
- **Key results** (Google Play case study): Time-to-production reduced from months → weeks; **+2% app installs from removing a discovered train/serve feature skew**. Serving: p99.9 latency during model loads cut from ~500-1500ms → ~75-150ms via dedicated load threadpool. Specialized lazy protobuf parser 2-5× faster.
- **Capabilities**: `generate_data_statistics`, `build_and_apply_schema`, `export_transforms_with_model`, `plan_warm_started_retraining`, `gate_model_promotion`, `isolate_serving_load_paths`
- **Use for**: ML registry (gap #5), feature store (gap #6), streaming transforms (gap #7), declarative rules (gap #8), production ML patterns (gap #14)

---

## Bonus gaps covered by additional papers

- **Repo amnesia** (V3 audit): `Challenges in Deploying ML` paper's `audit_deployment_pipeline` capability maps V2 RFC's "missing" services against existing `src/*.py` files — prevents re-building what already exists.
- **AGPL license contamination** (V3 audit): listmonk (AGPL-3.0) vs Apache 2.0 stack. SoK paper's `assess_protocol_coverage_gaps` maps to license-coverage analysis — flag listmonk as contaminated, recommend permissive alternative (nodemailer + custom templates, or Postfix+MailHog for dev).
- **Patent fabrication** (V3 audit): US20240012345A1/US20230187654B2/WO2024/098765A1 are SUSPECT-FABRICATED. Do NOT cite in pitch deck. Replace with citations from 40-paper KB (which has DOIs for all real papers).
- **Override endpoint contradiction**: V3 §12.1 promises dual-control but openapi.json has single-admin. SoK paper's `audit_agent_mandate_scoping` applies directly — implement dual-control: override requires 2 admin signatures (HMAC chain), per merchant consent log.
- **Idempotency cache memory leak**: `state["idem"]` is unbounded dict. Challenges paper's `audit_deployment_pipeline` flags this. Fix: bounded LRU (`cachetools.TTLCache(maxsize=10000, ttl=3600)`), or move to Redis with TTL.

---

## Top-3 actions to take NOW (highest leverage, per agent 2-knowledge)

1. **Wire `optimal_decision()` into `routes.py`** — replace static 0.15/0.60 thresholds. ~2h. Source: Cost-Sensitive Bayes Minimum Risk (Bahnsen 2013). **Closes gap #1.** → Day 1 Track C
2. **Build the 6-stage TFX pipeline** (profile → schema-validate → transform-in-artifact → train → canary-gate → serve) using lightweight OSS (whylogs, Great Expectations, sklearn, FastAPI). ~1 day. Source: TFX + Challenges in Deploying ML. **Closes 4 of 14 gaps (#5, #6, #7, #14).** → Day 2-3
3. **Build LabelFeedbackService with DDM + ADWIN drift detection**. ~4h. Source: Survey on Concept Drift Adaptation (Gama 2014). **Closes 3 of 14 gaps (#3, #4, sets up #5).** → Day 2 Track G

These 3 actions close 6 of 14 code gaps and create the demo moments for the 4-Question Gate:
- "is the model still right?" → needs drift detection (gap #3, #4)
- "did the math hold up?" → needs cost-optimizer wiring (gap #1)
- "is the audit truthful?" → needs Merkle upgrade (gap #11)

---

*Last updated: Aug 27, 2026. Source: agent 2-knowledge synthesis.*
