# Track I (5-a) — Next.js dashboard

## Scope
- Build the Stripe-like Next.js dashboard in the HOST sandbox (`/home/z/my-project`)
- 4 pages: Risk Console `/`, Audit Explorer `/audit`, Rules Manager `/rules`, Model Health `/model-health`
- 13 Next.js API routes proxying to the Python API at `http://localhost:8000` (env: `API_BASE_URL`)
- Mock mode: when Python API is unreachable → return mock data + `X-Mock-Mode: true` header

## Backend contract (from prior tracks B-H)
- `POST /risk/score` (scorer scope) — body: `OrderIn`. Returns `{prediction_id, risk_score, probability, decision, decision_source, cost_breakdown, explanation, rule_fired, gate_thresholds, mandate, audit_trail_url, latency_ms, model_version}`
- `GET /audit/{audit_id}` (admin scope) — single audit record by string id
- `GET /v1/audit/verify-chain` (admin scope) — `{intact, records_checked, first_bad_audit_id}`
- `GET /v1/audit/{record_id}/proof` (admin scope, int id) — Merkle proof dict
- `GET /v1/rules` (scorer) + `POST /v1/rules` (admin) + `DELETE /v1/rules/{rule_id}` (admin)
- `GET /v1/models/current` (scorer) — `{champion: {version, deployed_at, metrics}}`
- `GET /v1/models/drift` (admin) — `{status, n_observed, psi}`
- `GET /v1/policy/cost-curves` (scorer) — Drummond-Holte cost curves
- `GET /v1/compliance/audit-export` (admin) — CSV
- `GET /v1/usage` (admin) — metering + Merkle sealing cadence
- `POST /v1/simulate` (scorer) — dry-run policy explorer
- `POST /v1/feedback/ingest` (admin) — delayed is_returned label
- `GET /metrics` (no auth) — Prometheus text format with `rto_drift_ddm_state` + `rto_drift_adwin_state` gauges

## Decision precedence (Track C)
1. Rules-engine BLOCK → REJECT (`decision_source=rules_engine_block`)
2. Mandate BREACH → REJECT (`mandate_breach`)
2c. UPI Circle 24h cooling REVIEW (`mandate_review_required`)
3. Mandate TAMPERED/EXPIRED → REJECT (`mandate_invalid`)
4. Circuit breaker OPEN → degraded REVIEW (`degraded_review`)
5. optimal_decision(p, weights) → ACCEPT/REVIEW/REJECT (`cost_optimal_bmr` or `cost_optimal_bmr_review_rule`)

## Files OWNED by Track I
- `/home/z/my-project/src/**` — pages, API routes, components, lib
- `/home/z/my-project/src/app/globals.css` — dark theme palette
- `/home/z/my-project/src/app/layout.tsx` — theme provider wiring

## Files NOT touched
- Anything in `/home/z/my-project/upload/RTO_Trust_Layer_FULL/` (Python project — read-only)

## Demo orders
1. "Repeat customer" — prepaid, ₹2,400, address_quality=complete, tier_1, prior_orders=12, prior_returns=0 → ACCEPT
2. "High-value COD" — COD, ₹52,000, address_quality=vague, tier_3, prior_orders=0, prior_returns=0 → REJECT (RULE-001 amount>50000 fires)
3. "Prior returns" — COD, ₹8,400, address_quality=vague, tier_2, prior_returns=3, prior_orders=5 → REVIEW

## Theme
- GitHub-dark aesthetic: `--bg:#0d1117`, `--card:#161b22`, `--fg:#e6edf3`, `--border:#30363d`
- NO indigo/blue colors
- Emerald (ACCEPT), Amber (REVIEW), Red (REJECT), neutral grays (chrome)
- Dark mode default
- `min-h-screen flex flex-col` + `mt-auto` footer (sticky footer rule)

## Build order
1. globals.css + layout.tsx + theme provider
2. mock-data.ts (single source of truth for all mock returns)
3. api-proxy.ts (shared proxy helper + mock fallback)
4. 13 API routes
5. shared components (header, footer, api-key context, mock-mode badge)
6. 4 pages
7. Copilot NL Q&A panel (optional)
8. Lint + dev log check
