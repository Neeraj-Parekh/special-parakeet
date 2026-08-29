# RTO Trust Layer - UML Diagrams

> 8 diagram types covering architecture, sequence, data flow, ER schema,
> agent state, user journey, deployment topology, and class structure.
> All Mermaid syntax - renders natively on GitHub.
> Sources: actual source code read on 2026-08-28 (src/api/routes.py,
> src/api/agent_allowlist.py, src/api/mandates.py, src/api/keys.py,
> src/audit/logger.py, src/cases/service.py, src/ml/registry.py,
> src/rules/engine.py, src/business/cost_optimizer.py, src/stream/*.py,
> src/ingest/*.py, alembic/versions/001-007, docker-compose.yml,
> .github/workflows/*).

## Index

| # | Diagram | Mermaid Type | File |
|---|---------|------|------|
| 01 | 1. System Architecture | Component (flowchart TB, 5 subgraph layers) | [`figures/01-system-architecture.mmd`](figures/01-system-architecture.mmd) |
| 02 | 2. Score Request Sequence | Sequence Diagram (sequenceDiagram, actor + 17 participants + alt/else blocks) | [`figures/02-score-request-sequence.mmd`](figures/02-score-request-sequence.mmd) |
| 03 | 3. Data Flow | Data Flow Diagram (flowchart LR, cylinder data stores + processes) | [`figures/03-data-flow.mmd`](figures/03-data-flow.mmd) |
| 04 | 4. ER Schema | Entity-Relationship (erDiagram, 10 entities + attributes + 4 relationships) | [`figures/04-er-schema.mmd`](figures/04-er-schema.mmd) |
| 05 | 5. Agent Override State | State Machine (stateDiagram-v2, 9 success states + 8 failure terminals + notes) | [`figures/05-agent-override-state.mmd`](figures/05-agent-override-state.mmd) |
| 06 | 6. Merchant User Journey | User-Journey Flowchart (flowchart TD, decision diamonds + 3 terminal classes) | [`figures/06-merchant-user-journey.mmd`](figures/06-merchant-user-journey.mmd) |
| 07 | 7. Deployment Topology | Deployment (flowchart TB, 5 subgraphs: GitHub / Runner / Registry / Host / External) | [`figures/07-deployment-topology.mmd`](figures/07-deployment-topology.mmd) |
| 08 | 8. Class Diagram (bonus) | Class Diagram (classDiagram, 14 classes + composition/dependency edges) | [`figures/08-class-diagram.mmd`](figures/08-class-diagram.mmd) |

---

## 1. System Architecture

**Type:** Component (flowchart TB, 5 subgraph layers)

C4-style component view: 5 layers (Edge / Application / Domain / Data / Infra) showing how the FastAPI modular monolith orchestrates AgentAllowlist, MandateCounter, AuditLogger (MerkleSealer), CaseService, RulesEngine, CostOptimizer, ModelRegistry, KeyManager (HKDF), StreamProducer, OTel + CircuitBreaker against PostgreSQL + Redis.

Source: [`figures/01-system-architecture.mmd`](figures/01-system-architecture.mmd).

```mermaid
%% RTO Trust Layer — Component Diagram (C4-L2 style)
%% Grounded in: src/api/routes.py (FastAPI app, 28 endpoints),
%%   src/api/agent_allowlist.py (SCOPE_ACTION_MAP), src/api/mandates.py
%%   (MandateVerdict + UPI Circle caps), src/api/keys.py (HKDF derive),
%%   src/audit/logger.py (MerkleSealer), src/cases/service.py (CaseService),
%%   src/ml/registry.py (current_champion/register_model), src/rules/engine.py
%%   (RulesEngine), src/business/cost_optimizer.py (optimal_decision),
%%   docker-compose.yml (api/postgres/redis/stream-worker/stream-processor/
%%   drift-consumer/prometheus/grafana/jaeger/alertmanager), alembic 001-007.
flowchart TB
  classDef edge fill:#21262d,color:#e6edf3,stroke:#30363d
  classDef app fill:#0d1117,color:#e6edf3,stroke:#58a6ff,stroke-width:2px
  classDef domain fill:#161b22,color:#e6edf3,stroke:#3fb950
  classDef data fill:#1c2128,color:#e6edf3,stroke:#d29922
  classDef infra fill:#21262d,color:#e6edf3,stroke:#f85149
  classDef external fill:#0d1117,color:#8b949e,stroke:#6e7681,stroke-dasharray:3 3

  %% -------- Edge layer --------
  subgraph EDGE["Edge Layer — browsers / agents / external traffic"]
    direction LR
    MERCH["Merchant Console<br/>dashboard/index.html<br/>(vanilla JS SPA)"]
    AGENT["Bounded Dispatch Agent<br/>scripts/demo_agent.py<br/>X-Agent-Action header"]
    SIM["Ingest Simulators<br/>scripts/run_simulators.py"]
  end

  %% -------- Application layer --------
  subgraph APP["Application Layer — FastAPI modular monolith (src/api/routes.py)"]
    direction TB
    ROUTES["FastAPI App (28 routes)<br/>/risk/score · /v1/cases<br/>/v1/explain/shap · /v1/audit/{id}/proof<br/>/risk/{id}/override · /v1/feedback/ingest"]
    SEC["AuthMiddleware<br/>src/api/security.py<br/>bearer_token + check_key + TokenBucket (429)"]
    ALLOWLIST["AgentAllowlist<br/>src/api/agent_allowlist.py<br/>SCOPE_ACTION_MAP scorer/ops/admin<br/>7 actions + override pseudo-action"]
    KEYS["KeyManager<br/>src/api/keys.py<br/>HKDF-Extract+Expand (RFC 5869)<br/>salt=rto-override-v1 info=dual-control"]
    MANDATES["MandateCounter<br/>src/api/mandates.py<br/>UPI Circle caps: ₹5k/txn ₹15k/mo<br/>₹5k 24h cooling 5 devices 6-mo"]
    AUDIT["AuditLogger<br/>src/audit/logger.py<br/>SHA-256 hash chain + MerkleSealer<br/>(every 1000 records / 3600s)"]
    CASES["CaseService<br/>src/cases/service.py<br/>OPENED→UNDER_REVIEW→<br/>APPROVED/REJECTED/ESCALATED"]
    RULES["RulesEngine<br/>src/rules/engine.py<br/>RULE-001 (₹50k BLOCK)<br/>RULE-002 (vague addr REVIEW)"]
    COSTOPT["CostOptimizer<br/>src/business/cost_optimizer.py<br/>Bahnsen BMR Eq.5/6<br/>optimal_decision ACCEPT/REVIEW/REJECT"]
    REGISTRY["ModelRegistry<br/>src/ml/registry.py<br/>champion/challenger<br/>priors blob (E14)"]
    OTEL["OTel + CircuitBreaker<br/>src/api/otel.py + breaker.py<br/>5 risk.score sub-spans + circuit state"]
    STREAM["StreamProducer<br/>src/stream/producer.py<br/>5 streams fire-and-forget"]
  end

  %% -------- Domain layer (model + features) --------
  subgraph DOMAIN["Domain Layer — ML + Features"]
    direction LR
    MODEL["Champion HistGB<br/>models/champion/model.pkl<br/>+ feature_list.json (79-dim OHE)"]
    FB["KaggleFeatureBuilder<br/>src/models/feature_builder.py<br/>from_champion_dir() → transform(raw)"]
    PRIORS["priors.json<br/>p_orig=0.01697 p_und=0.01697<br/>calibration_method=bahnsen_eq6"]
    SHAP["SHAPExplainer<br/>src/models/explain.py<br/>reason_codes_batch + serialize"]
  end

  %% -------- Data plane --------
  subgraph DATA["Data Plane"]
    direction LR
    PG[("PostgreSQL 15<br/>alembic 001-007<br/>10 tables<br/>audit_records · cases ·<br/>model_registry · api_keys ·<br/>mandate_counters · override_nonces")]
    RD[("Redis 7<br/>5 streams:<br/>risk.scores · audit.records ·<br/>cases.created · model.drift ·<br/>notifications")]
  end

  %% -------- Infra layer (workers + observability) --------
  subgraph INFRA["Infra Layer — workers + observability"]
    direction TB
    SW["stream-worker<br/>src/stream/consumer.py<br/>XREADGROUP rto-workers"]
    SP["stream-processor<br/>src/stream/processor.py<br/>HyperLogLog + sliding-window<br/>3σ spike-factor (15-a)"]
    DC["drift-consumer<br/>src/feedback/drift_consumer.py<br/>DDM + ADWIN (Gama 2014)"]
    LS["LabelFeedbackService<br/>src/feedback/label_service.py<br/>late-label feedback loop"]
    PROM["Prometheus<br/>monitoring/prometheus.yml"]
    GRAF["Grafana<br/>monitoring/grafana/<br/>rto-dashboard.json"]
    JAEGER["Jaeger :16686<br/>OTLP gRPC :4317"]
    AM["Alertmanager v0.27"]
  end

  %% -------- External sources --------
  subgraph EXT["External Sources"]
    direction LR
    KAGGLE[("Kaggle COD dataset<br/>reports/kaggle/")]
    SIMATM["ATM sim<br/>src/ingest/atm.py"]
    SIMCC["Callcenter sim<br/>src/ingest/callcenter.py"]
    SIMMOB["Mobile sim<br/>src/ingest/mobile.py"]
    SIMEC["Ecommerce<br/>(= /risk/score itself)"]
  end

  %% -------- Edge → App --------
  MERCH -->|POST /risk/score + Bearer scorer-key| ROUTES
  AGENT -->|POST + X-Agent-Action| ROUTES
  SIM --> ROUTES

  %% -------- App internal wiring --------
  ROUTES --> SEC
  ROUTES --> ALLOWLIST
  ROUTES --> KEYS
  ROUTES --> MANDATES
  ROUTES --> AUDIT
  ROUTES --> CASES
  ROUTES --> RULES
  ROUTES --> COSTOPT
  ROUTES --> REGISTRY
  ROUTES --> OTEL
  ROUTES --> STREAM

  %% -------- App → Domain --------
  COSTOPT --> MODEL
  COSTOPT --> PRIORS
  ROUTES --> FB
  ROUTES --> SHAP
  FB --> MODEL

  %% -------- App → Data --------
  AUDIT --> PG
  CASES --> PG
  REGISTRY --> PG
  MANDATES --> PG
  KEYS --> PG
  STREAM --> RD

  %% -------- App → Infra --------
  STREAM -->|XADD risk.scores / audit.records / cases.created| RD
  RD --> SW
  RD --> SP
  SP -->|XADD model.drift| RD
  RD --> DC
  DC --> LS
  LS -->|/v1/feedback/ingest| ROUTES
  ROUTES --> PROM
  PROM --> GRAF
  PROM --> AM
  OTEL --> JAEGER

  %% -------- External → App/Domain --------
  KAGGLE -->|train.py + scripts/ingest_kaggle.py| MODEL
  KAGGLE --> PRIORS
  SIMATM -->|X-Channel: atm + OrderIn| ROUTES
  SIMCC -->|X-Channel: call_center + OrderIn| ROUTES
  SIMMOB -->|X-Channel: mobile + OrderIn| ROUTES
  SIMEC -->|X-Channel: ecommerce + OrderIn| ROUTES

  class EDGE,EXT edge
  class ROUTES,SEC,ALLOWLIST,KEYS,MANDATES,AUDIT,CASES,RULES,COSTOPT,REGISTRY,OTEL,STREAM app
  class MODEL,FB,PRIORS,SHAP domain
  class PG,RD data
  class SW,SP,DC,LS,PROM,GRAF,JAEGER,AM infra
  class MERCH,AGENT,SIM,SIMATM,SIMCC,SIMMOB,SIMEC,KAGGLE external
```

---

## 2. Score Request Sequence

**Type:** Sequence Diagram (sequenceDiagram, actor + 17 participants + alt/else blocks)

End-to-end sequence for a single score request: Merchant Console -> Nginx -> FastAPI -> enforce_agent_action -> bearer_token/check_key -> Idempotency-Key cache -> verify_mandate (UPI Circle 24h cooling / Rs.15k cap) -> RulesEngine.evaluate -> KaggleFeatureBuilder.transform -> predict_proba -> calibrate_probabilities (Bahnsen Eq.6) -> optimal_decision (ACCEPT/REVIEW/REJECT) -> CaseService.open_case -> AuditLogger.log + MerkleSealer.add -> StreamProducer.publish to risk.scores / audit.records / cases.created.

Source: [`figures/02-score-request-sequence.mmd`](figures/02-score-request-sequence.mmd).

```mermaid
%% RTO Trust Layer — Sequence Diagram: POST /risk/score
%% Grounded in: src/api/routes.py:960-1700 (score handler + lifespan state),
%%   src/api/security.py (bearer_token/check_key/TokenBucket),
%%   src/api/agent_allowlist.py (enforce_agent_action Depends + SCOPE_ACTION_MAP),
%%   src/api/mandates.py (verify_mandate MandateVerdict),
%%   src/rules/engine.py (RulesEngine.evaluate),
%%   src/business/cost_optimizer.py (calibrate_probabilities + optimal_decision
%%   + optimal_intervention), src/models/feature_builder.py (transform),
%%   src/audit/logger.py (AuditLogger.log + MerkleSealer.add),
%%   src/stream/producer.py (publish to risk.scores + audit.records +
%%   cases.created), src/cases/service.py (open_case).
sequenceDiagram
  autonumber
  actor Merchant as Merchant (dashboard)
  participant Console as Merchant Console<br/>(dashboard/index.html)
  participant NGINX as Nginx :80
  participant App as FastAPI :8000<br/>src/api/routes.py
  participant Auth as bearer_token + check_key<br/>src/api/security.py
  participant Allow as enforce_agent_action<br/>SCOPE_ACTION_MAP
  participant Idem as Idempotency-Key cache<br/>idempotency_keys (alembic 001)
  participant Mandate as verify_mandate<br/>src/api/mandates.py
  participant Rules as RulesEngine.evaluate<br/>src/rules/engine.py
  participant Feat as KaggleFeatureBuilder.transform<br/>src/models/feature_builder.py
  participant Model as state["model"]<br/>HistGB model.pkl (79-dim)
  participant Priors as get_priors() p_orig/p_und<br/>src/ml/registry.py
  participant Cost as optimal_decision + optimal_intervention<br/>Bahnsen Eq.5/6
  participant Audit as AuditLogger.log + MerkleSealer.add<br/>src/audit/logger.py
  participant Cases as CaseService.open_case<br/>src/cases/service.py
  participant Stream as StreamProducer.publish<br/>src/stream/producer.py

  Merchant->>Console: Enter order (amount ₹12499,<br/>address_quality=vague, tier_3, COD)
  Console->>NGINX: POST /risk/score<br/>Authorization: Bearer score-demo-key<br/>Idempotency-Key: ORD-WEB-001:12499<br/>X-Channel: ecommerce<br/>Content-Type: application/json
  NGINX->>App: reverse proxy to uvicorn :8000

  App->>Allow: enforce_agent_action Depends<br/>(reads X-Agent-Action header)
  alt header absent (legacy scorer path)
    Allow-->>App: bypass (scorer/admin auth applies)
  else X-Agent-Action declared
    Allow->>Allow: lookup caller scope via get_key_scope()<br/>SCOPE_ACTION_MAP[scope] contains action?
    alt action in scope (e.g. score_order for scorer)
      Allow-->>App: permitted
    else action out of scope
      Allow-->>App: 403 scope 'scorer' cannot perform action 'block_order'
    end
  end

  App->>Auth: bearer_token(authorization) + check_key(token, "scorer")
  alt invalid key
    Auth-->>App: 401 invalid key
    App-->>NGINX: 401
    NGINX-->>Console: HTTP 401
  end
  App->>App: TokenBucket.allow(client) (rate-limit / 429)

  App->>Idem: lookup Idempotency-Key
  alt cached response exists
    Idem-->>App: replayed=True cached body
    App-->>NGINX: 200 replayed
    NGINX-->>Console: cached JSON
  else fresh request
    App->>Mandate: verify_mandate(X-Mandate, amount_inr, device_id, user_id)
    alt MandateVerdict.TAMPERED or EXPIRED
      Mandate-->>App: 422 mandate_invalid
    else MandateVerdict.BREACH (cap exceeded)
      Mandate-->>App: 422 mandate_breach
    else MandateVerdict.REVIEW (24h cooling active)
      Mandate-->>App: REVIEW cooling_period_active
    else MandateVerdict.VALID
      Mandate-->>App: fall through to cost-optimizer
    end

    App->>Rules: RulesEngine.evaluate(order)
    alt RULE-001 amount > ₹50k
      Rules-->>App: BLOCK
    else RULE-002 high-value vague COD
      Rules-->>App: REVIEW
    end

    App->>Feat: KaggleFeatureBuilder.transform(order)
    Feat-->>App: X shape (1, 79)
    App->>Priors: get_priors() → p_orig=0.01697 p_und=0.01697
    App->>Model: state["model"].predict_proba(X)[:, 1]
    Model-->>App: raw_proba (e.g. 0.0395)
    App->>Cost: calibrate_probabilities([raw_proba], p_orig, p_und)<br/>(no-op when p_orig == p_und)
    Cost-->>App: p_rto (calibrated)
    App->>Cost: optimal_decision(p_rto, c_fp=50, c_fn=600,<br/>c_otp=5, c_block=1000, otp_eff=0.82,<br/>amount_inr=order.amount_inr)
    Cost-->>App: decision ∈ {ACCEPT, REVIEW, REJECT}

    alt decision == REVIEW
      App->>Cost: optimal_intervention(...) → otp_verify / partial_cod / address_check / hold
      Cost-->>App: intervention
      App->>Cases: open_case(prediction_id, order_id,<br/>priority, reason="model_review_gate", actor)
      Cases-->>App: case_id "CASE-<uuid>"
    end

    App->>Audit: AuditLogger.log({decision, p_rto, reasons,<br/>merchant_id, mandate_verdict, audit_id})
    Audit->>Audit: raw_hash = sha256(canonical(body) + prev_hash)
    Audit->>Audit: MerkleSealer.add(record_id, raw_hash)<br/>(seals when 1000 records or 3600s)
    Audit-->>App: audit_id

    App->>Stream: publish(STREAM_RISK_SCORES, {prediction_id,<br/>order_id, decision, score, ts})
    App->>Stream: publish(STREAM_AUDIT_RECORDS, {audit_id,<br/>prediction_id, model_version})
    alt decision == REVIEW
      App->>Stream: publish(STREAM_CASES_CREATED, {case_id,<br/>prediction_id, order_id})
    end

    App-->>NGINX: 200 OK<br/>{prediction_id, probability, risk_score,<br/>decision, intervention, explanation[],<br/>audit_id, audit_trail_url, gate_thresholds,<br/>model_version}
    NGINX-->>Console: 200 JSON
    Console-->>Merchant: render P(RTO) gauge + decision pill<br/>+ top-4 SHAP reasons + audit link
  end
```

---

## 3. Data Flow

**Type:** Data Flow Diagram (flowchart LR, cylinder data stores + processes)

Two-speed data view: hot path (Kaggle-trained champion model -> /risk/score -> audit_records + Redis Streams) and truth path (4 ingest simulators -> stream-worker -> stream-processor (HLL + 3-sigma spike-factor) -> drift-consumer (DDM+ADWIN) -> LabelFeedbackService -> retrain_real + canary_gate -> mlops.yml PR-AUC gate -> register_model -> champion swap on next lifespan).

Source: [`figures/03-data-flow.mmd`](figures/03-data-flow.mmd).

```mermaid
%% RTO Trust Layer — Data Flow Diagram (DFD L0 → L1)
%% Grounded in: src/ingest/{atm,callcenter,ecommerce,mobile}.py,
%%   scripts/ingest_kaggle.py, src/models/train.py, src/ml/registry.py,
%%   src/stream/producer.py + consumer.py + processor.py,
%%   src/feedback/label_service.py + drift_consumer.py,
%%   src/business/cost_optimizer.py, src/api/routes.py /risk/score,
%%   src/audit/logger.py, alembic 001-007.
flowchart LR
  classDef source fill:#161b22,color:#e6edf3,stroke:#58a6ff
  classDef store fill:#1c2128,color:#e6edf3,stroke:#d29922
  classDef process fill:#0d1117,color:#e6edf3,stroke:#3fb950
  classDef external fill:#21262d,color:#8b949e,stroke:#6e7681,stroke-dasharray:3 3
  classDef loop fill:#0d1117,color:#e6edf3,stroke:#f85149

  %% ===== External sources =====
  KAGGLE[("Kaggle COD dataset<br/>data/raw/cod_orders.csv<br/>~97k rows 1.64% RTO")]
  ATM["ATM switch log CSV<br/>src/ingest/atm.py<br/>(batch daily 02:00 IST)"]
  CC["CRM webhook<br/>src/ingest/callcenter.py<br/>~720 flags/day"]
  MOB["Kafka mobile.orders<br/>src/ingest/mobile.py<br/>~2/sec peak"]
  EC["Merchant web checkout<br/>src/ingest/ecommerce.py<br/>(identity normalize)"]
  RAZORPAY[("Razorpay webhook<br/>(future — outcome labels")]

  %% ===== Ingest / training stores =====
  RAW[("Raw feature frame<br/>src/models/train.py<br/>build_feature_frame + group_split")]
  MODEL[("Champion artifact<br/>models/champion/<br/>model.pkl + priors.json<br/>+ rate_lookup.json<br/>+ ohe_fitter.joblib")]
  REG[("ModelRegistry<br/>out/model_registry.json<br/>or alembic 001 model_registry<br/>(is_champion partial-unique)")]
  PRIORS[("priors blob<br/>p_orig=0.01697 p_und=0.01697<br/>calibration_method=bahnsen_eq6")]

  %% ===== Hot path stores =====
  PG[("PostgreSQL 15<br/>alembic 001-007<br/>audit_records · cases ·<br/>mandate_counters · api_keys<br/>override_nonces · idempotency_keys")]
  RD[("Redis 7 Streams<br/>5 streams:<br/>risk.scores · audit.records ·<br/>cases.created · model.drift ·<br/>notifications")]

  %% ===== Processes =====
  TRAIN["train.py<br/>fit_model(HistGB,<br/>max_iter=300 lr=0.08 depth=6)<br/>+ compute_priors + save_model"]
  REGISTER["register_model(version,<br/>model_path, metrics, priors)<br/>src/ml/registry.py"]
  SCORE["POST /risk/score<br/>src/api/routes.py:960<br/>feature_builder.transform<br/>→ predict_proba<br/>→ calibrate_probabilities<br/>→ optimal_decision"]
  AUDIT["AuditLogger.log<br/>src/audit/logger.py<br/>hash chain + Merkle interval"]
  CASES["CaseService.open_case<br/>src/cases/service.py"]

  %% ===== Stream consumers =====
  SW["stream-worker<br/>src/stream/consumer.py<br/>XREADGROUP rto-workers group"]
  SP["stream-processor<br/>src/stream/processor.py<br/>HyperLogLog + sliding-window<br/>3-sigma spike-factor (15-a)"]
  DC["drift-consumer<br/>src/feedback/drift_consumer.py<br/>DDM Level + ADWIN"]

  %% ===== Feedback loop =====
  LABELS["LabelFeedbackService<br/>src/feedback/label_service.py<br/>late-label prequential eval"]
  DRAIN["Drain (3+ same-reason<br/>-- retrain_request notification)"]
  RETRAIN["retrain_real.py<br/>scripts/retrain_real.py<br/>+ canary_gate.py"]
  PROMOTE["Promotion (MLOps)<br/>mlops.yml workflow<br/>relative PR-AUC gate 3x baseline"]
  SHAP["SHAPExplainer<br/>src/models/explain.py<br/>/v1/explain/shap"]

  %% ===== Hot path external consumer =====
  DASH["Merchant Console<br/>dashboard/index.html"]
  COMPL["Compliance Auditor<br/>/v1/audit/{id}/proof<br/>+ /v1/compliance/audit-export"]

  %% ===== Training flow =====
  KAGGLE --> RAW
  RAW --> TRAIN
  TRAIN --> MODEL
  TRAIN --> PRIORS
  MODEL --> REGISTER
  PRIORS --> REGISTER
  REGISTER --> REG

  %% ===== Hot path (inference) =====
  MODEL -.->|lifespan loads at boot| SCORE
  PRIORS -.->|"get_priors() at request time"| SCORE
  REG -.->|"current_champion() lookup"| SCORE
  EC -->|POST /risk/score| SCORE
  SCORE --> AUDIT
  SCORE --> CASES
  AUDIT --> PG
  CASES --> PG
  SCORE -.->|publish risk.scores + audit.records + cases.created| RD
  DASH -->|POST /risk/score| SCORE
  SCORE -->|200 decision + p_rto + reasons + audit_id| DASH
  SHAP -.->|/v1/explain/shap| DASH
  AUDIT --> COMPL

  %% ===== Cold path (feedback loop) =====
  ATM -->|POST /risk/score X-Channel: atm| SCORE
  CC -->|POST /risk/score X-Channel: call_center| SCORE
  MOB -->|POST /risk/score X-Channel: mobile| SCORE

  RD --> SW
  RD --> SP
  SP -->|XADD model.drift| RD
  RD --> DC
  DC --> LABELS
  LABELS --> DRAIN
  RAZORPAY -.->|delayed is_returned label T+30d| LABELS
  DRAIN -->|3+ consecutive anomalies<br/>-- retrain PR| RETRAIN
  LABELS -->|DDM 99% drift confirmed<br/>-- /v1/feedback/ingest| RETRAIN
  RETRAIN --> PROMOTE
  PROMOTE -.->|"register_model(new version, champion=True)"| REG
  REG -.->|champion swap on next /risk/score lifespan| SCORE

  %% styling
  class KAGGLE,RAZORPAY external
  class ATM,CC,MOB,EC source
  class RAW,MODEL,REG,PRIORS,PG,RD store
  class TRAIN,REGISTER,SCORE,AUDIT,CASES,SW,SP,DC,LABELS,DRAIN,RETRAIN,PROMOTE,SHAP process
  class DASH,COMPL source
```

---

## 4. ER Schema

**Type:** Entity-Relationship (erDiagram, 10 entities + attributes + 4 relationships)

10-table PostgreSQL schema: audit_records <-> audit_merkle_intervals (tamper-evidence layer), cases (case lifecycle), model_registry (champion/challenger partial-unique), idempotency_keys (TTL), psi_reference (drift baseline), mandate_counters + mandate_counter_events (UPI Circle cumulative state + 24h window), override_nonces (replay defense, alembic 006), api_keys (multi-tenant scope binding, alembic 007).

Source: [`figures/04-er-schema.mmd`](figures/04-er-schema.mmd).

```mermaid
%% RTO Trust Layer — ER Diagram (10 tables, alembic migrations 001-007)
%% Grounded in: alembic/versions/001_initial.py (audit_records, cases,
%%   model_registry, idempotency_keys, psi_reference),
%%   002_merkle_intervals.py (audit_merkle_intervals + audit_records
%%   interval_id/interval_position columns),
%%   003_mandate_counters.py (mandate_counters + mandate_counter_events),
%%   004_mandate_counter_concurrency.py (mandate_counters.month_key column),
%%   005_gin_audit_body.py (indexes only — audit_records),
%%   006_override_nonces.py (override_nonces),
%%   007_api_key_merchant_binding.py (api_keys).
erDiagram
  audit_records {
    BIGINT    id PK
    TEXT      audit_id UK
    JSONB     body
    TEXT      raw_hash
    TEXT      prev_hash
    TIMESTAMPTZ created_at
    TEXT      model_version
    TEXT      mandate_type
    TEXT      bh_purpose_code
    TEXT      device_id
    TEXT      user_id
    INT       interval_id FK
    INT       interval_position
  }

  audit_merkle_intervals {
    SERIAL   interval_id PK
    BIGINT   start_record_id
    BIGINT   end_record_id
    TEXT     merkle_root
    TEXT     prev_interval_root
    INT      leaf_count
    TIMESTAMPTZ sealed_at
  }

  cases {
    TEXT      case_id PK
    TEXT      prediction_id
    TEXT      order_id
    TEXT      merchant_id
    TEXT      status
    TEXT      priority
    TEXT      assigned_to
    TEXT      reason
    TIMESTAMPTZ created_at
    TIMESTAMPTZ updated_at
    TIMESTAMPTZ resolved_at
    TEXT      resolution_notes
    TEXT      resolution_by
    TEXT      resolution_decision
  }

  model_registry {
    TEXT       version PK
    TEXT       model_path
    JSONB      metrics
    BOOLEAN    is_champion
    BOOLEAN    is_challenger
    DOUBLE     traffic_split
    TEXT       drift_status
    TIMESTAMPTZ deployed_at
    TIMESTAMPTZ promoted_at
  }

  idempotency_keys {
    TEXT        key PK
    TEXT        request_body
    TEXT        response_body
    INTEGER     status_code
    TIMESTAMPTZ created_at
    TIMESTAMPTZ expires_at
  }

  psi_reference {
    SERIAL      id PK
    TEXT        feature_name
    JSONB       expected_distribution
    INTEGER     n_bins
    TEXT        model_version
    TIMESTAMPTZ created_at
  }

  mandate_counters {
    TEXT       mandate_sub PK
    NUMERIC_14_2 cumulative_monthly
    BIGINT     last_activity_ts
    VARCHAR_7  month_key
    TIMESTAMPTZ updated_at
  }

  mandate_counter_events {
    BIGSERIAL  id PK
    TEXT       mandate_sub
    BIGINT     ts
    NUMERIC_14_2 amount_inr
    TIMESTAMPTZ created_at
  }

  override_nonces {
    TEXT        nonce_hash PK
    TIMESTAMPTZ created_at
  }

  api_keys {
    TEXT        key_id PK
    TEXT        key_hash UK
    TEXT        scope
    TEXT        merchant_id
    TIMESTAMPTZ created_at
    BOOLEAN     revoked
  }

  %% Tamper-evidence layer: per-record hash chain → Merkle interval sealing.
  audit_records ||--o{ audit_merkle_intervals : "interval_id (sealed every 1000 recs / 3600s)"

  %% UPI Circle cumulative state split: monotone current state + windowed events.
  mandate_counters ||--o{ mandate_counter_events : "mandate_sub (24h cooling window)"

  %% Logical relationships (no FK in schema but enforced in application logic).
  cases }o--|| audit_records : "prediction_id (logical link)"
  api_keys }o--o| cases : "merchant_id (enforce_merchant_isolation)"
  api_keys }o--o| mandate_counters : "merchant_id (multi-tenant filter)"
  model_registry ||--o| psi_reference : "model_version (drift reference baseline)"
```

---

## 5. Agent Override State

**Type:** State Machine (stateDiagram-v2, 9 success states + 8 failure terminals + notes)

State machine for POST /risk/{prediction_id}/override: IDLE -> PROPOSED -> AWAITING_COSIGN -> SCOPE_CHECKED (SCOPE_ACTION_MAP) -> REPLAY_CHECKED (nonce ON CONFLICT, alembic 006) -> HKDF_DERIVED (RFC 5869, salt=rto-override-v1) -> HMAC_CHAIN (+/-30s skew) -> APPLIED -> MANDATE_CHECKED (UPI Circle Rs.5k/txn Rs.15k/mo, FOR UPDATE) -> AUDITED (hash chain + Merkle interval) -> CLOSED. Failure terminals: 403 scope / 403 admin1 / 400 same-key / 409 replay / 403 HMAC / 422 mandate_breach / cooling REVIEW.

Source: [`figures/05-agent-override-state.mmd`](figures/05-agent-override-state.mmd).

```mermaid
%% RTO Trust Layer — State Diagram: Bounded Agent Dual-Control Override
%% Grounded in: src/api/routes.py:2356 (POST /risk/{prediction_id}/override),
%%   src/api/agent_allowlist.py (SCOPE_ACTION_MAP + OVERRIDE_ACTION +
%%   check_agent_action), src/api/mandates.py (verify_mandate + MandateVerdict
%%   REVIEW/BREACH for 24h cooling cap exceeded),
%%   src/api/keys.py (derive_hmac_key via HKDF salt=rto-override-v1
%%   info=dual-control), alembic/006_override_nonces.py
%%   (override_nonces PK=nonce_hash, INSERT ON CONFLICT DO NOTHING → 409),
%%   alembic/003_mandate_counters.py (mandate_counters cumulative_monthly cap).
stateDiagram-v2
  direction LR
  [*] --> IDLE

  IDLE --> PROPOSED : agent reads decision<br/>+ decides to suggest override

  PROPOSED --> AWAITING_COSIGN : client builds body<br/>{prediction_id, decision, notes}<br/>+ generates fresh nonce<br/>(uuid4().hex 16-byte)
  PROPOSED --> REJECTED_SCOPE : X-Agent-Action=override<br/>not in scope_action_map[scope]
  note right of REJECTED_SCOPE : 403 Forbidden<br/>scope 'scorer' cannot perform action 'override'
  PROPOSED --> REJECTED_APPROVAL : override requires admin scope<br/>(scorer/ops cannot self-approve)
  note right of REJECTED_APPROVAL : 403 Forbidden<br/>(only admin scope may invoke override)

  AWAITING_COSIGN --> SCOPE_CHECKED : admin_signature_1<br/>is a valid admin key<br/>(check_key admin scope passes)
  AWAITING_COSIGN --> REJECTED_ADMIN1 : admin_signature_1<br/>invalid or non-admin
  note right of REJECTED_ADMIN1 : 403 dual-control requires<br/>2 valid admin API keys

  AWAITING_COSIGN --> REJECTED_SAMEKEY : admin_signature_1<br/>== admin_signature_2
  note right of REJECTED_SAMEKEY : 400 cannot self-approve<br/>(V3 §12.1)

  SCOPE_CHECKED --> REPLAY_CHECKED : INSERT INTO override_nonces<br/>(nonce_hash) ON CONFLICT DO NOTHING<br/>rowcount == 1 (first sighting)
  SCOPE_CHECKED --> REPLAY_409 : rowcount == 0<br/>(nonce already seen)
  note right of REPLAY_409 : 409 Conflict — replay detected<br/>(alembic 006, 24h prune window)

  REPLAY_CHECKED --> HKDF_DERIVED : derive_hmac_key(<br/>  raw_key=admin2 candidate,<br/>  salt=b"rto-override-v1",<br/>  info=b"dual-control",<br/>  length=32<br/>)
  note right of HKDF_DERIVED : RFC 5869<br/>(cached in src/api/keys.py)

  HKDF_DERIVED --> HMAC_CHAIN : expected_sig2 = HMAC(<br/>  derived_admin2_key,<br/>  admin_signature_1|canonical_body|ts,<br/>  sha256)
  HMAC_CHAIN --> APPLIED : hmac.compare_digest(<br/>sig2, expected_sig2) == True<br/>(tries ±30s clock skew)
  HMAC_CHAIN --> REJECTED_HMAC : no admin2 candidate matches
  note right of REJECTED_HMAC : 403 HMAC chain verification failed

  APPLIED --> MANDATE_CHECKED : verify_mandate(X-Mandate,<br/>amount_inr, device_id, user_id)
  MANDATE_CHECKED --> AUDITED : MandateVerdict.VALID<br/>(UPI Circle cap not breached)
  MANDATE_CHECKED --> COOLING_REVIEW : MandateVerdict.REVIEW<br/>24h cooling active (₹5k)
  note right of COOLING_REVIEW : case opens in REVIEW queue<br/>(requires human approval)
  MANDATE_CHECKED --> MANDATE_403 : MandateVerdict.BREACH<br/>monthly ₹15k / per-txn ₹5k<br/>exceeded OR device_id_not_allowed
  note right of MANDATE_403 : 422 mandate_breach<br/>(alembic 003 + 004<br/>mandate_counters.month_key<br/>FOR UPDATE row lock)

  AUDITED --> CLOSED : AuditLogger.log(<br/>{override, admin1_digest, admin2_digest,<br/>admin2_key_found, prediction_id,<br/>new_decision, nonce_hash})<br/>→ raw_hash chain append<br/>→ MerkleSealer.add
  note right of CLOSED : case updated to new decision<br/>+ audit_trail_url returned to dashboard

  CLOSED --> [*]

  %% failure terminals
  REJECTED_SCOPE --> [*]
  REJECTED_APPROVAL --> [*]
  REJECTED_ADMIN1 --> [*]
  REJECTED_SAMEKEY --> [*]
  REPLAY_409 --> [*]
  REJECTED_HMAC --> [*]
  MANDATE_403 --> [*]
  COOLING_REVIEW --> [*]
```

---

## 6. Merchant User Journey

**Type:** User-Journey Flowchart (flowchart TD, decision diamonds + 3 terminal classes)

Merchant's UI journey in dashboard/index.html: enter keys + order form -> POST /risk/score -> render P(RTO) gauge + decision pill + SHAP top-4 reasons + audit link. REVIEW branches to otp_verify / partial_cod / address_check / hold -> CaseService.open_case. Optionally tune rules via POST /v1/rules or trigger agent override -> dual-control co-sign (admin1 raw key + admin2 HKDF-derived HMAC) -> MandateCounter increments -> CaseService.resolve -> audit trail visible via /audit/{audit_id} + /v1/audit/{audit_id}/proof (Merkle inclusion proof).

Source: [`figures/06-merchant-user-journey.mmd`](figures/06-merchant-user-journey.mmd).

```mermaid
%% RTO Trust Layer — Merchant User Journey / Flowchart
%% Grounded in: dashboard/index.html (form fields, /risk/score call,
%%   /v1/policy/cost-curves fetch, /audit/{id} lookup),
%%   src/api/routes.py (POST /risk/score, POST /risk/{id}/override,
%%   GET /v1/explain/shap, GET /audit/{audit_id}, POST /v1/cases/{id}/resolve,
%%   POST /v1/rules, DELETE /v1/rules/{id}),
%%   src/rules/engine.py (RulesEngine.add/remove),
%%   src/business/cost_optimizer.py (optimal_decision → ACCEPT/REVIEW/REJECT),
%%   src/cases/service.py (open_case, resolve),
%%   src/models/explain.py (explain_with_shap, reason_codes_batch).
flowchart TD
  START([Merchant opens dashboard/index.html]) --> KEYS

  KEYS["Enter Scorer API key + Admin API key<br/>(two text inputs at top)"] --> FILL_FORM

  FILL_FORM["Fill order form:<br/>order_id · amount_inr · category ·<br/>payment_method · address_quality<br/>· city_tier · prior_orders · prior_returns"] --> CLICK

  CLICK["Click 'Score order' button"] --> CALL_SCORE

  CALL_SCORE["Browser POST /risk/score<br/>headers: Authorization + Idempotency-Key<br/>body: OrderIn (Pydantic)"] --> SCORE_RESPONSE{HTTP response?}

  SCORE_RESPONSE -- 401 --> SHOW_ERR["Show error:<br/>'HTTP 401: invalid key'"]
  SHOW_ERR --> KEYS

  SCORE_RESPONSE -- 429 --> RATE_ERR["Show rate-limit message"]
  RATE_ERR --> WAIT[("Wait + retry")]
  WAIT --> CLICK

  SCORE_RESPONSE -- 200 --> RENDER

  RENDER["Render result panel:<br/>P(RTO) gauge + decision pill<br/>(ACCEPT green / REVIEW amber / REJECT red)<br/>+ top-4 SHAP reasons (delta_prob arrows)<br/>+ audit_trail_url link"] --> DECISION{decision?}

  DECISION -- ACCEPT --> SHIP["Action: ship normal<br/>(cost = c_ship_fp only)"]
  SHIP --> DONE([Order placed in audit trail])

  DECISION -- REJECT --> BLOCK["Action: manual review<br/>(cost = c_block ₹1000)"]
  BLOCK --> DONE

  DECISION -- REVIEW --> INTERVENTION{Intervention shown}
  INTERVENTION --> |otp_verify| OTP["Send selective OTP<br/>(c_otp=₹5, eff=0.82)"]
  INTERVENTION --> |partial_cod| P_COD["Collect partial amount upfront"]
  INTERVENTION --> |address_check| ADDR["Call-center address verification"]
  INTERVENTION --> |hold| HOLD["Hold for manual review queue<br/>(opens case in cases table)"]

  OTP --> CASE_OPEN
  P_COD --> CASE_OPEN
  ADDR --> CASE_OPEN
  HOLD --> CASE_OPEN

  CASE_OPEN["CaseService.open_case()<br/>case_id = CASE-<uuid10><br/>status=OPENED reason=model_review_gate"] --> RULE_TUNE{Merchant wants to tune rules?}

  RULE_TUNE -- Yes --> ADJUST_RULES["Adjust rule thresholds<br/>POST /v1/rules<br/>DELETE /v1/rules/{rule_id}"]
  ADJUST_RULES --> CLICK

  RULE_TUNE -- No --> AGENT_OVERRIDE{Agent suggested<br/>dual-control override?}

  AGENT_OVERRIDE -- No --> RESOLVE_CASE
  AGENT_OVERRIDE -- Yes --> COSIGN["Ops reviewer co-signs<br/>client generates nonce +<br/>admin_signature_1 (raw key)<br/>admin_signature_2 (HMAC chain)"]

  COSIGN --> OVERRIDE["POST /risk/{prediction_id}/override<br/>(server-side checks:<br/>scope allowlist + nonce uniqueness<br/>+ HKDF-derived HMAC chain +<br/>mandate counter FOR UPDATE lock)"]

  OVERRIDE --> OVERRIDE_RESULT{Override outcome?}
  OVERRIDE_RESULT -- 200 --> MANDATE_INC["MandateCounter increments<br/>(monthly ₹15k cap updated<br/>+ 24h event row INSERTed)"]
  MANDATE_INC --> RESOLVE_CASE
  OVERRIDE_RESULT -- 403 scope --> SHOW_ERR
  OVERRIDE_RESULT -- 409 replay --> SHOW_ERR
  OVERRIDE_RESULT -- 422 mandate_breach --> SHOW_ERR

  RESOLVE_CASE["Merchant approves intervention in dashboard<br/>POST /v1/cases/{case_id}/resolve<br/>body: {decision: APPROVED, notes}"] --> CASE_CLOSE
  CASE_CLOSE["CaseService.resolve()<br/>cases.status → APPROVED<br/>resolved_at = NOW()"] --> DONE

  DONE --> AUDIT_LOOKUP{Merchant clicks audit link?}
  AUDIT_LOOKUP -- Yes --> FETCH_AUDIT["GET /audit/{audit_id}<br/>(admin key) → raw audit body<br/>(hash chain verified)"]
  FETCH_AUDIT --> VERIFY["Optionally GET /v1/audit/{audit_id}/proof<br/>→ Merkle inclusion proof<br/>(leaf + path to interval root)"]
  VERIFY --> DONE
  AUDIT_LOOKUP -- No --> END([End of journey])

  classDef start fill:#0d1117,color:#e6edf3,stroke:#3fb950,stroke-width:2px
  classDef err fill:#47201f,color:#e6edf3,stroke:#f85149
  classDef ok fill:#123b23,color:#e6edf3,stroke:#3fb950
  classDef review fill:#3d2e00,color:#e6edf3,stroke:#d29922
  classDef neutral fill:#161b22,color:#e6edf3,stroke:#30363d
  class START,DONE,END start
  class SHOW_ERR,RATE_ERR err
  class SHIP ok
  class BLOCK,CASE_OPEN,CASE_CLOSE review
  class KEYS,FILL_FORM,CLICK,CALL_SCORE,RENDER,OTP,P_COD,ADDR,HOLD,RESOLVE_CASE,COSIGN,OVERRIDE,MANDATE_INC,ADJUST_RULES,FETCH_AUDIT,VERIFY,WAIT neutral
  class INTERVENTION,SCORE_RESPONSE,DECISION,RULE_TUNE,AGENT_OVERRIDE,OVERRIDE_RESULT,AUDIT_LOOKUP neutral
```

---

## 7. Deployment Topology

**Type:** Deployment (flowchart TB, 5 subgraphs: GitHub / Runner / Registry / Host / External)

GitHub (Neeraj-Parekh/special-parakeet, PRIVATE) -> 5 GitHub Actions runners (ci.yml, mlops.yml 7-stage TFX pipeline, train.yml nightly Olist, docker.yml v* tag -> GHCR, screenshot.yml -> Pages) -> GHCR container registry + GitHub Pages. Docker host runs api container (:8000) + postgres:15 + redis:7 + stream-worker + stream-processor + drift-consumer + (profile=full) nginx + prometheus + grafana + jaeger:1.55 + alertmanager:0.27. External: Kaggle COD dataset, Olist dataset, Razorpay webhook (future), pitch deck embedding Pages screenshot URLs.

Source: [`figures/07-deployment-topology.mmd`](figures/07-deployment-topology.mmd).

```mermaid
%% RTO Trust Layer — Deployment Topology
%% Grounded in: git remote (Neeraj-Parekh/special-parakeet — repo is PRIVATE),
%%   .github/workflows/ci.yml (lint-test + docker-build + load-test jobs),
%%   .github/workflows/mlops.yml (7-stage TFX-style: data-analysis →
%%   data-validation → model-training → model-gate (canary) → container-build
%%   → deploy-staging → monitor-rollback),
%%   .github/workflows/train.yml (Nightly Retrain Olist cron 0 2 * * *),
%%   .github/workflows/docker.yml (Docker Release — tag v* → GHCR multi-arch
%%   amd64+arm64),
%%   .github/workflows/screenshot.yml (Screenshots → GitHub Pages deploy),
%%   docker-compose.yml (api + postgres + redis + stream-worker +
%%   stream-processor + drift-consumer + nginx + prometheus + grafana +
%%   jaeger + alertmanager),
%%   Dockerfile (python:3.12-slim → uvicorn on :8000),
%%   requirements.txt + pyproject.toml (FastAPI + psycopg + scikit-learn +
%%   shap + opentelemetry-sdk).
flowchart TB
  classDef github fill:#0d1117,color:#e6edf3,stroke:#58a6ff,stroke-width:2px
  classDef runner fill:#161b22,color:#e6edf3,stroke:#3fb950
  classDef registry fill:#1c2128,color:#e6edf3,stroke:#d29922
  classDef host fill:#0d1117,color:#e6edf3,stroke:#58a6ff
  classDef external fill:#21262d,color:#8b949e,stroke:#6e7681,stroke-dasharray:3 3
  classDef pages fill:#1c2128,color:#e6edf3,stroke:#58a6ff

  %% ===== Source =====
  subgraph GH["GitHub — github.com/Neeraj-Parekh/special-parakeet (PRIVATE)"]
    REPO[("Git repo<br/>main branch<br/>commits 1ab7f62 → 368ec19 →<br/>30d20d6 → 1f8b870")]
    CODE["src/ + tests/ + alembic/<br/>+ models/champion/ +<br/>dashboard/index.html +<br/>monitoring/ + .github/workflows/"]
  end

  %% ===== Runners =====
  subgraph RUN["GitHub Actions Runners (ubuntu-latest)"]
    CI["ci.yml<br/>(every push/PR to main)<br/>job: lint-test<br/>job: docker-build<br/>job: load-test (k6)"]
    MLOPS["mlops.yml<br/>(data/model/src change + weekly cron)<br/>7 stages:<br/>1 data-analysis<br/>2 data-validation<br/>3 model-training (HistGB + priors)<br/>4 model-gate (canary + slice)<br/>5 container-build<br/>6 deploy-staging<br/>7 monitor-rollback"]
    TRAIN["train.yml<br/>(cron: 0 2 * * *)<br/>Nightly Retrain Olist<br/>PR-AUC ≥ 0.35 gate<br/>→ git-auto-commit to main"]
    DOCKER["docker.yml<br/>(tag: v*)<br/>Docker Release<br/>multi-arch amd64+arm64<br/>tags: latest + semver + sha-<sha7>"]
    SCREEN["screenshot.yml<br/>(push: main)<br/>4 screenshots at 1280×800:<br/>/docs, /health, /, /risk/score"]
  end

  %% ===== Registry =====
  subgraph REG["Container Registry + Static Hosting"]
    GHCR[("GHCR<br/>ghcr.io/neeraj-parekh/special-parakeet<br/>:latest + :vX.Y.Z + :sha-<sha7><br/>(amd64 + arm64 manifests)")]
    PAGES["GitHub Pages<br/>https://neeraj-parekh.github.io/special-parakeet/<br/>openapi-docs.png · health.png ·<br/>dashboard.png · score-endpoint.png"]
  end

  %% ===== Docker host =====
  subgraph HOST["Docker Host (single VM or laptop)"]
    direction TB
    NGINX["nginx:alpine<br/>:80 (profile=full)<br/>TLS + idempotency + rate-limit"]
    API["api container (python:3.12-slim)<br/>uvicorn src.api.routes:create_app<br/>:8000"]
    PG["postgres:15-alpine<br/>riskdb (alembic 001-007)"]
    RD["redis:7-alpine<br/>5 streams"]
    SW["stream-worker<br/>python -m src.stream.consumer<br/>group=rto-workers"]
    SP["stream-processor<br/>python -m src.stream.processor<br/>group=rto-processors"]
    DC["drift-consumer<br/>python -m src.feedback.drift_consumer<br/>group=rto-drift-detectors"]
    PROM["prom/prometheus<br/>:9090 (profile=full)"]
    GRAF["grafana/grafana<br/>:3001 (profile=full)"]
    JAEGER["jaegertracing/all-in-one:1.55<br/>UI :16686 + OTLP gRPC :4317<br/>(profile=full)"]
    AM["prom/alertmanager:v0.27.0<br/>:9093 (profile=full)"]
    VOL[("audit-data volume<br/>/app/out (hash chain<br/>+ cases.jsonl)")]
    PGDATA[("postgres-data volume<br/>(audit_records + cases<br/>+ model_registry +<br/>mandate_counters)")]
  end

  %% ===== External =====
  subgraph EXT["External / Future"]
    KAGGLE[("Kaggle COD dataset<br/>~97k orders 1.64% RTO<br/>reports/kaggle/")]
    OLIST[("Olist Brazilian<br/>e-commerce dataset")]
    RAZORPAY["Razorpay webhook<br/>(future — outcome<br/>labels close feedback loop)"]
    PITCH["Pitch deck (PDF)<br/>embeds Pages screenshot URLs"]
  end

  REPO -->|triggers on push/PR| CI
  REPO -->|triggers on data/model/src + weekly| MLOPS
  REPO -->|triggers nightly 02:00 UTC| TRAIN
  REPO -->|triggers on v* tag| DOCKER
  REPO -->|triggers on push main| SCREEN

  CI -->|docker/build-push-action push=false<br/>Trivy scan| DOCKER
  MLOPS -->|"stage 5 container-build<br/>push=true sha-7chars"| GHCR
  DOCKER -->|"multi-arch push<br/>latest + semver + sha-7chars"| GHCR

  MLOPS -->|"stage 6 deploy hook<br/>kubectl set image<br/>(documented hook)"| API
  GHCR -->|image pull| API
  SCREEN -->|actions/deploy-pages| PAGES

  %% internal docker compose wiring
  NGINX --> API
  API --> PG
  API --> RD
  API --> SW
  API -.->|publish risk.scores + audit.records + cases.created| RD
  RD --> SW
  RD --> SP
  SP -->|XADD model.drift| RD
  RD --> DC
  API --> PROM
  PROM --> GRAF
  PROM --> AM
  API -->|OTLP gRPC :4317| JAEGER
  API --> VOL
  PG --> PGDATA

  %% external inputs
  KAGGLE -.->|scripts/ingest_kaggle.py<br/>-- train.py| REPO
  OLIST -.->|train.yml nightly| REPO
  RAZORPAY -.->|delayed is_returned label<br/>POST /v1/feedback/ingest| API
  PAGES -.->|image URL embeds| PITCH

  %% human consumers
  PITCH -->|"judge + integrator reads"| EXT

  class REPO,CODE github
  class CI,MLOPS,TRAIN,DOCKER,SCREEN runner
  class GHCR,PAGES registry
  class NGINX,API,PG,RD,SW,SP,DC,PROM,GRAF,JAEGER,AM,VOL,PGDATA host
  class KAGGLE,OLIST,RAZORPAY,PITCH external
```

---

## 8. Class Diagram (bonus)

**Type:** Class Diagram (classDiagram, 14 classes + composition/dependency edges)

Static structure of the key Python modules: Settings (pydantic-settings, dual-mode is_postgres switch), AuditLogger + MerkleSealer (composition), CaseService, ModelRegistry (module-level functions), MandateVerifier + MandateVerdict + _FileState (15-b cross-process state), RulesEngine + Rule, CostOptimizer (Bahnsen Eq.5/6), AgentAllowlist + SCOPE_ACTION_MAP + OVERRIDE_ACTION, KeyManager (HKDF derive + cache), TokenBucket, StreamProducer (fire-and-forget XADD), KaggleFeatureBuilder (from_champion_dir + transform), and the FastAPI state dict that wires them together in routes.py lifespan.

Source: [`figures/08-class-diagram.mmd`](figures/08-class-diagram.mmd).

```mermaid
%% RTO Trust Layer — Class Diagram (key Python modules + state object)
%% Grounded in: src/config/__init__.py (Settings), src/audit/logger.py
%%   (AuditLogger + MerkleSealer), src/cases/service.py (CaseService),
%%   src/ml/registry.py (register_model/current_champion/get_priors/psi),
%%   src/api/mandates.py (MandateVerdict + verify_mandate + issue_mandate +
%%   _FileState), src/rules/engine.py (Rule + RulesEngine),
%%   src/business/cost_optimizer.py (optimal_decision + optimal_intervention
%%   + calibrate_probabilities + cost_curve_sweep),
%%   src/api/agent_allowlist.py (SCOPE_ACTION_MAP + check_agent_action),
%%   src/api/keys.py (derive_hmac_key + _hkdf_extract + _hkdf_expand),
%%   src/api/security.py (TokenBucket + check_key + default_keys),
%%   src/api/routes.py (state dict assembled by lifespan),
%%   src/stream/producer.py (StreamProducer),
%%   src/models/feature_builder.py (KaggleFeatureBuilder).
classDiagram
  direction LR

  class Settings {
    +database_url
    +redis_url
    +rto_scorer_keys
    +rto_admin_keys
    +rto_mandate_secret
    +rto_audit_salt
    +audit_path
    +cases_path
    +model_registry_path
    +idem_maxsize
    +idem_ttl_seconds
    +is_postgres
    +get_settings Settings
  }

  class AuditLogger {
    +path
    +model_version
    +conn
    +sealer
    +log payload str
    +read audit_id dict
    +verify_chain tuple
    +merkle_proof record_id dict
    +merkle_intervals limit list
    +usage_counts since_hours dict
    +redact_customer id str
    +canonical payload str
  }

  class MerkleSealer {
    +conn
    +interval_size
    +interval_seconds
    +_pending list
    +_interval_started_at
    +add record_id raw_hash dict
    +seal dict
    +merkle_proof record_id dict
  }

  class CaseService {
    +path
    +settings
    +_lock
    +_conn
    +store
    +open_case prediction_id order_id priority reason actor str
    +resolve case_id decision notes actor dict
    +list_cases status list
  }

  class ModelRegistry {
    <<module>>
    +register_model version model_path metrics champion priors dict
    +current_champion dict
    +get_priors dict
    +set_priors version priors
    +psi expected actual float
    +load_registry path dict
    +_close_conn
  }

  class MandateVerifier {
    <<module>>
    +verify_mandate token amount device_id user_id tuple
    +issue_mandate scope sub amount ttl mandate_type str
    +reset_upi_counters
    +_mandate_state _FileState
    +MandateVerdict verdicts
  }

  class MandateVerdict {
    +VALID
    +TAMPERED
    +EXPIRED
    +BREACH
    +REVIEW
  }

  class RulesEngine {
    +_rules list
    +_lock
    +evaluate order Rule
    +add rule
    +remove rule_id bool
    +list_active list
  }

  class Rule {
    +rule_id
    +name
    +field
    +op
    +value
    +action
    +priority
    +active
    +created_by
  }

  class CostOptimizer {
    <<module>>
    +optimal_decision p c_fp c_fn c_otp c_block otp_eff amount tuple
    +optimal_intervention p weights amount tuple
    +calibrate_probabilities probas p_orig p_und list
    +cost_curve_sweep y_true y_pred weights list
    +bootstrap_cost_ci y_true y_pred weights n conf dict
    +find_cost_crossover curves dict
    +find_intervention_crossover interventions dict
  }

  class AgentAllowlist {
    <<module>>
    +ALLOWED_ACTIONS dict
    +SCOPE_ACTION_MAP dict
    +OVERRIDE_ACTION str
    +check_agent_action action mandate_scope key_scope tuple
    +get_key_scope key scorer_keys admin_keys str
    +get_key_merchant_id key str
  }

  class KeyManager {
    <<module>>
    +derive_hmac_key raw_key salt info length bytes
    +_hkdf_extract salt ikm hash_algo bytes
    +_hkdf_expand prk info length hash_algo bytes
    +clear_derived_key_cache
    -_derived_cache dict
  }

  class TokenBucket {
    +rate float
    +capacity float
    +buckets dict
    +updated dict
    +lock
    +allow client bool
  }

  class StreamProducer {
    +redis_url
    +client
    +_connect_attempted
    +publish stream fields str
    +close
    -_ensure_client
  }

  class KaggleFeatureBuilder {
    +model_bundle
    +ohe_fitter
    +rate_lookup dict
    +feature_names list
    +from_champion_dir dir KaggleFeatureBuilder
    +transform raw_order ndarray
    -_build_base_features order dict
    -_bin_amount amt int
    -_lookup_rate table key float
  }

  class FastAPIApp {
    <<state>>
    +model
    +feature_builder
    +audit
    +cases
    +rules
    +stream
    +tracer
    +keys dict
    +bucket
    +idem
    +settings
  }

  %% composition
  AuditLogger *-- MerkleSealer : sealed per 1000 records or 3600s
  FastAPIApp *-- AuditLogger : state.audit
  FastAPIApp *-- CaseService : state.cases
  FastAPIApp *-- RulesEngine : state.rules
  FastAPIApp *-- StreamProducer : state.stream
  FastAPIApp *-- TokenBucket : state.bucket
  FastAPIApp *-- KaggleFeatureBuilder : state.feature_builder

  %% dependency / consumption
  CaseService ..> AuditLogger : uses as JSONL store in file mode
  RulesEngine *-- Rule : default RULE-001 + RULE-002
  MandateVerifier *-- MandateVerdict : returns verdict constants
  MandateVerifier *-- _FileState : cross-process state 15-b
  AgentAllowlist *-- SCOPE_ACTION_MAP : frozenset per scope
  KeyManager ..> MandateVerifier : derived_admin2_key feeds HMAC chain
  CostOptimizer ..> ModelRegistry : calibrate_probabilities priors
  CostOptimizer ..> KaggleFeatureBuilder : predict_proba on transform
  FastAPIApp ..> Settings : get_settings in lifespan
  FastAPIApp ..> CostOptimizer : optimal_decision + optimal_intervention
  FastAPIApp ..> AgentAllowlist : enforce_agent_action Depends
  FastAPIApp ..> MandateVerifier : verify_mandate
  FastAPIApp ..> KeyManager : derive_hmac_key on override
  FastAPIApp ..> ModelRegistry : current_champion + get_priors
```

---
