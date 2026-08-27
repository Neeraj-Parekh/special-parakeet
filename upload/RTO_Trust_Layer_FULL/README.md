# RTO Trust Layer — Merchant-facing RTO risk command center

> A platform, not a model. Address-level COD return-risk scoring for Indian
> e-commerce, with explanations merchants can read, thresholds they can tune,
> an audit trail regulators can verify, and an agent layer that cannot spend
> money without a human co-sign.

| Decision | What it means | Demo order |
|---|---|---|
| **ACCEPT** | Ship normally. P(RTO) low enough that intervention cost > expected loss. | Prepaid repeat buyer, ₹1,200, complete address, tier-1 city. |
| **REVIEW** | Hold for cheap intervention (selective OTP, partial-COD, address check). | ₹12,400 COD, vague address, tier-3 city, new customer. |
| **REJECT** | Block outright. Either a rule fired, mandate breached, or expected loss exceeds block cost. | Customer with 3 prior returns, ₹50K COD, mandate cap exceeded. |

---

## The problem

Indian e-commerce loses roughly **₹50,000 Cr/yr** to COD returns. Up to
3 in 10 cash-on-delivery orders come back — courier both ways, refund
both ways, inventory tied up for weeks. Each failed delivery costs ~12x
what a verification call would have cost.

Razorpay's RTO Shield is **pincode-level and black-box**: a merchant
sees a binary flag at checkout but cannot see *why* an order was
flagged, cannot tune thresholds for their own category, and has no
audit trail to show a regulator or a CFO. And now AI agents are coming
— an agent with a wallet and no guardrails is a lawsuit waiting to
happen.

The RTO Trust Layer closes all three gaps: address-level scoring,
merchant-visible explanations, and a tamper-evident audit trail with
Merkle inclusion proofs — plus a bounded agent that physically cannot
self-approve a money-moving action.

---

## The solution — not a model, a platform

Six demo moments. Every one is shippable as a 30-second live clip.

| # | Demo moment | What the judge sees | What it proves |
|---|---|---|---|
| 1 | **Live Dashboard** | Dark-mode merchant console. Paste an order, click Score, get a decision + score + reason panel in <100ms. | You build products, not notebooks. |
| 2 | **Explainability** | "73% risk because: COD + ₹12,400 + new customer (PriorOrders=0) + vague address in tier-3 city." Top-5 ranked reason codes per prediction. | You understand black-box ML is useless in finance. |
| 3 | **Audit Trail** | Click any prediction ID → see the SHA-256 hash chain + the Merkle inclusion proof + the model version + the features used. CSV export for compliance. | You understand enterprise risk, not just data science. |
| 4 | **Rules Engine** | Toggle "Block COD > ₹50K from new customers." Re-score the same order. Instant REJECT. No redeploy. | You understand deterministic gates beat ML in known cases. |
| 5 | **Agent Console** | Type "Score order ORD-123." Agent responds. Type "Block order ORD-456." Agent says: *"I cannot perform this action. I have requested human approval."* Lands in the dual-control queue. | You understand unconstrained agents are dangerous. |
| 6 | **Model Health** | Grafana: PR-AUC = 0.55, PSI < 0.1, DDM STABLE, ADWIN STABLE, "Model v2.1 active since Aug 25." Live cost-curve explorer wired to `/v1/policy/cost-curves`. | You understand MLOps, not just model training. |

---

## Quick start

```bash
git clone <repo> && cd rto-trust-layer
docker compose up -d                  # api + postgres + redis + 3 workers (core stack)
open http://localhost:8000/dashboard/ # dark-mode merchant console
# paste an order, click Score, get a decision + reason panel + audit URL
```

Want the full stack with monitoring? `docker compose --profile full up -d`
adds nginx (TLS + security headers), Prometheus, Grafana (8-panel
auto-loaded dashboard). Developer docs at `http://localhost:8000/docs`
(Swagger UI, OpenAPI 3.1).

The Python API runs out of the box on Python 3.12 + `uvicorn
src.api.routes:create_app --factory --port 8000`. Tests: `./verify.sh`
(ruff + pytest + train/evaluate). Current status: **141 tests pass + 8
skipped (Postgres+Redis path; full suite w/ Docker services = 149)** (6
Postgres-path + 2 Redis-path; auto-run when `DATABASE_URL` /
`REDIS_URL` are set).

