# RTO Trust Layer — Architecture Overview (3-Minute Senior-Engineer Read)

> **Task ID:** tier3-C
> **Author:** general-purpose subagent (documentation only)
> **Date:** 2026-08-29
> **Scope:** A single-page architecture overview a senior engineer can
> read in 3 minutes and walk away knowing what runs, what is the
> production target, and what is documented-only. Every claim in
> this doc is verifiable from the repo or explicitly marked as a plan.
>
> **Reality split (the headline):** the deployed Vercel app is a
> Next.js 16 + TypeScript console with a thin proxy + mock fallback.
> The aspirational Python FastAPI scorer (under
> `upload/RTO_Trust_Layer_FULL/`) is the production target. The
> Python scorer is NOT running on Vercel — it runs locally in this
> sandbox or on the user's own infra when `NEXT_PUBLIC_API_BASE_URL`
> is configured.

---

## 1. Problem statement (2 sentences)

Cash-on-delivery (COD) returns cost Indian e-commerce an estimated
₹50,000 crore per year, with roughly 3 in 10 COD orders being
returned (the RTO — Return to Origin — problem). The RTO Trust
Layer is a per-merchant risk-scoring + audit + decision-override
service that scores each COD order in real time and emits a
tamper-evident Merkle audit trail strong enough to satisfy RBI's
June 2026 Model Risk Management (MRM) guidance.

---

## 2. System diagram (ASCII art)

```
                                  [Browser — merchant console]
                                              |
                                              | HTTPS
                                              v
                                  [Vercel Edge — CDN + TLS]
                                              |
                                              v
   +----------------------------------------------------------------+
   |  Vercel serverless function (Next.js 16, TypeScript)          |
   |  src/app/api/**/route.ts        (17 API routes)               |
   |  src/lib/api-proxy.ts:21       API_BASE_URL = localhost:8000 |
   |  src/lib/api-proxy.ts:153      proxyJson: 4s timeout + mock   |
   |  src/lib/mock-data.ts          deterministic scorer fallback  |
   +----------------------------------------------------------------+
        |                                       |
        | fetch() to Python backend             | on failure → mock fallback
        | (when NEXT_PUBLIC_API_BASE_URL set)   | (the Vercel-only path)
        v                                       v
   +-----------------------------+        +-----------------------------+
   | Python FastAPI scorer       |        | In-process mock scorer     |
   | upload/RTO_Trust_Layer_FULL |        | (src/lib/mock-data.ts)     |
   |                             |        | returns X-Mock-Mode: true  |
   | src/api/routes.py (5,107 L) |        +-----------------------------+
   |   - /risk/score             |
   |   - /v1/audit/verify-chain  |
   |   - /v1/admin/kill-switch   |
   |   - /risk/:id/override      |
   |                             |
   |  +----------+ +----------+ |
   |  | ONNX     | | Cost-Opt | |  [Bounded Agent — 7-action allowlist]
   |  | Runtime  | | BMR dec. | |   src/api/agent_allowlist.py:63
   |  +----------+ +----------+ |
   |         |                   |
   |  +----------+ +----------+ |
   |  | Rule     | | Mandate  | |  [OC-201B UPI Circle mandate caps]
   |  | Engine   | | Caps     | |   src/api/mandates.py:699
   |  +----------+ +----------+ |
   |         |                   |
   |  +------------------------+ |
   |  | Merkle Audit Chain     | |  [Tamper-evident log]
   |  | src/audit/logger.py:60 | |   RFC 6962 inclusion proofs
   |  +------------------------+ |
   |         |                   |
   |  +------------------------+ |
   |  | Stream Producer        | |  [Redis Streams / Kafka stub]
   |  | src/stream/processor   | |   HLL spike + DDM + ADWIN drift
   |  +------------------------+ |
   +-----------------------------+
        |                                       |
        v                                       v
   +------------------------+         +------------------------+
   | Postgres               |         | Redis                  |
   | audit_records          |         | risk.scores stream     |
   | audit_merkle_intervals |         | audit.records stream   |
   | idempotency_keys       |         | cases.created stream   |
   | mandate_counters      |         | (no-op on Vercel path) |
   | (NOT on Vercel path)  |         +------------------------+
   +------------------------+
```

