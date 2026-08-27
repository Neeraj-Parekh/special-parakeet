# API Specification — RTO Trust Layer

> REST API for the RTO Trust Layer scoring platform. OpenAPI 3.1
> machine-readable twin at [`openapi.json`](openapi.json) (auto-generated
> by FastAPI on every app reload; reflects the routes currently in
> `src/api/routes.py`). Interactive Swagger UI at `/docs` when the
> server is running.
>
> **22 endpoints** grouped by tag. Day 1-2 tracks (C/D/E/F/G/H) added
> the cost-curves, mandate expansion, streaming, feedback, Merkle
> proof, simulate, and metering endpoints; Day 3 Track K consolidated
> them here with Pydantic schemas + curl examples + error tables.

| Tag | Endpoints | Scope |
|---|---|---|
| Risk | `POST /risk/score`, `POST /risk/{id}/override`, `POST /v1/simulate` | scorer / admin |
| Audit | `GET /audit/{id}`, `GET /v1/audit/verify-chain`, `GET /v1/audit/{id}/proof`, `GET /v1/compliance/audit-export`, `GET /v1/compliance/model-card` | admin (one scorer-OK) |
| Rules | `GET /v1/rules`, `POST /v1/rules`, `DELETE /v1/rules/{id}` | scorer / admin |
| Cases | `GET /v1/cases`, `POST /v1/cases/{id}/resolve` | admin |
| Models | `GET /v1/models/current`, `GET /v1/models/drift` | scorer / admin |
| Policy | `GET /v1/policy/optimal`, `GET /v1/policy/cost-curves` | scorer |
| Mandates | `POST /v1/mandates` | admin |
| Feedback | `POST /v1/feedback/ingest` | admin |
| Metering | `GET /v1/usage` | admin |
| Health | `GET /health`, `GET /metrics` | public |

---

## 1. Overview

The RTO Trust Layer API is a FastAPI application exposing 22 REST
endpoints over HTTPS (nginx terminates TLS 1.2/1.3 + security headers;
Track B Day 1). The base URL is `https://<host>/` (or `http://localhost:8000/`
in dev). All paths are URL-versioned (`/v1/...`) except the legacy
`/risk/score`, `/audit/{id}`, `/health`, `/metrics` (kept for
backward-compat with the original v0.3 ship).

The API does three things: (1) **score** COD orders for RTO risk
returning `ACCEPT / REVIEW / REJECT` with explanations, (2) **prove**
every decision via a tamper-evident audit hash chain + Merkle interval
sealing, and (3) **bound** AI agent actions via HMAC-signed mandates +
dual-control human approval for money-moving operations.

Latency target: p99 ≤ 150 ms for `POST /risk/score` (measured ~35-60
ms local single-node). Rate limit: 25 req/s burst 50 per IP at nginx
(Track B). Idempotency: optional `Idempotency-Key` header on POST
endpoints (file mode: TTLCache 24h, 10k cap; Postgres mode: dedicated
table with `expires_at`).

---

## 2. Authentication & scopes

### Getting keys

API keys are configured at app start via env vars (see `src/api/security.py`):

| Env var | Format | Scope |
|---|---|---|
| `RTO_SCORER_KEYS` | Comma-separated list of scorer-scope keys | `scorer` |
| `RTO_ADMIN_KEYS` | Comma-separated list of admin-scope keys | `admin` |

The Dockerfile no longer bakes default keys (Track B Day 1 — removed
the `change-me-scorer` / `change-me-admin` ENV defaults). For local
dev, the file-mode fallback uses demo keys printed to stderr on
first boot. **Never ship demo keys to production.**

### Passing keys

All authenticated endpoints expect:

```http
Authorization: Bearer <api-key>
```

The key is hashed (sha256) + compared in constant time. Invalid or
missing keys return `401 invalid api key`. Scope mismatches return
`403 <reason> requires <scope> scope`.

### Scopes

| Scope | Can | Cannot |
|---|---|---|
| `scorer` | `POST /risk/score`, `POST /v1/simulate`, `GET /v1/rules`, `GET /v1/models/current`, `GET /v1/policy/optimal`, `GET /v1/policy/cost-curves`, `GET /v1/compliance/model-card` | mint mandates, override decisions, CRUD rules, read audit, ingest feedback, view usage, view Merkle proofs |
| `admin` | All scorer endpoints + `POST /v1/mandates`, `POST /risk/{id}/override` (dual-control), `POST / DELETE /v1/rules`, `GET /v1/cases`, `POST /v1/cases/{id}/resolve`, `GET /audit/{id}`, `GET /v1/audit/verify-chain`, `GET /v1/audit/{id}/proof`, `GET /v1/compliance/audit-export`, `GET /v1/models/drift`, `POST /v1/feedback/ingest`, `GET /v1/usage` | (nothing above scorer + admin) |
| `public` | `GET /health`, `GET /metrics`, `GET /dashboard/` | everything else |

JWT RS256 with 5-min expiry is added per V2 §6 (Track J Day 3); for
the demo, bearer API keys are sufficient.

### Request / response conventions

- All request bodies are `application/json` (Pydantic-validated).
- All responses are `application/json` except `/v1/compliance/audit-export`
  (CSV), `/metrics` (Prometheus text), `/health` (JSON).
- Date/time fields are ISO 8601 with timezone (`2026-08-27T01:23:45.678901+00:00`).
- PII is salted+hashed into digests (`cust_<sha256-truncate-16>`).
- Errors return `{"detail": "<message>"}` with the appropriate HTTP status.
- The internal-error incident ID (UUID) is logged to stderr; the
  response body never leaks stack traces.

---

## 3. Endpoints

### 3.1 Risk — `POST /risk/score`

The primary endpoint. Scores a COD order for RTO risk and returns the
cost-optimal BMR decision (Bahnsen 2013) with explanations, the
audit URL, and (on REVIEW) the case ID.

**Auth:** `scorer` scope.

**Headers:**

| Header | Required | Purpose |
|---|---|---|
| `Authorization: Bearer <scorer-key>` | yes | scorer auth |
| `Idempotency-Key: <uuid>` | optional | deduplicates retries for 24h (file) or until `expires_at` (Postgres) |
| `X-Mandate: <HMAC-token>` | optional | agent-initiated; admin-minted bounded mandate (UPI Circle / cod_order) |
| `X-Device-Id: <id>` | optional | UPI Circle per-txn device (OC-201B §3.7); validated against mandate's allowlist |
| `X-User-Id: <id>` | optional | UPI Circle per-txn user (OC-201B §3.3); validated against mandate's user_id |

**Request body** (`OrderIn`):

```json
{
  "order_id": "ORD-12345",
  "amount_inr": 12400.0,
  "category": "Electronics",
  "customer_id": "CUST-67890",
  "address_quality": "vague",
  "city_tier": "tier_3",
  "payment_method": "COD",
  "prior_orders": 0,
  "prior_returns": 0,
  "items": 1,
  "order_hour": 14,
  "device": "Android App"
}
```

