# RTO Trust Layer — prompt-razor.txt Extraction
## What's in the original 2102-line prompt that's NOT in the chat messages

> Source: agent 2-knowledge read of `/home/z/my-project/upload/prompt-razor.txt` (2102 lines, 104KB)
> The user's latest chat message already contained: North Star, Done state, 4-Question Gate, 5 Missions, 3-Act pitch, architecture diagram, final priority list. This file captures what's NOT in those pasted sections.

---

## 1. prompt-razor.txt section map (with line numbers)

| § | Lines | Content |
|---|---|---|
| `/autoresearch:ship` header | 1-24 | Invokes `opencode-autoresearch` skill package; ship-readiness workflow with subagent pool — the agent harness Neeraj ran locally |
| "The Honest Gap Analysis" | 39-55 | Diagnostic: "data science notebook dressed as an API… not a system" |
| "The Pivot" RTO Shield Pro → Trust & Risk Intelligence Layer | 56-62 | Rename moment; positions project against Razorpay RTO Shield |
| Production Architecture (6-box ASCII) | 64-137 | Earlier simpler 6-box diagram (Client → Kong+Nginx → FastAPI → Rules+Feature Store Redis+SQLite → ML HistGB/SHAP/Evidently/SQLite → Prom+Grafana → Audit JSONL→Parquet). User's chat paste had a more elaborate diagram — V3 expanded this |
| **5-Day Build Plan** | 138-191 | Day 1 infra skeleton, Day 2 frontend "Whoa factor", Day 3 backend hardening, Day 4 model+monitoring, Day 5 polish/docs/video |
| §1 docker-compose.yml | 196-281 | Full YAML with 6 services (nginx, api, redis, postgres, prometheus, grafana, frontend) |
| §2 Enhanced FastAPI Backend (main.py) | 282-712 | ~430 lines of Python: Pydantic v2 + SQLAlchemy + Redis feature store + circuit breaker + HMAC mandates + rate limiter (fastapi_limiter) + audit hash chain + Prometheus metrics. **The code-template section.** |
| §3 React Frontend Scaffold (App.js) | 713-921 | ~200 lines: Risk Console, Audit Explorer, Rules Manager, Model Monitor, Agent Console pages; Recharts LineChart |
| §4 Rules Engine Config (default.yaml) | 922-950 | YAML rule schema with operator/clauses/action |
| §5 Nginx Config | 951-1001 | rate-limit + proxy + /metrics CIDR gating |
| **The "Agentic" Layer (Day 5 Add-on)** | 1003-1050 | `BoundedAgent` class with hardcoded `ALLOWED_ACTIONS` dict — score_order (cost 0), request_otp (cost 1), flag_review (cost 2), block_order (cost 10, requires_approval=True). **The actual agent skeleton code.** |
| **Architecture RFC v2.0** | 1093-1227 | 6-layer "RFC" with §1 Architectural Principles (System First, Defense in Depth, Fail Loud/Safe, Audit Everything, Zero Trust, Explainability by Design) + §2 Full System Architecture (6-layer ASCII: Edge/CDN → API Gateway → App Services → ML Platform & Feature Store → Data & Persistence → Observability → Frontend) |
| §3 The 10 Services | 1228-1469 | See §2 below — the 10 backend services with responsibilities, endpoints, DBs, dependencies |
| §4 Agentic Layer Phase 2 | 1470-1491 | Agent Gateway Service as consumer (not controller); 5-action allowlist (risk.score, risk.explain, case.create, notify.merchant, rules.suggest); guardrails list |
| §5 Data Architecture | 1492-1516 | DB-per-service table + Event Bus (Kafka or Redis Streams) with 5 topics: risk.scores, audit.records, cases.created, model.drift, notifications |
| §6 Security Model | 1517-1537 | Zero-Trust: JWT RS256 5-min expiry at gateway, mTLS service-to-service, dedicated DB user per service, HashiCorp Vault or Docker Secrets; LUKS at rest, TLS 1.3 in transit, SHA-256 PII hashing in audit, bcrypt API keys; RBI PA data localization, PCI DSS tokenization, GDPR right-to-explanation |
| §7 Frontend Architecture | 1538-1570 | 4 dashboards: Merchant /dashboard, Case Mgmt /cases, Admin /admin, Agent /agent — each with 3-4 named pages |
| §8 Observability Stack | 1572-1600 | 6 Prom metrics (risk_score_latency_seconds, risk_score_decisions_total, model_drift_psi, circuit_breaker_state, feature_store_miss_rate, audit_log_write_errors); 4 Grafana dashboards (Risk Ops, Model Health, Business Impact, Infrastructure); Jaeger trace-id propagation; ELK/Loki structured JSON logs |
| §9 Deployment Architecture | 1602-1635 | §9.1 docker-compose with 16 services (labeled "12 services" — typo); §9.2 production migration: EKS, RDS Multi-AZ, ElastiCache, MSK Kafka, S3, Istio, HPA |
| §10 Implementation Roadmap | 1637-1690 | Day-by-day agent tasks (concrete: write compose with 16 services, init-databases.sql, init-kafka-topics.sh, etc.) |
| §11 Deliverables Checklist | 1692-1707 | 10-row table for Razorpay: GitHub repo, README, ARCHITECTURE.md, API_SPEC.md, PITCH_SCRIPT.md, Docker Compose, 5-min pitch video, Live demo URL, pytest, integration tests |
| §12 Competitive Moat | 1709-1718 | Pitch angle: "RTO Shield is pincode-level and opaque. We're address-level, explainable, and give merchants control via the rules engine" |
| 1:1 Coverage Map §A-D | 1725-1820 | Honest gap analysis of the agent's own work: A (original asks), B (what was asked from the agent), C (what it added), D (still missing — explicit: GitHub repos list, paper references, Kaggle API integration, real geocoding integration) |
| Missing Assets Brief §1-9 | 1826-2090 | The actual command-folder spec — 9 numbered subsections |
| V3 audit closure note | 2093-2102 | States `docs/ARCHITECTURE_V3.md` (~558 lines) was delivered after the prompt-razor V2 RFC, with 19 findings rejecting ~80% of V2's enterprise boxes |

