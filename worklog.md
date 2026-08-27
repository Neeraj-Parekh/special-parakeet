# RTO Trust Layer — Project Worklog

Shared work log for all agents working on the RTO Trust Layer project.
Each agent appends a new section starting with `---` containing:
Task ID, Agent, Task, Work Log (concrete steps), Stage Summary (key results/decisions/artifacts).

**Project root (extracted):** `/home/z/my-project/upload/RTO_Trust_Layer_FULL`
**User's benchmark:** Microsoft Fabric real-time fraud-detection reference architecture
https://learn.microsoft.com/en-us/fabric/real-time-intelligence/architectures/fraud-detection
**User's goal:** Make this project look professional + full-stack, comparable to the
Microsoft reference, by incorporating insights from 40 distilled papers (the
`paper studied/` knowledge base the user built — NOTE: NOT present in the current
zip upload; only the 10 tracked MD files arrived. Must ask user to upload
`paper studied/all_skills.yaml` + `paper studied/index.md` separately).

**Current phase:** READ-ONLY scoping. No code changes yet. Output = sharp questions.

---

---
Task ID: 1-c
Agent: general-purpose (infra/scripts/dashboard reader)
Task: Read all scripts + dashboard + docker + nginx + monitoring + configs, produce high-signal synthesis

Work Log:
- Read /home/z/my-project/worklog.md (existing context: prior agents framed benchmark vs Microsoft Fabric fraud-detection reference)
- Listed project root; found uv.lock (3 lines, stub) and infra/README.md (present, intentionally no IaC)
- Read all 5 scripts: scripts/security_probes.py, scripts/ingest_kaggle.py, scripts/evaluate.py, scripts/demo_agent.py, scripts/cost_table.py
- Read dashboard/index.html (138 lines, single-file vanilla JS, no CDN framework, no chart library)
- Read verify.sh (8 lines), Dockerfile (17 lines), docker-compose.yml (57 lines), nginx/nginx.conf (30 lines)
- Read monitoring/prometheus.yml (8 lines, 1 scrape job) and monitoring/grafana/rto-dashboard.json (4 panels)
- Read pyproject.toml (6 lines, ruff-only), requirements.txt (9 lines, 9 deps), uv.lock (stub, 3 lines)
- Inspected docs/openapi.json: 16 paths, 4 schemas (HTTPValidationError, ValidationError, OrderIn, RuleIn) — operationIds follow FastAPI auto-gen pattern (e.g. score_risk_score_post), confirms auto-generated from code, not hand-written
- Inspected autoresearch-results.tsv: 6 data rows + header, 17 columns — looks like an autonomous research-loop trace (iteration/decision/metric/verify/guard/hypothesis), NOT a model×dataset×metric sweep
- Confirmed dashboard is actually wired: src/api/routes.py:498-505 mounts /dashboard via FastAPI StaticFiles
- Appended this record to worklog.md