---

## Architecture

Full system design, component register, and scaling analysis in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). The 6-box view:

```
  Client (merchant SPA, agent, ops console)
        |
        v
  nginx  --- TLS 1.2/1.3, security headers, rate limit 25 r/s, /metrics CIDR-gated
        |
        v
  FastAPI modular monolith (src/api/routes.py)
   |   |   |   |   |   |
   |   |   |   |   |   +-- Audit service  (SHA-256 hash chain + Merkle intervals, RFC 6962)
   |   |   |   |   +------ Case mgmt       (REVIEW queue + dual-control override, V3 §12.1)
   |   |   |   +---------- Model registry  (champion/challenger, PSI, versioned artifacts)
   |   |   +-------------- Rules engine    (deterministic fast-path, admin-tunable via /v1/rules)
   |   +------------------ Cost optimizer  (Bahnsen BMR per-order argmin, ICMLA 2013)
   +---------------------- Feature builder  (order + address-quality; Kandula 2021 ladder)
        |                 |                  |
        v                 v                  v
  Postgres 15 + Alembic   Redis Streams      MinIO/S3 (model artifacts + parquet audit lake)
  (5 tables, dual-mode)   (5 streams + 3    (WORM audit archive, deferred to prod)
        |                 consumer groups)
        v                       |
  Prometheus + Grafana (8 panels, auto-loaded) + Jaeger (Day 4)
```

Decision precedence (the heart of the system):

1. **Rules** fast-path BLOCK → REJECT (no model call).
2. **Mandate** BREACH → REJECT. Mandate REVIEW (UPI Circle 24h cooling, OC-201B) → REVIEW.
3. Mandate TAMPERED/EXPIRED-with-header → REJECT.
4. **Circuit breaker** OPEN → degraded rules-only REVIEW (`degraded=true`, never fail-open).
5. **Cost-optimal BMR** `optimal_decision(p)` → ACCEPT/REVIEW/REJECT (primary path, Bahnsen 2013).
6. **Audit** hash-chain append + Merkle leaf insert (Postgres transaction).
7. **Stream** fire-and-forget publish to `risk.scores` + `audit.records` + `cases.created`.

---

## Results

| Metric | Value | Source |
|---|---|---|
| PR-AUC (synthetic CODScore, E2 features) | **0.5495** | `scripts/evaluate.py`, customer-grouped holdout (leakage=0) |
| ROC-AUC (E2) | **0.808** | same |
| Cost-optimal threshold | **0.15** | `docs/cost_table.md`, FN = 12x FP (Bahnsen Eq.1; Drummond-Holte 2006) |
| Tests passing | **141/149** (+ 8 skipped on Postgres+Redis paths; full suite w/ Docker services = 149) | `./verify.sh` |
| Endpoints | **22** (OpenAPI 3.1, auto-generated) | `docs/openapi.json` |
| Docker services (core) | **5** (api, postgres, redis, stream-worker, stream-processor) | `docker-compose.yml` |
| Docker services (full stack) | **9** (+ nginx, prometheus, grafana, drift-consumer) | `docker-compose --profile full` |

**Real-data upgrade path (Day 4 Track L):** Amazon India Sale Report on
Kaggle (~129,000 orders, `Status=Returned → is_returned=1`). Target
PR-AUC ≥ 0.72 — benchmark is Kandula et al. (DSS 2021) reporting
AUC 73-79% on real Indian e-commerce delivery data. `docs/cost_table.md`
+ `docs/feature_importance.md` will be regenerated on real data before
the pitch video.

### Real data — instructions for the user

The synthetic 7,235-row CODScore CSV in `data/raw/cod_orders.csv` is a
schema-compat placeholder; the model's headline PR-AUC (0.55) reflects
synthetic labels, not real Indian e-commerce outcomes. Track L Day 4
ships a drop-in upgrade path so the user can retrain on real Amazon
India data without touching any source file.

