# Research — the 5 pitch papers (2 peer-reviewed w/ DOIs, 3 industry briefs w/ URLs)

> These are the **executive-pitch citations** — blog/industry sources
> chosen for the 5-minute video and the README, NOT the engineering
> bibliography. The engineering bibliography (18 papers with DOIs +
> local PDFs) is in [`research/INDEX.md`](research/INDEX.md) and
> covers He & Garcia 2009, Bahnsen 2013, Drummond-Holte 2006, Gama
> 2014, TFX Baylor 2017, Paleyes 2022, SoK Mao 2026, Amariles 2026,
> Goodman & Flaxman 2017, etc.
>
> Source: lines 1863-1875 of the original `prompt-razor.txt` buildathon
> brief (not in this repo's `docs/` tree; preserved in the project
> control plane). Per V3 §21 claims ledger: every claim carries a
> status (MEASURED > CITED > ASSUMED > OMITTED). These 5 are CITED
> or ASSUMPTION-industry — not MEASURED in the repo.
>
> **Citation-type split (Track T 11-d honesty fix):** of the 5 pitch
> papers, **2 are peer-reviewed journal articles with DOIs**
> (Papers 1 + 2) and **3 are vendor industry briefs cited by URL**
> (Papers 3 + 4 + 5 — Liminal, Pragma, Atlan). The earlier framing
> implied all 5 carried DOIs; this was inflated. Each paper's table
> below now has an explicit **Citation type** row so the reader can
> tell peer-reviewed evidence from industry-reported numbers at a
> glance. The anti-fabrication policy (last section) is unchanged.

---

## Paper 1 — E-Commerce Fraud Detection: Systematic Literature Review

| Field | Value |
|---|---|
| Title | E-Commerce Fraud Detection Based on Machine Learning Techniques: A Systematic Literature Review |
| Venue | *Big Data Mining and Analytics* (Tsinghua University Press / IEEE) |
| Year | 2024 |
| Link | https://doi.org/10.26599/BDMA.2024.9020015 (volume 7, issue 3) |
| Citation type | **Peer-reviewed — DOI: 10.26599/BDMA.2024.9020015** (Tsinghua/IEEE journal; indexed in Crossref) |
| Status | CITED |

### Summary

A systematic review of 170+ e-commerce fraud detection papers covering
2015-2023. Three findings dominate: (1) ensemble methods (Random
Forest, XGBoost, stacking) outperform single-classifier approaches
across nearly every benchmark; (2) class-imbalance handling via
SMOTE / ADASYN / cost-sensitive learning matters more than algorithm
choice — a RandomForest with proper resampling beats a deep neural
net without; (3) feature engineering (RFM, velocity, address-quality,
device-fingerprint) carries more lift than model tuning. The review
explicitly calls out the gap between published AUC numbers and
real-world deployment economics: most papers optimize F1, but
merchants care about money saved.

### How it informed the project (Methodology)

The methodology section of [`MODEL_CARD.md`](MODEL_CARD.md) and the
experiment ladder (E1 → E2 → E3 → E4) in [`../README.md`](../README.md)
are anchored here. The decision to use a single HistGB rather than a
6-model ensemble (per the hybrid-multistage paper in the engineering
bibliography) is a deliberate hackathon-scale cut — this review
confirms ensembles are the production-grade answer, so the
single-model choice is documented as a limitation, not a hidden
trade-off. The `address_quality` feature engineering ladder (E1 → E2)
matches the review's callout that feature engineering beats model
tuning.

---

## Paper 2 — Credit Card Fraud Risk Management: Threshold Optimization

| Field | Value |
|---|---|
| Title | Modeling and Optimization of Deep and Machine Learning Methods for Credit Card Fraud Risk Management |
| Venue | *Mathematics* (MDPI) |
| Year | 2026 |
| Link | https://doi.org/10.3390/math14010021 (volume 14, issue 1) |
| Citation type | **Peer-reviewed — DOI: 10.3390/math14010021** (MDPI open-access journal; indexed in Crossref) |
| Status | CITED — directly informs the Threshold Manager |

### Summary

