# RTO Trust Layer — Master Command Plan
## Context-window-loss-proof source of truth

> **If you lose all chat context, read this file first.** Then read `01-EXECUTION-SEQUENCE.md` (the day-by-day plan) and `05-PAPER-SKILLS-MAP.md` (paper knowledge → code gaps). Check `07-EXECUTION-LOG.md` for current status.

---

## 1. Project identity

| Field | Value |
|---|---|
| Builder | **Neeraj Parekh**, ENTC TY, MITAOE |
| Submission | **Razorpay AI Buildathon — Track 02 (AI Risk Manager)** |
| Internal deadline | **Aug 28-29, 2026** (so GitHub + video prep can happen after) |
| Audience | Razorpay judges (primary) |
| Project root | `/home/z/my-project/upload/RTO_Trust_Layer_FULL` |
| Command folder | `/home/z/my-project/command/` (you are here) |
| Worklog (all agent activity) | `/home/z/my-project/worklog.md` |
| Original prompt (2102 lines) | `/home/z/my-project/upload/prompt-razor.txt` |
| Paper KB (40 papers, 135 MDs) | `/home/z/my-project/upload/RTO_Trust_Layer_FULL/paper studied/` |
| Next.js sandbox (port 3000) | `/home/z/my-project` (host project) |
| Dev server log | `/home/z/my-project/dev.log` |

---

## 2. North Star

> "A merchant-facing RTO risk command center — not just a model, not just an API, but a complete product that a Flipkart seller or D2C brand would log into every morning to see which orders will cost them money, why, and what to do about it."

**The shift**: Stop thinking "I need to add a frontend and a backend." Start thinking: **"I am shipping a product."** A product has a user, a story, a screen they look at, a decision they make, and money they save.

---

## 3. The "Done" state — 6 judge demo moments

| # | What judge sees | What it proves |
|---|---|---|
| 1 | **Live Dashboard** — dark mode, paste 3 Indian addresses, click Score, get ACCEPT/REVIEW/REJECT with color-coded badges | You can build products, not notebooks |
| 2 | **Explainability Panel** — "73% risk because: COD + ₹12,400, new customer, vague address in Tier-3 city" | You understand black-box ML is useless in finance |
| 3 | **Audit Trail** — click prediction ID, see features + model version + SHA-256 hash chain + CSV download | You understand compliance, trust, enterprise requirements |
| 4 | **Rules Engine** — toggle "Block COD > ₹50K from new customers," re-score, instant REJECT | You understand business rules beat ML in known cases |
| 5 | **Agent Console** — type "Score order ORD-123," agent responds + "I cannot block. I have requested human approval." | You understand unconstrained agents are dangerous |
| 6 | **Model Health Page** — Grafana: PR-AUC = 0.72, PSI = 0.02, "Model v2.1 active since Aug 25" | You understand MLOps and production reality |

---

## 4. The 4-Question Gate (every feature must pass)

1. Does this make the JUDGE say "wow"? No? → Skip.
2. Does this prove I understand ENTERPRISE RISK? No? → Kill.
3. Can I demo this in 30 SECONDS without debugging? No? → Cut.
4. Does this differentiate me from "a guy who trained XGBoost"? No? → Don't waste time.

---

## 5. 5 Missions

1. **Make the Dashboard Tell the Story** — Next.js (NOT vanilla JS) frontend with 4 pages: Risk Console, Audit Explorer, Rules Manager, Model Health. Every page demo-able in 30 sec without refresh. 3 demo orders (repeat customer, high-value COD, prior returns).
2. **Make the Backend Unbreakable** — wire the 10 services (see `06-PROMPT-RAZOR-EXTRACTION.md` §2). Circuit breaker fails gracefully (show fallback in demo). Audit hash chain integrity check in demo.
3. **Make the Agent a Prop, Not a Star** — Agent console is 4th tab, not 1st. Agent can only call 4 APIs. Any other intent returns "Action not permitted." Show approval queue in demo.
4. **Make the Numbers Credible** — ingest Amazon India Kaggle dataset. PR-AUC above 0.70. Document cost model. Generate cost table showing merchant savings.
5. **Make the Docs Sell the Product** — README = product landing page (not homework). PITCH_SCRIPT = word-for-word video. ARCHITECTURE = Mermaid + scaling analysis.