| Field | Type | Default | Validation |
|---|---|---|---|
| `order_id` | string | (required) | min 3, max 64 chars |
| `amount_inr` | float | (required) | > 1, ≤ 1,000,000 |
| `category` | string | (required) | min 2, max 32 chars |
| `customer_id` | string | (required) | min 3, max 64 chars; salted+hashed into audit |
| `address_quality` | string | `"complete"` | one of `complete\|partial\|vague` |
| `city_tier` | string | `"tier_2"` | one of `tier_1\|tier_2\|tier_3` |
| `payment_method` | string | `"COD"` | one of `COD\|Prepaid` |
| `prior_orders` | int | `0` | 0-10,000 |
| `prior_returns` | int | `0` | 0-10,000 |
| `items` | int | `1` | 1-100 |
| `order_hour` | int | `12` | 0-23 |
| `device` | string | `"Android App"` | max 32 chars |

**Response — 200 OK:**

```json
{
  "prediction_id": "550e8400-e29b-41d4-a716-446655440000",
  "risk_score": 64.2,
  "probability": 0.6418,
  "decision": "REVIEW",
  "decision_source": "cost_optimal_bmr",
  "cost_breakdown": {
    "ACCEPT": 385.08,
    "REVIEW": 122.04,
    "REJECT": 358.2
  },
  "intervention": "otp_verify",
  "intervention_costs": {
    "ship": 7958.32,
    "otp_verify": 1436.0,
    "partial_cod": 2840.0,
    "address_check": 4481.0,
    "hold": 2404.5
  },
  "intervention_weights": {
    "c_ship_fp": 50.0, "c_ship_fn": 0.0,
    "c_otp": 5.0, "c_otp_effectiveness": 0.82,
    "c_partial_cod": 10.0, "c_partial_cod_effectiveness": 0.65,
    "c_address_check": 3.0, "c_address_check_effectiveness": 0.45,
    "c_hold": 20.0, "c_hold_fn": 0.0,
    "c_block": 1000.0, "c_hold_residual_ship_rate": 0.30
  },
  "gate_thresholds": {
    "policy": "cost_optimal_bmr",
    "weights": {
      "c_fp": 50.0, "c_fn": 600.0, "c_otp": 5.0,
      "c_block": 1000.0, "otp_effectiveness": 0.82
    },
    "legacy_accept_t": 0.15,
    "legacy_reject_t": 0.60
  },
  "explanation": [
    {"feature": "city_tier",       "value": "tier_3", "delta_prob": 0.419, "direction": "raises_risk"},
    {"feature": "log_order_value", "value": 9.43,    "delta_prob": 0.268, "direction": "raises_risk"},
    {"feature": "is_cod",          "value": 1,       "delta_prob": 0.180, "direction": "raises_risk"},
    {"feature": "PriorReturns",    "value": 0,       "delta_prob": 0.115, "direction": "raises_risk"},
    {"feature": "PriorOrders",     "value": 0,       "delta_prob": 0.070, "direction": "raises_risk"}
  ],
  "rule_fired": null,
  "degraded": false,
  "policy_hint": "REVIEW",
  "model_version": "v20260827T1430",
  "latency_ms": 42,
  "case_id": "case_a1b2c3d4",
  "mandate": {
    "verdict": "valid",
    "note": null,
    "verdict_reason": "ok",
    "mandate_type": "cod_order",
    "bh_purpose_code": null
  },
  "audit_trail_url": "/audit/aud_5ddf72cb-...",
  "timestamp": "2026-08-27T14:30:15.123456+00:00"
}
```

**`decision` vs `intervention` (Track N — V3 §11.6):**

The 3-way `decision` field (ACCEPT / REVIEW / REJECT) is the primary
authorization signal — Track C's Bahnsen BMR `optimal_decision()` with
the constant `c_fn=600` default. The 5-way `intervention` field
(ship / otp_verify / partial_cod / address_check / hold) is the
cost-optimal *next-step recommendation* for the operator — Track N's V3
§11.6 `optimal_intervention()` with the per-amount FN cost from Bahnsen
Eq.(5) (`FN = amount_inr`, not constant). The two layers compose:

- `decision=ACCEPT` + `intervention=ship` → ship the order as-is.
- `decision=REVIEW` + `intervention=otp_verify` → hold for OTP, ship on verify.
- `decision=REVIEW` + `intervention=address_check` → call-center address check.
- `decision=REVIEW` + `intervention=hold` → queue for manual review.
- `decision=REJECT` + `intervention=null` → block (no 5-way recommendation).

When the 3-way decision short-circuits (rules BLOCK, mandate BREACH,
degraded REVIEW), `intervention` is `null` — the 5-way policy only fires
when the cost-optimizer's primary path runs.

**`intervention_costs` breakdown:**

The full 5-way cost breakdown in INR. Each cost is the expected monetary
loss for that intervention given the per-transaction FN cost = `amount_inr`
(Bahnsen Eq.(5)) and the per-intervention effectiveness rate. The argmin
of this dict is the `intervention` field. Used by the dashboard for
explainability + by the operator to override the recommendation when the
cost-model's weights don't match local reality.

**`intervention_weights`:**

The 5-way cost-model weights actually used (a copy of
`DEFAULT_INTERVENTION_WEIGHTS` from `src/business/cost_optimizer.py`).
Surfaces the provenance of the recommendation — the per-intervention
effectiveness rates from the Pragma 2025 RTO-mitigation benchmark (OTP
0.82, partial COD 0.65, address check 0.45) and the per-intervention
fees. The operator / dashboard can verify the assumptions behind the
recommendation.

**`decision_source` vocabulary** (which layer chose the decision):

| Value | When |
|---|---|
| `rules_engine_block` | a BLOCK rule fired (no model call) |
| `mandate_breach` | mandate amount/cap/device/user breach |
| `mandate_review_required` | UPI Circle 24h cooling period (OC-201B) |
| `mandate_invalid` | mandate TAMPERED or EXPIRED with `X-Mandate` header present |
| `degraded_review` | circuit breaker OPEN or model failure → degraded rules-only REVIEW |
| `cost_optimal_bmr` | primary path: `optimal_decision(p, weights)` chose the decision |
| `cost_optimal_bmr_review_rule` | BMR chose ACCEPT but a REVIEW rule forced REVIEW |

**Decision precedence** (earlier short-circuits later):

1. Rules fast-path BLOCK → REJECT
2a. Mandate BREACH → REJECT
2b. Mandate REVIEW (UPI Circle 24h cooling) → REVIEW
2c. Mandate TAMPERED/EXPIRED-with-header → REJECT
3. Circuit breaker OPEN → degraded rules-only REVIEW
4. `optimal_decision(p, weights)` → ACCEPT/REVIEW/REJECT (primary)
   - REVIEW rule gate: forces REVIEW even if BMR chose ACCEPT

**Errors:**

| Status | When |
|---|---|
| 401 | missing / invalid scorer key |
| 422 | `OrderIn` validation failed (e.g., `amount_inr <= 0`, `address_quality` not in enum, non-finite floats rejected) |
| 429 | rate limit exceeded (25 r/s burst 50 per IP) |
| 500 | internal error (logged with incident UUID; never leaks stack) |

**curl example:**