Cost-sensitive threshold optimization for credit-card fraud
classifiers. Derives the closed-form cost-optimal threshold rule
`τ* = C_FP / (C_FP + C_FN)` — the ratio that minimizes expected
cost when the classifier's score is monotonically calibrated. The
paper validates on European card-transaction datasets (~750k txns)
and shows cost-optimal thresholds save 23% vs F1-optimal thresholds
(Bahnsen 2013 reports the same gap on a different dataset). The
paper also formalizes the per-transaction-amount FN cost (a
₹50,000 RTO costs more than a ₹500 RTO) — the per-amount
generalization of the Bahnsen BMR rule.

### How it informed the project (Threshold Manager + cost-optimizer)

This is the **Threshold Manager** source. The cost-optimal threshold
formula `τ* = C_FP / (C_FP + C_FN)` is the global version of the
per-order Bahnsen BMR argmin (`optimal_decision()` in
`src/business/cost_optimizer.py`). With our defaults
(`C_FP = 50`, `C_FN = 600`), `τ* = 50 / 650 = 0.077` — close to
the empirical cost-optimal 0.15 from the threshold sweep
(`docs/cost_table.md`); the difference is the REVIEW gate's
intervention cost (`C_OTP = 5`) which the closed-form τ* doesn't
account for. The per-amount FN cost generalization is documented as
the Day 4 Track N stretch goal (V3 §11.6: extend the 3-way BMR to a
5-way intervention argmin `{ship, otp_verify, partial_cod,
address_check, hold}` with per-transaction-amount FN cost).

---

## Paper 3 — Building Trust in Agentic Commerce

| Field | Value |
|---|---|
| Title | Building Trust in Agentic Commerce |
| Venue | Liminal (industry analyst brief) |
| Year | 2025 |
| Link | https://www.liminal.co/insights/building-trust-in-agentic-commerce (registration-gated) |
| Citation type | **Industry brief — URL: https://www.liminal.co/insights/building-trust-in-agentic-commerce** (vendor analyst note; no DOI; not peer-reviewed) |
| Status | CITED-industry — V3 §21 marks as PUBLIC-MARKETING-derived |

### Summary

A 3-pillar trust framework for AI agents that transact: (1)
**Authentication** — agents must prove who they are per action, not
per session; (2) **Authorization** — agents must have explicit,
narrow, revocable authority scoped per task, not broad ambient
authority; (3) **Verification** — every agent-initiated transaction
must leave a tamper-evident trail that an external auditor can verify
without trusting the agent or the merchant. The brief predicts that
by 2028, 40% of CIOs will demand "guardian agents" — second-agent
watchers whose sole job is to verify the primary agent's authority
chain before any money moves.

### How it informed the project (Agent Gateway + bounded agent)

The `BoundedAgent` class in `scripts/demo_agent.py` and the mandate
system in `src/api/mandates.py` map directly to the 3 pillars:

- **Authentication** → each agent has its own scorer-scope API key
  (rotatable, with a 300s overlap window per V3 §12.1) + carries an
  HMAC-signed `X-Mandate` token per request. The mandate encodes the
  merchant's identity (admin-scope key minted it) + the customer's
  salted digest + the per-txn `device_id` / `user_id` (UPI Circle per
  NPCI OC-201B §3.3/§3.7).
- **Authorization** → the 7-action allowlist (4 COD-order + 3 UPI
  Circle). High-cost actions (`block_order`,
  `upi_circle_delegated_pay`) require `requires_approval=True` —
  they create a case in the dual-control queue, never execute. The
  agent cannot mint, refund, discount, or edit addresses. Absence of
  these capabilities is enforced by route-level scope checks with
  tests proving 403s.
- **Verification** → every decision lands in the SHA-256 hash chain +
  the Merkle interval sealer (Track H Day 2, V3 §10.3). The
  `GET /v1/audit/{id}/proof` endpoint returns an O(log N) inclusion
  proof — a regulator can verify one decision without re-reading the
  whole audit table.

The "guardian agent" prediction (40% of CIOs by 2028) is the future
direction — the dual-control queue is the human equivalent today; a
second bounded agent that auto-verifies the mandate chain is the
natural upgrade.

---

## Paper 4 — COD Fraud in Indian E-commerce

| Field | Value |
|---|---|
| Title | COD Fraud in Indian E-commerce |
| Venue | Pragma (industry brief) |
| Year | 2025 |
| Link | https://www.pragma.in/blog/cod-fraud-indian-ecommerce (free, registration-gated for download) |
| Citation type | **Industry brief — URL: https://www.pragma.in/blog/cod-fraud-indian-ecommerce** (vendor blog; no DOI; not peer-reviewed) |
| Status | ASSUMPTION-industry — per V3 §21, the 78-84% / 4-7% / 89-93% / 42-48% numbers are UNVERIFIED until a primary source (logistics whitepaper) is found |