The dashed paths (Postgres, Redis, Kafka, the Python scorer itself)
are not invoked on the Vercel-only deploy — they are invoked when
the user wires `NEXT_PUBLIC_API_BASE_URL` to a running Python
backend.

---

## 3. Component table (box → file → status)

| Box | What it does | File(s) | Status |
|---|---|---|---|
| Browser console | Renders the merchant dashboard; calls `/api/*`. | `src/app/page.tsx`, `src/app/audit/page.tsx`, `src/app/rules/page.tsx`, `src/app/model-health/page.tsx` | shipped — live on Vercel |
| Vercel Edge | CDN + TLS termination. | (managed by Vercel) | shipped |
| Next.js serverless function | Hosts the 17 API routes; calls Python backend or falls back to mock. | `src/app/api/**/route.ts`, `src/lib/api-proxy.ts:21-177` | shipped — Vercel-deployed |
| TS mock scorer | Deterministic in-process scorer; mirrors the Python decision precedence (rules → mandate → cost-optimizer). | `src/lib/mock-data.ts` (`mockScore`) | shipped — fires when Python backend is unreachable |
| Python FastAPI scorer | The production scorer: Pydantic parse + feature builder + ONNX `session.run` + cost-optimal BMR + audit log + stream publish. | `upload/RTO_Trust_Layer_FULL/src/api/routes.py:1226` (`/risk/score` handler) | aspirational — runs locally, not on Vercel |
| ONNX Runtime inference | C++ inference engine; 49 KB ONNX model on a 79-dim feature vector; measured at 1.59 us/row on a 1000-row batch. | `upload/RTO_Trust_Layer_FULL/src/models/feature_builder.py:781` (`predict_proba`), `models/champion/model.onnx` | aspirational — wired into the Python scorer |
| Cost-optimal BMR decision | Bahnsen ICMLA 2013 Bayes Minimum Risk cost-optimal ACCEPT/REVIEW/REJECT decision (Eq. 5: per-amount FN cost). | `upload/RTO_Trust_Layer_FULL/src/business/cost_optimizer.py:85` | aspirational — wired into the Python scorer |
| Rule engine | Deterministic pre-screen: 4 default rules (RULE-001 high-value REJECT, RULE-002 prior-returns REVIEW, RULE-003 vague-address REVIEW, RULE-004 tier-3 REJECT) with ±₹500 randomized threshold jitter. | `upload/RTO_Trust_Layer_FULL/src/rules/engine.py:128`; mirrored in `src/lib/mock-data.ts` for the TS path | aspirational (Python); mock-mirrored (TS) |
| OC-201B UPI Circle mandate caps | NPCI OC-201B spec compliance: ₹5K/txn, ₹15K/month, 24h cooling, 5-device, 6-month auto-revoke. | `upload/RTO_Trust_Layer_FULL/src/api/mandates.py:699` | aspirational — wired into the Python scorer |
| Merkle audit chain | Tamper-evident audit log with hash-pointer linkage; RFC 6962 §2.1.1 inclusion proofs. | `upload/RTO_Trust_Layer_FULL/src/audit/logger.py:60` (`MerkleSealer`), `routes.py:2821` (`/v1/audit/verify-chain`) | aspirational — wired into the Python scorer; known to break in file-mode (see `AUDIT_REPORT.md` gap 2) |
| Bounded agent | 7-action allowlist (`ALLOWED_ACTIONS`); the agent literally cannot perform an off-list action. | `upload/RTO_Trust_Layer_FULL/src/api/agent_allowlist.py:63` | aspirational — wired into the Python scorer |
| Dual-control HMAC override | RFC 5869 HKDF-Extract+Expand two-signature override path; no single admin can override a decision. | `upload/RTO_Trust_Layer_FULL/src/api/keys.py:92` + `routes.py:2833` | aspirational — wired into the Python scorer |
| Stream producer | Fire-and-forget Redis `XADD` on 3 streams (risk.scores, audit.records, cases.created); Kafka compatibility stub when `KAFKA_BROKERS` is set. | `upload/RTO_Trust_Layer_FULL/src/stream/producer.py`, `src/stream/kafka_producer.py:80` | aspirational — wired into the Python scorer; no-op on Vercel path (no `REDIS_URL`) |
| Streaming CEP | HLL spike detector + sliding-window velocity counter + DDM 2σ/3σ drift + ADWIN Hoeffding bound drift. | `upload/RTO_Trust_Layer_FULL/src/stream/processor.py:71`, `src/ml/drift.py:55,176` | aspirational — wired into the Python scorer; no-op on Vercel path |
| Postgres | Persistent store for audit_records, audit_merkle_intervals, idempotency_keys, mandate_counters, cases. | `alembic/versions/001-007` (7 migrations) | aspirational — not on Vercel path; Neon free tier recommended |
| Redis | Streams for risk.scores / audit.records / cases.created; per-IP rate limit sliding window; feature vector cache (`transform_cached`, dead code). | (deployed via `docker-compose.yml`) | aspirational — not on Vercel path |