```bash
# 1. Download the Amazon India Sale Report (~129k orders) from Kaggle:
#    https://www.kaggle.com/datasets/thedevastator/unlock-profits-with-e-commerce-market
#    (or any similar Amazon India Sale Report dataset)
# 2. Place the CSV at data/raw/amazon_sale_report.csv
# 3. Ingest (maps columns to the unified schema, normalises RTO labels):
python scripts/ingest_kaggle.py                              # --source amazon (default)
# 4. Retrain on real data + auto-register as champion if PR-AUC beats synthetic:
python scripts/retrain_real.py
#    → trains HistGB on the leakage-safe CustomerID-grouped split,
#      evaluates PR-AUC + ROC-AUC + F1 + precision/recall@threshold,
#      promotes to champion in the registry if better than the incumbent,
#      regenerates docs/cost_table.md + docs/feature_importance.md,
#      exits 1 if PR-AUC < 0.60 (CI gate per mlops.yml Stage 3).
```

After the retrain completes, the model-card + dashboard Model Health page
will reflect the real-data champion (target PR-AUC ≥ 0.72 per Kandula
2021). The synthetic-data fallback is preserved (the `load_data()`
dispatcher in `src/features/cleaning.py` auto-detects
`data/raw/ingested_real.csv` and falls back to `cod_orders.csv` when
absent) so the project still runs out-of-the-box before the user
downloads the Kaggle CSV. See [`data/raw/README.md`](data/raw/README.md)
for download instructions + alternative datasets.

**Cost-curve explorer (Day 1 Track C):** `/v1/policy/cost-curves`
returns a Drummond-Holte sweep (19 thresholds, ≥500 bootstrap CIs
preserving row marginals). The dashboard cost bars fetch from this
endpoint live — no more hardcoded arrays. Cost-optimal threshold
highlighted green; legend shows precision/recall + n_pos/n_neg +
data_source. Math cited to Bahnsen ICMLA 2013 + Drummond-Holte 2006.

---

## Documentation

| Doc | What's in it |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Consolidated system design: Mermaid diagrams, 10-service inventory, decision precedence, scaling analysis (10x → 100x → 1000x), security model, what we're NOT doing. |
| [`docs/API_SPEC.md`](docs/API_SPEC.md) | Full OpenAPI 3.1 spec: 22 endpoints grouped by tag (Risk, Audit, Rules, Cases, Models, Policy, Mandates, Feedback, Metering, Health). Curl examples + Pydantic schemas. |
| [`docs/PITCH_SCRIPT.md`](docs/PITCH_SCRIPT.md) | Word-for-word 5-minute pitch video script. 3-act structure (Problem 45s → System 3min → Impact 45s). Time-stamped. |
| [`docs/MODEL_CARD.md`](docs/MODEL_CARD.md) | Model card per Google Model Card spec (Mitchell et al. 2019). Training data, metrics, limitations, bias analysis. |
| [`docs/RESEARCH.md`](docs/RESEARCH.md) | The 5 pitch papers cited in the executive narrative (different from the 40-paper KB). Title, venue, DOI, 2-3 sentence summary, how each shaped a component. |
| [`docs/cost_table.md`](docs/cost_table.md) | 8-row threshold sweep, cost-optimal = 0.15. (Day 4 Track L regenerates on real Amazon India data.) |
| [`docs/feature_importance.md`](docs/feature_importance.md) | Permutation AP-drop on held-out set. (Same Day 4 refresh.) |
| [`docs/research/INDEX.md`](docs/research/INDEX.md) | The 18-citation engineering bibliography (He & Garcia, Bahnsen, Gama, TFX, Paleyes, SoK Mao 2026, etc.). |

Historical architecture snapshots retained for context:
[`docs/ARCHITECTURE_V2.md`](docs/ARCHITECTURE_V2.md) (enterprise 9-service
spec, superseded) and [`docs/ARCHITECTURE_V3.md`](docs/ARCHITECTURE_V3.md)
(the engineering audit trail with 19 findings and 12 code deltas; V3 is
authoritative for engineering decisions, ARCHITECTURE.md is the
user-facing consolidation).

---

## Identity

Built by **Neeraj Parekh**, ENTC TY, MITAOE — for **Razorpay AI
Buildathon Track 02 (AI Risk Manager)**. Single-author sprint, 4 days,
papers + code + infra + docs. The competitive moat: address-level
scoring + merchant-tunable rules + tamper-evident Merkle audit +
bounded agents with cryptographic mandates — the boring, provable
machinery underneath agentic commerce.

For the 5-minute video script, see [`docs/PITCH_SCRIPT.md`](docs/PITCH_SCRIPT.md).
Forthcoming on the buildathon deadline: live demo URL + 5-min pitch video.