---

## 6. 3-Act pitch

- **Act 1 — Problem (45 sec)**: "Indian e-commerce loses ₹50,000 Cr/yr to COD returns. Razorpay's RTO Shield is pincode-level and black-box. Merchants can't see WHY, can't tune thresholds, no audit trail for regulators. And now AI agents are coming — an agent with a wallet and no guardrails is a lawsuit."
- **Act 2 — System (3 min)**: "So I built the RTO Trust Layer. Not a model — a platform." Show Dashboard → Rules → Audit → Model Monitor → Agent Console.
- **Act 3 — Impact (45 sec)**: "On real Indian e-commerce data, this reduces RTO losses by 34% with FP under 10%. It's not a notebook. It's a product."

---

## 7. Final priority list (the deep truth)

1. **Frontend looks like Stripe** (dark mode, clean, no bugs)
2. **Backend never 500s** (circuit breaker, validation, graceful failure)
3. **One perfect demo flow** (3 orders, 3 decisions, 1 audit trail, 1 agent action)
4. **README sells the product** (not the code)

**Execution focus order (user's directive)**: PRIMARY = read papers → suggest tech stack → work on code path → improve the idea deeper using paper skills. LATER = frontend (Stripe-like), docs.

---

## 8. Tier 1 answers (blocking decisions — all confirmed by user)

| Q | Answer |
|---|---|
| Tech path | **c+d** (Next.js dashboard over Python API + Python improvements + infra/docs) — but PRIMARY focus is papers + tech stack + code, NOT web design first |
| Track | **02 — AI Risk Manager** |
| Deadline | **Aug 28-29 internal** |
| Audience | **Razorpay primary** |
| Paper studied in zip | **YES** (was a Glob-space-in-path bug on my side; re-extracted, 145 .md on disk now) |

---

## 9. Tier 2 — ALL 16 items approved ("DO IT")

(See `03-WORK-ITEMS.md` for full status tracker. Brief:)
1. Split dashboard into 3 surfaces (live ops console + reporting + Copilot Q&A)
2. Wire cost optimizer into actual decision
3. Fix the 6 decorative bugs
4. Add real streaming path (Redis Streams per V3 §9.3)
5. Add real DB + migrations (Postgres + Alembic)
6. Add feedback loop (label ingestion + drift + calibration)
7. Add CI workflow (.github/workflows/)
8. Fix infra theater items (verify.sh, grafana mount, uv.lock, pyproject, Dockerfile secrets, nginx TLS+headers, dead shap)
9. Add OpenAPI examples + API_SPEC schemas
10. Add V3-specified missing endpoints (/v1/audit/{id}/proof, /v1/simulate, /v1/usage, outcome-ingest)
11. Add mandate action-class expansion (V3 §13) — the differentiator
12. Add OpenTelemetry tracing + structured logging + alerting rules
13. Add IaC (OpenTofu)
14. Multi-source ingest simulators (4 channels)
15. TLS + security headers in nginx
16. Real uv.lock + pyproject [project] + dev/runtime dep split

---

## 10. Tier 3 answers (framing / honesty / narrative)

| Q | Answer |
|---|---|
| is_cod tautology | **Keep + reframe** (best framing per vision: "is_cod gates model invocation; model runs only on COD orders; is_cod is pass-through for logging") |
| Mandate angle | **YES, differentiator.** Expand per V3 §13. UPI Circle / delegated payments. Implement expansion. |
| Cost optimizer depth | **(a) wire existing `optimal_decision()` into decision path FIRST**, then **(b) implement full V3 §11.6 intervention policy argmin** over {ship, otp_verify, partial_cod, address_check, hold} |
| Real data | **YES**, user has Kaggle account, will get Amazon Sale Report. **No synthetic.** |
| Pitch identity | **Neeraj Parekh, ENTC TY, MITAOE** |
| Dashboard defaults | **Remove defaults**, make more real-looking |

---

## 11. Things to PRESERVE (don't lose in the polish)

- **Audit hash chain** — `src/audit/logger.py` (SHA-256 chain, byte-offset index, verify_chain tested by `test_ship.py`)
- **HMAC mandates** with VALID/TAMPERED/BREACH/EXPIRED verdicts — `src/api/mandates.py` (UPI Circle / delegated payments angle — real, not stubbed)
- **group_split** with GroupShuffleSplit on CustomerID + group_leakage() assertion = 0 leakage — `src/models/splitting.py`
- **Circuit breaker** with degraded-mode rules-only fallback — `src/api/breaker.py`
- **Cost-table + cost-optimizer math** (BMR per-amount FN cost — Bahnsen ICMLA 2013) — `docs/cost_table.md` + `src/business/cost_optimizer.py`
- **5 real pytest files** (~526 LOC) + real k6 load profile (3 scenarios, p99<400ms) — `tests/`
- **V3 architecture register** (append-only decisions, revisit triggers, 19-finding self-audit) — `docs/ARCHITECTURE_V3.md`

---

## 12. The 24 broken / stubbed / decorative items (full list in `03-WORK-ITEMS.md`)

From agents 1-b (code) + 1-c (infra) syntheses. Highlights:
- Cost optimizer NOT wired into decision (`routes.py:36, 194` uses static 0.15/0.60)
- Idempotency cache unbounded dict (memory leak)
- `add_geo_features` dead code (`features/enrich.py:27`)
- `register_model` dead in prod (only called from tests; champion always None)
- `_latest()` always returns None (`cases/service.py:50-64`)
- `shap` in requirements, never imported (dead dep)
- docker-compose `--profile full` starts postgres+redis API never connects to (V3 finding A2)
- Grafana provisioning mount path wrong (`dashboards-src` vs `dashboards`)
- `verify.sh` hardcodes `/mnt/20265E15265DEC72/...` venv path
- `uv.lock` is a 3-line stub
- `pyproject.toml` has no `[project]` table
- No CI workflow file despite TSV claiming it
- API_SPEC.md bare (16 path names, no schemas/examples)
- V3-specified endpoints missing from openapi.json
- Override single-admin vs V3 §12.1 dual-control contradiction
- No DB / no migrations
- No streaming / message bus
- No OpenTelemetry / no alerting rules
- Dockerfile bakes ENV defaults
- nginx no TLS / no security headers
- Dashboard hardcoded cost bars, default demo keys visible
- `is_cod` 0.18 near-tautological
- Single-author pitch, Track 02 vs 05 was undecided (now locked: 02)
- Synthetic dataset only

---

## 13. The 3 perceived-gap drivers vs Microsoft Fabric

1. **ONE static HTML dashboard** vs Microsoft's 3 surfaces (Real-Time Dashboard + Power BI + Copilot)
2. **REST-only, no event/streaming backbone** — Microsoft has Eventstreams → Eventhouse → Activator
3. **No DB / no migrations / no feedback loop** — Microsoft has Eventhouse (hot) + OneLake (cold) + Activator (case mgmt with SLA)

---

## 14. Resolved tech stack (full detail in `04-TECH-STACK-DECISIONS.md`)

| Layer | Choice | Rationale |
|---|---|---|
| Backend language | Python 3.12 (keep; NOT Go for rules — V3 rejected rewrite) | Existing code is Python; rewrite is out of scope for 3-day sprint |
| Web framework | FastAPI (keep) | Already wired, tests pass |
| DB | Postgres 15 + Alembic migrations | Replaces JSONL files for audit/cases/registry/idempotency/PSI |
| Message bus | Redis Streams now (per V3 §9.3), NATS/Kafka later | V3 explicitly rejected Kafka as cargo-cult |
| Feature store | Redis (online) + Postgres+Parquet (offline) + Feast (registry) | V2's Feast inconsistency resolved: Feast for registry only |
| ML serving | in-process HistGB (keep), wrapped by Model Registry | V3 rejected MLflow-server as overkill; implement TFX-style canary gate |
| ML registry | lightweight Postgres-backed | Closes gap #5 (champion/challenger promotion) |
| Drift detection | PSI (existing) + DDM + ADWIN (per Gama 2014 paper) | PSI for batch distribution, DDM for online error stream, ADWIN for change-point localization |
| Explainability | SHAP KernelExplainer (replaces LOO) | TreeExplainer doesn't support HistGB per prompt-razor line 1737 |
| Observability | Prometheus + Grafana (keep) + OpenTelemetry + Jaeger (add) + AlertManager (add) | Microsoft parity |
| Frontend | Next.js 16 + TypeScript + Tailwind + shadcn/ui (NEW) | Replaces vanilla JS dashboard; Stripe-like dark mode |
| Auth | API keys (existing) + JWT RS256 (add per V2 §6) | Keep simple for demo |
| Secrets | ENV vars (existing) — for demo; document Vault/SOPS for prod | V3 explicitly refused half-deployed IaC |
| IaC | OpenTofu (V3 explicitly rejected Terraform BSL) | Defer to Day 4 |
| CI | GitHub Actions (ruff + pytest + leakage gate + docker build + Trivy scan) | Closes gap #12 |

---

## 15. Tailscale bridge (offered by user)

- User offered Tailscale access to their laptop (most toolchain ready there)
- Use case: when downloads >10GB or need to run Python/tests/k6 against real data
- **Defer for now**; bring up when we hit a verification wall (e.g., need to run the test suite against real Kaggle data, or run k6 load tests against a deployed stack)

---

## 16. Top-3 actions to take NOW (per agent 2-knowledge, highest leverage)

1. **Wire `optimal_decision()` into `routes.py`** — replace static 0.15/0.60 thresholds. ~2h. Source: Cost-Sensitive Bayes Minimum Risk (Bahnsen 2013). Closes gap #1.
2. **Build the 6-stage TFX pipeline** (profile → schema-validate → transform-in-artifact → train → canary-gate → serve) using lightweight OSS. ~1 day. Source: TFX (Baylor 2017) + Challenges in Deploying ML (Paleyes 2022). Closes 4 of 14 gaps (#5 ML registry, #6 feature store, #7 streaming transforms, #14 production ML patterns).
3. **Build LabelFeedbackService with DDM + ADWIN drift detection**. ~4h. Source: Survey on Concept Drift Adaptation (Gama 2014). Closes 3 of 14 gaps (#3 feedback loop, #4 concept drift, sets up #5 champion/challenger triggers).

These 3 actions close 6 of 14 code gaps and create the demo moments for the 4-Question Gate.

---

## 17. Reference paths

| What | Where |
|---|---|
| Project root | `/home/z/my-project/upload/RTO_Trust_Layer_FULL` |
| Paper knowledge base (40 papers) | `/home/z/my-project/upload/RTO_Trust_Layer_FULL/paper studied/` |
| Paper skills aggregated (1 file) | `paper studied/all_skills.yaml` |
| Paper TOC + tag cloud | `paper studied/index.md` |
| Paper relationships | `paper studied/knowledge-graph.md` |
| Original prompt (2102 lines) | `/home/z/my-project/upload/prompt-razor.txt` |
| Worklog (all agent activity) | `/home/z/my-project/worklog.md` |
| This command folder | `/home/z/my-project/command/` |
| Microsoft Fabric fraud ref (fetched) | `/home/z/my-project/reference/fabric/fraud-detection.png` |

---

*Last updated: Aug 27, 2026. Maintained by: Z.ai Code orchestrator. If this file is the only thing you have, you can pick up the work.*
