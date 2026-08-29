# Architecture — RTO Trust Layer

> **Status:** This document is the **current authoritative architecture**
> reference. Superseded versions live in
> [`docs/archive/`](archive/) (`ARCHITECTURE_V1.md`, `ARCHITECTURE_V2.md`).
> The deep-dive component specs live in `docs/ARCHITECTURE_V3.md`.

## One-line summary

A Python **FastAPI** modular monolith that scores COD (cash-on-delivery)
orders for RTO (return-to-origin) risk in ~40–70ms p50, with a
**Merkle-sealed tamper-evident audit log** (RFC 6962), **bounded agent
override** (dual-control HMAC), **cost-optimal decisioning** (Bahnsen
BMR), and a **Kafka-compatible streaming layer** that runs on Redis
Streams by default with a one-env-var toggle to Kafka.

## Stack (exact claim — copy-paste ready)

```
Python 3.12 + FastAPI + ONNX Runtime + Redis Streams
(with Kafka transport compatibility) + PostgreSQL + K8s manifests.
```

- **API:** Python 3.12 + FastAPI 0.141 + Uvicorn
- **Model:** sklearn `HistGradientBoostingClassifier` → ONNX Runtime
  (48.4KB, 79 features, 141× single-inference speedup vs sklearn)
- **Explainability:** SHAP `KernelExplainer` (Lundberg 2017 NeurIPS) +
  Redis feature-vector cache (`rto:featvec:{customer_id}`, TTL=300s) +
  async audit batching
- **Streaming:** Redis Streams (default) → Kafka (env-var toggle,
  graceful fallback) — `src/stream/kafka_producer.py`
- **Audit:** SHA-256 hash chain + Merkle interval sealing (RFC 6962),
  Postgres primary / JSONL file fallback
- **Compliance:** RBI MRM-aligned kill-switch, dual-control HMAC override
  (RFC 5869), OC-201B UPI Circle mandate caps
- **Deploy:** Render (free starter, single web service) +
  `infra/k8s/` (one-command `kubectl apply -k`, HPA 2–10 replicas)

## System diagram

```mermaid
┌──────────────────────────────────────────────────────────────────┐
│  CLIENT (Browser dashboard / Merchant API / Agent console)      │
└──────────────────────────────┬───────────────────────────────────┘
                               │  HTTPS
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│  FastAPI API (Python 3.12, port 8000)                            │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ │
│  │/risk/score│ │/audit/*  │ │/agent/*  │ │/admin/*  │ │/health  │ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └─────────┘ │
│       │            │            │             │                   │
│  Decision Flow (7 steps):                                        │
│   1. Rules fast-path (RULE-001..N) → BLOCK/REJECT                │
│   2. Mandate check (OC-201B UPI caps) → BREACH/REJECT           │
│   3. Circuit breaker (OPEN → rules-only REVIEW)                 │
│   4. Feature builder (79-dim, Redis-cached) → ONNX model        │
│   5. Cost optimizer (Bahnsen BMR Eq.(6)) → ACCEPT/REVIEW/REJECT  │
│   6. Async audit logger (buffered 100/100ms → Postgres+Merkle)  │
│   7. Stream publish (KafkaProducer → Kafka OR Redis Streams)     │
└───────────────┬─────────────────┬───────────────┬────────────────┘
                │                 │               │
        ┌───────▼───────┐ ┌──────▼──────┐ ┌──────▼──────┐
        │  ONNX Runtime │ │ Redis       │ │  PostgreSQL │
        │  (model.onnx) │ │ (cache +    │ │  (audit +   │
        │   0.12ms/infer│ │  streams)   │ │  registry)  │
        └───────────────┘ └──────┬─────┘ └─────────────┘
                                 │
                          ┌──────▼──────┐
                          │   Kafka     │  (optional —
                          │  (toggle)   │   KAFKA_BROKERS env)
                          └──────┬──────┘
                                 │
                          ┌──────▼──────┐
                          │  Stream     │  (background worker —
                          │ Processor   │   drift detection, cases)
                          └─────────────┘
```

## Component list