---

## 4. Decision flow (the 3-decision model)

The `/risk/score` handler returns one of three decisions. The
decision is the cost-optimal Bayes Minimum Risk choice given the
model's predicted probability, the per-amount FN cost (Bahnsen BMR
Eq. 5), and the per-merchant FP cost.

```
                  +---------------------------+
                  |  Score request received   |
                  |  (POST /api/risk/score)   |
                  +---------------------------+
                              |
                              v
                  +---------------------------+
                  |  RULE ENGINE              |
                  |  (RULE-001..RULE-004)     |
                  |  ±₹500 threshold jitter   |
                  +---------------------------+
                  |   |   |   |
                  |   |   |   v--- hard REJECT (e.g. amount > ₹50K)
                  |   |   v--- hard REVIEW (e.g. vague address)
                  |   v--- no hard rule fires
                  v
                  +---------------------------+
                  |  MANDATE CHECK            |
                  |  (OC-201B UPI Circle)     |
                  |  If mandate breach → REJECT
                  +---------------------------+
                              |
                              v
                  +---------------------------+
                  |  ONNX INFERENCE           |
                  |  predict_proba on 79-dim  |
                  +---------------------------+
                              |
                              v
                  +---------------------------+
                  |  COST-OPTIMAL BMR         |
                  |  (Bahnsen ICMLA 2013)     |
                  |  C_fn = amount × loss_rate
                  |  C_fp = merchant FP cost |
                  +---------------------------+
                              |
            +-----------------+-----------------+
            |                 |                 |
            v                 v                 v
      +-----------+     +-----------+     +-----------+
      |  ACCEPT   |     |  REVIEW   |     |  REJECT   |
      | (release  |     | (hold +   |     | (refuse   |
      |  for COD) |     |  OTP     |     |  COD;     |
      |           |     |  verify) |     |  ship     |
      |           |     |           |     |  prepaid) |
      +-----------+     +-----------+     +-----------+
            |                 |                 |
            +-----------------+-----------------+
                              |
                              v
                  +---------------------------+
                  |  AUDIT LOG + STREAM PUBLISH
                  |  Merkle leaf add; XADD to  |
                  |  risk.scores stream        |
                  +---------------------------+
```

The TS mock scorer (`src/lib/mock-data.ts:mockScore`) mirrors this
precedence (rules → mandate → cost-optimizer) so the dashboard
renders honest decisions even when the Python backend is not wired.

---

## 5. The 6-box architecture (concise restatement)

The RTO Trust Layer is organised as six cooperating subsystems. The
boundary between them is the boundary a senior engineer should keep
in mind when reading the codebase:

1. **Per-customer scoring model** — the ONNX Runtime inference over
   a 79-dim feature vector; the champion is a HistGradientBoosting
   classifier with PR-AUC 0.3950 on the Olist external validation
   set and 32x baseline lift on per-customer-history features
   (the headline business claim from `README.md`).
2. **Cost-optimal decision engine** — the Bahnsen BMR Eq. 5
   per-amount FN cost computation that picks ACCEPT / REVIEW /
   REJECT to minimise expected merchant loss (not to maximise
   accuracy). This is the dimension shift from "I built a model"
   to "I understand the business" per the README pitch.
3. **Merkle audit trail** — the tamper-evident log of every
   decision with hash-pointer linkage and RFC 6962 §2.1.1
   inclusion proofs; satisfies RBI MRM's "tamper-evident audit
   trail" mandate before it becomes law.
4. **Bounded agent guardrails** — the 7-action allowlist
   (`ALLOWED_ACTIONS`) that makes the LLM copilot mathematically
   unable to perform an off-list action; OC-201B UPI Circle
   mandate caps (₹5K/txn, ₹15K/month, 24h cooling, 5-device,
   6-month auto-revoke) cap the per-customer exposure even when
   the agent acts.
