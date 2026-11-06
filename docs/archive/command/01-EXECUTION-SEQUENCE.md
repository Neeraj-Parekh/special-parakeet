# RTO Trust Layer — Execution Sequence
## Day-by-day sprint plan with parallel subagent assignments

> **Internal deadline: Aug 28-29. Today ~ Aug 26-27. Budget: ~3 build days + 1 buffer day.**
> Read `00-MASTER-PLAN.md` first if you don't have context.

---

## Day 1 (today) — Foundation + quick wins

**Goal**: Command folder live + Tier 4 quick fixes done + cost-optimizer wired + mandates expanded.

### Parallel tracks:

**Track A (orchestrator)**:
- [x] Read project (4 reader agents — worklog)
- [x] Read paper studied (agent 2-knowledge — worklog)
- [x] Read prompt-razor.txt (agent 2-knowledge — worklog)
- [x] Build command folder (4 of 8 files written; 4 remaining)
- [ ] Write remaining 4 files (02, 03, 04, 07)
- [ ] Launch execution tracks B, C, D

**Track B (subagent — Tier 4 infra theater)** — Task ID **3-a**:
Touch files: `verify.sh`, `docker-compose.yml`, `Dockerfile`, `nginx/nginx.conf`, `requirements.txt`, `pyproject.toml`, `src/features/enrich.py`, `src/cases/service.py`
- [ ] Fix `verify.sh` hardcoded venv path → `python3` (or `uv run python`)
- [ ] Fix Grafana provisioning mount path (`dashboards-src` → `dashboards`)
- [ ] `pyproject.toml [project]` table (name, version, deps, entry points) — runtime/dev split
- [ ] Dockerfile: move ENV defaults to runtime-only (don't bake `change-me-scorer` into image layers)
- [ ] nginx: add TLS stub + security headers (CSP/HSTS/XFO/XCTO) + gzip
- [ ] Remove dead `shap` dep from requirements.txt (or wire it for real via KernelExplainer — see 04-TECH-STACK)
- [ ] Fix `cases/service.py::_latest()` (currently returns None — placeholder stub; either implement or delete)
- [ ] `add_geo_features` called-or-removed (currently dead code in `features/enrich.py:27`)
- [ ] Note: `uv.lock` real resolution needs `uv lock` run on user's laptop (they have the toolchain) — write a script + instructions for user to run

**Track C (subagent — cost-optimizer wiring)** — Task ID **3-b**:
Touch files: `src/api/routes.py`, `src/business/cost_optimizer.py`, `tests/test_ship.py`, `dashboard/index.html` (cost-curve explorer wiring)
- [ ] Wire `optimal_decision()` into `routes.py` decision path (replaces static `ACCEPT_T=0.15, REJECT_T=0.60`)
- [ ] Apply Bahnsen Eq.(6)-(7) recalibration fn (for future SMOTE usage)
- [ ] Update `tests/test_ship.py` to verify cost-optimizer is now the decision path (not just stored as `policy_hint`)
- [ ] Add `/v1/policy/cost-curves` endpoint (Drummond-Holte cost curves with ≥500 bootstrap CIs)
- [ ] Wire `dashboard/index.html` cost-curve explorer to `/v1/policy/cost-curves` (replace hardcoded `COSTS=[[0.15,1258],...]` array)
- [ ] Source paper: `paper studied/cost-sensitive-fraud-detection-bayes-minimum-risk/`

**Track D (subagent — mandate action-class expansion)** — Task ID **3-c**:
Touch files: `src/api/mandates.py`, `src/api/routes.py` (mandate verification path), `scripts/demo_agent.py` (bounded agent ALLOWED_ACTIONS), `tests/test_mandates.py`
- [ ] Extend `ALLOWED_ACTIONS` dict in bounded_agent demo per V3 §13
- [ ] Add UPI Circle / delegated-payment actions:
  - `upi_circle_delegated_pay` (cost 5, requires_approval=True, hard cap ₹5000/txn ₹15000/month)
  - `validate_device_id` (cost 1, no approval)
  - `revoke_delegation_on_inactivity` (cost 2, auto-triggered at 6-month inactivity)
- [ ] Wire per-txn `device_id` + `user_id` validation in `mandates.py` HMAC chain
- [ ] Tag BH purpose code in audit records (per NPCI OC-201B)
- [ ] Update `tests/test_mandates.py` to cover the new action classes + device_id validation + revocation
- [ ] Source papers: `paper studied/upi-circle-iot-circular-oc201b/` + `paper studied/upi-delegated-payments-npci-oc201b/` (if present) + SoK paper

### End of Day 1 success criteria:
- [ ] Command folder live with 8 files
- [ ] 9 Tier 4 fixes done
- [ ] Cost optimizer wired into decision (gap #1 closed)
- [ ] Mandate expansion done (gap #2 closed)
- [ ] Cost-curve endpoint + dashboard wiring (gap #13 closed)
- [ ] 3 of 14 paper-skills gaps closed

---

## Day 2 (~Aug 27-28) — Backend hardening + streaming + DB

**Goal**: Real DB, real streaming, real feedback loop, V3 missing endpoints, ML registry live.

### Parallel tracks:

**Track E (subagent — DB + migrations)** — Task ID **4-a**:
Touch files: `docker-compose.yml`, `pyproject.toml`, `src/audit/logger.py`, `src/cases/service.py`, `src/ml/registry.py`, `src/api/routes.py` (config), new `alembic/` folder, new `src/config.py`
- [ ] Add Postgres 15 to docker-compose (already there, just wire DATABASE_URL)
- [ ] Add Alembic with first migration: `audit_records`, `cases`, `model_registry`, `idempotency_keys`, `psi_reference` tables
- [ ] Refactor `AuditLogger` to write to Postgres (keep hash chain logic; sink JSONL → Postgres + Parquet rotation to MinIO/S3)
- [ ] Refactor `CaseService` to use Postgres (replace JSONL)
- [ ] Refactor `MLRegistry` to use Postgres (replace JSON file)
- [ ] Add `DATABASE_URL` env var + `pydantic-settings` config validation
- [ ] Replace idempotency unbounded dict with Postgres table (or Redis with TTL) — closes memory leak

**Track F (subagent — streaming path)** — Task ID **4-b**:
Touch files: `docker-compose.yml`, new `src/stream/producer.py`, new `src/stream/consumer.py`, `src/api/routes.py` (publish after decision)
- [ ] Add Redis Streams to docker-compose (already there, wire REDIS_URL)
- [ ] Create `src/stream/consumer.py` — consumes `risk.scores` stream
- [ ] Create `src/stream/producer.py` — produces to 5 topics: `risk.scores`, `audit.records`, `cases.created`, `model.drift`, `notifications` (per V2 §5)
- [ ] Refactor `routes.py` to publish to streams after decision (async, fire-and-forget; outbox pattern per V3 §10.3)
- [ ] Add stream-processor worker: normalize/filter/aggregate (Microsoft Eventhouse streaming-transforms equivalent)
- [ ] Source: TFX `generate_data_statistics` capability + MLOps-DevOps Integration paper

**Track G (subagent — feedback loop)** — Task ID **4-c**:
Touch files: new `src/feedback/label_service.py`, new `src/ml/drift.py` (DDM + ADWIN), `src/api/routes.py` (new endpoint), `monitoring/grafana/rto-dashboard.json` (new panels)
- [ ] Create `src/feedback/label_service.py` — consumes delayed `is_returned` labels (chargeback-style delay)
- [ ] Add DDM detector (Gama 2014): `p+sigma >= p_min + 2*sigma_min` WARNING, `+3*sigma_min` DRIFT
- [ ] Add ADWIN detector (variable sliding window, Hoeffding bound `ε_cut = √((1/2m)ln(4|W|/δ))`)
- [ ] On DRIFT, trigger shadow-retraining of model on rolling 90-day window
- [ ] Track detection-delay + false-alarm-run-length as Prometheus metrics
- [ ] Add `POST /v1/feedback/ingest` endpoint (is_returned label by prediction_id)
- [ ] Source: `paper studied/survey-concept-drift-adaptation/`

**Track H (subagent — V3 missing endpoints + dual-control override)** — Task ID **4-d**:
Touch files: `src/api/routes.py`, `src/audit/logger.py` (Merkle intervals), `docs/openapi.json`, `docs/API_SPEC.md`
- [ ] `GET /v1/audit/{id}/proof` — Merkle path from record to last published root (V3 §10.3)
- [ ] Implement Merkle interval sealing (every N records or T seconds → compute root → chain to prev_root)
- [ ] `POST /v1/simulate` — replay a transaction through the pipeline (dry-run, no audit write)
- [ ] `GET /v1/usage` — metering (per-merchant request counts, last 24h/7d/30d)
- [ ] `POST /v1/feedback/ingest` — outcome label ingestion (Track G owns the impl, this is the route)
- [ ] Add dual-control to override endpoint (V3 §12.1: 2 admin signatures, HMAC chain)
- [ ] Update `docs/openapi.json` + `docs/API_SPEC.md` with the new endpoints + examples

### End of Day 2 success criteria:
- [ ] DB + migrations done
- [ ] Streaming path done
- [ ] Feedback loop done
- [ ] V3 missing endpoints done
- [ ] Dual-control override done
- [ ] Merkle audit intervals done
- [ ] 6 of 14 paper-skills gaps closed (cumulative)

---

## Day 3 (~Aug 28) — Frontend + CI + docs

**Goal**: Stripe-like Next.js dashboard, CI workflow, docs that sell.

### Parallel tracks:

**Track I (subagent — Next.js dashboard)** — Task ID **5-a**:
Touch files: `/home/z/my-project` (host Next.js sandbox), new `src/app/page.tsx`, new `src/components/` for 4 pages, new `src/lib/api.ts` (proxy to Python API)
- [ ] Scaffold Next.js 16 + TypeScript + Tailwind + shadcn/ui (host sandbox)
- [ ] 4 pages: 
  - **Risk Console** — paste 3 Indian addresses, click Score, ACCEPT/REVIEW/REJECT badges + explainability panel
  - **Audit Explorer** — click prediction ID, see features + model version + SHA-256 hash chain + CSV download
  - **Rules Manager** — toggle rule "Block COD > ₹50K from new customers," re-score, instant REJECT
  - **Model Health** — PR-AUC, PSI, "Model v2.1 active since Aug 25" (live Grafana embed or pull from /metrics)
- [ ] Dark mode, Stripe-like aesthetic (no indigo/blue per styling rules)
- [ ] WebSocket/SSE for live updates (auto-refresh, no manual refresh)
- [ ] API routes proxy to Python API at `/api/*` (server-side, hides backend URL)
- [ ] Remove default demo keys from input fields (user directive)
- [ ] 3 demo orders wired (repeat customer, high-value COD, prior returns)
- [ ] Copilot-style NL Q&A panel (LLM skill — optional, if time permits)

**Track J (subagent — CI workflow)** — Task ID **5-b**:
Touch files: new `.github/workflows/ci.yml`, new `.github/workflows/mlops.yml`
- [ ] `.github/workflows/ci.yml` — ruff + pytest + leakage gate + docker build + Trivy scan
- [ ] `.github/workflows/mlops.yml` — 7-stage TFX-style: CI quality, CI data validation, CT model training, CT model registry, CD container build, CD deploy, Monitor
- [ ] k6 load test integration in CI (use existing `tests/load/risk_api_load.js`)
- [ ] Fix `verify.sh` to use CI's python (already done in Track B Day 1)

**Track K (subagent — docs that sell)** — Task ID **5-c**:
Touch files: `README.md`, `docs/PITCH_SCRIPT.md`, `docs/ARCHITECTURE.md` (new Mermaid version, may consolidate V1/V2/V3), `docs/MODEL_CARD.md`, `docs/RESEARCH.md`, `docs/API_SPEC.md`
- [ ] `README.md` — rewrite as product landing page (not homework). Hero, problem, solution, demo screenshots, run instructions, results.
- [ ] `docs/PITCH_SCRIPT.md` — word-for-word 5-min video script (3-act structure)
- [ ] `docs/ARCHITECTURE.md` — Mermaid diagrams + scaling analysis (what breaks at 10x). Consolidate V1/V2/V3 into one current truth.
- [ ] `docs/MODEL_CARD.md` — training data, metrics, limitations, bias analysis (per Google model card spec)
- [ ] `docs/RESEARCH.md` — 5 papers cited + learnings (per prompt-razor §2 lines 1863-1875 — these are blog/industry citations, different from the 40-paper KB)
- [ ] `docs/API_SPEC.md` — full OpenAPI with examples (not just path names)

### End of Day 3 success criteria:
- [ ] Next.js dashboard live (Stripe-like, 4 pages, dark mode)
- [ ] CI workflow live
- [ ] Docs that sell
- [ ] 10 of 14 paper-skills gaps closed (cumulative)

---

## Day 4 (~Aug 29) — Buffer + video prep + real data

**Goal**: Real data ingestion, IaC, OpenTelemetry, multi-source simulators, full V3 §11.6 intervention policy, video prep.

### Parallel tracks:

**Track L (user + orchestrator — real data)** — Task ID **6-a**:
- [ ] User downloads Amazon India Sale Report from Kaggle (~129k orders) — **user action**
- [ ] User uploads to `/home/z/my-project/upload/data/raw/amazon_sale_report.csv` — **user action**
- [ ] Wire `ingest_kaggle.py` → `cleaning.py` properly (currently unfinished — `ingest_kaggle.py` prints "next: extend src/features/cleaning.py load_orders to read ingested_real.csv")
- [ ] Re-train model on real data; target PR-AUC > 0.70 (paper benchmark: Kandula e-commerce delivery AUC 73-79%)
- [ ] Re-generate `docs/cost_table.md` + `docs/feature_importance.md` on real data
- [ ] If Tailscale bridge needed for the user to run the training, set it up now (per `00-MASTER-PLAN.md` §15)

**Track M (subagent — IaC + OpenTelemetry + multi-source)** — Task ID **6-b**:
Touch files: new `infra/main.tf` (OpenTofu), `src/api/routes.py` (OTel instrumentation), `docker-compose.yml` (add Jaeger + AlertManager), new `src/ingest/mobile.py`, `src/ingest/atm.py`, `src/ingest/callcenter.py`
- [ ] `infra/main.tf` (OpenTofu) — Postgres RDS, ElastiCache Redis, S3, EKS, Istio, HPA (per V2 §9.2 prod target)
- [ ] OpenTelemetry instrumentation in `src/api/routes.py` (spans for each `/v1/risk/score`)
- [ ] Jaeger in docker-compose
- [ ] AlertManager config + 3 alert rules (circuit breaker open >5min, drift DRIFT, audit write errors)
- [ ] Multi-source ingest simulators: mobile banking (Kafka topic consumer), ATM (batch CSV), call center (webhook) — 4 channels per Microsoft Fabric
- [ ] Note: OpenTelemetry + Jaeger is "nice to have" — if time runs short, Prometheus alone is enough for demo

**Track N (subagent — full V3 §11.6 intervention policy)** — Task ID **6-c**:
Touch files: `src/business/cost_optimizer.py`, `tests/test_ship.py`, `docs/cost_table.md`
- [ ] Extend `cost_optimizer.py` from 3-way (ACCEPT/REVIEW/REJECT) to 5-way intervention argmin: `{ship, otp_verify, partial_cod, address_check, hold}`
- [ ] Per-amount FN cost (Bahnsen Eq.(5): `R(fraud|x) = Ca·P(fraud|x) + Ca·P(legit|x)` vs `R(legit|x) = Amt_i·P(fraud|x)`)
- [ ] Cost-sensitive threshold sweep (Drummond-Holte cost curves, ≥500 bootstrap CIs preserving row marginals)
- [ ] Wire into dashboard cost-curve explorer (already done in Track C Day 1 — just extend to 5-way)

**Track O (orchestrator — video prep)**:
- [ ] Dry-run the 5-min pitch against the 6 demo moments
- [ ] Verify each demo moment works in <30 sec without debugging
- [ ] Final `bun run lint` + pytest pass
- [ ] Git tag v1.0 + push

### End of Day 4 success criteria:
- [ ] Real data ingested, PR-AUC > 0.70
- [ ] IaC + OpenTelemetry + multi-source done (or cut per triage rules)
- [ ] Full V3 §11.6 5-way intervention policy done
- [ ] All 14 paper-skills gaps closed
- [ ] All 16 Tier 2 items done
- [ ] All 24 broken/stubbed/decorative items fixed
- [ ] 3 perceived-gap drivers vs Microsoft closed
- [ ] Video prep done

---

## Triage rules (if time runs short)

Cut in this order (least painful first):
1. **Multi-source ingest simulators** (Track M) — nice for Microsoft parity but not core
2. **IaC** (Track M) — V3 explicitly said "an unapplied partial IaC is worse than a precise spec"
3. **Full V3 §11.6 5-way intervention** (Track N) — keep the 3-way (Track C Day 1)
4. **OpenTelemetry** (Track M) — Prometheus is enough for demo
5. **Copilot NL Q&A panel** (Track I optional) — nice but not core
6. **Multi-tenant Merchant service** — not in V3's 12 code deltas

**Never cut**: the 6 judge demo moments, the audit hash chain, the mandates, the cost optimizer wiring, the real Kaggle data, the README, the pitch script.

---

## Tailscale bridge trigger

Bring up the Tailscale bridge (per `00-MASTER-PLAN.md` §15) when:
- We need to run the test suite against real Kaggle data (Day 4 Track L)
- We need to run k6 load tests against a deployed stack (Day 3 Track J)
- We need to flash ESP32 (different project, not this one)
- Any download >10GB

Until then, I write Python code; user runs it on their laptop.

---

## Worklog protocol (for subagents)

Every subagent MUST:
1. Read `/home/z/my-project/worklog.md` before starting (see what previous agents did)
2. Append their work record after finishing, using this template:
```
---
Task ID: <e.g., 3-a>
Agent: <agent name>
Task: <what you were asked to do>

Work Log:
- <concrete step 1>
- <concrete step 2>
- ...

Stage Summary:
- <key results / decisions / produced artifacts>
```
3. Use `Bash: cat >> /home/z/my-project/worklog.md << 'WORKLOG_EOF' ... WORKLOG_EOF` to append atomically (do NOT overwrite)

---

*Last updated: Aug 27, 2026. Maintained by: Z.ai Code orchestrator.*
