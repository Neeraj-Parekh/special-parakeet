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
| 6 | **Model Health** | Grafana: PR-AUC = 0.1027 (Amazon India champion, 6.05× baseline) / 0.3950 (Olist boleto champion, 32× baseline, 3.8× Amazon — `?dataset=olist`), PSI < 0.1, DDM STABLE, ADWIN STABLE. Live cost-curve explorer wired to `/v1/policy/cost-curves`. | You understand MLOps, not just model training. **Honest** numbers: 0.10 is low because Amazon has no `user_id` history (ceiling ~0.12); Olist has real repeat-customer history so `user_rto_rate` actually fires there. |

### Live dataset switch — `?dataset=amazon|olist`

Every `POST /risk/score` accepts a `dataset` query param that selects which
champion model answers. A judge can flip datasets mid-demo to watch the
`user_rto_rate` lift in real time:

| Param | Champion | PR-AUC | Why the lift | Sample request |
|---|---|---|---|---|
| `?dataset=amazon` (default) | `rto_kaggle_histgb_20260827` | **0.1027** | Amazon Sale Report has NO `user_id` history — `user_rto_rate` / `merchant_id_rto_rate` are inert. Ceiling ~0.12 for any model on this data. | `curl -X POST localhost:8000/risk/score?dataset=amazon -d '{"order_id":"A-1","amount_inr":12400,"category":"Fashion","customer_id":"C-1"}'` |
| `?dataset=olist` | `rto_olist_histgb_20260828` | **0.3950** | Olist boleto subset has real `customer_unique_id` / `seller_id` history (494 repeat users). The expanding-window `user_id_rto_rate` / `merchant_id_rto_rate` features actually fire — 3.8× the Amazon champion. | `curl -X POST localhost:8000/risk/score?dataset=olist -d '{"order_id":"O-1","amount_inr":120,"category":"beleza_saude","customer_id":"C-1","merchant_id":"S-1","payment_method":"boleto","pincode":"01310","state":"SP","city":"sao_paulo","created_at":"2018-04-15T10:00:00"}'` |

The response payload carries `dataset: "amazon"` or `dataset: "olist"` so the
judge can verify which model answered. Both paths share the same downstream
pipeline (calibrate → cost-optimal decision → rules engine → audit hash-chain
append → Redis Streams publish) — only the feature builder + model + priors
differ. See [`src/models/olist_feature_builder.py`](src/models/olist_feature_builder.py)
+ [`data/olist/README.md`](data/olist/README.md) for the honest
caveats (boleto ≠ Indian COD; order_status canceled/unavailable ≠ true RTO).

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

**Deployed champion metrics (what the live `/risk/score` endpoint actually
serves — measured from the committed artifacts, not aspirational):**

| Metric | Value | Source |
|---|---|---|
| **PR-AUC — Amazon India champion (default `/risk/score`)** | **0.1027** | `models/champion/metrics.json` (best=`QtyZero_Region_histgb`, 96,944 train rows / 24,236 test rows, RTO rate 1.70% — 6.05× baseline lift; honest for 1.7% prevalence; Amazon has NO `user_id` history so `user_rto_rate` is inert) |
| **PR-AUC — Olist boleto champion (`/risk/score?dataset=olist`)** | **0.3950** | `data/olist/artifacts/metrics.json` (15,827 train / 3,957 test, Brier 0.0439, ROC-AUC 0.7676 — 32× baseline, **3.8× the Amazon champion**; Olist has real `user_id`/`merchant_id` history so `user_rto_rate`/`merchant_id_rto_rate` actually fire here) |
| ROC-AUC — Amazon | 0.660 (champion) | `models/champion/` (low because 1.7% prevalence — PR-AUC is the primary metric) |
| ROC-AUC — Olist | 0.7676 | `data/olist/artifacts/metrics.json` |
| Cost-optimal threshold | dynamic per-order | `docs/cost_table.md`, Bahnsen Eq.1 + per-amount FN cost (Drummond-Holte 2006) |
| Tests passing | **141/149** (+ 8 skipped on Postgres+Redis paths; full suite w/ Docker services = 149) | `./verify.sh` |
| Endpoints | **23** (OpenAPI 3.1, auto-generated — incl. `?dataset=olist`) | `docs/openapi.json` |
| Docker services (core) | **5** (api, postgres, redis, stream-worker, stream-processor) | `docker-compose.yml` |
| Docker services (full stack) | **9** (+ nginx, prometheus, grafana, drift-consumer) | `docker-compose --profile full` |