---

## 2. The 10 services (RFC §3.1-3.10, lines 1228-1469)

| # | Service | Responsibility | API endpoints | Database | Ports |
|---|---|---|---|---|---|
| 3.1 | **Risk Scorer** | Core scoring: validate → circuit breaker → rules fast-path → feature fetch → model inference → SHAP-like explain → async audit | `POST /v1/risk/score` (sync, <100ms p99), `GET /v1/risk/health`, `GET /v1/risk/metrics` | Stateless (model in memory) | 8000 |
| 3.2 | **Rules Engine** | Deterministic sub-ms rule eval. **Written in Go or Rust for speed, Python fallback acceptable for hackathon.** YAML rule schema with operator/clauses/action/merchant_scope | `POST/GET/PUT/DELETE /v1/rules...`, `POST /v1/rules/evaluate` | PostgreSQL (small relational) | 8001 |
| 3.3 | **Feature Store** | Virtual feature store. Online (Redis <5ms) + Offline (PostgreSQL+Parquet point-in-time correct) + Registry (PostgreSQL or Feast) | `GET /v1/features/{type}/{id}`, `POST /v1/features/batch`, `POST /v1/features/backfill`, `GET /v1/features/registry` | Redis + PostgreSQL + Parquet | 8002 |
| 3.4 | **Model Registry & MLOps** | Versioning, champion/challenger, A/B, drift detection (PSI > 0.25 alert) | `GET /v1/models/current`, `POST /v1/models/{version}/promote`, `GET /v1/models/{version}/drift`, `POST /v1/models/drift/check` | SQLite/PostgreSQL | 8003 |
| 3.5 | **Audit** | Tamper-evident append-only hash chain. Court-admissible | `POST /v1/audit/log`, `GET /v1/audit/{id}` (with integrity check), `GET /v1/audit/search`, `GET /v1/audit/export` | PostgreSQL + Parquet→MinIO/S3 | 8004 |
| 3.6 | **Case Management** | Human-in-the-loop review queue for REVIEW decisions | `GET /v1/cases`, `POST /v1/cases/{id}/resolve`, `POST /v1/cases/{id}/escalate`, `GET /v1/cases/metrics` | PostgreSQL | 8005 |
| 3.7 | **Merchant** | Multi-tenancy: API keys, rate limits per tier (FREE/STARTUP/GROWTH/ENTERPRISE), custom rules, per-merchant thresholds | `POST /v1/merchants`, `GET /v1/merchants/{id}`, `PUT /v1/merchants/{id}/thresholds`, `GET /v1/merchants/{id}/usage` | PostgreSQL | 8006 |
| 3.8 | **Notification** | Webhook/email/SMS to merchants | `POST /v1/notify/webhook`, `POST /v1/notify/email`, `GET /v1/notify/delivery/{id}` | PostgreSQL (implied) | 8007 |
| 3.9 | **Threshold Manager** | Dynamic per-merchant threshold optimization. **Suggests optimal threshold based on cost model (FP vs FN cost).** Default ACCEPT<0.15, REVIEW 0.15-0.60, REJECT>0.60 | (no explicit endpoints — operates via Merchant thresholds API) | PostgreSQL | 8008 |
| 3.10 | **Compliance Export** | RBI/PCI DSS reports + model cards | `GET /v1/compliance/audit-export`, `GET /v1/compliance/model-card/{version}`, `GET /v1/compliance/drift-report` | (reads from Audit + Model Registry) | 8009 |
| (§4) | **Agent Gateway** (Phase-2 plugin, NOT in the 10) | Bounded agent: 5-action allowlist (risk.score, risk.explain, case.create, notify.merchant, rules.suggest). Action allowlist hardcoded; agent has no DB access; high-cost actions require approval queue | (no explicit endpoints listed) | (none) | 8010 |

