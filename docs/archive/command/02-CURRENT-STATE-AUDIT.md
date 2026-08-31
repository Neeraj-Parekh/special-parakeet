# RTO Trust Layer — Current State Audit
## What exists today (from 4 parallel reader agents)

> Source: agents 1-a (docs), 1-b (code), 1-c (infra), 1-d (Microsoft ref). Full reports in `/home/z/my-project/worklog.md`.

---

## Project framing (confirmed)

**RTO Trust Layer** — for Razorpay AI Buildathon Track 02 (AI Risk Manager). RTO = Return-to-Origin in Indian e-commerce (COD orders refused at door, costing reverse-logistics + loss). Address-level COD return-risk scorer returning `ACCEPT / REVIEW / REJECT` with per-prediction reason codes + tamper-evident audit trail. Customer = Razorpay judges.

---

## What's genuinely strong (preserve — don't lose in the polish)

| Strength | Where | Why it matters |
|---|---|---|
| Audit hash chain (SHA-256, byte-offset index, verify_chain) | `src/audit/logger.py` | Tamper-evident, tested by `test_ship.py` (tampers record #1, confirms chain breaks). Production quality. |
| HMAC mandates with VALID/TAMPERED/BREACH/EXPIRED verdicts | `src/api/mandates.py` | UPI Circle / delegated-payments angle from papers. Real, not stubbed. Differentiator vs Microsoft. |
| group_split with GroupShuffleSplit on CustomerID + group_leakage() assertion = 0 | `src/models/splitting.py` | Leakage-aware — better than typical hackathon code. |
| Circuit breaker with degraded-mode rules-only fallback | `src/api/breaker.py` | Fail-safe: on model failure, returns REVIEW with `degraded=True`. |
| Cost-table + cost-optimizer math (BMR per-amount FN cost) | `docs/cost_table.md` + `src/business/cost_optimizer.py` | Cites Bahnsen ICMLA 2013. Mathematically correct. |
| 5 real pytest files (~526 LOC) + real k6 load profile | `tests/` | 3 scenarios, p99<400ms threshold. Real tests, not stubs. |
| V3 architecture register (19-finding self-audit) | `docs/ARCHITECTURE_V3.md` | Append-only decisions, revisit triggers. More rigorous than Microsoft's marketing page. |

---

## What's actually broken / stubbed / decorative (the 24 items)

See `03-WORK-ITEMS.md` §A for the full 24-item table with file:line + assigned track. Highlights:

**Code logic gaps** (agent 1-b):
- Cost optimizer NOT wired into decision (`routes.py:36,194` uses static `0.15/0.60`; `cost_optimizer.py` only stored as `policy_hint`)
- Idempotency cache `state["idem"]` unbounded dict (memory leak)
- `add_geo_features` dead code (`features/enrich.py:27` — never called from lifespan)
- `register_model` dead in prod (only called from tests; champion always None; model-card hardcodes "dev")
- `_latest()` always returns None (`cases/service.py:50-64` — placeholder stub)
- `shap` in requirements, never imported (dead dep, ~30MB)
- No ASGI entrypoint in `src/` (only TestClient + scripts call `create_app`)
- No middleware wired (no CORS, TrustedHost, GZip, OTel)
- Override single-admin vs V3 §12.1 dual-control contradiction

**Infra theater** (agent 1-c):
- `--profile full` starts postgres+redis the API never connects to (V3 finding A2 "decorative infra")
- Grafana provisioning mount path wrong (`dashboards-src` vs expected `dashboards`) — dashboard won't auto-load
- `verify.sh` hardcodes `/mnt/20265E15265DEC72/study/CODE/linux_venv/bin/python`
- `uv.lock` is a 3-line stub — `uv lock` never run
- `pyproject.toml` has no `[project]` table — package not declared
- No CI workflow file despite `autoresearch-results.tsv` claiming it
- Dockerfile bakes ENV defaults (`change-me-scorer`) visible via `docker history`
- nginx no TLS, no security headers (CSP/HSTS/XFO/XCTO), no gzip
- `dashboard/index.html` cost-threshold bars are **hardcoded** in JS array, not fetched from `/v1/policy/optimal`; `score-demo-key`/`admin-demo-key` visible as default values in input fields

**Docs/pitch gaps** (agent 1-a):
- `API_SPEC.md` is bare (16 path names + auth table, no schemas/examples); `openapi.json` has zero `example` fields anywhere
- V3 specifies endpoints not in openapi.json: `/v1/audit/{id}/proof`, `/v1/simulate`, `/v1/usage`, outcome-ingest
- `is_cod` 0.18 permutation importance is near-tautological (whole problem is COD RTO)
- Single-author pitch ("I'm Neeraj"); Track 02 vs 05 was undecided (now locked: 02)
- Synthetic dataset only (7,235 CODScore rows); real Indian labeled data needs Kaggle credentials

---

## The 3 perceived-gap drivers vs Microsoft Fabric

| # | Gap | Microsoft has | User has today | How to close |
|---|---|---|---|---|
| 1 | Dashboard surfaces | Real-Time Dashboard (DirectQuery, sub-second) + Power BI (BI/reporting) + Copilot (NL Q&A) = 3 surfaces | ONE static `dashboard/index.html` (vanilla JS, no framework, hardcoded cost bars) | Day 3 Track I: Next.js dashboard with 4 pages + WebSocket auto-refresh + Copilot Q&A panel |
| 2 | Streaming backbone | Eventstreams → Eventhouse → Activator (declarative rule routing with auto-block/notify/investigate) | REST-only, synchronous request/response. Redis declared but unused. | Day 2 Track F: Redis Streams + producer/consumer + 5 topics + stream-processor worker |
| 3 | DB / migrations / feedback loop | Eventhouse (hot) + OneLake (cold) + Activator (case mgmt with SLA) | No DB / no migrations — audit, cases, registry, idempotency, PSI reference all JSONL/CSV files. No feedback loop for `is_returned` ground truth. | Day 2 Track E + G: Postgres + Alembic + LabelFeedbackService with DDM/ADWIN |

---

## Code quality summary

- **22 .py files in src/** (~1,418 LOC) + **5 pytest files** (~526 LOC) + **1 k6 load profile** (61 LOC)
- **FastAPI**, Python 3.12, `create_app()` factory pattern
- **No ASGI entrypoint in src/** — `create_app()` only called from tests + scripts. Production launch needs `uvicorn src.api.routes:create_app --factory` or a `main.py`.
- **No middleware wired** (no CORS, no TrustedHost, no GZip, no OTel middleware, no exception_handlers)
- **Types**: Python 3.12 syntax (`X | None`, `list[dict]`), no mypy/pyright config (only `[tool.ruff]` in pyproject.toml)
- **Zero TODO/FIXME in src/** — one placeholder string in `cases/service.py:64`
- **Dead code**: `_latest()` in cases/service.py, `add_geo_features` in features/enrich.py, `shap` in requirements, `encode_categoricals` in splitting.py
- **Inconsistent `__init__.py`**: present in src/, src/ml/, src/cases/, src/features/, src/models/ (all empty), **missing** in src/api/, src/audit/, src/business/, src/rules/

---

## Infra summary

- **docker-compose.yml**: `api` (build: `.`) + `--profile full`: nginx, redis (UNUSED), postgres (no schema/migrations), prometheus, grafana (wrong mount path)
- **Dockerfile**: single-stage `python:3.12-slim`, ENV defaults baked into image layers, HEALTHCHECK pings /health, `CMD uvicorn src.api.routes:create_app --factory`
- **nginx.conf**: `limit_req_zone` 25r/s, no TLS, no security headers, no gzip, /metrics CIDR-gated
- **monitoring/prometheus.yml**: 1 job `rto-api`, scrape_interval 15s. No alerting rules.
- **monitoring/grafana/rto-dashboard.json**: 4 panels (decisions/min, circuit breaker, degraded share, scoring latency p50 approx). No alerts.
- **pyproject.toml**: only `[tool.ruff]` config. No `[project]` table.
- **requirements.txt**: 9 deps, `>=` everywhere, no dev/runtime split, `shap` dead.
- **uv.lock**: 3-line stub. `uv lock` never run.
- **verify.sh**: hardcodes author's venv path. No CI workflow file.
- **openapi.json**: 16 paths, OpenAPI 3.1.0, auto-generated from FastAPI. Zero `example` fields. Matches v0.4 "platform-complete" per `autoresearch-results.tsv` iteration 6.

---

## Where the user is AHEAD of Microsoft Fabric

| Area | User | Microsoft |
|---|---|---|
| Cost-model math | Explicit BMR per-amount FN cost (`cost_table.md` + `cost_optimizer.py`) | Abstract "cost optimization" section |
| Audit chain rigor | SHA-256 hash chain + V3 prescribes Merkle intervals + outbox | "Immutable audit trails" (abstract) |
| Concrete observability stack | Real Prometheus + Grafana with 4 panels | Abstract (Fabric is managed) |
| Architecture register discipline | V3 with 19-finding self-audit, append-only decisions, revisit triggers | Nothing this rigorous |

The "mid at best" feeling is NOT a code-quality problem — it's a **presentation + streaming** gap. Both fixable in ~1 sprint.

---

*Last updated: Aug 27, 2026. Source: agents 1-a, 1-b, 1-c, 1-d syntheses.*
