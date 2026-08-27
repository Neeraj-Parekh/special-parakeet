# RTO Trust Layer — Cross-Verification Matrix
## The honest "what's actually REAL vs STUB vs DECORATIVE vs FALSE" scorecard
## Source: 5 parallel Explore subagents (10-a through 10-e), each verifying specific claims

> **Read this file THIRD** (after `00-MASTER-PLAN.md` and `08-SESSION-SNAPSHOT.md`).
> The prior session's scorecard (`07-EXECUTION-LOG.md` "13/14 tracks done, 105 tests passing") was self-reported.
> This file is the cross-check against the actual code. **34 of 48 claims are REAL (~71%). The rest need work.**

---

## 0. Headline scorecard

| Category | Count | % of 48 |
|---|---|---|
| **REAL** (code exists, wired in, tests cover it) | 34 | 71% |
| **PARTIAL / STUB-with-inaccurate-claim** (code exists, doesn't do what was claimed) | 7 | 15% |
| **DECORATIVE** (code exists, never invoked, or tautological tests) | 3 | 6% |
| **FALSE / INACCURATE claim** (claim contradicts code) | 2 | 4% |
| **WEAK tests** (REAL code, decorative tests) | 2 | 4% |
| **MISSING** | 0 | 0% |

**Real test count**: 117 passed + 8 skipped = 125 total (NOT 105 as the prior worklog claimed, NOT 93 as README/PITCH_SCRIPT say).

**Honest verdict**: The project IS substantial and most of the claimed work is genuinely real. But there are **17 specific gaps** ranging from "stale test count in docs" to "real production-correctness bug (Merkle sealing isn't atomic, dual-control HMAC chain is fake, mandate caps reset on process restart)". These 17 are the next real work.

---

## 1. The 17 gaps, tiered by severity

### Tier 1 — Production correctness bugs (MUST fix; these break claimed behavior)

> These are the gaps where the code claims to do X but actually does Y. A judge who reads the code will catch these. A real production deployment would fail on these.

| # | Gap | File:line | What's wrong | Fix | Paper/skill source |
|---|---|---|---|---|---|
| **T1.1** | Dual-control "HMAC chain" is FAKE | `routes.py:1415-1418` | Stores two INDEPENDENT `sha256-truncate-16` digests of each admin key. NO HMAC. NO chaining between `signature_1` and `signature_2`. | Implement real HMAC: `signature_2 = HMAC(key=admin2_key, msg=signature_1 + canonical(body) + timestamp)`. Verify on submit. | SoK Mao 2026 `audit_agent_mandate_scoping` |
| **T1.2** | Merkle sealing is NOT atomic | `logger.py:612,616` | `self._conn.commit()` (audit INSERT) happens BEFORE `sealer.add()` (sealer INSERT/UPDATE). If sealer fails, audit row is committed without Merkle interval — tamper-evidence can silently break. | Wrap audit INSERT + sealer.add + sealer INSERT/UPDATE in a single Postgres transaction. Roll back all on any failure. | SoK Mao 2026 + V3 §10.3 |
| **T1.3** | `test_merkle_proof_reconstructs_root` is tautological | `tests/test_v3_endpoints.py:155` | Contains `or True` making the assert always pass. The follow-up `assert root == _merkle_root(leaves)` calls the same function twice (also tautological). | Remove `or True`. Fix `MerkleSealer.proof`'s position bookkeeping (sibling_idx XOR for odd leaf indices is asymmetric — likely the bug that drove the `or True` placeholder). Add real test that reconstructs root from proof path. | RFC 6962 |
| **T1.4** | Mandate cumulative counters NOT persisted | `mandates.py:47-49` | `_cumulative_monthly` / `_cumulative_24h` / `_last_activity` are in-memory dicts. Reset on every process restart. So ₹15k/month cap and 6-mo auto-revoke are real within a single process but UNENFORCED across redeploys. | Add `mandate_counters` table to Alembic migration 003. Persist cumulative counters + last_activity timestamp. Read on every `verify_mandate()` call. | NPCI OC-201B |
| **T1.5** | `BoundedAgent` not in production path | `scripts/demo_agent.py:72` + `routes.py` (no import) | The 7-action allowlist is a doc claim. `routes.py` uses `verify_mandate()` directly via X-Mandate header — NO server-side enforcement of "agent can only call N APIs". Mission 3 ("agent can only call 4 APIs") is NOT what's shipping. | Add server-side `agent_action` middleware: check `X-Agent-Action` header against allowlist, return 403 if not allowed. Move `ALLOWED_ACTIONS` dict from `scripts/demo_agent.py` to `src/api/agent_allowlist.py` so prod imports it. | SoK Mao 2026 `audit_agent_mandate_scoping` |
| **T1.6** | `test_db.py` swallows ALL alembic failures | `tests/test_db.py:80-81` | `except Exception: pass # pragma: no cover — assume schema is already applied`. If `alembic upgrade head` fails for ANY reason (broken migration, bad DSN, perms), tests silently proceed and fail later with confusing table-not-found errors. | Remove the try/except. Let alembic failures surface clearly. Add `pytest.skip()` ONLY for the specific `psycopg.OperationalError` "connection refused" case. | — |
| **T1.7** | `/v1/audit/{id}/proof` takes wrong identifier | `routes.py:1543` | Endpoint takes `record_id:int` (internal SERIAL PK). But `/risk/score` returns `audit_id` (string). External verifier can't drive the proof endpoint from what the API returns — they'd need direct DB read access. | Change route to accept `audit_id` string. Look up the `record_id` internally. | RFC 6962 + SoK Mao 2026 |

### Tier 2 — Paper-skill application gaps (the user's directive: deep paper work, NOT web design)

> These are the gaps where a paper's headline result is half-wired or never invoked. Fixing these is exactly the "use skills in MD files to improve the work even more, deeper" the user asked for.

| # | Gap | File:line | What's wrong | Fix | Paper/skill source |
|---|---|---|---|---|---|
| **T2.1** | Bahnsen Eq.(5) per-amount FN cost NOT in 3-way decision | `routes.py:566` | `optimal_decision(proba, **DEFAULT_COST_WEIGHTS)` is called WITHOUT `amount_inr`. Uses constant `c_fn=600`. So cost-optimal ACCEPT/REVIEW/REJECT is the SAME for a ₹600 order and a ₹52,000 order at the same probability — opposite of the Bahnsen 2013 paper's headline. The 5-way `optimal_intervention()` at line 578 DOES get `order.amount_inr`, but that's the secondary field, not the primary decision. | Pass `amount_inr=order.amount_inr` to `optimal_decision()` at routes.py:566. Update `test_ship.py:271-313` to assert different decisions at same probability for different amounts. | Bahnsen 2013 ICMLA `bayes_minimum_risk_decision_layer` |
| **T2.2** | `calibrate_probabilities()` (Bahnsen Eq.6) is DEAD CODE | `cost_optimizer.py:259` + `routes.py:32` | Function correctly implements `P*(f\|x) = P(f\|x)·P_orig/P_und` (recalibration after under-sampling). Imported at `routes.py:32` but NEVER CALLED outside tests. | Store `p_orig`/`p_und` in model artifact (ModelRegistry row). Call `calibrate_probabilities(proba, p_orig, p_und)` before `optimal_decision()` in routes.py:566. | Bahnsen 2013 ICMLA `post_resampling_probability_calibration` |
| **T2.3** | `/v1/usage` per-merchant claim is FALSE | `routes.py:1795-1801` | Implementation is AGGREGATE. Docstring admits "multi-tenant merchant_id is not yet implemented — the counts are aggregate". `merchant_id` is NOT wired from `/risk/score` into the audit body. | Add `merchant_id` field to `OrderIn` schema. Persist in audit records. Query per-merchant in `/v1/usage` (last 24h/7d/30d). | Microsoft Activator parity (G3) |
| **T2.4** | `observe_summary()` defined but NEVER INVOKED | `metrics.py:41` | Gama §5 detector-quality metrics `rto_drift_detection_delay_seconds` + `rto_drift_false_alarm_run_length` are DECORATIVE — defined but no caller. | Call `state["metrics"].observe_summary("rto_drift_detection_delay_seconds", delta_ts)` from `LabelFeedbackService.ingest_label` on DRIFT detection. Track `false_alarm_run_length` on each STABLE-after-WARNING case. | Gama 2014 CSUR §5 `evaluate_streaming_model` |
| **T2.5** | StreamProcessor HLL doesn't drive a detector | `processor.py:108,180-206,311` | `_seen_order_ids: dict` is the in-memory EXACT counter used by `_detect_anomalies`. Redis HLL (`PFADD`/`PFCOUNT`) only published as `cardinality_estimate_per_min` field — never drives a detector decision. The "TFX `generate_data_statistics` port" claim is half-real. | Have `_detect_anomalies` use HLL for cross-process cardinality (when the in-memory dict exceeds N entries, fall back to HLL). Add a 4th detector: HLL-cardinality-spike (sudden burst of new `order_id`s across processes). | TFX Baylor 2017 `generate_data_statistics` |
| **T2.6** | mlops.yml Stage 6 + 7 are `echo` no-ops | `mlops.yml:374-377,429-434` | Stage 6 "blue-green deploy" is `echo "Blue-green deploy of ghcr.io/..."`. Stage 7 "auto-rollback" is `echo "::warning::ROLLBACK..."`. ARCHITECTURE.md §8 markets these as real. `check_error_rate.py` IS real (queries Prometheus), but the action triggered by its failure is a no-op. | Either (a) implement actual `kubectl`/`helm` deploy + rollback (heavy — needs a K8s cluster), OR (b) rewrite ARCHITECTURE.md §8 to say "Stage 6-7 are deploy hooks — for hackathon demo, `check_error_rate.py` is the real monitor; deploy/rollback are documented prod patterns, not sandbox-runnable." Take option (b) — V3 says no half-baked IaC. | TFX Baylor 2017 + Paleyes 2022 `plan_three_axis_cicd` |
| **T2.7** | RESEARCH.md DOI claim INFLATED | `docs/RESEARCH.md:106,159,217` | Only Papers 1 (`doi.org/10.26599/BDMA.2024.9020015`) and 2 (`doi.org/10.3390/math14010021`) have actual DOIs. Papers 3 (Liminal), 4 (Pragma), 5 (Atlan) are vendor industry briefs cited by URL. | Reframe to "5 pitch papers (2 peer-reviewed w/DOIs, 3 industry briefs w/URL citations)". | — |

### Tier 3 — Test coverage gaps (less critical but should fix for credibility)

| # | Gap | File:line | What's wrong | Fix |
|---|---|---|---|---|
| **T3.1** | HyperLogLog actively stubbed in tests | `tests/test_streaming.py:456-457` | `proc._hll_add_order = lambda oid, bucket: None` + `proc._hll_count_orders = lambda bucket: None` — the Redis `PFADD`/`PFCOUNT` path in `processor.py:180-206` is NEVER exercised by any test. | Add a fakeredis-backed test that asserts `PFCOUNT` increases as distinct `order_id`s are `PFADD`ed. |
| **T3.2** | No end-to-end DRIFT test via `/v1/feedback/ingest` | `tests/test_feedback.py` | `test_feedback_ingest_endpoint` only posts 1 label with `prediction_id="pred-nonexistent-12345"` → `predicted_p=None` → `error=0` → STABLE. NO test triggers DRIFT via the endpoint and asserts the shadow-retrain notification fires. | Add a test that posts 30+ wrong-prediction labels to drive `p` past `p_min + 3·σ_min`, assert `state["feedback"]._producer.publish("notifications", {... "type": "retrain_request"})` was called. |
| **T3.3** | BoundedAgent has zero test coverage | `scripts/demo_agent.py:72-147` | 22 mandate tests + 0 BoundedAgent tests. Only manual `python scripts/demo_agent.py` exercises the 7-action allowlist. | Add `tests/test_bounded_agent.py`: assert `dispatch("refund_order")` → 403/REVIEW; `dispatch("upi_circle_delegated_pay", amount_inr=6000)` → BREACH (cap exceeded); `dispatch("upi_circle_delegated_pay", amount_inr=3000)` → VALID. |
| **T3.4** | 5 drift Prometheus gauges — only 3 tested | `tests/test_feedback.py:296-298` | `test_feedback_metrics_endpoint_exposes_drift_gauges` only asserts `rto_drift_ddm_state`, `rto_drift_adwin_state`, `rto_drift_samples_processed`. Doesn't check `rto_drift_ddm_p` or `rto_drift_adwin_window_len`. | Add assertions for the 2 missing gauges. |
| **T3.5** | Stale test count in 3 docs | `README.md:68,122` + `PITCH_SCRIPT.md:45,176` + `RESEARCH.md:270` | All 4 locations say "93 tests pass". Worklog said "105". Actual is 117 passed + 8 skipped (125 total). | Update all 4 to "117 passed + 8 skipped (Postgres+Redis path; full suite w/ Docker services = 125)". |
| **T3.6** | Dashboard uses `n_resamples=100` not ≥500 | `dashboard/index.html:111` | The endpoint default at `routes.py:103` is 500 (Drummond-Holte §3.6 recommendation). Dashboard fetches with `?n_resamples=100` for latency. | Either (a) bump dashboard to `n_resamples=500` + accept ~3-5s latency, OR (b) add a UI toggle "Fast (100 resamples) / Rigorous (500 resamples)" and default to Fast. |
| **T3.7** | mlops.yml stale test count claim | `.github/workflows/mlops.yml` | Stage 3 training gate says `if pr_auc < 0.60: sys.exit(1)`. But the gate runs against whatever model — there's no test that the gate actually fires on a low-PR-AUC model. | Add a CI test that runs `mlops.yml` Stage 3 against a deliberately-bad model and asserts the gate exits 1. |

---

## 2. What the prior session got RIGHT (preserve — don't lose)

These 34 claims ARE genuinely real. The work is not fake — it's just optimistic in places.

### Cost-optimizer (Track C + N) — 6 of 7 REAL
- ✅ `optimal_decision()` IS called in routes.py:566 (the live decision path)
- ✅ Static `ACCEPT_T, REJECT_T = 0.15, 0.60` are NOT consulted in the decision path (only surfaced as `legacy_*` for backward-compat display)
- ✅ `test_decision_uses_cost_optimizer_not_static_thresholds` actually asserts the right things
- ✅ `/v1/policy/cost-curves` endpoint does real bootstrap CIs (≥500 default)
- ✅ 5-way `optimal_intervention(p, amount_inr)` IS the argmin over {ship, otp_verify, partial_cod, address_check, hold}
- ✅ Pragma 2025 effectiveness rates ARE in DEFAULT_INTERVENTION_WEIGHTS
- ✅ `intervention` + `intervention_costs` ARE in /risk/score response + audit payload
- ✅ Default demo keys ARE removed (`type="password" placeholder="Enter scorer key"`)

### Mandates (Track D) — 6 of 8 REAL
- ✅ All 7 actions in ALLOWED_ACTIONS dict (4 COD + 3 UPI Circle, correct costs/caps)
- ✅ `device_id` + `user_id` ARE in HMAC payload AND validated
- ✅ BH purpose code + mandate_type + device_id + user_id ARE in audit payload
- ✅ 12-value `verdict_reason` — every value is a real return path
- ✅ `MandateVerdict.REVIEW` cooling-period gate IS implemented (24h rolling window)
- ✅ ₹5000/txn + ₹15000/month caps ARE enforced server-side
- ✅ 5-device cap + 6-mo auto-revoke ARE implemented (mint-time + verify-time)
- ✅ 22 tests in test_mandates.py, 13 new UPI Circle, all pass

### DB + Merkle + dual-control (Track E + H) — 4 of 10 REAL
- ✅ 5 tables + 9 indexes in Alembic migration 001
- ✅ Dual-mode Postgres + file fallback IS real
- ✅ Idempotency TTLCache + Postgres IS wired
- ✅ MerkleSealer RFC 6962 padding IS correct
- ✅ `/v1/simulate` dry-run IS real (no audit write)
- ⚠️ `register_model` in lifespan is PARTIAL (only Postgres mode, untested)
- ⚠️ `/v1/audit/{id}/proof` route exists but takes wrong identifier
- ⚠️ Dual-control 2-key enforcement is REAL but "HMAC chain" is FALSE
- ⚠️ Merkle proof test is tautological (`or True`)

### Streaming + feedback + drift (Track F + G) — 7 of 10 REAL
- ✅ StreamProducer publishes to 5 topics (3 from /risk/score, model.drift from processor, notifications from label_service)
- ✅ StreamConsumer uses XREADGROUP + SIGTERM/SIGINT handler
- ✅ StreamProcessor runs real Redis PFADD/PFCOUNT (not `len(set())`)
- ✅ `/v1/feedback/ingest` accepts is_returned + prediction_id, replays through DDM
- ✅ DDM 2σ/3σ math IS correct (Gama §3.2)
- ✅ ADWIN Hoeffding bound IS implemented (`ε_cut = √((1/2m)·ln(4|W|/δ))`)
- ✅ 5 drift Prometheus gauges ARE populated live
- ✅ Grafana 4 → 8 panels (4 new drift panels)
- ⚠️ `observe_summary()` defined but never called (Gama §5 metrics DECORATIVE)
- ⚠️ HLL actively stubbed in tests
- ⚠️ No end-to-end DRIFT test via the endpoint

### Tests + CI + docs (Track J + K) — 11 of 13 REAL
- ✅ 117 tests + 8 skipped (full suite)
- ✅ ci.yml: 3 jobs, Postgres+Redis services, Alembic upgrade, leakage gate (`assert leak==0`), Trivy scan (`CRITICAL,HIGH + exit=1`), k6 load test
- ✅ 5 helper scripts (canary_gate.py 252L, check_error_rate.py 170L, profile_data.py 205L, slice_metrics.py 224L, validate_data.py 275L)
- ✅ mlops.yml PR-AUC≥0.60 gate IS real (`if pr_auc < 0.60: sys.exit(1)`)
- ✅ README is a product landing page (hero/prob/solution/quick-start/results/docs/identity)
- ✅ PITCH_SCRIPT is 3-act word-for-word with time-stamped stage directions
- ✅ ARCHITECTURE.md is 654 lines EXACT, has 3 Mermaid diagrams + 10x/100x/1000x scaling analysis
- ✅ MODEL_CARD.md is 403 lines (understated as 381), Google spec, is_cod reframed
- ✅ API_SPEC.md is 1385 lines (understated as 1239), 22 endpoints in 10 tags, real JSON req/resp bodies
- ✅ alert_rules.yml has exactly 5 rules with real PromQL expressions
- ✅ alertmanager.yml has routes + 3 receivers + inhibit_rules
- ✅ V2+V3 superseded banners present
- ⚠️ mlops.yml Stage 6 + 7 are `echo` no-ops
- ⚠️ RESEARCH.md DOI claim inflated (only 2 of 5 papers have DOIs)

---

## 3. What needs more serious work — the next work plan (per user directive)

> **User's directive**: "primary focus should NOT be web design — read papers, improve tech stack, work on code path, use skills in MD files to improve the work even more, deeper."

The 7 Tier-1 fixes + 7 Tier-2 fixes ARE exactly that. None of them are web design. All of them apply paper skills more deeply or fix production correctness. The 7 Tier-3 fixes are smaller polish.

### Proposed execution order (one track per day, parallel where possible)

**Day 5 (next) — Tier 1 production-correctness fixes (parallel subagents)**

- **Track P (11-a)** — Mandate production correctness (T1.4 + T1.5 + T3.3):
  - Persist cumulative mandate counters in Postgres (new Alembic migration 003)
  - Add server-side `agent_action` middleware (move ALLOWED_ACTIONS to `src/api/agent_allowlist.py`)
  - Write `tests/test_bounded_agent.py` (7 dispatch cases)
  - Source: SoK Mao 2026 `audit_agent_mandate_scoping`

- **Track Q (11-b)** — Merkle + dual-control correctness (T1.1 + T1.2 + T1.3 + T1.6 + T1.7):
  - Implement real HMAC chain for dual-control override
  - Wrap audit INSERT + sealer.add in single Postgres transaction
  - Remove `or True` from test_v3_endpoints.py:155, fix proof builder position bookkeeping
  - Remove `except Exception: pass` from test_db.py:80-81
  - Change `/v1/audit/{id}/proof` to accept `audit_id` string, look up `record_id` internally
  - Source: SoK Mao 2026 + RFC 6962

**Day 6 — Tier 2 paper-skill application (parallel subagents)**

- **Track R (11-c)** — Bahnsen Eq.(5) + Eq.(6) full wiring (T2.1 + T2.2):
  - Pass `amount_inr` to `optimal_decision()` in routes.py:566
  - Store `p_orig`/`p_und` in ModelRegistry row
  - Call `calibrate_probabilities(proba, p_orig, p_und)` before `optimal_decision()`
  - Update test_ship.py to assert different decisions at same probability for different amounts
  - Source: Bahnsen 2013 ICMLA `bayes_minimum_risk_decision_layer` + `post_resampling_probability_calibration`

- **Track S (11-d)** — Microsoft Activator parity (T2.3 + T2.4):
  - Add `merchant_id` field to `OrderIn` schema, persist in audit, query per-merchant in `/v1/usage`
  - Call `observe_summary("rto_drift_detection_delay_seconds", ...)` from `LabelFeedbackService.ingest_label` on DRIFT
  - Track `false_alarm_run_length` on STABLE-after-WARNING cases
  - Source: Microsoft Activator parity (G3) + Gama 2014 §5 `evaluate_streaming_model`

- **Track T (11-e)** — StreamProcessor HLL detector + mlops.yml honesty (T2.5 + T2.6 + T2.7):
  - Add 4th anomaly detector: HLL-cardinality-spike (cross-process burst detection)
  - Have `_detect_anomalies` use HLL when in-memory dict exceeds N entries
  - Rewrite ARCHITECTURE.md §8 to be honest about Stage 6-7 (echo no-ops documented as deploy hooks)
  - Reframe RESEARCH.md to "5 pitch papers (2 peer-reviewed w/DOIs, 3 industry briefs w/URL citations)"
  - Source: TFX Baylor 2017 `generate_data_statistics`

**Day 7 — Tier 3 test coverage + doc sync (parallel subagents)**

- **Track U (11-f)** — Test coverage gaps (T3.1 + T3.2 + T3.4 + T3.7):
  - fakeredis-backed HLL test
  - End-to-end DRIFT test via `/v1/feedback/ingest`
  - Assert all 5 drift gauges (not just 3)
  - CI test that mlops.yml Stage 3 gate fires on low-PR-AUC model

- **Track V (11-g)** — Doc sync (T3.5 + T3.6):
  - Update README/PITCH_SCRIPT/RESEARCH test count to "117 passed + 8 skipped"
  - Add UI toggle "Fast (100 resamples) / Rigorous (500 resamples)" to dashboard cost-curve explorer (this is the ONE web-design touch — justified because it's about the math credibility, not the visual)

### Triage (if time runs short — cut in this order)
1. ❌ Track V (doc sync) — important but not blocking
2. ❌ Track T mlops.yml honesty — the echo no-ops don't break anything, just market inflated
3. ❌ Track T HLL detector — nice-to-have, in-memory dict works for demo
4. ❌ Track S Gama §5 metrics — Prometheus gauges already work, the §5 metrics are nice-to-have
5. ❌ Track U fakeredis test — Redis path is skipped in CI anyway

**Never cut**: Track P (mandate production correctness), Track Q (Merkle + dual-control correctness), Track R (Bahnsen full wiring). These are the differentiators vs Microsoft Fabric and the paper-skill depth the user asked for.

---

## 4. What this means for the Razorpay submission

**Current honest state**: 34 of 48 verified claims are REAL. The project genuinely has:
- A working Next.js dashboard with 4 pages + 13 API routes + Copilot Q&A panel
- A working Python backend with cost-optimizer-wired decision + 5-way intervention + 7-action mandate allowlist + Merkle audit + DDM/ADWIN drift + Redis Streams + Postgres + Alembic
- 117 passing tests + 8 skipped (Postgres+Redis path)
- CI workflow with 3 jobs + 7-stage TFX-style mlops.yml
- 6 rewritten docs (~3,716 lines)

**What's still broken enough that a careful judge would notice**:
- The "dual-control HMAC chain" is just "2 keys required" — not cryptographically chained (T1.1)
- The Merkle sealing isn't atomic — tamper-evidence can silently break (T1.2)
- The mandate caps reset on process restart (T1.4) — production-credibility bug
- The `BoundedAgent` is not in the production path (T1.5) — Mission 3 false
- The Bahnsen per-amount FN cost doesn't drive the primary decision (T2.1) — paper headline half-wired

**After Days 5-7 (the proposed plan)**:
- All Tier 1 fixes done → no production-correctness bugs a careful judge would catch
- All Tier 2 fixes done → paper-skill depth genuine (Bahnsen full, Gama §5 metrics, Microsoft Activator parity, HLL detector)
- All Tier 3 fixes done → test coverage credible

**Net effect**: The "complex and big system that works in real" the user asked for becomes ACTUALLY real, not just claimed-real. The "doesn't look like a bloated gobble-up monster" requirement is met because we're not adding new features — we're making the claimed features actually do what they say.

---

## 5. How to resume if context is lost

1. Read `command/00-MASTER-PLAN.md` (single source of truth)
2. Read `command/08-SESSION-SNAPSHOT.md` (session recovery + infra diagnosis)
3. Read **THIS FILE** (`command/09-CROSS-VERIFICATION-MATRIX.md`) — the honest gap analysis
4. Check `command/03-WORK-ITEMS.md` for the original 43-item tracker (now superseded by this matrix for accuracy)
5. Pick a track from §3 above (Track P/Q/R/S/T/U/V)
6. Read the relevant file:line refs from §1 or §2
7. Spin a subagent with the Task ID (11-a through 11-g) and the file:line refs
8. Subagent appends to `worklog.md` per protocol

**Critical**: Do NOT trust the prior session's `07-EXECUTION-LOG.md` claims without checking this matrix. The log is aspirational; this matrix is verified.

---

*Last updated: Aug 27, 2026. Source: 5 parallel Explore subagents (10-a through 10-e) cross-verifying the prior session's claims against the actual Python code in `/home/z/my-project/upload/RTO_Trust_Layer_FULL/`. Each subagent's full report is in `/home/z/my-project/worklog.md` under their respective Task ID.*