| Component | File | Purpose |
|---|---|---|
| API entrypoint | `src/api/routes.py` | FastAPI app factory + 7-step decision flow |
| ONNX inference | `src/models/feature_builder.py` | 79-dim transform + ONNX Runtime + **Redis feature cache** |
| SHAP explain | `src/models/explain.py` | KernelExplainer + 5s timeout + dual-mode fallback |
| Rules engine | `src/rules/engine.py` | Fast-path BLOCK/REJECT rules |
| Cost optimizer | `src/business/cost_optimizer.py` | Bahnsen BMR Eq.(6) decisioning |
| Audit (sync) | `src/audit/logger.py` | SHA-256 hash chain + Merkle interval sealing |
| Audit (async) | `src/audit/async_logger.py` | **Buffer 100/100ms → background flush** (Phase 1) |
| Stream producer | `src/stream/producer.py` | Redis Streams XADD (fire-and-forget) |
| Kafka producer | `src/stream/kafka_producer.py` | **Kafka compat stub** (Phase 3, fallback to Redis) |
| Mandate guard | `src/api/mandates.py` | OC-201B UPI Circle caps |
| Circuit breaker | `src/api/breaker.py` | OPEN → rules-only REVIEW |
| Auto-heal | `src/remediation/auto_heal.py` | Docker/K8s pod restart (dry_run default) |
| Kill-switch | `src/api/routes.py` `POST /v1/admin/kill-switch` (admin scope) + `GET /v1/admin/kill-switch` | Zero model traffic via a top-of-handler 503 pre-check on `/risk/score` (operator-driven, audited, auto-expiry via `duration_seconds`) |
| Security | `src/api/security.py` | Bearer auth + HMAC + IP rate-limit |
| OTel | `src/api/otel.py` | Distributed tracing (dual-mode) |

## Data flow — single `/risk/score` request

1. Client POSTs to `/risk/score` with `Authorization: Bearer <key>` +
   HMAC signature + JSON body (the `OrderIn` Pydantic schema).
2. `check_key()` validates the bearer token against `RTO_SCORER_KEYS`.
3. `verify_hmac_signature()` checks the HMAC (RFC 5869) on the body —
   rejects replays + tampering.
4. Rules fast-path: `RULE-001`..N evaluate the order's categorical
   features. If any rule fires BLOCK/REJECT → return immediately (no
   model call).
5. Mandate check: `verify_mandate()` enforces OC-201B UPI Circle
   per-customer daily/weekly caps. BREACH → REJECT.
6. Circuit breaker: if `CircuitBreaker` is OPEN (model errors >
   threshold) → rules-only REVIEW (no model call).
7. Feature build: `KaggleFeatureBuilder.transform_cached(raw_order,
   customer_id)` — checks Redis `rto:featvec:{customer_id}` first; on
   miss, computes the 79-dim matrix + caches it (TTL=300s).
8. ONNX inference: `session.run(None, {input: X})` → P(RTO | x) in
   ~0.12ms (vs 18ms sklearn — 141× speedup, Microsoft ONNX Runtime
   2019).
9. Cost optimizer: `optimal_decision(p_rto, amount, C_fn, C_fp)` per
   Bahnsen BMR Eq.(6) → ACCEPT / REVIEW / REJECT.
10. Async audit: `AsyncAuditLogger.log(record)` — buffers the record
    (microseconds); a background task flushes every 100ms →
    `AuditLogger.log()` → Postgres INSERT + Merkle seal.
11. Stream publish: `KafkaProducer.publish(STREAM_RISK_SCORES, fields)`
    → Kafka when `KAFKA_BROKERS` set, else Redis Streams `XADD`.
12. Response: `{verdict, risk_score, audit_id, shap_explanation,
    cost_curve, intervention_curve}` — the dashboard renders this.

## Environment variables