5. **Deterministic rule engine** — the 4 default rules with ±₹500
   randomized threshold jitter (Tramèr USENIX 2016 §6 anti-
   extraction defense + IEEE Access 2024 §IV.A threshold
   randomization); precedes the model in the decision pipeline.
6. **Streaming CEP** — the HLL spike detector + sliding-window
   velocity counter + DDM 2σ/3σ drift + ADWIN Hoeffding bound
   drift; consumes the risk.scores stream and emits Prometheus
   gauges at `/metrics` for the model-health dashboard.

---

## 6. Honest scope statement

The system has three layers of reality. A senior engineer should
read this list before any other doc.

### 6.1 What runs on Vercel today (shipped)

- The Next.js 16 + TypeScript console (17 API routes).
- The TS proxy with 4-second `AbortController` timeout and mock
  fallback (`src/lib/api-proxy.ts:153-177`).
- The deterministic in-process mock scorer (`src/lib/mock-data.ts`).
- The dashboard pages: score, audit explorer, rules manager, model
  health, copilot (the copilot is wired to the z-ai-web-dev-sdk
  via `src/app/api/copilot/route.ts` with a deterministic server-
  side refusal classifier — see `frontend-wiring-1` worklog entry).
- The Vercel deployment config (`vercel.json` — 10 lines, no
  secrets, no security headers).

### 6.2 Committed-but-not-deployed (in the repo, not on Vercel)

- The Python FastAPI scorer (`upload/RTO_Trust_Layer_FULL/`).
- The 7 alembic migrations (Postgres schema for audit_records,
  audit_merkle_intervals, idempotency_keys, mandate_counters,
  api_keys, override_nonces, cases).
- The Kafka compatibility stub (`src/stream/kafka_producer.py:80`).
- The K8s manifests (`infra/k8s/` — 11 manifests: namespace,
  postgres-statefulset, redis-deployment, api-deployment, hpa,
  kustomization, README).
- The multi-AZ pool code (referenced in `PRODUCTION_COMPARISON.md`
  as G6 — single-AZ assumption today; multi-AZ pool code is the
  plan, not shipped).
- The Docker Compose stack (`docker-compose.yml` — 5 services:
  api + postgres + redis + 3 stream workers).

### 6.3 Documented-only (no code, no build, no deploy)

- Kernel bypass (DPDK, AF_XDP). See `docs/LATENCY_ENGINEERING.md`
  section 3.2 — `plan`, post-funding.
- `io_uring` async I/O. See `docs/LATENCY_ENGINEERING.md` section
  3.3 — `plan`, post-funding.
- Busy-wait polling. See `docs/LATENCY_ENGINEERING.md` section 3.4
  — `out-of-scope`, explicitly NOT applicable to our workload.
- Thread-per-core (ScyllaDB / seastar model). See
  `docs/LATENCY_ENGINEERING.md` section 3.5 — `plan`, post-funding.
- The Go / Rust rewrite of the `/risk/score` hot path. See
  `docs/LATENCY_ENGINEERING.md` section 3.1 — `plan`, post-funding.
- LitmusChaos experiments (referenced in
  `upload/RTO_Trust_Layer_FULL/docs/CHAOS_ENGINEERING.md` — 210
  lines of doc, no `chaos-experiments/` directory).
- Federated learning (referenced in
  `upload/RTO_Trust_Layer_FULL/docs/FEDERATED_LEARNING.md` — 285
  lines of doc, no `MerchantFLClient` / `FLServer` classes in the
  repo).
- Adversarial training (listed as a defense in
  `upload/RTO_Trust_Layer_FULL/docs/ADVERSARIAL_DEFENSES.md` but
  no `train_perturbed` or `adversarial_training` calls in any
  Python source file — verified by the `AUDIT_REPORT.md` audit).

### 6.4 What is broken today (the honest gap list, top 3)

1. **Merkle audit chain returns `intact:false` in file-mode** —
   the `fcntl.flock` fix only serializes threads within one
   process; the running uvicorn + concurrent test writers race on
   `out/audit.jsonl`. Fix: set `DATABASE_URL` to a real Postgres
   (Neon free tier, ~30 minutes). See `AUDIT_REPORT.md` gap 2.