```bash
curl -X POST http://localhost:8000/risk/score \
  -H "Authorization: Bearer score-demo-key" \
  -H "Idempotency-Key: 550e8400-e29b-41d4-a716-446655440000" \
  -H "Content-Type: application/json" \
  -d '{
    "order_id": "ORD-12345",
    "amount_inr": 12400.0,
    "category": "Electronics",
    "customer_id": "CUST-67890",
    "address_quality": "vague",
    "city_tier": "tier_3",
    "payment_method": "COD",
    "prior_orders": 0,
    "prior_returns": 0
  }'
```

---

### 3.2 Risk — `POST /risk/{prediction_id}/override` (dual-control per V3 §12.1)

Overrides a previous decision. Two request shapes are accepted
(auto-detected):

**Form A (V3-recommended dual-control JSON body):**

```json
{
  "decision": "REVIEW",
  "notes": "approved post call with customer",
  "admin_signature_1": "admin-demo-key",
  "admin_signature_2": "admin-second-key"
}
```

Both signatures must be valid admin-scope keys AND different (no
self-approval). The audit hash chain records both signature *digests*
(`adm_<sha256-truncate-16>`), not the raw keys.

**Form B (legacy single-admin query-param):**

```
POST /risk/{prediction_id}/override?new_decision=REVIEW
Authorization: Bearer admin-demo-key
```

Retained for backward-compat with Track D's test suite.

**Response — 200 OK (dual-control form):**

```json
{
  "overridden": "550e8400-...",
  "new_decision": "REVIEW",
  "audit_id": "aud_...",
  "dual_control": true,
  "signatures_required": 2,
  "signatures_provided": 2
}
```

**Errors:**

| Status | When |
|---|---|
| 400 | `admin_signature_1 == admin_signature_2` (no self-approval) |
| 403 | one or both signatures invalid; or legacy form with non-admin key |
| 422 | `decision` not in `{ACCEPT, REVIEW, REJECT, APPROVED, REJECTED, ESCALATED}` |

---

### 3.3 Risk — `POST /v1/simulate` (dry-run policy explorer, Track H)

Replays a transaction through the same decision pipeline as
`POST /risk/score` WITHOUT writing to the audit hash chain, opening a
case, or publishing to Redis Streams. `dry_run=True` is forced
server-side. Useful for merchant "what-if" tuning.

**Auth:** `scorer` scope. (Admin-scope keys are not in the scorer set
by default → 401 for admin callers.)

**Request body** (`SimulateIn`):

```json
{
  "order": { /* same shape as POST /risk/score OrderIn */ },
  "mandate": "<optional HMAC mandate string>",
  "dry_run": true
}
```

**Response — 200 OK** — same shape as `POST /risk/score` plus a
`rule_trace` array showing every rule evaluated + whether each fired
(the `/risk/score` endpoint surfaces only the single `rule_fired`):

```json
{
  "dry_run": true,
  "order_id": "ORD-12345",
  "probability": 0.6418,
  "risk_score": 64.2,
  "decision": "REVIEW",
  "decision_source": "cost_optimal_bmr_review_rule",
  "cost_breakdown": { /* same shape as /risk/score */ },
  "intervention": "otp_verify",
  "intervention_costs": { /* same shape as /risk/score */ },
  "intervention_weights": { /* DEFAULT_INTERVENTION_WEIGHTS */ },
  "explanation": [ /* top-5 reason codes */ ],
  "rule_fired": "RULE-002",
  "rule_trace": [
    {"rule_id": "RULE-001", "action": "BLOCK", "fired": false},
    {"rule_id": "RULE-002", "action": "REVIEW", "fired": true}
  ],
  "degraded": false,
  "policy_hint": "REVIEW",
  "mandate": { /* same shape as /risk/score */ },
  "model_version": "v20260827T1430",
  "latency_ms": 38,
  "audit_trail_url": null,
  "case_id": null,
  "prediction_id": null
}
```

The 5-way `intervention` + `intervention_costs` + `intervention_weights`
fields mirror `/risk/score` (Track N — V3 §11.6) so the simulate
"what-if" explorer surfaces the same cost-optimal next-step
recommendation. No audit/log/case side-effects (dry_run stays true).

---

### 3.4 Audit — `GET /audit/{audit_id}`

Reads one audit record by its ID (the `audit_trail_url` returned by
`POST /risk/score`).

**Auth:** `admin` scope.

**Path param:** `audit_id` (string, the `aud_<uuid>` returned in the
score response's `audit_trail_url`).

**Response — 200 OK:**

```json
{
  "audit_id": "aud_5ddf72cb-...",
  "request": { /* the OrderIn body, customer_id redacted to cust_<digest> */ },
  "probability": 0.6418,
  "decision": "REVIEW",
  "decision_source": "cost_optimal_bmr",
  "cost_breakdown": { /* same as /risk/score */ },
  "intervention": "otp_verify",
  "intervention_costs": { /* same as /risk/score */ },
  "reason_codes": [ /* top-5 */ ],
  "mandate_verdict": "valid",
  "mandate_verdict_reason": "ok",
  "mandate_type": "cod_order",
  "bh_purpose_code": null,
  "device_id": null,
  "user_id": null,
  "rule_fired": null,
  "degraded": false,
  "features_used": { /* the feature vector the model saw */ },
  "latency_ms": 42,
  "prediction_id": "550e8400-...",
  "case_id": "case_a1b2c3d4",
  "prev_hash": "00000000...0000",  /* SHA-256 hash chain */
  "raw_hash": "a1b2c3...64hex"      /* sha256(canonical(body) + prev_hash) */
}
```

Day 4 Track N — the audit record now carries the 5-way intervention
recommendation (`intervention` + `intervention_costs`) alongside the
3-way `decision` + `cost_breakdown` so an auditor can verify which
intervention the cost-optimizer recommended (the operator may execute
a different intervention — the audit captures the BMR-vs-execution
gap Bahnsen 2013 closes).

**Errors:**

| Status | When |
|---|---|
| 401 | missing / invalid admin key |
| 404 | audit record not found |

---

### 3.5 Audit — `GET /v1/audit/verify-chain`

Verifies the integrity of the per-record SHA-256 hash chain
(end-to-end recomputation in O(N)). Editing any historical record
breaks every later link.

**Auth:** `admin` scope.

**Response — 200 OK:**

```json
{
  "intact": true,
  "records_checked": 1234,
  "first_bad_audit_id": null
}
```

If `intact` is `false`, `first_bad_audit_id` names the first record
whose recomputed `raw_hash` doesn't match the stored value (i.e., the
record that was tampered with, or the one immediately after).

---

### 3.6 Audit — `GET /v1/audit/{record_id}/proof` (Merkle inclusion proof, Track H)

Returns the Merkle inclusion proof for a single audit record — the
path from the record's leaf hash up to its sealed interval's Merkle
root, plus the interval's `prev_interval_root` (the chain anchor).

O(log N) inclusion verification per record. The coarse
tamper-evidence layer on top of the per-record hash chain. Per V3
§10.3 + RFC 6962 padding rule.

**Auth:** `admin` scope.

