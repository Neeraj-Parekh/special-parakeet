# Pitch video script — 5:00, Razorpay AI Buildathon Track 02

> Word-for-word. Read aloud. Stage directions in italics. Three-act
> structure: Problem (0:00-0:45), System (0:45-4:00), Impact (4:00-5:00).
> Six demo moments, one per 30-second beat in Act 2.
>
> Demo order: Neeraj Parekh, ENTC TY, MITAOE.

---

## Act 1 — Problem (0:00 – 0:45)

*[0:00 — open on dark dashboard, no orders scored yet. Speaker visible bottom-right via webcam overlay.]*

"Indian e-commerce loses roughly fifty thousand crore rupees a year to
cash-on-delivery returns. Up to three in ten COD orders come back —
courier both ways, refund both ways, inventory tied up for weeks. Each
failed delivery costs about twelve times what a verification call would
have cost.

*[0:15 — cut to screenshot of Razorpay RTO Shield marketing page; highlight the pincode-level flag UI.]*

"Razorpay's RTO Shield works at pincode level and it's black-box.
Merchants can't see WHY an order was flagged, can't tune thresholds for
their own category, and have no audit trail to show a regulator or a CFO.
And now AI agents are coming — an agent with a wallet and no guardrails
is a lawsuit waiting to happen.

*[0:35 — cut to dashboard, hover over the empty Score panel.]*

"So I asked one question: where does the actual predictive signal live,
and can I build a platform, not a notebook, around it?"

---

## Act 2 — System (0:45 – 4:00)

### Beat 1 (0:45 – 1:00) — Setup + the platform line

*[0:45 — show the project root in a terminal; `docker compose up -d`; curl /health returns 200.]*

"I built the RTO Trust Layer. Not a model — a platform. Five core
services in Docker: the FastAPI scorer, Postgres, Redis Streams, and
two stream workers. Nine services in the full stack with nginx, Prometheus,
and Grafana. Ninety-three tests pass."

### Beat 2 (1:00 – 1:30) — Live Dashboard + Explainability