### Summary

An industry brief on COD fraud patterns specific to the Indian
market. Three intervention-effectiveness numbers drive the REVIEW →
OTP business case:

- **Selective OTP** (call the customer to confirm before dispatch)
  reduces COD fraud by **78-84%** at a **4-7% conversion cost**
  (customers who don't answer the OTP call cancel the order — that's
  the friction).
- **Velocity controls** (block a customer who places > N COD orders
  per hour across multiple pincodes) block **89-93%** of COD fraud
  attempts by repeat offenders.
- **Address validation** (cross-check the delivery address against
  India Post pincode directory + reject known-undeliverable pincodes)
  prevents **42-48%** of COD returns driven by fake/incomplete
  addresses.

These are the *selective intervention* numbers that justify the
REVIEW gate's existence — `cost_review = C_OTP + (1-p) · C_FP + p ·
(1-otp_eff) · C_FN` is positive (i.e., REVIEW is chosen) precisely
because `otp_effectiveness = 0.82` is high enough that the OTP's
RTO-catch rate exceeds its friction cost.

### How it informed the project (REVIEW → OTP intervention business case)

- The `otp_effectiveness = 0.82` weight in `DEFAULT_COST_WEIGHTS`
  (`src/api/routes.py:73`) is the midpoint of the 0.78-0.84 range.
- The `REVIEW` decision's `cost_breakdown` in the `/risk/score`
  response is computed with this weight — a merchant sees
  `cost_review = ₹5 + (1-p)·₹50 + p·(1-0.82)·₹600` per order, so
  REVIEW is chosen when `p ∈ (0.077, 0.5)` roughly (the Bahnsen
  per-order argmin).
- The Day 4 Track N full V3 §11.6 5-way intervention policy
  (`{ship, otp_verify, partial_cod, address_check, hold}`) ports
  these three intervention classes into the cost argmin. The
  `partial_cod` intervention (customer pays a small advance, merchant
  holds the rest until delivery) is the friction-reduced variant of
  REJECT — it keeps the customer acquisition while reducing the
  RTO loss exposure.
- The `address_check` intervention is the future home of the India
  Post pincode directory integration (Track L Day 4 stretch — the
  `add_geo_features` dead code removed in Track B Day 1 is the
  placeholder; the re-introduction recipe is in
  `src/features/enrich.py`'s docstring).

---

## Paper 5 — AI Agent Risks & Guardrails: 2026 Enterprise Security Guide

| Field | Value |
|---|---|
| Title | AI Agent Risks & Guardrails: 2026 Enterprise Security Guide |
| Venue | Atlan (data-management vendor) |
| Year | 2026 |
| Link | https://www.atlan.com/ai-agent-risks-guardrails (free, registration-gated for full PDF) |
| Citation type | **Industry brief — URL: https://www.atlan.com/ai-agent-risks-guardrails** (vendor marketing; no DOI; not peer-reviewed) |
| Status | CITED-industry — V3 §21 marks as PUBLIC-MARKETING-derived |

### Summary

A 5-layer guardrail stack for enterprise AI agents: (1) **prompt /
tool hygiene** — input/output schema validation, no PII in prompts,
allowlisted tools only; (2) **verified execution context** — every
tool call runs in a sandboxed runtime with audited syscalls; (3)
**payment authorization + custody separation** — money movement
requires a second principal's signature, never the agent's alone;
(4) **inter-agent trust controls** — agents that talk to other
agents must verify the counterparty's mandate chain; (5) **market &
compliance monitoring with tamper-evident audit trails** — every
agent-initiated transaction is logged to an append-only audit log
that an external regulator can verify independently. Gartner predicts
40% of CIOs will demand "guardian agents" by 2028.

### How it informed the project (security architecture)

The 5-layer stack maps 1:1 to the RTO Trust Layer's security model
(see [`ARCHITECTURE.md`](ARCHITECTURE.md) §9):

| Atlan layer | RTO Trust Layer implementation |
|---|---|
| (1) Prompt / tool hygiene | `BoundedAgent.ALLOWED_ACTIONS` allowlist (7 actions, hardcoded). Agent LLM output is never interpreted as instruction by our services — no LLM in the decision path at all. |
| (2) Verified execution context | V3 A12 explicitly **rejected** E2B sandbox as a category error — our agent performs allowlisted API calls, not arbitrary code-exec. The allowlist + HMAC mandate IS the verified execution context. |
| (3) Payment authorization + custody separation | **Dual-control override** (V3 §12.1, Track H) — `admin_signature_1` + `admin_signature_2` must be different. The `POST /risk/{id}/override` endpoint records both digests in the audit hash chain. |
| (4) Inter-agent trust controls | The HMAC mandate (`src/api/mandates.py`) IS the inter-agent trust token. The mandate encodes the merchant's identity + per-txn device/user validation; breach escalates to deterministic REJECT with a 12-value `verdict_reason` vocabulary. |
| (5) Market & compliance monitoring + tamper-evident audit | **Merkle audit intervals** (V3 §10.3, Track H) — `audit_merkle_intervals` table + `MerkleSealer` class in `src/audit/logger.py`. `GET /v1/audit/{id}/proof` returns the RFC 6962-style inclusion proof. |

The "guardian agent by 2028" prediction is the future direction —
today's dual-control queue is the human equivalent; a second bounded
agent that auto-verifies the primary agent's mandate chain is the
natural upgrade path (the `audit_agent_mandate_scoping` capability
from the SoK Mao 2026 paper in the engineering bibliography is the
formal model).

---

## Anti-fabrication policy

Per V3 §21 claims ledger, every external claim in this project's
pitch + docs carries a status:

| Claim | Status |
|---|---|
| Selective OTP cuts COD fraud 78-84% @4-7% conversion cost (Paper 4) | UNVERIFIED-industry — primary source (logistics whitepaper) not yet identified; phrase as "industry-reported" in pitch |
| FN ≈ 12× FP cost ratio (Paper 2's generalization, our `C_FN=600` / `C_FP=50` defaults) | ASSUMPTION-model — kept as parameterized assumption, sensitivity-charted in cost table |
| Velocity controls block 89-93% of COD fraud (Paper 4) | UNVERIFIED-industry |
| Address validation prevents 42-48% of COD returns (Paper 4) | UNVERIFIED-industry |
| Gartner predicts 40% of CIOs demand guardian agents by 2028 (Paper 5) | PUBLIC-MARKETING-derived — phrase as "industry analysts predict…" |
| E1/E2/E3 PR-AUC numbers (0.524 / 0.550 / 0.545) | **MEASURED** (repo) — citable, reproducible via `./verify.sh` |
| 141/149 tests pass | **MEASURED** (repo) — 141 passed + 8 skipped (Postgres+Redis path; full suite w/ Docker services = 149). Final count locked by Track V 11-g. |
| Patent numbers US20240012345A1, US20230187654B2, WO2024/098765A1 | **SUSPECT-FABRICATED** — DO NOT CITE in pitch deck (per V3 §21) |

Rule: **measured > cited > assumed > omitted.**

---

## Cross-references

- **Engineering bibliography** (18 papers with DOIs + local PDFs in
  `docs/research/`): [`research/INDEX.md`](research/INDEX.md).
  Covers He & Garcia 2009 (imbalanced data), Bahnsen 2013 (BMR),
  Drummond-Holte 2006 (cost curves), Gama 2014 (drift), Kandula 2021
  (e-commerce delivery), Hu 2025 (logistics SHAP), TFX Baylor 2017,
  Paleyes 2022 (deploying ML), SoK Mao 2026 (agentic commerce
  security), Amariles 2026 (AI agents in payments), Goodman &
  Flaxman 2017 (right to explanation).
- **Paper skills → code gaps map** (14 rows, the engineering bridge
  between the 40-paper KB and the code improvements): see the
  engineering bibliography cross-references in [`ARCHITECTURE.md`](ARCHITECTURE.md)
  §5 (decision precedence) + §6 (tech stack) for the paper-to-component
  mappings.
- **Architecture + scaling analysis**: [`ARCHITECTURE.md`](ARCHITECTURE.md).
- **Model card** (training data, metrics, limitations, bias):
  [`MODEL_CARD.md`](MODEL_CARD.md).
- **Pitch script** (5-min video, 3-act): [`PITCH_SCRIPT.md`](PITCH_SCRIPT.md).