Stage Summary:
- Scripts: 4/5 are genuinely useful (evaluate.py trains+saves+registers model; cost_table.py produces cost_table.md; security_probes.py is a real authz/PII/idempotency probe harness; demo_agent.py is a working narrative demo with abuse drills). ingest_kaggle.py is the weakest — manual Kaggle CSV download, writes data/raw/ingested_real.csv, but warns that "load_orders" must be extended to read it (so the wiring is unfinished).
- Dashboard: vanilla JS, no framework, no chart lib — bars and gauge are hand-rolled CSS. It IS genuinely wired to /risk/score and /audit/{id} (not mocked). But "scored orders this session" is in-memory only (no persistence, lost on refresh), and the cost-threshold explorer uses a HARDCODED array `COSTS=[[0.15,1258],...]` — not fetched from /v1/policy/optimal. Slick-looking but half-toy.
- verify.sh: ruff + pytest + evaluate.py. Hardcoded interpreter path `/mnt/20265E15265DEC72/...linux_venv/bin/python` (author's machine only — will not run anywhere else). No CI config present in repo.
- docker-compose: api (build) + nginx/redis/postgres/prometheus/grafana all under `profile: full`. Postgres has no volumes, no migrations, no healthcheck. Redis is declared but UNUSED (no service depends on it, no env wiring). Grafana volume path is wrong (`/etc/grafana/provisioning/dashboards-src` is not a standard Grafana path; should be `/etc/grafana/provisioning/dashboards`).
- Dockerfile: single-stage python:3.12-slim, copies src/scripts/dashboard/tests but NOT data/raw except cod_orders.csv. Bakes default ENV secrets (change-me-scorer / rotate-in-prod) — defaults are placeholders but visible in image layers.
- nginx.conf: rate-limit 25r/s burst 50, /metrics gated to private CIDRs, /dashboard proxied, no TLS, no cache headers, no security headers (CSP/HSTS/XFO).
- Monitoring: prometheus.yml scrapes only api:8000/metrics (no postgres/redis/nginx exporters). Grafana dashboard has 4 panels (Decisions/min by outcome, Circuit breaker state, Degraded share, Scoring latency avg). No alerting rules. Looks minimal but real (metric names risk_decisions_total, rto_circuit_state, rto_score_latency_seconds — these are referenced from src/api/metrics.py per the openapi /metrics endpoint).
- pyproject.toml vs requirements.txt: NOT in sync. pyproject.toml has ONLY [tool.ruff] config, no [project] table, no dependencies. requirements.txt is the source of truth. uv.lock is a 3-line stub (version=1, revision=3, requires-python>=3.11) — NOT a real lockfile, uv was never run. No dev/runtime split. Pin discipline is loose (>=).
- openapi.json: 16 endpoints, auto-generated from FastAPI, recent and detailed (matches v0.4 platform-complete state per autoresearch TSV).
- autoresearch-results.tsv: 6 iterations, looks like an autonomous orchestrator's research loop trace (decision kept/discarded, metric_value = pr_auc 0.524→0.5495, hypothesis, change_summary, branch, parent_id). Iteration 5 mentions "5/5 probes mitigated", iteration 6 = v0.4 platform-complete with "41 tests green". This is the project's own build history, not external research.
- Gaps vs production: no CI/CD workflow file in repo (verify.sh references a venv path), no secrets manager, no DB migrations runner (no alembic/flyway), no message bus (kafka/redis-streams) — Redis is declared but unused, Postgres has no schema, no object store, no tracing (no OTel), no log aggregation, no alerting rules in Prometheus/Grafana, no runbook, infra/README.md explicitly defers IaC ("intentionally contains no half-deployable Terraform").
Task ID: 1-a
Agent: general-purpose (docs reader)
Task: Read all 10 documentation MD files + openapi.json skim, produce high-signal synthesis

Work Log:
- Read /home/z/my-project/worklog.md (header + protocol)
- Read README.md (root) — pitch, results table E1-E4, run instructions
- Read docs/ARCHITECTURE.md (v1) — small-box mermaid, model slice + audit jsonl
- Read docs/ARCHITECTURE_V2.md — enterprise 9-service spec, OAuth2/Kafka/ClickHouse/Feast/MLflow, cost model, SLOs
- Read docs/ARCHITECTURE_V3.md (full 558 lines, 3 chunks) — DRAFT-FOR-REVIEW, supersedes V2, 19-finding audit (A1-A19), modular monolith doctrine, CD-1..CD-12 code deltas, phased P0-P6 roadmap, claims ledger §21
- Read docs/API_SPEC.md — bare: 16 paths + auth scope table, no request/response schemas, no examples
- Skimmed docs/openapi.json — confirms 16 paths match API_SPEC; FastAPI auto-gen; no examples, descriptions only on /v1/mandates + /risk/{id}/override
- Read docs/cost_table.md — 8-row threshold sweep (0.15-0.6), FN=12×FP, optimal=0.15
- Read docs/feature_importance.md — 10 features, permutation AP drop, is_cod 0.1796 / PriorReturns 0.1150 lead
- Read docs/PITCH_SCRIPT.md — 5:00 Razorpay buildathon, 4-beat demo (verify.sh → demo_agent.py → tamper → cost_table)
- Read docs/research/INDEX.md — 18 citations across ML methodology/platform/agentic-trust; PDFs only committed for Tramer/NIST/Fraud-RLA (3 of 18)
- Read infra/README.md — short, defers to ARCHITECTURE_V2 §9.2, deliberately no Terraform
- Appended this record to worklog.md

Stage Summary:
- Confirmed framing: COD RTO (Return-to-Origin) risk scoring for Indian e-commerce; target customer = Razorpay AI Buildathon Track 02 (AI Risk Manager).
- Architecture truth = V3 (DRAFT-FOR-REVIEW); V1 is the model-only snapshot; V2 is explicitly superseded but infra/README.md still points at V2 §9.2 as production spec — stale reference, contradicts V3.
- V3 is unusually self-aware: §1 audit A1-A19 rejects ~80% of V2's enterprise boxes (Kafka, ClickHouse, Feast, MLflow-server, Hyperledger Aries, E2B sandbox, Listmonk, Go/Rust rules) as cargo-cult / resume-driven / license-inconsistent; flags V2 patent numbers as SUSPECT-FABRICATED.
- Real code footprint in src/: routes.py, mandates.py, breaker.py, engine.py, logger.py, cost_optimizer.py, cases/service.py, ml/registry.py, audit/logger.py, api/{security,metrics,mandates,breaker,routes}.py + dashboard/index.html — V3 §20 says only 12 code deltas (CD-1..CD-12) needed to close plan↔code gap.
- API surface = 16 endpoints, all exist (openapi.json matches API_SPEC.md). But API_SPEC.md is path-list-only; no request/response bodies, no examples — looks mid vs MS Fabric reference.
- Cost model = simple 8-row table, FN=12×FP, optimal threshold 0.15. V3 §11.6 promises to extend into full intervention-policy engine {ship, otp_verify, partial_cod, address_check, hold} — aspirational, not in code.
- Feature importance = 10 features, permutation AP drop, is_cod dominates (0.18) then PriorReturns (0.115). OrderDay/OrderHour negative (noise). Tied to real E2 model run via scripts/evaluate.py — NOT aspirational.
- Pitch = single-author demo ("I'm Neeraj"); customer=Razorpay judges; asks: real labeled data + pincode dir + Agent Studio integration. No monetization/pricing ask — pure buildathon entry.
- Research bibliography strong (18 papers, incl. 2026 DOI-anchored anchor paper on AI Agents in Payments); but only 3 of 18 PDFs committed (Tramer/NIST/Fraud-RLA); the `paper studied/` KB mentioned in worklog header is DIFFERENT from docs/research/ — docs/research/ is the project's own citations, NOT the user's 40-paper distillation.
- Top gaps/smells: (1) API_SPEC.md has no schemas/examples — judges will judge by this file first; (2) infra/README.md points at superseded V2; (3) compose `--profile full` starts Postgres+Redis that nothing wires to (V3 finding A2); (4) model trains-at-startup inside API (V3 A3, CD-1); (5) audit is fire-and-forget BackgroundTasks violating RPO=0 (V3 A5, CD-3/CD-4); (6) Merkle proof endpoint /v1/audit/{id}/proof is specified in V3 §10.3 but NOT in openapi.json (16 paths) — V3 spec > code; (7) /v1/simulate, /v1/usage, /v1/cases vs /v1/cases — V3 mentions many endpoints not yet in API; (8) no real IaC, no Alembic, no SDK clients published; (9) "16 paths" but no examples — comparing to MS Fabric fraud reference, missing: real-time ingestion pipeline diagram, eventhouse/lakehouse layer, KQL queries, PowerBI dashboard artifact, activation loop.
- Did not modify any project file. Read-only confirmed.

---
Task ID: 1-b
Agent: general-purpose (Python source reader)
Task: Read all src/*.py + tests/*.py + pyproject/requirements, produce high-signal synthesis

Work Log:
- Read existing worklog.md (header only, prior agents had not yet appended).
- Listed project tree; confirmed 22 .py files in src/ + 5 test files + 1 k6 .js load profile + pyproject.toml + requirements.txt in scope.
- Read src/__init__.py (empty), src/api/{routes,security,metrics,mandates,breaker}.py, src/audit/logger.py, src/ml/{registry,__init__}.py, src/rules/engine.py, src/cases/{service,__init__}.py, src/features/{enrich,cleaning,__init__}.py, src/models/{train,splitting,explain,__init__}.py, src/business/cost_optimizer.py.
- Read tests/test_{pipeline,security,mandates,platform,ship}.py and tests/load/risk_api_load.js.
- Read pyproject.toml (ruff config only, 7 lines) and requirements.txt (9 deps).
- Grepped src/ for TODO/FIXME/NotImplementedError/pass/placeholder; only hit: src/cases/service.py:64 "placeholder, real return below" (dead code in CaseService._latest).
- Grepped project for `create_app|^app =|uvicorn` to find ASGI entrypoint; NONE in src/ — `create_app()` is a factory used only from tests/scripts. No main.py / no module-level `app =`.
- Grepped src/ for `register_model|current_champion`; register_model is only ever invoked from tests (test_platform.py:69-70), never from the lifespan in routes.py — so champion metadata is empty at runtime.
- Appended this record to worklog.md.

Stage Summary:
- App: FastAPI factory `create_app()` in src/api/routes.py (524 LOC, single file doing routes + decision pipeline + audit + metrics + dashboard mount). No ASGI entrypoint file in src/. Lifespan loads CSV `data/raw/cod_orders.csv` -> trains HistGradientBoostingClassifier in-process -> dumps to `out/model_api.joblib` if missing. No middleware wired (no CORS, no TrustedHost, no GZip, no OTel middleware).
- Real working subsystems: hash-chained JSONL audit (`AuditLogger.verify_chain`, with byte-offset index), HMAC-signed mandates with VALID/TAMPERED/EXPIRED/BREACH verdicts, in-memory thread-safe circuit breaker (CLOSED/OPEN/HALF_OPEN) protecting model.predict_proba only, rules engine with 4 ops (gt/lt/eq/in) + 2 default rules + short-circuit on first match, token-bucket rate limiter, idempotency cache (unbounded), PSI drift calc, permutation-importance + leave-one-out local attribution (NOT SHAP despite shap in requirements.txt — dead dep).
- Stubs / half-implementations: CaseService._latest returns None always (line 64 dead placeholder); no DB anywhere (everything is JSONL/CSV files); register_model never called from runtime so `current_champion()` returns None and model-card endpoint hardcodes fallback `"dev"`; cost_optimizer is only attached as a `policy_hint` field, NOT used in the actual ACCEPT/REVIEW/REJECT decision (routes.py:172-180 uses static thresholds 0.15/0.60); no A/B, no shadow deploy, no challenger promotion path; features are pure toy (no device fingerprint / velocity / graph features, only 8 numeric + 3 categorical).
- Security: 2 static demo keys hardcoded as fallback ("score-demo-key", "admin-demo-key") with sha256-equality check; no JWT, no per-merchant RBAC, no nonce/replay protection beyond idempotency-key, no HMAC on requests (mandates are the only HMAC). Secrets from env: RTO_SCORER_KEYS, RTO_ADMIN_KEYS, RTO_MANDATE_SECRET, RTO_AUDIT_SALT.
- Tests: 5 real pytest files (~526 LOC), actually assert behavior (hash-chain tamper detection, mandate breach escalation, circuit breaker open, degraded mode, RBAC scopes). k6 load profile (steady+ramp+spike, p99<400ms threshold). No CI config visible in scope.
- Critical production gaps vs Microsoft Fabric fraud ref: no DB/migrations, no message bus / streaming, no feature store, no model server, no OTel tracing, no graceful shutdown handler, no retry/timeout on the model call (the breaker swallows exceptions but there's no retry, no timeout), idempotency cache is in-memory and unbounded (memory leak + lost on restart), config has zero validation (no pydantic-settings, no env schema), audit log is local file only (single-replica, no replication).

---
Task ID: 1-d
Agent: general-purpose (Microsoft Fabric fraud-detection reference fetcher)
Task: Fetch + analyze Microsoft Fabric real-time fraud-detection reference architecture

Work Log:
- Fetched https://learn.microsoft.com/en-us/fabric/real-time-intelligence/architectures/fraud-detection via `z-ai function -n page_reader` and `curl` (both 200 OK; 31.5 KB plain text extracted)
- Downloaded the architecture image to /home/z/my-project/reference/fabric/fraud-detection.png (PNG 1000x296, 38 KB — wide left-to-right diagram, page text fully describes components so vision parse not required)
- Parent index page (https://learn.microsoft.com/en-us/fabric/real-time-intelligence/architectures/) returned 404 — but main page sidebar TOC shows fraud-detection is one of ~8 vertical reference architectures under "Financial services" alongside Automotive/Energy/Industrials/Retail/Healthcare/Telecom
- Extracted: 10 named Fabric components, 4 operational phases, end-to-end data flow trace, ML/rules/storage/observability sections
- Cross-referenced user's project tree (src/api/, src/rules/engine.py, src/features/, src/cases/service.py, dashboard/index.html, monitoring/grafana, docs/ARCHITECTURE_V3.md) to produce gap list
- Appended this record to worklog.md

Stage Summary:
- Microsoft's reference is a 4-phase pipeline: **Ingest & process → Analyze, transform & enrich → Train & score → Visualize & activate**
- Named components: Eventstreams (stream ingest) | Data Factory (batch ERP→lake sync) | Eventhouse (hot KQL streaming + raw tx table) | OneLake (cold governed lakehouse) | Data Science (ML workspace, online scoring) | Activator (declarative rules + auto-actions) | Real-Time Dashboard (live, DirectQuery, drill-down) | Power BI (BI/reporting, separate from ops dashboard) | Copilot (NL Q&A) | DirectQuery (live query mode)
- Key sleekness drivers that make it look production-grade: (1) Real-Time Dashboard vs Power BI separation, (2) DirectQuery live-push mode, (3) declarative Activator rule routing w/ auto-blocking + auto-notifications, (4) governed OneLake lakehouse w/ raw→clean zones, (5) explicit hot (Eventhouse) vs cold (OneLake) split, (6) streaming transformations as first-class Eventhouse capability, (7) ERP batch path separated from transaction stream path by velocity, (8) Copilot NL layer, (9) RBAC/MFA/PAM/immutable-audit as named architectural sections, (10) capacity planning / autoscaling / lifecycle tiers as named sections, (11) phased rollout playbook (Foundation→Pilot→Ops Validation→Advanced→Enterprise-Scale)
- Gap list vs user's RTO Trust Layer (concrete):
  - User has rules engine but no Activator-equivalent declarative rule UI / auto-actioning layer (current: Python module + DEFAULT_RULES list, no rule DSL, no auto-block/auto-notify wiring beyond cases/service.py)
  - User has ONE static dashboard/index.html "merchant console v2" — Microsoft splits into Real-Time Dashboard (live, DirectQuery, drill-down) + Power BI (reporting) + Copilot (NL). User needs: (a) live WebSocket/SSE push dashboard, (b) separate reporting dashboard, (c) LLM Q&A layer
  - User has REST API ingest only (routes.py) — no Eventstream-equivalent streaming ingestion layer (Kafka/Redpanda/Redis Streams). ARCHITECTURE_V3 §9.3 already prescribes Redis Streams now → NATS → Kafka by trigger — implement that to match
  - User has features/enrich.py called inline — Microsoft runs streaming transformations as a separate continuous stage. User needs a stream-processor step (Faust/Bytewax/Quix-like) for normalize/filter/aggregate
  - User has cases/service.py (good parity) but unclear if there's a case queue UI; Microsoft's Activator orchestrates investigation workflows w/ SLA timers
  - User has cost_table.md + cost_optimizer.py (actually AHEAD of Microsoft in explicit cost-model math) — surface this in architecture doc
  - User has src/audit/logger.py + mandates.py + security.py (parity with Microsoft's RBAC/audit/MFA framing) — but the architecture doc needs to call these out as named architectural sections, not just modules
  - User has src/models/explain.py — parity with Microsoft's "explainable AI for investigation support" — surface as first-class
  - User has src/features/cleaning.py + enrich.py — needs explicit raw/clean/curated zone partitioning to match OneLake's governed lakehouse pattern (Iceberg/Delta tables via DuckDB+Parquet minimum)
  - User has Prometheus + Grafana (parity with Microsoft's "system health dashboards" + "performance metrics w/ SLA monitoring") — Microsoft's observability is abstract; user's is concrete. User is actually ahead here.
  - User has no ERP/asset enrichment batch path — Microsoft explicitly separates ERP batch sync (Data Factory → OneLake) from tx stream (Eventstreams → Eventhouse). Add a merchant-profile/device-registry batch ingest path
  - User has ingest_kaggle.py (single dataset) — Microsoft explicitly designs for multi-channel (mobile banking + ATM + e-commerce + call center). User needs a multi-source ingestion story even if mocked
  - ARCHITECTURE_V3.md is actually MORE rigorous than Microsoft's page (register-based decisions, revisit triggers, persona map) — but lacks a single named 4-phase architecture diagram. Redraw as Ingest→Analyze→Score→Activate.
- Recommendation: User is NOT as far behind Microsoft as they fear. The "mid at best" feeling comes from (a) the single static HTML dashboard vs Microsoft's three-tier Real-Time Dashboard + Power BI + Copilot split, and (b) the absence of a streaming ingestion layer between producers and the API. Both are addressable with ~1 sprint of work, not a rewrite.
---
Task ID: 1-orchestrator
Agent: main (Z.ai Code orchestrator)
Task: Coordinate 4 parallel readers, synthesize findings, prepare clarifying questions

Work Log:
- Extracted RTO_Trust_Layer_FULL_20260827.zip → /home/z/my-project/upload/RTO_Trust_Layer_FULL (35 MB)
- Mapped structure via Glob: 10 MD, 22 src/*.py + 5 tests + 1 k6, 5 scripts, dashboard/index.html, Dockerfile, docker-compose.yml, nginx/nginx.conf, monitoring/{prometheus.yml,grafana/rto-dashboard.json}, pyproject.toml, requirements.txt, uv.lock (stub), docs/openapi.json, autoresearch-results.tsv
- **CRITICAL FINDING: `paper studied/` knowledge base (40 papers × 5 files = 135 MD/YAML/JSON) described in user's message is NOT in the zip — only 10 tracked MD files arrived. Must request separate upload.**
- Spawned 4 parallel general-purpose agents (1-a docs, 1-b code, 1-c infra, 1-d Microsoft ref arch)
- All 4 returned tight syntheses; consolidated findings below.

Stage Summary:
- **Reframe**: User believes project is "mid at best" vs Microsoft Fabric ref. Truth: project is MORE rigorous than Microsoft's marketing page in 4 areas (audit hash chain + Merkle, BMR cost math, real Prometheus+Grafana stack, V3 architecture register with self-audit). The "mid" feeling is real but localized to PRESENTATION + STREAMING, not code quality. Fixable in ~1 sprint, not a rewrite.
- **Genuinely strong**: audit/logger.py hash chain (test_ship.py tampers + breaks chain), mandates.py HMAC verdicts (UPI Circle / delegated payments — real, not stubbed), group_split leakage control, circuit breaker degraded-mode fallback, cost_table.py + cost_optimizer.py BMR math, 5 real pytest files + k6 load profile, V3 self-audit (calls out own "repo amnesia" + "decorative infra" + "cargo-cult boxes").
- **Actually broken / stubbed / decorative**:
  1. cost_optimizer.py NOT wired into actual decision (uses static 0.15/0.60 thresholds)
  2. idempotency cache `state["idem"]` unbounded dict (memory leak)
  3. features/enrich.py::add_geo_features dead code (never called from lifespan)
  4. ml/registry.py::register_model dead in prod (only called from tests; champion always None)
  5. cases/service.py::_latest() always returns None (placeholder stub)
  6. requirements.txt has `shap` but never imported (dead dep, ~30MB)
  7. docker-compose `--profile full` starts postgres+redis the API never connects to (V3 finding A2, "decorative infra")
  8. Grafana provisioning mount path is wrong (`/etc/grafana/provisioning/dashboards-src` vs expected `dashboards`) — dashboard won't auto-load
  9. verify.sh hardcodes `/mnt/20265E15265DEC72/study/CODE/linux_venv/bin/python` — won't run elsewhere
  10. uv.lock is a 3-line stub — `uv lock` never actually run
  11. pyproject.toml has no `[project]` table — package not declared
  12. No CI workflow file despite autoresearch TSV claiming "CI workflow(quality+data-model-gate+docker)"
  13. API_SPEC.md is bare (16 path names + auth table, no schemas/examples); openapi.json has zero `example` fields
  14. V3 specifies endpoints not in openapi.json: /v1/audit/{id}/proof, /v1/simulate, /v1/usage, outcome-ingest
  15. Override endpoint single-admin in API vs dual-control promised in V3 §12.1 (contradiction)
  16. No DB / migrations — everything JSONL/CSV files
  17. No streaming / message bus — Redis declared but unused; synchronous request/response only; no feedback loop for `is_returned` ground truth
  18. No OpenTelemetry, no structured logging, no alerting rules
  19. Dockerfile bakes ENV defaults (`change-me-scorer`) visible via `docker history`
  20. nginx no TLS, no security headers, no gzip
  21. dashboard/index.html vanilla JS, slick-looking but obviously 1-screen demo; cost-threshold bars hardcoded, not fetched from /v1/policy/optimal; defaults `score-demo-key`/`admin-demo-key` visible in input fields
  22. `is_cod` 0.18 permutation importance is near-tautological (whole problem is COD RTO)
  23. Single-author pitch ("I'm Neeraj"); Track 02 vs 05 still undecided (V3 §22.1)
  24. Synthetic dataset only (7,235 CODScore rows); real Indian labeled data needs Kaggle credentials
- **3 biggest perceived-gap drivers vs Microsoft Fabric** (per agent 1-d):
  1. ONE static HTML dashboard vs Microsoft's Real-Time Dashboard + Power BI + Copilot (3 surfaces)
  2. REST-only, no event/streaming backbone (Microsoft has Eventstreams → Eventhouse → Activator)
  3. No DB / no migrations / no feedback loop (Microsoft has Eventhouse + OneLake + Activator)
- **Tech-stack elephant**: Project is Python+FastAPI; this sandbox is Next.js 16 + TypeScript. 4 paths: (a) improve Python in-place (can't run here), (b) port to Next.js (large rewrite), (c) build new Next.js dashboard/control-plane that talks to existing Python API (recommended), (d) focus on what I can verify here (dashboard/docs/openapi/cost presentation) + user runs Python changes on laptop.
- Next step: present synthesis + tiered clarifying questions to user.
