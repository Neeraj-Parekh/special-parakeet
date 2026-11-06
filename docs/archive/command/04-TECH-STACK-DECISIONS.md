# RTO Trust Layer — Tech Stack Decisions
## Resolved authoritative stack (V3 supersedes V2)

> **V3 (`docs/ARCHITECTURE_V3.md`, 558 lines) is AUTHORITATIVE.** V2 (`prompt-razor.txt` RFC v2.0, 2102 lines) is historical context. This file resolves all contradictions.

---

## Resolved stack

| Layer | Choice | Rationale | V2/V3 conflict resolved |
|---|---|---|---|
| **Backend language** | Python 3.12 (keep) | Existing code is Python; rewrite is out of scope for 3-day sprint | V2 said "Rules Engine in Go or Rust" — V3 rejected rewrite. Keep Python, document as "could be Go for <5ms latency at scale". |
| **Web framework** | FastAPI (keep) | Already wired, tests pass, OpenAPI auto-gen | No conflict. |
| **ASGI server** | uvicorn (keep) | Already in Dockerfile CMD | No conflict. |
| **DB** | Postgres 15 + Alembic migrations | Replaces JSONL files for audit/cases/registry/idempotency/PSI. ACID + migrations + real queries. | V2 said "PostgreSQL + ClickHouse". V3 rejected ClickHouse as cargo-cult (no named query). Postgres only. |
| **Message bus** | Redis Streams now (per V3 §9.3), NATS/Kafka later | V3 explicitly rejected Kafka as cargo-cult. Redis Streams is sufficient for hackathon demo + has upgrade path. | V2 said "Kafka or Redis Streams". V3 §audit rejected Kafka. Use Redis Streams. |
| **Feature store** | Redis (online) + Postgres+Parquet (offline) + Feast (registry only) | Online/offline parity + Feast for registry layer | V2 internal inconsistency: §2 diagram said "PostgreSQL + Feast", §3.3 said "PostgreSQL", §5.1 didn't mention Feast. RESOLUTION: Feast for registry layer only (not Feast-server). |
| **ML serving** | in-process HistGB (keep), wrapped by Model Registry | V3 rejected TensorFlow Serving + MLflow-server as overkill for hackathon | V2 said "TensorFlow Serving". V3 rejected. Keep in-process. |
| **ML registry** | lightweight Postgres-backed TFX-style canary gate | V3 explicitly rejected MLflow-server as "fork instead of pip-install" cargo-cult | V2 said "MLflow". V3 §audit rejected MLflow-server. Implement lightweight Postgres-backed registry with TFX-style canary gate (champion/challenger + slice metrics + warm-start). |
| **Drift detection** | PSI (existing) + DDM + ADWIN (per Gama 2014 paper) | PSI for batch distribution, DDM for online error stream (O(1) memory), ADWIN for change-point localization (Hoeffding bound) | V2 said "Evidently". V3 didn't object. Keep Evidently for HTML reports if useful, but DDM+ADWIN are the production detectors (per Gama 2014 paper, gap #4 in paper-skills map). |
| **Explainability** | SHAP KernelExplainer (replaces LOO) | TreeExplainer doesn't support HistGB per prompt-razor line 1737. KernelExplainer works on any model. | V2 said "SHAP". Current code uses LOO + permutation (shap is dead dep). Switch to SHAP KernelExplainer or hybrid-multistage paper's perturbation-based explainer. |
| **Observability** | Prometheus + Grafana (keep) + OpenTelemetry + Jaeger (add) + AlertManager (add) | Microsoft parity. Real stack, shippable. | V2 said "Prometheus + Grafana + Jaeger + ELK/Loki + OpenTelemetry + AlertManager". V3 didn't reject. Keep Prometheus+Grafana, add OTel+Jaeger+AlertManager if time (Day 4 Track M, cut if short). |
| **Frontend** | Next.js 16 + TypeScript + Tailwind + shadcn/ui (NEW) | Replaces vanilla JS dashboard. Stripe-like dark mode. User directive. | V2 said "React + Vite + Tailwind + Recharts". User's latest directive: Next.js. RESOLUTION: Next.js 16 (App Router) + shadcn/ui (per sandbox stack). |
| **Auth** | API keys (existing) + JWT RS256 (add per V2 §6) — keep simple for demo | Existing API keys work; JWT adds enterprise credibility | V2 said "JWT RS256 5-min expiry + mTLS + bcrypt + Vault". V3 didn't object but flagged as overkill for hackathon. RESOLUTION: API keys (keep) + JWT RS256 (add, simple). Defer mTLS/Vault to prod (document in `infra/`). |
| **Secrets** | ENV vars (existing) — for demo; document Vault/SOPS for prod | V3 explicitly refused half-deployed IaC | V2 said "HashiCorp Vault or Docker Secrets". V3 said no half-baked IaC. RESOLUTION: ENV vars for demo; document Vault/SOPS in `infra/README.md` for prod. |
| **IaC** | OpenTofu (V3 explicitly rejected Terraform BSL) | License reason (Terraform BSL vs OpenTofu MPL) | V2 said "OpenTofu over Terraform BSL" (lines 1839). V3 agreed. Use OpenTofu. Day 4 Track M, cut if time short. |
| **CI** | GitHub Actions (ruff + pytest + leakage gate + docker build + Trivy scan) + 7-stage TFX-style mlops.yml | Closes gap #14 (production ML patterns) | No conflict. V2 §10 implementation roadmap aligns. |
| **Container registry** | GHCR (GitHub Container Registry) | Free for public repos, integrated with GitHub Actions | V2 didn't specify. Use GHCR. |
| **Load testing** | k6 (existing `tests/load/risk_api_load.js`) | Already there, 3 scenarios, p99<400ms threshold | No conflict. Integrate into CI (Day 3 Track J). |
| **Reverse proxy** | nginx (keep) + add TLS stub + security headers + gzip | Existing nginx.conf is bare minimum | V2 §5 had nginx config. Day 1 Track B adds TLS stub + CSP/HSTS/XFO/XCTO + gzip. |
| **API Gateway (Kong)** | SKIP for hackathon | V3 rejected as overkill. nginx is enough. | V2 said "Kong + Nginx". V3 didn't explicitly reject Kong but V3's modular monolith doctrine implies skip. RESOLUTION: nginx only, document Kong for prod. |
| **Notifications** | nodemailer + custom templates (NOT listmonk) | V3 flagged listmonk AGPL-3.0 vs Apache 2.0 conflict | V2 said "listmonk (AGPL)". V3 §audit flagged AGPL contamination. RESOLUTION: nodemailer + custom templates, or Postfix+MailHog for dev. |
| **Data protection** | TLS 1.3 in transit (nginx), SHA-256 PII hashing in audit (existing) | LUKS at rest is prod-only, document in infra | V2 said "LUKS at rest, Redis ACL+TLS, TLS 1.3, SHA-256 PII". For hackathon: TLS 1.3 (nginx) + SHA-256 PII (audit, existing). Defer LUKS to prod. |
| **Compliance posture** | RBI PA data localization (document), PCI DSS tokenization (document), GDPR right-to-explanation (implement audit export) | Document for pitch; implement audit export + model card | V2 §6 lines 1531-1534. Implement: audit export (existing `/v1/compliance/audit-export`), model card (existing `/v1/compliance/model-card`). Document: RBI PA, PCI DSS, GDPR in `docs/ARCHITECTURE.md`. |
| **Package manager** | uv (resolve lockfile properly) | Existing `uv.lock` is 3-line stub. Run `uv lock` for real. | V2 used uv (uv.lock exists). Day 1 Track B: write script for user to run `uv lock` on laptop (can't run uv in this sandbox). |
| **Linter** | ruff (keep) | Existing `[tool.ruff]` in pyproject.toml | No conflict. |
| **Type checker** | mypy or pyright (add, optional) | V2 didn't specify. V3 didn't require. | Optional. Add `[tool.mypy]` to pyproject.toml if time permits. Cut if time short. |

---

## What we're NOT doing (and why)

| Item | Why cut |
|---|---|
| Go/Rust rules engine | V3 rejected rewrite. Python rules engine is fast enough for demo (<5ms is aspirational). |
| ClickHouse | V3: "no named query" — cargo-cult. Postgres handles all our query patterns. |
| Kafka | V3: cargo-cult. Redis Streams is sufficient + has upgrade path. |
| Feast-server (full) | V3: "fork instead of pip-install" cargo-cult. Use Feast for registry layer only (pip-installable). |
| MLflow-server | V3: overkill. Lightweight Postgres-backed TFX-style canary gate is enough. |
| TensorFlow Serving | V3: overkill. In-process HistGB is enough for demo. |
| Hyperledger Aries | V3: misprescribed. Merkle chain on Postgres is enough for tamper-evidence. |
| E2B sandbox | V3: misprescribed for allowlist-API agent. Allowlist + HMAC mandate is enough. |
| Kong API gateway | V3 modular monolith doctrine. nginx is enough. |
| listmonk (AGPL) | V3: AGPL-3.0 vs Apache 2.0 conflict. Use nodemailer. |
| LUKS at rest | Prod-only. Document in `infra/`. |
| mTLS service-to-service | Prod-only. Document in `infra/`. |
| HashiCorp Vault | Prod-only. Document in `infra/`. ENV vars for demo. |
| Multi-tenant Merchant service (full) | Not in V3's 12 code deltas. Defer to post-submission. |

---

## Versioning

- **V1** (`docs/ARCHITECTURE.md`): minimal mermaid, single-node, "what breaks at 10x" honesty. Historical.
- **V2** (`docs/ARCHITECTURE_V2.md` + `prompt-razor.txt` RFC v2.0): enterprise 9-service spec, 16-container compose. Spec only, mostly unimplemented. Historical.
- **V3** (`docs/ARCHITECTURE_V3.md`): modular monolith + registry + audit + cases + workers. 12 code deltas (CD-1…CD-12). **AUTHORITATIVE.** Supersedes V2 via 19 findings.
- **Day 3 Track K will consolidate** V1+V2+V3 into one current-truth `docs/ARCHITECTURE.md` with Mermaid + scaling analysis.

---

## Patent numbers (DO NOT CITE)

V3 §21 flags these as `SUSPECT-FABRICATED`:
- `US20240012345A1`
- `US20230187654B2`
- `WO2024/098765A1`

**DO NOT cite these in the pitch deck.** Replace with citations from the 40-paper KB (which has DOIs for all real papers) or the 5 pitch papers in `06-PROMPT-RAZOR-EXTRACTION.md` §4.

---

*Last updated: Aug 27, 2026. Source: V3 + agent 2-knowledge synthesis of V2/V3 conflicts.*