Plus §9.1 lists **infra services** (not counted among the 10): nginx, postgres, redis, kafka (or redis streams), prometheus, grafana, jaeger, frontend (nginx serving React) = 8 more, total = 18 services in `docker-compose up` (RFC says "12" — typo).

---

## 3. Specific tech choices committed in prompt-razor.txt

| Layer | Choice | Lines |
|---|---|---|
| Rules engine language | **Go or Rust** for speed (Python fallback for hackathon) | 1256 |
| Message bus | **Apache Kafka OR Redis Streams** (RabbitMQ also drawn); 5 topics: risk.scores, audit.records, cases.created, model.drift, notifications | 1505-1513, 1195-1197 |
| Feature store | **Redis (online <5ms) + PostgreSQL+Parquet (offline) + Feast (registry)** — V2 §5.1 line 1499 says "PostgreSQL (offline registry)" not Feast, but §2 diagram line 1170 says "PostgreSQL + Feast". **Internal inconsistency: one place says Feast, the other doesn't.** RESOLUTION: use Feast for registry only (per `04-TECH-STACK-DECISIONS.md`) | 99-105, 1166-1172, 1499 |
| Audit storage | **PostgreSQL (transactional) + daily Parquet rotation to MinIO/S3** | 1379, 1500 |
| ML serving | **TensorFlow Serving** (multi-tenant, dedicated threadpool size 1-2, specialized lazy protobuf parser) — but V3 rejects this as overkill for hackathon; keep in-process HistGB | 33, 624-660 |
| ML registry | **MLflow** (model versioning, Staging→Production, artifact store to MinIO/S3) — V3 rejects MLflow-server as overkill; implement lightweight Postgres-backed TFX-style canary gate | 1175-1180, 1837 |
| Drift detection | **Evidently** (PSI, KS-test, data quality, HTML reports; PSI > 0.25 alert threshold) | 1178, 1344, 1838 |
| Observability | Prometheus + Grafana + **Jaeger** (tracing) + **ELK or Loki** (logs) + **OpenTelemetry** (instrumentation) + **AlertManager** (PagerDuty) | 1204-1210, 1572-1598 |
| Frontend | React + **Vite** + **Tailwind** + **Recharts** — but user's latest directive is Next.js; resolve in favor of Next.js 16 (per `04-TECH-STACK-DECISIONS.md`) | 1216 |
| API Gateway | **Kong + Nginx** with rate limiting (per-merchant-tier: 100/500/2000 req/min), JWT+API-key dual auth, WAF SQLi/XSS, request-id propagation | 80-85, 1127-1135, 1839 |
| Secrets | **HashiCorp Vault or Docker Secrets** (hackathon) | 1523 |
| Auth | **JWT RS256 5-min expiry** at gateway, **mTLS** service-to-service, dedicated DB user per service, **bcrypt** API keys | 1520-1529 |
| Data protection | **LUKS** PostgreSQL at rest, Redis ACL+TLS, **TLS 1.3** in transit, **SHA-256** PII hashing in audit | 1526-1529 |
| Compliance posture | **RBI PA** data localization (India region only), **PCI DSS** tokenization, **GDPR-style** right-to-explanation + right-to-deletion | 1531-1534 |