**Path param:** `record_id` (int, the SERIAL primary key of
`audit_records`, NOT the `audit_id` text field).

**Response — 200 OK:**

```json
{
  "record_id": 42,
  "leaf_hash": "a1b2c3...64hex",
  "interval_id": 1,
  "position": 42,
  "proof": [
    {"position": "right", "hash": "deadbeef...64hex"},
    {"position": "left",  "hash": "cafebabe...64hex"}
  ],
  "merkle_root": "f00dface...64hex",
  "prev_interval_root": "00000000...0000",
  "leaf_count": 1000,
  "sealed_at": "2026-08-27T01:23:45.678901+00:00"
}
```

**Errors:**

| Status | When |
|---|---|
| 401 | missing / invalid admin key |
| 404 | record not found, OR interval not yet sealed (call `seal_interval()` first), OR file mode (no Merkle layer — use `/v1/audit/verify-chain`) |

**Sealing thresholds:** count-based (default 1000 records) OR
time-based (default 3600 s) — whichever trips first. Padding: pad to
next power of 2 by repeating the LAST leaf's hash (RFC 6962-style;
no synthetic zero-leaf).

---

### 3.7 Audit — `GET /v1/compliance/audit-export` (CSV)

Exports the audit tail (last 100k records) as CSV. Court-friendly
compliance bundle.

**Auth:** `admin` scope.

**Response — 200 OK** (`text/csv`, attachment):

```http
Content-Type: text/csv
Content-Disposition: attachment; filename="audit-export-20260827T013045Z.csv"
```

CSV columns: `audit_id, request, probability, decision,
decision_source, cost_breakdown, reason_codes, mandate_verdict,
mandate_verdict_reason, mandate_type, bh_purpose_code, device_id,
user_id, rule_fired, degraded, features_used, latency_ms,
prediction_id, case_id, prev_hash, raw_hash, created_at`.

---

### 3.8 Audit — `GET /v1/compliance/model-card`

Returns the model card for the current champion model. Per Google
Model Card spec (Mitchell et al. 2019). Full human-readable version
at [`MODEL_CARD.md`](MODEL_CARD.md).

**Auth:** `scorer` scope (so merchant dashboards can render the card).

**Response — 200 OK:**

```json
{
  "model_name": "RTO Trust Layer scorer",
  "model_type": "HistGradientBoostingClassifier (sklearn)",
  "version": "v20260827T1430",
  "metrics_at_registration": {
    "pr_auc": 0.5495, "roc_auc": 0.808
  },
  "training_data": "CODScore synthetic-but-realistic COD orders (7235 rows); real-data upgrade path documented",
  "label_definition": "DeliveryStatus == Returned -> is_returned=1",
  "split_discipline": "customer-grouped holdout; group leakage asserted 0",
  "primary_metric": "PR-AUC (class imbalance ~23% positives)",
  "intended_use": "pre-dispatch COD return-risk gating with human-in-the-loop review",
  "limitations": [
    "synthetic training data; validate before production use",
    "no address-string features in this dataset revision",
    "state-level geo features showed no lift (see E3)"
  ],
  "ethical_notes": "defense-only tool; every decision explainable + hash-chained audited"
}
```

---

### 3.9 Rules — `GET /v1/rules`

Lists active rules. Rules fire in priority order; first match
short-circuits for BLOCK, sets the REVIEW gate for REVIEW.

**Auth:** `scorer` scope.

**Response — 200 OK:**

```json
{
  "rules": [
    {
      "rule_id": "RULE-001",
      "name": "Block COD > 50K from new customers",
      "field": "amount_inr",
      "op": "gt",
      "value": 50000,
      "action": "BLOCK",
      "priority": 100,
      "created_by": "admin"
    }
  ]
}
```

Operators: `gt`, `lt`, `eq`, `in` (the `in` value is a list).

---

### 3.10 Rules — `POST /v1/rules`

Adds a rule (admin only). Hot-reloadable — no redeploy.

**Auth:** `admin` scope.

**Request body** (`RuleIn`):

```json
{
  "rule_id": "RULE-002",
  "name": "REVIEW COD + tier-3 + new customer",
  "field": "city_tier",
  "op": "eq",
  "value": "tier_3",
  "action": "REVIEW",
  "priority": 200,
  "created_by": "admin"
}
```

**Response — 200 OK:**

```json
{ "added": "RULE-002" }
```

**Errors:** `403` (non-admin), `422` (validation: `op` not in
`gt|lt|eq|in`; `action` not in `BLOCK|REVIEW`; `rule_id` length
violations).

---

### 3.11 Rules — `DELETE /v1/rules/{rule_id}`

Soft-deletes a rule (admin only).

**Auth:** `admin` scope.

**Response — 200 OK:**

```json
{ "removed": "RULE-002" }
```

Returns `{"removed": false}` if the rule didn't exist (idempotent).

---

### 3.12 Cases — `GET /v1/cases`

Lists REVIEW cases (the human-in-the-loop queue). Optional
`?status=OPEN|UNDER_REVIEW|APPROVED|REJECTED|ESCALATED` filter.

**Auth:** `admin` scope.

**Response — 200 OK:**

```json
{
  "cases": [
    {
      "case_id": "case_a1b2c3d4",
      "prediction_id": "550e8400-...",
      "order_id": "ORD-12345",
      "status": "OPEN",
      "reason": "review_gate",
      "created_at": "2026-08-27T14:30:15+00:00",
      "resolved_at": null,
      "resolution_decision": null
    }
  ]
}
```

---

### 3.13 Cases — `POST /v1/cases/{case_id}/resolve`

Resolves a REVIEW case. `decision` is `ACCEPT`, `REVIEW`, or
`REJECT` (the `/risk/score` vocabulary) or `APPROVED`, `REJECTED`,
`ESCALATED` (the V3 §12.1 vocabulary).

**Auth:** `admin` scope.

**Query params:**

| Param | Type | Required | Notes |
|---|---|---|---|
| `decision` | string | yes | one of `ACCEPT\|REVIEW\|REJECT\|APPROVED\|REJECTED\|ESCALATED` |
| `notes` | string | no | max 2000 chars |

**Response — 200 OK:** the resolved case row (with `status`,
`resolved_at`, `resolution_decision`).

**Errors:** `403` (non-admin), `422` (invalid `decision`).

---

### 3.14 Models — `GET /v1/models/current`

