# RTO Trust Layer — Work Items Tracker
## All 43 items (24 broken + 3 gap drivers + 16 Tier 2) with status

> Status: ⏳ pending / 🔄 in progress / ✅ done / ❌ cut (per triage rules in `01-EXECUTION-SEQUENCE.md`)
> Track IDs reference the day-by-day plan in `01-EXECUTION-SEQUENCE.md`.

---

## A. The 24 broken / stubbed / decorative items (from agents 1-b + 1-c)

| # | Item | File:line | Status | Track |
|---|---|---|---|---|
| 1 | Cost optimizer NOT wired into decision (uses static `0.15/0.60`) | `routes.py:36,194` vs `cost_optimizer.py` | ⏳ | C (Day 1) |
| 2 | Idempotency cache `state["idem"]` unbounded dict (memory leak) | `routes.py` | ⏳ | E (Day 2) |
| 3 | `add_geo_features` dead code (never called from lifespan) | `features/enrich.py:27` | ⏳ | B (Day 1) |
| 4 | `register_model` dead in prod (only called from tests; champion always None) | `ml/registry.py` | ⏳ | E (Day 2) |
| 5 | `_latest()` always returns None (placeholder stub) | `cases/service.py:50-64` | ⏳ | B (Day 1) |
| 6 | `shap>=0.52` in requirements, never imported (dead dep ~30MB) | `requirements.txt` | ⏳ | B (Day 1) |
| 7 | `--profile full` starts postgres+redis API never connects to ("decorative infra") | `docker-compose.yml` | ⏳ | E (Day 2) |
| 8 | Grafana provisioning mount path wrong (`dashboards-src` vs `dashboards`) | `docker-compose.yml` | ⏳ | B (Day 1) |
| 9 | `verify.sh` hardcodes `/mnt/20265E15265DEC72/...` venv path | `verify.sh` | ⏳ | B (Day 1) |
| 10 | `uv.lock` is 3-line stub — `uv lock` never run | `uv.lock` | ⏳ | B (Day 1, user runs on laptop) |
| 11 | `pyproject.toml` has no `[project]` table — package not declared | `pyproject.toml` | ⏳ | B (Day 1) |
| 12 | No CI workflow file despite TSV claiming "CI workflow" | `.github/` missing | ⏳ | J (Day 3) |
| 13 | `API_SPEC.md` bare (16 path names + auth table, no schemas/examples) | `docs/API_SPEC.md` | ⏳ | K (Day 3) |
| 14 | `openapi.json` has zero `example` fields anywhere | `docs/openapi.json` | ⏳ | K (Day 3) |
| 15 | V3 endpoints not in openapi.json: `/v1/audit/{id}/proof`, `/v1/simulate`, `/v1/usage`, outcome-ingest | `docs/openapi.json` vs V3 | ⏳ | H (Day 2) |
| 16 | Override single-admin vs V3 §12.1 dual-control contradiction | `routes.py:463` vs V3 §12.1 | ⏳ | H (Day 2) |
| 17 | No DB / no migrations — audit, cases, registry, idempotency, PSI reference all JSONL/CSV files | across `src/` | ⏳ | E (Day 2) |
| 18 | No streaming / message bus — Redis declared but unused; synchronous request/response only; **no feedback loop for `is_returned` ground truth** | across `src/` | ⏳ | F + G (Day 2) |
| 19 | No OpenTelemetry traces, no structured logging, no alerting rules | `monitoring/` | ⏳ | M (Day 4, cut if time short) |
| 20 | Dockerfile bakes ENV defaults (`change-me-scorer`) visible via `docker history` | `Dockerfile` | ⏳ | B (Day 1) |
| 21 | nginx no TLS, no security headers (CSP/HSTS/XFO), no gzip | `nginx/nginx.conf` | ⏳ | B (Day 1) |
| 22 | `dashboard/index.html` vanilla JS, cost bars **hardcoded** in JS array, `score-demo-key`/`admin-demo-key` visible as defaults | `dashboard/index.html` | ⏳ | C (Day 1) + I (Day 3) |
| 23 | `is_cod` 0.18 permutation importance is near-tautological (whole problem is COD RTO) | `docs/feature_importance.md` | ⏳ | Reframe in docs (Day 3 K) |
| 24 | Synthetic dataset only (7,235 CODScore rows); real Indian labeled data needs Kaggle credentials | `README.md`, `scripts/ingest_kaggle.py` | ⏳ | L (Day 4, user provides Kaggle data) |

---

## B. The 3 perceived-gap drivers vs Microsoft Fabric

| # | Gap | Status | Track |
|---|---|---|---|
| G1 | One static HTML dashboard vs Microsoft's 3 surfaces (Real-Time Dashboard + Power BI + Copilot) | ⏳ | I (Day 3) |
| G2 | REST-only, no event/streaming backbone (Microsoft has Eventstreams → Eventhouse → Activator) | ⏳ | F (Day 2) |
| G3 | No DB / no migrations / no feedback loop (Microsoft has Eventhouse + OneLake + Activator with SLA) | ⏳ | E + G (Day 2) |

---

## C. The 16 Tier 2 items (all approved by user — "DO IT")

