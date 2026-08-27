# Infra / production deployment

Demo runs on Docker Compose (`docker compose --profile full up`).
Production target is specified in `docs/ARCHITECTURE_V2.md` §9.2:

- Cloud-neutral boxes map to managed services (Postgres→Cloud SQL/RDS,
  Redis→Memorystore/ElastiCache, queue→Pub/Sub/SQS+workers, warehouse→BigQuery/Snowflake)
- Multi-AZ stateless API behind gateway+WAF, IaC via OpenTofu (MPL-2.0) preferred over
  Terraform BSL for licensing hygiene
- Canary rollout + kill-switch = model registry alias repoint (no deploy)

This directory intentionally contains no half-deployable Terraform: an unapplied partial
IaC is worse than a precise spec. Wire-up order when infra lands: network → data stores →
secrets → api → observability.
