# Pitch video script (5:00)

## 0:00-0:30 Hook: the money that walks away
"In India, up to 3 in 10 cash-on-delivery orders never come back - the customer refuses,
the courier returns, the merchant eats the shipping both ways. Each failure costs roughly
12x what a verification call costs. Razorpay's RTO Shield works at pincode level. I asked
one question: where does the actual predictive signal live?"

## 0:30-1:30 What I built (demo the ladder)
Show `verify.sh` output. "Three experiments, all measured on held-out customers -
group-split so repeat buyers can't leak across train and test. Order features alone:
PR-AUC 0.52. Add address quality - complete vs vague - 0.55. I also tested state-level
postal infrastructure... it added nothing, so I cut it and documented why." Point at the
results table in README.

## 1:30-2:30 The trust layer (live demo)
Run `scripts/demo_agent.py`. Walk through the three orders: prepaid repeat buyer sails
through at 0.1. Twelve-thousand-rupee COD, vague address, tier-3, new customer: 64.2,
blocked to manual review - and here's WHY: city tier plus order value, printed right there.
Third order: customer with three prior returns gets REVIEW - selective OTP, partial-COD.
"Every money action is explainable, bounded, and gated."

## 2:30-3:30 Break something on purpose
Send amount_inr: -500. HTTP 422, and the agent falls back to hold-and-notify.
"Nothing is ever silently approved. And every single decision - accept, review, reject -
lands in an append-only audit record with the exact request, the model version, and the
ranked causes." Show out/audit.jsonl.

## 3:30-4:30 Honest numbers, honest economics
Show docs/cost_table.md. "At best-F1 threshold my precision is only 48 percent - and that's
fine, because the cost math says catch wide: recall 79 percent at threshold 0.15, applied
through cheap interventions, which matches industry results of cutting COD fraud 78-84
percent at under 7 percent conversion impact. I also report what didn't work - coarse geo
features - because you should trust the numbers I do show."

## 4:30-5:00 Roadmap + ask
"Next steps need real labeled data: swap this synthetic dataset for real Amazon.in order
history, then pincode-level intelligence using India Post's open directory, then plug this
endpoint into an Agent Studio agent as its risk tool. The code is public, tests pass, the
audit trail is real. I'm Neeraj - let's stop the money walking away."