Returns the current champion model metadata (registered at app start
via Track E's `register_model` lifespan hook).

**Auth:** `scorer` scope.

**Response — 200 OK:**

```json
{
  "champion": {
    "version": "v20260827T1430",
    "model_path": "out/model_api.joblib",
    "metrics": {"pr_auc": 0.5495, "roc_auc": 0.808},
    "is_champion": true,
    "registered_at": "2026-08-27T14:30:00+00:00"
  }
}
```

Returns `{"champion": null}` if no model is registered (file mode
fallback).

---

### 3.15 Models — `GET /v1/models/drift`

Returns PSI (Population Stability Index) per feature for the recent
audit tail (last 300 records). PSI > 0.1 = WARNING, > 0.25 = CRITICAL.
The formal DDM/ADWIN drift detectors are surfaced via `/metrics`
gauges (Track G); this endpoint is the batch PSI complement.

**Auth:** `admin` scope.

**Response — 200 OK:**

```json
{
  "status": "OK",
  "n_observed": 300,
  "psi": {
    "log_order_value": 0.04,
    "PriorReturns": 0.02,
    "PriorOrders": 0.01,
    "city_tier": 0.03
  }
}
```

Returns `{"status": "insufficient_data", "observed": <n>, "psi": {}}`
if fewer than 30 records with features are found in the audit tail.

---

### 3.16 Policy — `GET /v1/policy/optimal`

Computes the cost-optimal BMR decision for a given probability with
customizable cost weights. Pure function — no audit write, no side
effects.

**Auth:** `scorer` scope.

**Query params:**

| Param | Type | Default | Notes |
|---|---|---|---|
| `probability` | float | (required) | must be in `[0, 1]` |
| `c_fp` | float | 50 | false-positive cost |
| `c_fn` | float | 600 | false-negative cost |

**Response — 200 OK:**

```json
{
  "probability": 0.6418,
  "optimal_action": "REVIEW",
  "expected_costs": {
    "ACCEPT": 385.08,
    "REVIEW": 122.04,
    "REJECT": 358.2
  }
}
```

**Errors:** `422` if `probability` outside `[0, 1]`.

---

### 3.17 Policy — `GET /v1/policy/cost-curves` (Drummond-Holte cost-curve explorer, Track C + Track N)

Threshold sweep (0.05 → 0.95) over the labeled training set with
per-threshold confusion counts + Bahnsen cost (Eq. 1) + precision +
recall, plus bootstrap CIs preserving row marginals (Drummond-Holte
§3.6, `bootstrap_performance_ci` capability). The cost-minimizing
threshold is the global analog of the per-order `optimal_decision()`
BMR policy in the live decision path.

Day 4 Track N — also returns the V3 §11.6 5-way intervention sweep
(`intervention_curves` + `intervention_crossover`) computed from the
per-amount FN cost (Bahnsen Eq.(5): `FN = amount_inr`). The
`amount_inr` query param overrides the default representative order
value (the dataset median) so the dashboard can render the
cost-optimal intervention for any order-value bracket.

**Source papers:** Bahnsen et al. ICMLA 2013 (DOI 10.1109/ICMLA.2013.68)
for the cost matrix; Drummond & Holte, *Machine Learning* 65:95-130,
2006 (DOI 10.1007/s10994-006-8199-5) for cost curves + bootstrap CIs;
Pragma 2025 RTO-mitigation benchmark for per-intervention
effectiveness rates.

**Auth:** `scorer` scope.

**Query params:**

| Param | Type | Default | Notes |
|---|---|---|---|
| `n_resamples` | int | 500 | bootstrap iterations; clamped to `[1, 5000]` |
| `confidence` | float | 0.90 | CI coverage; must be in `(0.5, 0.999)` |
| `amount_inr` | float | null | Day 4 Track N — per-transaction FN cost (Bahnsen Eq.(5)); must be in `[1, 1_000_000]`. None → dataset median (or ₹12400 fallback) used for the 5-way intervention sweep. |

**Response — 200 OK:**

```json
{
  "thresholds": [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40,
                 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80,
                 0.85, 0.90, 0.95],
  "curves": [
    {
      "threshold": 0.05, "tp": 412, "fp": 251, "fn": 0, "tn": 0,
      "cost": 33150.0, "precision": 0.6214, "recall": 1.0
    },
    {
      "threshold": 0.15, "tp": 325, "fp": 474, "fn": 87, "tn": 6363,
      "cost": 70170.0, "precision": 0.4069, "recall": 0.7888
    }
  ],
  "bootstrap_ci": {
    "0.05": {"low": 28140.0, "high": 38190.0, "mean": 33148.2,
             "n_resamples": 500, "confidence": 0.90},
    "0.15": {"low": 59280.0, "high": 81030.0, "mean": 70166.4,
             "n_resamples": 500, "confidence": 0.90}
  },
  "optimal_threshold": 0.05,
  "cost_crossover": {
    "status": "single_model",
    "incumbent_version": "v20260827T1430",
    "challenger_version": null,
    "crossover_threshold": null,
    "note": "only one model registered; no crossover available"
  },
  "intervention_curves": [
    {
      "threshold": 0.05,
      "intervention": "otp_verify",
      "costs": {
        "ship": 620.0, "otp_verify": 116.6,
        "partial_cod": 224.0, "address_check": 346.0, "hold": 206.0
      }
    },
    {
      "threshold": 0.40,
      "intervention": "otp_verify",
      "costs": {
        "ship": 4960.0, "otp_verify": 897.8,
        "partial_cod": 1746.0, "address_check": 2731.0, "hold": 1508.0
      }
    }
  ],
  "intervention_crossover": {
    "crossover_thresholds": [],
    "per_region_intervention": [
      {"threshold": 0.05, "intervention": "otp_verify"},
      {"threshold": 0.10, "intervention": "otp_verify"}
    ],
    "regions": [
      {"low_threshold": 0.05, "high_threshold": 0.95,
       "intervention": "otp_verify", "n_points": 19}
    ]
  },
  "intervention_amount_inr": 12400.0,
  "intervention_weights": {
    "c_ship_fp": 50.0, "c_ship_fn": 0.0,
    "c_otp": 5.0, "c_otp_effectiveness": 0.82,
    "c_partial_cod": 10.0, "c_partial_cod_effectiveness": 0.65,
    "c_address_check": 3.0, "c_address_check_effectiveness": 0.45,
    "c_hold": 20.0, "c_hold_fn": 0.0,
    "c_block": 1000.0, "c_hold_residual_ship_rate": 0.30
  },
  "cost_model": {
    "c_fp": 50.0, "c_fn": 600.0, "c_otp": 5.0, "c_block": 1000.0,
    "otp_effectiveness": 0.82,
    "source_paper": "Bahnsen ICMLA 2013, DOI 10.1109/ICMLA.2013.68",
    "curve_paper": "Drummond & Holte 2006, DOI 10.1007/s10994-006-8199-5",
    "intervention_paper": "Bahnsen 2013 Eq.(5) per-amount FN cost; Pragma 2025 RTO-mitigation benchmark for per-intervention effectiveness rates"
  },
  "data_source": "train_df_in_sample",
  "n_samples": 5788,
  "n_pos": 1324,
  "n_neg": 4464
}
```

**`intervention_curves` (Track N — V3 §11.6 5-way intervention sweep):**

For each probability threshold, the cost-optimal intervention
(`ship` / `otp_verify` / `partial_cod` / `address_check` / `hold`)
plus the full 5-way cost breakdown. Each cost is computed with the
per-amount FN cost (Bahnsen Eq.(5): `FN = amount_inr`) and the
per-intervention effectiveness rate (Pragma 2025). This is the 5-way
analog of the 3-way `curves` field above.

**`intervention_crossover` (Track N — Drummond-Holte `find_model_crossover`):**

The threshold(s) where the cost-optimal intervention changes (e.g.
`ship` → `otp_verify` at `p·amount ≈ 6.10 INR`). Collapsed into
contiguous `regions` for dashboard rendering. With default weights,
OTP effectiveness 0.82 dominates the soft interventions for any
non-trivial `p·amount`, so the crossover is typically a single
`ship → otp_verify` boundary at low `p·amount`. Re-tuning the weights
(e.g. higher `c_otp` in a market without cheap SMS gateways, or lower
`c_hold` + `c_hold_residual_ship_rate` when manual review is cheap)
shifts the crossovers and exposes `partial_cod`, `address_check`, or
`hold` as cost-optimal in their respective regions.

**Errors:**

| Status | When |
|---|---|
| 401 | missing / invalid scorer key |
| 422 | `n_resamples` outside `[1, 5000]` OR `confidence` outside `(0.5, 0.999)` OR `amount_inr` outside `[1, 1_000_000]` |
| 503 | model not loaded (circuit breaker OPEN) OR no labeled data available |

**Dashboard wiring:** `dashboard/index.html` calls this endpoint on
first successful scorer-key entry. Bars rendered from `curves[].cost`;
cost-optimal threshold highlighted green; legend shows optimal
threshold's precision/recall + `n_pos`/`n_neg` split + `data_source`.
The 5-way `intervention_curves` + `intervention_crossover` enable a
secondary view: the cost-optimal intervention band per probability
threshold, indexed by the operator's order-amount bracket.

---

### 3.18 Mandates — `POST /v1/mandates` (UPI Circle / cod_order, Track D)

Merchant backend (admin scope) mints a bounded, HMAC-signed mandate
that an agent must present on every money-moving call. Two mandate
types: `cod_order` (legacy, default) and `upi_circle_delegation`
(NPCI Operating Circular 201-B compliant).

**Source papers:**

- "Addendum to NPCI/UPI/2024-25/OC 201 — Introduction of IoT devices
  & software on UPI Circle" (NPCI/UPI/OC-201B/2025-26, 8 Oct 2025)
- Walia, Gautam, Shrivastava (Khaitan & Co), Lexology, 21 Nov 2025
- "SoK: Security of Autonomous LLM Agents in Agentic Commerce"
  (Mao 2026, arXiv 2604.15367v2) — D2 transaction-authorization

**Auth:** `admin` scope. Agents (scorer) cannot mint mandates.

**Query params:**

| Param | Type | Default | Notes |
|---|---|---|---|
| `customer_ref` | str | (required) | merchant customer reference; salted+hashed into mandate `sub` |
| `max_amount_inr` | float | (required) | overall ceiling; must be in `[1, 1_000_000]` |
| `ttl_seconds` | int | 3600 | must be in `[30, 86_400]` |
| `mandate_type` | str | `cod_order` | `cod_order` or `upi_circle_delegation` |
| `device_ids` | str (CSV) | null | UPI Circle only; max 5 per OC-201B |
| `user_id` | str | null | UPI Circle only; validated per txn |
| `bh_purpose_code` | str | `"90"` | NPCI BH code; `"90"` = commercial payment |
| `max_per_txn_inr` | float | 5000 | OC-201B per-txn cap |
| `max_per_month_inr` | float | 15000 | OC-201B monthly cap |
| `cooling_24h_inr` | float | 5000 | OC-201B 24h cooling threshold |
| `inactivity_revoke_days` | int | 180 | OC-201B inactivity auto-revoke window |

**Response — 200 OK:**

```json
{
  "mandate": "<HMAC-signed-token>",
  "max_amount_inr": 15000,
  "ttl_seconds": 3600,
  "mandate_type": "upi_circle_delegation",
  "device_ids": ["device-watch-01", "device-tv-02"],
  "user_id": "user-neeraj-01",
  "bh_purpose_code": "90",
  "note": "agents cannot mint or widen mandates"
}
```

**Errors:** `401` (scoper-scope cannot mint), `422` (bounds
violations, > 5 device IDs).

**`verdict_reason` vocabulary** (12 values, machine-readable; surfaced
in `mandate.verdict_reason` of `POST /risk/score` response + audit body):

| verdict_reason | verdict | When |
|---|---|---|
| `ok` | VALID | all checks passed |
| `missing_mandate` | TAMPERED | no `X-Mandate` header |
| `hmac_signature_mismatch` | TAMPERED | HMAC sig doesn't verify |
| `decode_error` | TAMPERED | base64/JSON decode failure |
| `expired_ttl` | EXPIRED | mandate TTL elapsed |
| `inactivity_auto_revoke` | EXPIRED | OC-201B 6-month inactivity auto-revoke |
| `per_txn_cap_exceeded` | BREACH | OC-201B per-txn cap exceeded |
| `monthly_cap_exceeded` | BREACH | OC-201B monthly cumulative cap exceeded |
| `device_id_not_allowed` | BREACH | OC-201B §3.7 device not in allowlist |
| `user_id_mismatch` | BREACH | OC-201B §3.3 user_id mismatch |
| `amount_exceeds_max` | BREACH | cod_order legacy amount-breach |
| `cooling_period_active` | REVIEW | OC-201B 24h cooling gate; routes to case queue |

---

### 3.19 Feedback — `POST /v1/feedback/ingest` (DDM + ADWIN drift, Track G)

Ingests a delayed `is_returned` ground-truth label (chargeback-style
delay, days-weeks post-prediction), computes the per-prediction
error indicator, updates the in-memory DDM + ADWIN detectors, and on
DRIFT fires a `retrain_request` notification to the `notifications`
Redis Stream.

**Source paper:** Gama, Žliobaitė, Bifet, Pechenizkiy, Bouchachia,
"A Survey on Concept Drift Adaptation," ACM Computing Surveys 46(4),
Article 44, March 2014. DOI 10.1145/2523813. See §3.2 (DDM), §3.3
(ADWIN), §5 (detector-quality metrics), §6 (Monitoring and Control
application category).

**Auth:** `admin` scope (label-poisoning prevention — merchants cannot
self-report labels to suppress retrain triggers).

**Request body** (`FeedbackIn`):

```json
{
  "prediction_id": "550e8400-e29b-41d4-a716-446655440000",
  "is_returned": true,
  "returned_at": "2026-09-04T10:30:00+00:00"
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `prediction_id` | string | yes | min 1, max 128 chars |
| `is_returned` | bool | yes | ground-truth label |
| `returned_at` | ISO 8601 string | no | if None, server uses `datetime.now(timezone.utc)` |

**Response — 200 OK:**

```json
{
  "status": "ingested",
  "prediction_id": "550e8400-...",
  "is_returned": true,
  "predicted_p": 0.6418,
  "error": 0,
  "ddm_state": "STABLE",
  "adwin_state": "STABLE",
  "drift_detected": false,
  "n_processed": 1,
  "ddm_p": 0.0,
  "adwin_window_len": 1,
  "prediction_not_found": false
}
```

| Field | Notes |
|---|---|
| `predicted_p` | the model's P(RTO) for this prediction (read from the audit log body's `probability` field). `null` if prediction_id not found in the last 5000 audit records. |
| `error` | Bernoulli trial outcome: 1 if the model was wrong (predicted_p ≥ 0.15 AND not returned, OR predicted_p < 0.15 AND returned), else 0. |
| `ddm_state` | `STABLE` / `WARNING` (95% / 2σ) / `DRIFT` (99% / 3σ) per Gama 2014 §3.2 |
| `adwin_state` | `STABLE` / `WARNING` / `DRIFT` per Gama 2014 §3.3 (Hoeffding bound `ε_cut = √((1/2m)·ln(4|W|/δ))`, δ = 0.002) |
| `drift_detected` | True iff `ddm_state == "DRIFT"` OR `adwin_state == "DRIFT"`. On True: retrain_request published + detectors reset (Gama §4 recommendation). |
| `prediction_not_found` | True if the prediction_id couldn't be found in the audit tail (error defaults to 0 — no contribution to drift signal). |

**Errors:** `403` (scorer-scope key used — label-poisoning prevention),
`422` (Pydantic validation).

**Prometheus metrics** (Track G):

| Metric | Type | Notes |
|---|---|---|
| `rto_drift_ddm_state` | gauge | 0=STABLE, 1=WARNING, 2=DRIFT |
| `rto_drift_adwin_state` | gauge | same vocabulary |
| `rto_drift_samples_processed` | gauge | total delayed labels ingested |
| `rto_drift_ddm_p` | gauge | running DDM error rate |
| `rto_drift_adwin_window_len` | gauge | current ADWIN window length |
| `rto_drift_detection_delay_seconds` | summary | wall-clock seconds between prediction + DRIFT (Gama §5) |
| `rto_drift_false_alarm_run_length` | summary | samples between false alarms (Gama §5) |

---

### 3.20 Metering — `GET /v1/usage` (per-window metering, Track H)

Returns audit-record counts for each window in `since_hours` (default
`24,168,720` = 24h / 7d / 30d), plus the Merkle interval sealing
cadence so a billing auditor can verify the audit trail's
tamper-evidence layer is up-to-date alongside the metering numbers.

**Auth:** `admin` scope.

**Query params:**

| Param | Type | Default | Notes |
|---|---|---|---|
| `since_hours` | CSV of positive ints | `24,168,720` | each clamped to `[1, 87600]` (10y); non-int → 422 |

**Response — 200 OK:**

```json
{
  "counts": {"24": 1234, "168": 8500, "720": 34000},
  "since_hours": [24, 168, 720],
  "intervals_sealed_total": 9,
  "intervals_sealed_in_window": 9,
  "latest_interval": {
    "interval_id": 9,
    "start_record_id": 8001,
    "end_record_id": 9000,
    "merkle_root": "f00dface...64hex",
    "prev_interval_root": "deadbeef...64hex",
    "leaf_count": 1000,
    "sealed_at": "2026-08-27T01:23:45+00:00"
  },
  "note": "aggregate counts (multi-tenant merchant_id not yet implemented — Track E schema has the JSONB column ready)"
}
```

**Errors:** `401` (scorer-scope key used; admin-only metering),
`422` (`since_hours` not a CSV of positive integers).

---

### 3.21 Health — `GET /health`

Liveness probe. No auth.

**Response — 200 OK:**

```json
{
  "status": "ok",
  "model_loaded": true,
  "circuit_state": "CLOSED",
  "active_rules": 2,
  "version": "0.4.0"
}
```

---

### 3.22 Health — `GET /metrics`

Prometheus-format metrics. Public (but nginx CIDR-gates to private
ranges per Track B — only `172.16.0.0/12`, `10.0.0.0/8`, `127.0.0.1`).

**Response — 200 OK** (`text/plain; version=0.0.4`):

```
# HELP risk_decisions_total ...
# TYPE risk_decisions_total counter
risk_decisions_total{decision="ACCEPT",degraded="False"} 1234
risk_decisions_total{decision="REVIEW",degraded="False"} 456
risk_decisions_total{decision="REJECT",degraded="False"} 78
# TYPE rto_circuit_state gauge
rto_circuit_state 0
# TYPE rto_score_latency_seconds summary
rto_score_latency_seconds_count 1768
rto_score_latency_seconds_sum 12.345
rto_score_latency_seconds_avg 0.00698
# TYPE rto_drift_ddm_state gauge
rto_drift_ddm_state 0
# TYPE rto_drift_adwin_state gauge
rto_drift_adwin_state 0
# TYPE rto_drift_samples_processed gauge
rto_drift_samples_processed 42
...
```

Grafana scrapes this endpoint every 15s (8-panel auto-loaded
dashboard; Track B Day 1 fixed the Grafana provisioning mount path).

---

## 4. Streaming — Redis Streams backbone (Track F)

The RTO Trust Layer publishes every `POST /risk/score` decision to
**Redis Streams** (5 streams per V2 §5; Redis Streams over Kafka per
`04-TECH-STACK-DECISIONS.md`: "V3 explicitly rejected Kafka as
cargo-cult"). This closes perceived-gap driver G2 (REST-only, no
event/streaming backbone) + §A item 18 (Redis declared but unused) +
§D item P7 (streaming transformations absent).

**Fire-and-forget contract.** After the audit hash-chain append + the
case open (on REVIEW decisions), the API calls
`state["stream"].publish(stream, fields)` three times. If `REDIS_URL`
is unset (test mode) OR Redis is down, `publish()` returns `None`
silently — the API response is unaffected.

### Stream names + field schemas

| Stream | When published | Field schema |
|---|---|---|
| `risk.scores` | every `POST /risk/score` decision | `prediction_id`, `order_id`, `decision`, `score` (float string; `""` if degraded), `decision_source`, `model_version`, `ts` |
| `audit.records` | every audit hash-chain append | `audit_id`, `prediction_id`, `decision`, `ts` |
| `cases.created` | REVIEW decisions only (case opened) | `case_id`, `prediction_id`, `order_id`, `reason` (`review_gate` / `rule:<id>` / `mandate:<verdict_reason>`), `ts` |
| `model.drift` | stream-processor anomaly | `stream`, `prediction_id`, `order_id`, `anomaly_reason` (`duplicate_order_id` / `score_velocity_spike` / `score_mean_drift`), `cardinality_estimate_per_min`, `ts` + anomaly-specific fields |
| `notifications` | reserved for Track H (merchant notify) + Track I (dashboard fan-out) | TBD by those tracks |

The `prediction_id` is generated **once** in the decision section +
flows into the case row, all three stream publishes, and the response
body — so a `risk.scores` message can be correlated to its
`cases.created` sibling (the same UUID appears in both).

### Consumer groups (3)

| Group | Streams | Service | Purpose |
|---|---|---|---|
| `rto-workers` | `risk.scores` + `audit.records` + `cases.created` | `stream-worker` (`python -m src.stream.consumer`) | default stderr handler logs the event flow end-to-end; real handlers (Track G feedback loop, Track I dashboard, Track H notifications) install their own callback via `StreamConsumer.consume(streams, fn)` |
| `rto-processors` | `risk.scores` | `stream-processor` (`python -m src.stream.processor`) | Microsoft Eventhouse equivalent — TFX `generate_data_statistics` pattern via Redis `PFADD`/`PFCOUNT` (HyperLogLog cardinality) + sliding-window deque for rolling rate + 3 anomaly detectors → publishes to `model.drift` |
| `rto-drift-detectors` | `model.drift` | `drift-consumer` (`python -m src.feedback.drift_consumer`) | run-length heuristic on consecutive same-reason anomalies (3+ = sustained shift) → parallel `retrain_request` notification (fast-reactive path; the formal label-side DDM fires days later) |

### Anomaly reasons (`model.drift` stream)

| `anomaly_reason` | When | Action |
|---|---|---|
| `duplicate_order_id` | same `order_id` published twice within the rolling window (5 min default) | strong RTO signal — merchant bot retrying the same SKU |
| `score_velocity_spike` | message rate > 3x rolling baseline | traffic flood — auto-scale alert |
| `score_mean_drift` | rolling score mean deviates > 2 sigma from baseline | streaming-PSI equivalent — Track G DDM/ADWIN consumes for retrain trigger |

### Configuration

| Env var | Default | Notes |
|---|---|---|
| `REDIS_URL` | `None` (test mode) | set to `redis://redis:6379` in docker-compose. When unset, producer is a no-op. |
| `STREAM_CONSUMER_NAME` | `worker-<pid>` | override for explicit naming (CI / debugging) |
| `STREAM_PROCESSOR_NAME` | `processor-<pid>` | override |
| `STREAM_PROCESSOR_WINDOW_SECONDS` | 300 | sliding-window size for rolling rate + score stats |
| `STREAM_PROCESSOR_BASELINE_SEED` | 30 | messages to seed the rolling baseline before anomaly detection kicks in (avoid spurious cold-start alerts) |

---

## 5. Error codes

| Status | When | Body shape |
|---|---|---|
| 400 | bad request (e.g., dual-control self-approval) | `{"detail": "<reason>"}` |
| 401 | missing or invalid API key | `{"detail": "invalid api key"}` |
| 403 | scope mismatch (e.g., scorer key on admin endpoint) | `{"detail": "<endpoint> requires admin scope"}` or `"feedback ingestion requires admin scope (label poisoning prevention)"` |
| 404 | resource not found (audit record, case, Merkle interval not sealed) | `{"detail": "<resource> not found"}` or `"no Merkle interval sealed for this record ..."` |
| 422 | Pydantic validation error | `{"detail": [{"loc": [...], "msg": "...", "type": "..."}}` (FastAPI default) |
| 429 | rate limit exceeded (25 r/s burst 50 per IP at nginx) | `{"detail": "rate limit exceeded"}` |
| 500 | internal error (incident UUID logged; never leaks stack) | `{"detail": "internal_error incident=<uuid>"}` |
| 503 | service unavailable (model not loaded, no labeled data for cost curves) | `{"detail": "cost curves unavailable — model not loaded"}` |

---

## 6. Quick curl examples

```bash
# Health (no auth)
curl http://localhost:8000/health

# Score an order (scorer scope)
curl -X POST http://localhost:8000/risk/score \
  -H "Authorization: Bearer score-demo-key" \
  -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d '{"order_id":"ORD-1","amount_inr":12400,"category":"Electronics","customer_id":"CUST-1","address_quality":"vague","city_tier":"tier_3","prior_orders":0,"prior_returns":0}'

# Verify the audit hash chain (admin scope)
curl -H "Authorization: Bearer admin-demo-key" \
  http://localhost:8000/v1/audit/verify-chain

# Get the Merkle proof for record #42 (admin scope)
curl -H "Authorization: Bearer admin-demo-key" \
  http://localhost:8000/v1/audit/42/proof

# Get the cost-curve sweep (scorer scope)
curl -H "Authorization: Bearer score-demo-key" \
  "http://localhost:8000/v1/policy/cost-curves?n_resamples=100"

# Add a rule (admin scope)
curl -X POST http://localhost:8000/v1/rules \
  -H "Authorization: Bearer admin-demo-key" \
  -H "Content-Type: application/json" \
  -d '{"rule_id":"RULE-001","name":"Block COD > 50K","field":"amount_inr","op":"gt","value":50000,"action":"BLOCK","priority":100}'

# Mint a UPI Circle mandate (admin scope)
curl -X POST "http://localhost:8000/v1/mandates?customer_ref=CUST-1&max_amount_inr=15000&mandate_type=upi_circle_delegation&device_ids=dev-01,dev-02&user_id=user-1&bh_purpose_code=90" \
  -H "Authorization: Bearer admin-demo-key"

# Ingest a delayed label (admin scope)
curl -X POST http://localhost:8000/v1/feedback/ingest \
  -H "Authorization: Bearer admin-demo-key" \
  -H "Content-Type: application/json" \
  -d '{"prediction_id":"550e8400-e29b-41d4-a716-446655440000","is_returned":true}'

# Get usage metering (admin scope)
curl -H "Authorization: Bearer admin-demo-key" \
  "http://localhost:8000/v1/usage?since_hours=24,168,720"

# Dry-run policy simulation (scorer scope)
curl -X POST http://localhost:8000/v1/simulate \
  -H "Authorization: Bearer score-demo-key" \
  -H "Content-Type: application/json" \
  -d '{"order":{"order_id":"ORD-1","amount_inr":52000,"category":"Electronics","customer_id":"CUST-1"},"dry_run":true}'

# Override a decision with dual-control (admin scope, two different keys)
curl -X POST http://localhost:8000/risk/550e8400/override \
  -H "Content-Type: application/json" \
  -d '{"decision":"REVIEW","notes":"approved post call","admin_signature_1":"admin-demo-key","admin_signature_2":"admin-second-key"}'

# Export the audit CSV (admin scope)
curl -H "Authorization: Bearer admin-demo-key" \
  http://localhost:8000/v1/compliance/audit-export -o audit.csv

# Get the model card (scorer scope)
curl -H "Authorization: Bearer score-demo-key" \
  http://localhost:8000/v1/compliance/model-card

# Prometheus metrics (public, CIDR-gated by nginx)
curl http://localhost:8000/metrics
```

---

## 7. Related docs

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — system design, Mermaid
  diagrams, 10-service inventory, decision precedence, scaling
  analysis (10x → 100x → 1000x), security model.
- [`MODEL_CARD.md`](MODEL_CARD.md) — training data, metrics,
  limitations, bias analysis (Google Model Card spec).
- [`RESEARCH.md`](RESEARCH.md) — the 5 pitch papers cited in the
  executive narrative.
- [`PITCH_SCRIPT.md`](PITCH_SCRIPT.md) — 5-min video script.
- [`cost_table.md`](cost_table.md) — 8-row threshold sweep,
  cost-optimal = 0.15 (Day 4 Track L regenerates on real data).
- [`feature_importance.md`](feature_importance.md) — permutation
  AP-drop on held-out set.
- [`research/INDEX.md`](research/INDEX.md) — 18-citation engineering
  bibliography.
- [`openapi.json`](openapi.json) — machine-readable twin (FastAPI
  auto-generates on every app reload; the post-Track C/D/F/G/H
  refresh happens on the next `docker compose up`).