---

## 4. 5 papers cited for `docs/RESEARCH.md` (RFC §2, lines 1863-1875)

**IMPORTANT**: These 5 papers are NOT in the 40-paper KB. They are blog/industry citations for the **executive pitch deck**, not engineering depth.

1. **"E-Commerce Fraud Detection Based on Machine Learning Techniques: Systematic Literature Review"** — Big Data Mining and Analytics, 2024 — 170+ fraud papers; ensembles + SMOTE/ADASYN + feature engineering > algorithm choice → cite in Methodology section
2. **"Modeling and Optimization of Deep and Machine Learning Methods for Credit Card Fraud Risk Management"** — Mathematics, 2026 — `τ* = C_FP / (C_FP + C_FN)` cost-sensitive threshold rule → cite in Threshold Manager section
3. **"Building Trust in Agentic Commerce"** — Liminal, 2025 — 3-pillar trust framework (Authentication, Authorization, Verification) → cite in Agent Gateway pitch
4. **"COD Fraud in Indian E-commerce"** — Pragma, 2025 — selective OTP reduces fraud 78-84% at 4-7% conversion cost; velocity controls block 89-93%; address validation prevents 42-48% → cite for REVIEW→OTP intervention business case
5. **"AI Agent Risks & Guardrails: 2026 Enterprise Security Guide"** — Atlan, 2026 — 5-layer guardrail stack; Gartner predicts 40% of CIOs demand guardian agents by 2028 → cite in security architecture

---

## 5. 3 datasets named (RFC §4, lines 1894-1912)

1. **"Amazon India Sales Report"** — Kaggle (user-uploaded, public), ~129,000 orders. Columns: Status (Delivered/Cancelled/Returned), Amount, Category, Qty, ship-city, ship-state, B2B flag. Direct mapping: `Status=Returned`→`is_returned=1`. Drop-in compatible. **Expected lift: PR-AUC 0.55 → 0.72-0.78. This is the primary dataset upgrade path.**
2. **"Indian E-commerce Dataset"** — Kaggle, ~50,000 orders, has explicit COD flag + return status, smaller but cleaner
3. **"Online Retail Dataset"** — UCI ML Repo / Kaggle, ~541,000 transactions (UK-based), no RTO labels but good for RFM feature engineering patterns

Plus geocoding dataset layer (§5, lines 1914-1936): India Post pincode directory (free, official, for pincode existence validation), Here Technologies POI data (12 amenity types × 9 radii = 108 features compressed via robust autoencoders to 12 — this comes from the prescriptive-analytics paper).

---

## 6. Milestones beyond Aug 28-29

**No additional explicit deadlines** beyond Aug 28-29 internal. But there are nested milestones inside prompt-razor.txt:

