# RTO Trust Layer

Address-level COD return-risk scoring with gated decisions, per-prediction explanations,
and a complete audit trail. Built for the **Razorpay AI Buildathon — Track 02
(AI Risk Manager)**: a working detector for one class of loss (COD Return-to-Origin),
with measured precision/recall on a held-out test set and honest false-positive costs.

## Why this exists

COD RTO eats margin in Indian e-commerce: industry data puts RTO at 15-30% of COD
orders, each failed delivery costing ~10-15x the price of a cheap intervention
(verification call / selective OTP / partial-COD). Existing shields stop at pincode
granularity. This project measures where predictive signal actually lives:

| Experiment | Features | PR-AUC | ROC-AUC | FP share of flagged | Verdict |
|---|---|---|---|---|---|
| E1 | order/customer only | 0.524 | 0.794 | 56.2% | baseline |
| **E2** | + address quality | **0.550** | **0.808** | **52.4%** | **kept** |
| E3 | + state infra aggregates | 0.545 | 0.808 | 56.4% | no lift, cut |
| E4 | threshold x cost model | - | - | - | optimal thr = 0.15 |

Key findings, stated honestly:
- Address-quality features (complete/partial/vague) carry real lift over order-only.
- Coarse geographic aggregates add nothing at this granularity; finer signal needs real
  address/pincode data (roadmap, not vaporware claims).
- With realistic economics (FN = 12x FP), the cost-optimal policy is a wide review net:
  recall 79% at precision 41%, applied through *cheap* gates (selective OTP, partial-COD),
  matching published COD-fraud results (78-84% fraud reduction at 4-7% conversion cost).

## The trust layer

Every scored order returns `ACCEPT / REVIEW / REJECT` with top contributing factors and a
tamper-evident audit record (SHA-256 hash chain - editing any historical record breaks
every later link; verify via `GET /v1/audit/verify-chain`). Malformed inputs fail loudly
(HTTP 422) with an agent-side fallback (hold + notify ops) - nothing is ever silently
scored or dropped.

Hardened per a mechanical security review (`scripts/security_probes.py`, all findings
mitigated): scoped API keys (scorer vs admin), per-key rate limiting, bounded input
contracts, idempotency keys, PII redaction, incident-scrubbed errors.

**Agents hold zero ambient authority.** Money-affecting actions require server-enforced,
HMAC-signed mandates (max amount + TTL) minted only by merchant backends (admin scope);
breach/tamper escalates to deterministic REJECT. Decision overrides are admin-only -
an agent physically cannot self-approve (proven in `scripts/demo_agent.py` abuse drills).

Platform hardening: deterministic rules engine (evaluated before ML, admin-tunable via
`/v1/rules` without redeploy), circuit breaker with rules-only degraded mode
(`degraded=true`, never fail-open to silent approval), three-way cost-optimal policy
endpoint, `/health` liveness, Docker packaging (`docker-compose up`).

```json
{
  "risk_score": 64.2,
  "decision": "REJECT",
  "explanation": [
    {"feature": "city_tier", "value": "tier_3", "delta_prob": 0.419, "direction": "raises_risk"},
    {"feature": "log_order_value", "value": 9.43, "delta_prob": 0.268, "direction": "raises_risk"}
  ],
  "audit_trail_url": "/audit/5ddf72cb-..."
}
```

Architecture: see `docs/ARCHITECTURE_V3.md` (current living plan - audited, expanded,
with component/connection registers and phased roadmap). `docs/ARCHITECTURE*.md` are
historical snapshots.

## Run it

```bash
./verify.sh                      # lint + tests + full evaluation (PR-AUC report)
python scripts/cost_table.py     # threshold sweep + business cost analysis
python scripts/demo_agent.py     # scoring demo incl. security controls + agent-abuse drills
python scripts/security_probes.py  # mechanical security probe suite (evidence over claims)
uvicorn src.api.routes:create_app --factory --port 8000   # live API
# then open http://localhost:8000/dashboard/  (merchant console)
docker compose up                # containerized API (pulls base image once)
```

Key endpoints: `POST /risk/score` (scorer key) · `GET /audit/{id}` + `GET /v1/audit/verify-chain`
(admin key) · `POST /v1/mandates`, `POST /risk/{id}/override`, `POST|GET|DELETE /v1/rules`
(admin) · `GET /v1/policy/optimal`, `GET /health`.

Requires Python 3.12. Data downloads are scripted in `scripts/`; `data/raw/`
holds the CODScore orders dataset and the India Post pincode directory.

## Key references
Methodology: He & Garcia (IEEE TKDE 2009) for imbalanced-data metric choice · Drummond & Holte
(Mach. Learn. 2006) for cost-threshold analysis · Gama et al. (ACM CSUR 2014) for drift policy ·
DSS 147 (2021) delivery-success features · Hu et al. (ACM) delay-risk SHAP interpretability.
Thesis: Amariles et al., "AI Agents in Payments" (Eur. J. Risk Regulation, 2026,
DOI 10.1017/err.2026.10103) · SoK: Security of Autonomous LLM Agents in Agentic Commerce
(arXiv:2604.15367) · Goodman & Flaxman (AI Magazine 2017). Full map: docs/research/INDEX.md.

## Honest limitations

- Training data is synthetic-but-realistic (7,235 CODScore rows); schema mirrors real
  e-commerce orders so swapping in a real labeled dataset is drop-in. Real Indian
  labeled data (e.g., Amazon Sale Report on Kaggle) requires API credentials.
- No address strings exist in this dataset, so leakage protection uses customer-grouped
  splits instead of address-grouped ones.
- Attribution is one-at-a-time perturbation vs population reference (not SHAP TreeExplainer,
  which does not support HistGradientBoosting); XGBoost is a drop-in swap when network
  allows installing it.