**Honest framing — Indian real-COD true rate is 0.25–0.60 (Shiprocket,
Delhivery NDA data); we report the best public-proxy metrics, not
aspirational ones.** The Amazon Kaggle number (0.1027) is a ceiling
check — there is no public Indian COD dataset with user history. The
Olist number (0.3950) is the closest public proxy (Brazilian `boleto` ≈
COD semantics) AND it validates the `user_rto_rate` / `merchant_id_rto_rate`
features that are inert on Amazon (Amazon has zero repeat users). Flip
`?dataset=amazon|olist` on the live endpoint to watch the lift in real
time.

**Synthetic-data baseline (legacy, NOT deployed):** the original
`data/raw/cod_orders.csv` synthetic placeholder produced PR-AUC 0.5495 /
ROC-AUC 0.808 on 7,235 rows with 23% positive rate (see
`scripts/evaluate.py` + `docs/MODEL_CARD.md` §"Synthetic baseline").
These numbers were superseded by the real Amazon Kaggle champion on Day
4 (Track L); they remain in `MODEL_CARD.md` for traceability only — the
live `/risk/score` endpoint does NOT serve the synthetic model.

### Real data — instructions for the user

The synthetic 7,235-row CODScore CSV in `data/raw/cod_orders.csv` is a
schema-compat placeholder (kept for the cost-curve precompute + the
8-dim legacy stub path). The **deployed** `/risk/score` champion is
the Kaggle Amazon India Sale Report model (PR-AUC 0.1027 — honestly low
for 1.7% prevalence; Amazon has no `user_id` so the `user_rto_rate`
feature is inert). The Olist boleto champion (PR-AUC 0.3950) is wired
as the `?dataset=olist` alternate path — it carries real `user_id` /
`merchant_id` history, which is the lift driver Amazon cannot test.

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
2021 — needs NDA-gated Shiprocket/Delhivery data; the public-proxy
ceiling is the Olist 0.3950). The synthetic-data fallback is preserved
(the `load_data()` dispatcher in `src/features/cleaning.py` auto-detects
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

## Engineering status (2025-08-29)

This section is the honest delta from the most recent hardening pass. Three
real bugs were closed; the audit trail for each is in `docs/`.

### 1. SHAP explainability — fixed (was returning all 0.0)

`src/models/explain.py` now prefers `shap.TreeExplainer` for the live
`HistGradientBoostingClassifier` (shap 0.42+ supports HistGB directly;
the original "NOT supported" comment was outdated). KernelExplainer
remains the fallback for non-tree models.

The real root cause of "SHAP returns all 0.0" was NOT KernelExplainer
itself — it was that KernelExplainer fell back to a 1-row background =
the input itself when the lifespan background cache was empty, making
every marginal contribution trivially zero. TreeExplainer sidesteps
this entirely: it computes exact TreeSHAP values from the tree
structure with the model's expected value as base, no background
dataset needed.

Verified: `explain_with_shap` on a real HistGB returns 8/8 non-zero
values (was 0/8), max abs 1.07, `method=shap_tree`. Full suite:
**397 passed, 11 skipped, 0 failures.**

### 2. Audit hash-chain — fixed (was reporting intact:false)

`verify_chain` was working correctly — it detected real chain breaks.
The 7.3 MB `out/audit.jsonl` had 87 internal breaks caused by
**concurrent writers** (test suite + dev server) racing on the shared
file with only in-process `threading.Lock` (which serializes threads,
not processes).

Fix: `src/audit/logger.py:_log_file` now acquires `fcntl.flock(LOCK_EX)`
on the audit file before computing `previous_hash`, so concurrent
writers serialize at the OS level. After acquiring the lock it
re-derives the true last record's `raw_hash` (O(1) per write) so the
chain links correctly even if another process appended since
construction. Verified: 2 concurrent processes × 50 records = 100
records, `intact=True, records_checked=100`.

The broken fragment was rotated to `out/audit.broken-fragment-2025-08-29.jsonl`
(preserved for forensics; `out/` is gitignored so it stays local).
Full diagnosis: [`docs/MERKLE_AUDIT_DIAGNOSIS.md`](docs/MERKLE_AUDIT_DIAGNOSIS.md).

### 3. Secret hygiene — purged

A dead Render API token had been committed to git history (commit
`766f0ae`) and was scrubbed from the working tree later but **still
lived in the historical blob**. Purged from all 21 commits via
`git filter-repo --replace-text` (the token was already revoked by the
user, so this is hygiene, not emergency). Worklog 8-char prefix
scrubbed. Full audit + the exact redaction commands:
[`docs/SECRET_SCAN_REPORT.md`](docs/SECRET_SCAN_REPORT.md).

**Required user action:** force-push the rewritten history to GitHub:
```bash
cd /home/sync/upload/RTO_Trust_Layer_FULL
git push --force-with-lease origin main   # use YOUR GitHub PAT/SSH key
```

### New comprehensive UML

[`docs/UML_COMPREHENSIVE.md`](docs/UML_COMPREHENSIVE.md) — 2,112 lines,
19 Mermaid diagrams across 10 sections (C4 L1/L2, component, class,
6 sequence diagrams, ERD derived from all 7 Alembic migrations, DFD,
3 deployment topologies, 2 state, 2 activity). Every endpoint, class,
and table is traceable to a real file + line range. 28 endpoints
enumerated by grepping the actual `@router` decorators.

---

## Deployment

Two paths. Pick based on whether you want the dashboard only, or the
dashboard + the Python API.

### Path A — Vercel (dashboard only, no credit card, ~3 min)

The `web/` directory is a self-contained Next.js app that talks to the
FastAPI backend via a configurable `NEXT_PUBLIC_API_BASE_URL`. With no
backend configured it falls back to mock-mode so the dashboard renders
cleanly for a demo.

1. Push the repo to GitHub (done — `Neeraj-Parekh/special-parakeet`).
2. Go to https://vercel.com → New Project → Import the repo.
3. Set **Root Directory** to `web`.
4. Set env var `NEXT_PUBLIC_API_BASE_URL` to your Render URL (from
   Path B) once the backend is up, or leave unset for mock-mode.
5. Deploy. Vercel gives you a public `*.vercel.app` URL instantly.

**Security note (read this):** never paste a Vercel token (`vcp_...`)
into chat, a commit message, or a doc. Generate one at
https://vercel.com/account/tokens only when needed, store it as an
environment variable in the deploy shell, and revoke immediately after
use. A token was accidentally pasted in plaintext during this session;
it has been flagged for revocation — see
[`docs/SECRET_SCAN_REPORT.md`](docs/SECRET_SCAN_REPORT.md) §4.

### Path B — Render (dashboard + API, single free web service)

The repo's `render.yaml` is a one-command Blueprint. The FastAPI app
serves the API at `/` and the pre-built dashboard at `/dashboard`
(same origin — no CORS, no second service to cold-start).

1. Push the repo to GitHub (done).
2. Go to https://render.com → New → Blueprint Instance.
3. Select the `Neeraj-Parekh/special-parakeet` repo.
4. Render reads `render.yaml` → click Apply.
5. URL: `https://rto-trust-layer.onrender.com` (or similar).
6. Verify:
   ```bash
   curl -s https://rto-trust-layer.onrender.com/health | jq .
   curl -s https://rto-trust-layer.onrender.com/dashboard/ | head
   ```
Render free tier: 750 instance-hours/month, spins down after 15 min
idle (~30s cold start), no persistent disk (audit JSONL is wiped on
re-deploy — for RBI MRM compliance, set `DATABASE_URL` to a Render
managed Postgres after first apply).

Full deploy walkthrough: [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

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