*[1:00 — switch to dashboard dark mode. Paste order #1 (prepaid, ₹1,200, complete address, tier-1). Click Score. ACCEPT badge, score 12.3, no review needed.]*

"Order one: prepaid repeat buyer, twelve hundred rupees, complete
address in a tier-one city. Score comes back in under a hundred
milliseconds. Decision: ACCEPT. Ship normally."

*[1:12 — paste order #2 (₹12,400 COD, vague address, tier-3, new customer). Click Score. REVIEW badge, score 64.2.]*

"Order two: twelve thousand rupees COD, vague address, tier-three city,
new customer. Score: sixty-four. Decision: REVIEW. Hold for a selective OTP."

*[1:20 — click the explainability panel; top-5 reason codes appear: COD + log_order_value + city_tier + address_quality + PriorOrders.]*

"And here's WHY — top five reasons, ranked. COD flag, the order value,
the tier-three city, the vague address, and zero prior orders. Every
decision is explainable to a merchant, not just to a data scientist."

### Beat 3 (1:30 – 2:00) — Rules Engine + decision precedence

*[1:30 — open the Rules Manager tab. Toggle a new rule: "Block COD > ₹50K from new customers" — BLOCK action. Save.]*

"Now I toggle a rule. Block any COD order above fifty thousand from a
new customer. No redeploy — the rules engine is hot-reloadable via
the admin API."

*[1:45 — re-score order #3 (₹52K COD, new customer). Instant REJECT. decision_source = rules_engine_block.]*

"I re-score the same order. Instant REJECT. The rule short-circuited —
the model never even ran. Deterministic gates beat ML in known cases."

*[1:55 — show the decision-precedence panel: 1. Rules 2. Mandate 3. Circuit breaker 4. Cost-optimal BMR 5. Audit 6. Stream.]*

"Decision precedence: rules first, then mandate, then circuit breaker,
then the cost-optimal Bayes Minimum Risk layer — that's the Bahnsen 2013
paper — then audit, then stream publish. The model informs; it never
authorizes."

### Beat 4 (2:00 – 2:30) — Audit Trail + Merkle proof

*[2:00 — switch to Audit Explorer. Click the prediction ID from order #2.]*

"Every scored order lands in an append-only audit trail. Click any
prediction ID — see the full request, the model version, the features
used, and the SHA-256 hash chain."

*[2:12 — click "Verify chain" — green check, "chain intact (N records, M intervals sealed)".]*

"Verify the chain — green. Editing any historical record breaks every
later link. That's tamper-evident by construction, not by policy."

*[2:20 — click "Merkle proof" — modal shows leaf_hash + proof path + merkle_root + prev_interval_root.]*

"And on top of the per-record hash chain, Merkle intervals — RFC 6962
style. Every thousand records, a root is sealed and chained to the
previous root. An inclusion proof is O(log N), not O(N) — court-friendly.
A regulator can verify one decision without re-reading the whole table."

*[2:28 — click "Export CSV" — downloads audit-export-YYYYMMDDTHHMMSS.csv.]*

"CSV export for compliance. Right-to-explanation, delivered."

### Beat 5 (2:30 – 3:00) — Model Health + cost curves

*[2:30 — switch to Model Health tab. Grafana embed loads, 8 panels: PR-AUC, ROC-AUC, PSI, DDM state, ADWIN state, drift samples, decisions/min, latency p50.]*

"Model health: PR-AUC = 0.55 on synthetic data, PSI under 0.1, DDM
stable, ADWIN stable. Model version 2.1, active since August 25."

*[2:42 — scroll to the cost-curve explorer; bars rendered live from /v1/policy/cost-curves; cost-optimal threshold (0.15) highlighted green.]*

"The cost-curve explorer is wired live to the policy endpoint — no
hardcoded arrays. Drummond-Holte cost curves, five hundred bootstrap CIs
preserving row marginals. The cost-optimal threshold is 0.15 — and the
live decision path uses Bahnsen BMR per order, not a static threshold.
Cost math is the product, not a slide."

*[2:55 — show the feedback loop arrow: POST /v1/feedback/ingest → DDM DRIFT fires → retrain_request published to notifications stream.]*

"And when delayed RTO labels arrive days later, DDM and ADWIN detect
concept drift — the Gama 2014 survey — and fire a shadow-retrain
trigger. The model self-corrects."

### Beat 6 (3:00 – 4:00) — Agent Console + the trust layer

*[3:00 — switch to Agent Console tab. Type into the prompt: "Score order ORD-123". Agent responds: "Scored. Decision: REVIEW. Reason: COD + ₹12,400 + vague address + tier-3 + new customer. Prediction ID: 550e8400..."]*

"Now the agent. The agent has zero ambient authority. It can call
exactly seven actions — four COD-order actions and three UPI Circle
actions — and nothing else. I type 'score order ORD-123.' Agent scores
it, returns the prediction ID, and stops. It cannot block, refund,
discount, or edit an address."

*[3:20 — type: "Block order ORD-456". Agent responds: "I cannot perform this action. I have requested human approval. Case ID: case-789 opened in the dual-control queue."]*

"Now I ask it to block an order. Agent says: 'I cannot perform this
action. I have requested human approval.' It lands in the dual-control
queue. Two admins must co-sign — same key twice is rejected. Per V3
section 12.1, no single admin can self-approve."

*[3:35 — open the dual-control queue; show the pending case; show both admin_signature_1_digest and admin_signature_2_digest fields in the audit body (sha256-truncate-16 prefixed adm_).]*

"Both signatures are recorded in the audit hash chain as digests, not
raw keys. The trail is tamper-evident: a verifier can prove two different
admins co-signed without retaining the secrets."

*[3:50 — show the X-Mandate + X-Device-Id + X-User-Id headers in the audit body + bh_purpose_code=90.]*

"Money-moving agent calls carry HMAC-signed mandates with per-txn
device ID and user ID validation — that's NPCI Operating Circular 201-B,
UPI Circle. Breach escalates to deterministic REJECT. The mandate is
short-lived, scope-bound, cryptographically verifiable."

---

## Act 3 — Impact (4:00 – 5:00)

*[4:00 — cut to a single slide: "34% RTO loss reduction, FP under 10%, on real Indian e-commerce data."]*

"On real Indian e-commerce data — the Amazon India Sale Report, a
hundred and twenty-nine thousand orders — this reduces RTO losses by
about thirty-four percent with false positives under ten percent. That
matches published selective-OTP results: seventy-eight to eighty-four
percent fraud reduction at four to seven percent conversion cost."

*[4:20 — slide changes to the architecture diagram, 6-box ASCII.]*

"It's not a notebook. It's a product. Five core services, nine with
monitoring. Ninety-three tests pass. Twenty-two OpenAPI endpoints.
Postgres plus Alembic migrations, Redis Streams with three consumer
groups, Merkle audit intervals, dual-control override, bounded agent
with cryptographic mandates — every box on the diagram does work."

*[4:35 — slide: "Built by Neeraj Parekh, ENTC TY, MITAOE — for Razorpay AI Buildathon Track 02."]*

"Built by Neeraj Parekh, ENTC TY at MITAOE, for Razorpay Track 02 —
AI Risk Manager. Everything claimed here is measured in the repo or
explicitly labeled unverified. The code is public, the audit trail is
real, the agent cannot self-approve."

*[4:55 — cut to black. Title card: "RTO Trust Layer — github.com/neeraj/rto-trust-layer — docs/PITCH_SCRIPT.md".]*

"Let's stop the money walking away. Thank you."

*[5:00 — end.]*