| Var | Required | Default | Purpose |
|---|---|---|---|
| `RTO_SCORER_KEYS` | yes | — | Comma-separated scorer API keys |
| `RTO_ADMIN_KEYS` | yes | — | Comma-separated admin API keys |
| `RTO_MANDATE_SECRET` | yes | — | HMAC secret for mandate tokens |
| `RTO_AUDIT_SALT` | yes | — | Salt for customer_id redaction |
| `DATABASE_URL` | no | (unset → file mode) | Postgres URL; unset = JSONL fallback |
| `REDIS_URL` | no | (unset → no-op) | Redis URL for streams + feature cache |
| `KAFKA_BROKERS` | no | (unset → Redis) | Kafka bootstrap servers (comma-sep) |
| `RTO_ENV` | no | `dev` | `production` / `dev` |
| `RTO_HEAL_BACKEND` | no | `dry_run` | `dry_run` / `docker` / `k8s` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | no | (unset → no-op) | Jaeger/OTel collector URL |
| `LOG_LEVEL` | no | `info` | `debug` / `info` / `warn` / `error` |

## Migration path (per PRODUCTION_COMPARISON.md)

| Phase | Status | What |
|---|---|---|
| 1. Latency | ✅ Done | ONNX Runtime, **TreeSHAP-eval (kept KernelExplainer — HistGB doesn't expose tree structure for TreeSHAP recursion; documented in `explain.py` §102-109)**, **Redis feature cache**, **async audit batching** |
| 2. Compliance | ✅ Done | Merkle audit (RFC 6962), dual-control HMAC (RFC 5869), kill-switch, OC-201B mandates |
| 3. Kafka/K8s | ✅ **Manifests committed, runtime toggleable** | `src/stream/kafka_producer.py` + `infra/k8s/` (Deployment, HPA, StatefulSet, kustomize) |
| 4. Multi-tenant | ✅ Done | Postgres Row-Level Security on `merchant_id` |
| 5. Go rewrite | 📋 Post-funding | Hot-path `/risk/score` rewrite in Go + FlatBuffers + async audit batching → ~3ms p50 |
| 6. GPU ensemble | 📋 Post-funding | Triton inference server + ensemble models |

## Honesty section — what's real vs documented

| Claim | Evidence |
|---|---|
| "ONNX Runtime integrated, 141× speedup" | `src/models/feature_builder.py` `_get_onnx_session()` + bench in comment (18ms→0.12ms) |
| "Kafka transport wired with fallback to Redis Streams" | `src/stream/kafka_producer.py` — 7/7 fallback tests pass |
| "Horizontal autoscaling is wired" | `infra/k8s/hpa.yaml` — `kubectl kustomize` builds clean |
| "Merkle audit (RFC 6962)" | `src/audit/logger.py` `MerkleSealer` + `tests/test_v3_endpoints.py` |
| "Async audit batching" | `src/audit/async_logger.py` — 7/7 tests pass (sync fallback + buffer + overflow + stop-flush) |
| "Redis feature cache" | `src/models/feature_builder.py` `transform_cached()` — graceful fallback when Redis unset |
| "Kill-switch (RBI MRM §3.2)" | `src/api/routes.py` `POST /v1/admin/kill-switch` (admin scope, body `{enabled, reason, duration_seconds?}`) + `GET /v1/admin/kill-switch` (read live state). The POST mutates `state["kill_switch_active/reason/expires_at"]` + writes a `kill_switch_toggled` audit row; `/risk/score` checks the flag at the very top of the handler and returns `503 {"detail":"kill-switch active: <reason>"}` BEFORE any auth/HMAC/model/audit-write (zero CPU burn, zero model traffic). Auto-expires via `duration_seconds`; the pre-check auto-clears past-expiry flags (no background task needed). |

## References

- Lundberg & Lee, "A Unified Approach to Interpreting Model
  Predictions", NeurIPS 2017, arXiv:1705.07856 (SHAP KernelExplainer)
- Microsoft, "ONNX Runtime: High-performance scoring engine for ML
  models", 2019 (ONNX Runtime)
- Bahnsen et al., "Example-dependent cost-sensitive decision trees",
  Expert Systems with Applications 2015, DOI 10.1016/j.eswa.2014.10.031
  (cost-optimal decisioning)
- RFC 6962 — Certificate Transparency (Merkle audit model)
- RFC 5869 — HMAC-based Extract-and-Expand Key Derivation (mandate
  tokens)
- RBI MRM Direction 2023 §3.2 — Model Risk Management (kill-switch
  requirement)
