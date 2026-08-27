# Infra / production deployment

OpenTofu spec for production. NOT applied (V3: "an unapplied partial IaC is
worse than a precise spec"). To apply:

```bash
cd infra && tofu init && tofu plan
cd infra && tofu apply
```

After `tofu apply`, `tofu output` surfaces:

- `rds_endpoint` / `rds_proxy_endpoint` — Postgres (Multi-AZ) for the audit
  hash chain + cases + model registry + idempotency cache
- `redis_endpoint` — ElastiCache Redis (Multi-AZ replication group) for the
  Redis Streams backbone (Track F producer + Track I consumer)
- `s3_bucket_arn` — model artifacts (.joblib from `src/models/train.py`) +
  audit Parquet cold storage (object-locked WORM, 7-year retention)
- `eks_cluster_name` — EKS cluster running the API + stream-worker +
  stream-processor + drift-consumer pods

The spec provisions: a 3-AZ VPC, RDS Postgres 15 Multi-AZ + RDS Proxy,
ElastiCache Redis 7 replication group, S3 artifact bucket (versioned +
object-locked + lifecycle to Glacier), EKS 1.30 cluster + node group,
KMS data key, SecretsManager DB password, IAM roles for EKS + RDS Proxy.
Istio service mesh + HPA are documented as post-apply `istioctl install`
+ `kubectl apply` steps (don't half-deploy IaC — the V3 rule).

Wire-up order when infra lands: network → data stores → secrets → api →
observability. The blocks in `main.tf` follow that order.

Track M (Day 4) owns this file. Track B wrote the 15-line stub pointing at
`docs/ARCHITECTURE_V2.md` §9.2; this file is the precise spec that stub
deferred to.