2. **The `AsyncAuditLogger` is dead code** —
   `routes.py:914` constructs the synchronous `AuditLogger`, not
   the async batching one. Fix: swap the constructor + add
   `await state["audit"].start()/stop()` to the lifespan
   (~30 minutes). See `AUDIT_REPORT.md` gap 6.
3. **The Redis `transform_cached` is dead code** —
   `routes.py:1609` calls `transform()` not `transform_cached()`.
   Fix: change the call site (~15 minutes). See
   `AUDIT_REPORT.md` gap 7.

---

## 7. Cross-references

| Doc | What it covers | When to read it |
|---|---|---|
| `docs/LATENCY_ENGINEERING.md` | The honest latency ceiling (~45-75 ms p50 / ~120 ms p99 warm; ~250-400 ms cold start) and the Phase 5 sub-5 ms plan (Go/Rust rewrite, DPDK, `io_uring`, thread-per-core). | When you need the latency numbers or the kernel-bypass plan. |
| `docs/SECURITY_HARDENING.md` | The STRIDE threat model, JWT auth plan (G7), Merkle audit chain integrity, webhook signature verification, secrets handling, supply chain (bun audit / Semgrep), security headers, cold-start DoS protection (RULE-005 / SEC-5). | When you need the security posture or the SEC-1 through SEC-5 / G7 plan labels. |
| `upload/RTO_Trust_Layer_FULL/docs/ARCHITECTURE.md` | The Python backend's own architecture doc — the source of truth for the file:line references in this overview. | When you need the deep Python-side architecture. |
| `upload/RTO_Trust_Layer_FULL/docs/RBI_MRM_MAPPING.md` | The 7-row compliance table mapping our features to RBI's June 2026 MRM guidance (3 shipped, 3 partial, 1 future). | When you need the regulatory mapping. |
| `upload/RTO_Trust_Layer_FULL/docs/SECURITY_HARDENING.md` | The Python-side security doc with the 7 attack vectors (model extraction, evasion, replay, feature-starvation, audit-poisoning, cold-start, stream-poisoning). | When you need the deep security analysis. |
| `upload/RTO_Trust_Layer_FULL/docs/CHAOS_ENGINEERING.md` | The 7 chaos experiments + 5-event auto-remediation map (doc-only, no `chaos-experiments/` directory). | When you need the chaos engineering plan. |
| `upload/RTO_Trust_Layer_FULL/docs/FEDERATED_LEARNING.md` | The FedAvg + DP-SGD protocol (doc-only, no `MerchantFLClient` / `FLServer` classes). | When you need the federated learning plan. |
| `upload/RTO_Trust_Layer_FULL/docs/ADVERSARIAL_DEFENSES.md` | The adversarial defenses doc (adversarial training is documented-only, no `train_perturbed` calls). | When you need the adversarial defense plan. |
| `AUDIT_REPORT.md` | The brutal, evidence-based 1-to-1 audit of all 37 features against the 16 prompts — `file:line` evidence for each. | When you need the gap analysis context. |
| `UML_COMPREHENSIVE.md` | The 12 code-verified Mermaid diagrams (every box annotated with `%% evidence: file:line`). | When you need the visual system map. |
| `README.md` | The project pitch + honest status (real / partial / stub / decorative / missing). | When you need the pitch + honest status. |

### Planned docs (referenced in the user's tier-3 brief but not yet shipped by this agent)

The user's tier-3 brief references the following docs, owned by
other tier-3 agents — they may or may not exist at the time you
read this. If they do not exist, the references here are
forward-looking:

- `docs/RULE_DSL.md` — the declarative rule DSL design (closes the
  `G2` gap: "No declarative rule DSL" — see worklog line 4043).
- `docs/STREAMING_ARCHITECTURE.md` — the streaming CEP architecture
  (closes the `G1` gap: "No Kafka+Flink streaming").
- `docs/MULTI_AZ.md` — the multi-AZ / multi-region / multi-shard
  plan (closes the `G6` gap: "No multi-AZ/multi-region/multi-shard").
- `docs/INTEGRATIONS.md` — the courier / NPCI / ERP integrations
  plan (closes the `G8` gap: "No real courier/NPCI/ERP
  integrations").

When all four are shipped, the cross-reference map in section 7
above should be updated to include them.
