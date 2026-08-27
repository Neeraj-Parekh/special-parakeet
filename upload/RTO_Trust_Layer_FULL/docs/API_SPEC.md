# API Specification (OpenAPI 3.1)

> Auto-generated from the FastAPI app - `16 paths`, machine-readable twin at `docs/openapi.json`.
> Interactive docs: run server -> `/docs` (Swagger UI).

- `GET` `/audit/{audit_id}`
- `GET` `/health`
- `GET` `/metrics`
- `POST` `/risk/score`
- `POST` `/risk/{prediction_id}/override`
- `GET` `/v1/audit/verify-chain`
- `GET` `/v1/cases`
- `POST` `/v1/cases/{case_id}/resolve`
- `GET` `/v1/compliance/audit-export`
- `GET` `/v1/compliance/model-card`
- `POST` `/v1/mandates`
- `GET` `/v1/models/current`
- `GET` `/v1/models/drift`
- `GET` `/v1/policy/optimal`
- `GET / POST` `/v1/rules`
- `DELETE` `/v1/rules/{rule_id}`

## Auth model

| Scope | Key | Endpoints |
|---|---|---|
| scorer | `Authorization: Bearer <scorer-key>` | POST /risk/score, GET /v1/rules, GET /v1/models/current, GET /v1/policy/optimal, GET /v1/compliance/model-card |
| admin | `Bearer <admin-key>` | audit reads, verify-chain, mandates minting, rules CRUD, overrides, case resolve, drift, CSV export |
| public | none | /health, /metrics, /dashboard/ |
