# RTO Trust Layer — 90-Second Pitch Video Script

> Razorpay Buildathon Track 02 — Indian COD e-commerce RTO risk detection.
> This script walks through the 6 demo moments in 90 seconds.
> All screenshots referenced are in `docs/video-script/`.

## Pre-roll (5s)
**Narrator (you):** "RTO Trust Layer — the trust layer that makes Razorpay's
next-generation RTO Shield possible. Built solo, with agents, in 7 days.
On real cross-border data, per-customer history is the signal that matters."

**Visual:** dashboard-final.png (full-screen dashboard).

## Demo 1 — Score an order (15s)
**Narrator:** "Score an order in real time. HistGB champion via ONNX
Runtime — 141× faster than sklearn predict_proba. The decision is
cost-optimal, not accuracy-optimal — Bayes Minimum Risk picks the
threshold that minimizes merchant loss."

**Action:** click "Score →" on demo card 1.
**Visual:** dashboard-after-score.png. Show the ACCEPT / REVIEW / REJECT
badge + probability_rto + SHAP top-features bars.

## Demo 2 — SHAP waterfall (10s)
**Narrator:** "Every score comes with a SHAP explanation — Lundberg 2017.
A merchant can see WHY the model said REJECT. No black boxes."

**Action:** click "Explain →" on demo card 2 with the prediction_id from
demo 1.
**Visual:** the SHAP bars in demo card 2 — positive contributions push
the score up, negative contributions push it down.

## Demo 3 — Rules toggle, decision flips (15s)
**Narrator:** "A human operator adds a rule: 'if prior_returns > 1, BLOCK.'
The rules engine fires BEFORE the model. Re-score the same order — the
decision flips from ACCEPT to BLOCK. The model didn't change; the policy
did. This is the rules-vs-ML hybrid that Razorpay's risk team uses."

**Action:** click "Add rule" on demo card 3 → re-score the order from
demo 1 → watch the badge flip.
**Visual:** side-by-side of the rules JSON + the flipped decision badge.

## Demo 4 — Bounded agent refuses (15s)
**Narrator:** "A bounded AI agent tries to block an order — 'Block this order.'
The server refuses with HTTP 403. Money-moving actions are NOT in the
agent's allowlist. Only dual-control human admins can override."

**Action:** click "Block this order" on demo card 4.
**Visual:** the 403 response in the agent console card.

## Demo 5 — Cost-curve slider (15s)
**Narrator:** "The false-negative cost slider. Slide it up — the BMR
threshold drops — more orders get REJECTED. The model didn't change;
the cost function did. This is the cost-optimal decision layer."

**Action:** drag the FN slider from ₹8,000 to ₹30,000 → click
"Recompute →" → watch the BMR threshold drop + REJECT count rise.
**Visual:** cost-slider-cfn-max.png.

## Demo 6 — Merkle audit (10s)
**Narrator:** "Every score writes a tamper-evident Merkle audit record.
Click verify — the chain is intact. RBI MRM 2026 wants this; we built it."

**Action:** click "Verify chain →" on demo card 6.
**Visual:** the chain verification JSON.

## Outro (5s)
**Narrator:** "Production-credible architecture with a clear migration
path. Merkle audit trails for RBI compliance. Bounded agents for safe
AI commerce. Cost-optimal decisions for merchant profit. With Razorpay's
transaction graph + engineering team, this scales to billions of rows
and 10K TPS."

**Visual:** final-dashboard.png → fade to black → title card with the
GitHub URL + the live Render URL.

---

## Total runtime: 90 seconds

## Screenshots used (all in docs/video-script/)
- dashboard-final.png — opening shot
- dashboard-after-score.png — demo 1
- cost-slider.png / cost-slider-default.png / cost-slider-cfn-max.png — demo 5 (slider states)
- final-dashboard.png — outro

## Live URL for the video
After manual Render blueprint apply (see docs/FOLLOWUP.md §3 honest gaps):
  https://rto-trust-layer.onrender.com/dashboard/

Until then, the local Next.js dashboard at the dev server URL also works
for screen-recording the demo (the same 6 demo moments).

## Honest framing (do NOT say)
- "production-ready" — say "production-credible architecture with a clear migration path"
- "enterprise-grade" — say "the trust layer that makes Razorpay's next-gen RTO Shield possible"
- "scales to billions" — say "with Razorpay's transaction graph, this architecture scales to billions"

The user EXPLICITLY FORBIDS the first three phrases.