| # | Item | Status | Track | Overlap with §A |
|---|---|---|---|---|
| T1 | Split dashboard into 3 surfaces (live ops console + reporting + Copilot Q&A) | ⏳ | I (Day 3) | = G1 |
| T2 | Wire cost optimizer into actual decision | ⏳ | C (Day 1) | = A1 |
| T3 | Fix the 6 decorative bugs (cost optimizer, idempotency, add_geo, register_model, _latest, shap) | ⏳ | B + C + E | = A1, A2, A3, A4, A5, A6 |
| T4 | Add real streaming path (Redis Streams per V3 §9.3) | ⏳ | F (Day 2) | = G2 |
| T5 | Add real DB + migrations (Postgres + Alembic) | ⏳ | E (Day 2) | = A17, G3 (partial) |
| T6 | Add feedback loop (label ingestion + drift + calibration) | ⏳ | G (Day 2) | = A18, G3 (partial) |
| T7 | Add CI workflow (`.github/workflows/`) | ⏳ | J (Day 3) | = A12 |
| T8 | Fix infra theater items (verify.sh, grafana, uv.lock, pyproject, Dockerfile, nginx, dead shap) | ⏳ | B (Day 1) | = A6, A8, A9, A10, A11, A20, A21 |
| T9 | Add OpenAPI examples + API_SPEC schemas | ⏳ | K (Day 3) | = A13, A14 |
| T10 | Add V3-specified missing endpoints (`/v1/audit/{id}/proof`, `/v1/simulate`, `/v1/usage`, outcome-ingest) | ⏳ | H (Day 2) | = A15 |
| T11 | Add mandate action-class expansion (V3 §13) — the differentiator | ⏳ | D (Day 1) | (new, gap #2 in paper-skills map) |
| T12 | Add OpenTelemetry tracing + structured logging + alerting rules | ⏳ | M (Day 4, cut if time short) | = A19 |
| T13 | Add IaC (OpenTofu) | ⏳ | M (Day 4, cut if time short) | (new) |
| T14 | Multi-source ingest simulators (4 channels) | ⏳ | M (Day 4, cut if time short) | (new, gap #10 in paper-skills map) |
| T15 | TLS + security headers in nginx | ⏳ | B (Day 1) | = A21 |
| T16 | Real `uv.lock` + `pyproject [project]` + dev/runtime dep split | ⏳ | B (Day 1) | = A10, A11 |

---

## D. The 14 paper-skills gaps (from agent 2-knowledge, see `05-PAPER-SKILLS-MAP.md`)

| # | Code gap | Paper | Status | Track |
|---|---|---|---|---|
| P1 | Cost optimizer wiring | Bahnsen 2013 | ⏳ | C (Day 1) |
| P2 | Mandate action-class expansion | NPCI OC-201B + UPI Lexology | ⏳ | D (Day 1) |
| P3 | Feedback loop missing | Gama 2014 | ⏳ | G (Day 2) |
| P4 | Concept drift detection (PSI not enough) | Gama 2014 | ⏳ | G (Day 2) |
| P5 | ML registry dead in prod | Baylor TFX 2017 | ⏳ | E + H (Day 2) |
| P6 | Feature store absent | Kandula 2021 + TFX | ⏳ | E + F (Day 2) |
| P7 | Streaming transformations absent | TFX + MLOps-DevOps | ⏳ | F (Day 2) |
| P8 | Declarative rule routing absent | TFX + Paleyes 2022 | ⏳ | H (Day 2) |
| P9 | Case management stub | Paleyes 2022 | ⏳ | E + H (Day 2) |
| P10 | Multi-channel ingest absent | Kandula 2021 + TFX | ⏳ | M (Day 4, cut if short) |
| P11 | Tamper-evident audit incomplete (Merkle intervals) | SoK Mao 2026 | ⏳ | H (Day 2) |
| P12 | Model interpretability (LOO → SHAP) | Hu 2025 | ⏳ | Day 3 retraining |
| P13 | Cost-sensitive threshold sweep (Drummond-Holte cost curves) | Drummond & Holte 2006 | ⏳ | C (Day 1) |
| P14 | Production ML deployment patterns (no CI/CD, no canary) | TFX + Paleyes + MLOps-DevOps | ⏳ | J (Day 3) |

---

## Triage rules (if time runs short — cut in this order)

1. ❌ T14 Multi-source ingest simulators (Track M) — nice for Microsoft parity but not core
2. ❌ T13 IaC (Track M) — V3: "an unapplied partial IaC is worse than a precise spec"
3. ❌ P10 + Day 4 Track N full V3 §11.6 5-way intervention — keep 3-way (Track C Day 1)
4. ❌ T12 OpenTelemetry (Track M) — Prometheus is enough for demo
5. ❌ Copilot NL Q&A panel (Track I optional) — nice but not core
6. ❌ Multi-tenant Merchant service — not in V3's 12 code deltas

**Never cut**: the 6 judge demo moments (00-MASTER-PLAN §3), the audit hash chain, the mandates, the cost optimizer wiring, the real Kaggle data, the README, the pitch script.

---

*Last updated: Aug 27, 2026. Maintained by: Z.ai Code orchestrator. Update this file as items move from ⏳ → 🔄 → ✅.*