- **5-Day Build Plan** (lines 138-191): Day 1 infra skeleton, Day 2 frontend, Day 3 backend hardening, Day 4 model+monitoring, Day 5 polish/docs/video
- **10-row Deliverables Checklist** (lines 1692-1707) with day-tagged Status Targets: Day 1 docker-compose, Day 2 ARCHITECTURE.md + API_SPEC.md + pytest, Day 3 integration tests, Day 5 GitHub repo + README + PITCH_SCRIPT + 5-min video + live demo URL
- **~16 hours total agent integration time** estimated (line 2088): repos 30min, papers 1h, Kaggle data 2h, geocoding 1h, k6 2h, CI/CD 3h, IaC 4h, cost model 2h

---

## 7. Contradictions with V3 (V3 is AUTHORITATIVE — V2 is historical)

V3 (`docs/ARCHITECTURE_V3.md`, 558 lines, DRAFT-FOR-REVIEW) explicitly supersedes V2 (prompt-razor.txt RFC v2.0). Treat V3 as truth; V2 is historical context.

| # | V2 (prompt-razor) says | V3 says | Resolution |
|---|---|---|---|
| 1 | "10 services we specified" + docker-compose lists 16 | 10 backend services + 8 infra = 18 total. The "12 services" in §9.1 is a typo. | 10 backend services confirmed; 18 total in compose |
| 2 | Kafka/ClickHouse/Feast/MLflow-server/Hyperledger-Aries/E2B | V3 §audit rejects all as "misprescriptions" (cargo-cult boxes) | Use Redis Streams (not Kafka), Postgres (not ClickHouse), Feast for registry only (not Feast-server), lightweight Postgres-backed registry (not MLflow-server), Merkle chain (not Hyperledger), allowlist-API agent (not E2B sandbox) |
| 3 | Patent numbers US20240012345A1, US20230187654B2, WO2024/098765A1 | V3 §21 says these are `SUSPECT-FABRICATED` | **DO NOT CITE THESE in the pitch deck.** Replace with citations from 40-paper KB (which has DOIs for all real papers) |
| 4 | listmonk for notifications (AGPL-3.0) | V3 audit flags AGPL vs Apache 2.0 conflict | Replace listmonk with permissive-license alternative (nodemailer + custom templates, or Postfix+MailHog for dev) |
| 5 | Feature store: §2 diagram says "PostgreSQL + Feast", §3.3 says "PostgreSQL", §5.1 doesn't mention Feast | Internal inconsistency in V2 | RESOLUTION: use Feast for registry layer only (per `04-TECH-STACK-DECISIONS.md`) |
| 6 | Track 02 or 05 (undecided) | V3 §22.1 still open | User confirmed Track 02 in chat. Lock it. |
| 7 | 5 Missions (in user's chat paste) | Not in prompt-razor.txt | The 5 Missions are a V3 framing or a synthesis layer Neeraj added. Not in V2. |
| 8 | 5-Day Build Plan (Day 1-5) | Not in V3; V3 has 12 code deltas (CD-1…CD-12) | Use V3's CD-1…CD-12 as the authoritative work list. The 5-Day Build Plan is for context. |

---

## 8. Bonus findings from prompt-razor.txt

- **The BoundedAgent class** (lines 1003-1050) is the actual agent skeleton code with `ALLOWED_ACTIONS = {score_order: cost 0, request_otp: cost 1, flag_review: cost 2, block_order: cost 10 + requires_approval}`. This is what Day 1 Track D extends with UPI Circle actions.
- **§12 Competitive Moat** (lines 1709-1718): "RTO Shield is pincode-level and opaque. We're address-level, explainable, and give merchants control via the rules engine." Use this verbatim in the pitch.
- **§11 Deliverables Checklist** (lines 1692-1707): 10-row table — GitHub repo, README, ARCHITECTURE.md, API_SPEC.md, PITCH_SCRIPT.md, Docker Compose, 5-min pitch video, Live demo URL, pytest, integration tests. This is the submission checklist.
- **Missing Assets Brief §1-9** (lines 1826-2090): the original command-folder spec — 9 numbered subsections that this very command folder (`/home/z/my-project/command/`) is now fulfilling.

---

*Last updated: Aug 27, 2026. Source: agent 2-knowledge synthesis of prompt-razor.txt (2102 lines).*
