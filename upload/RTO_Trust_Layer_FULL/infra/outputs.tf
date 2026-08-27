# =============================================================================
# OpenTofu outputs for the RTO Trust Layer prod target.
#
# Outputs are surfaced to the user via `tofu output` after `tofu apply` —
# these values are then passed to the API pods as env vars / config-map
# entries in the EKS cluster (via the Helm release or kubectl apply).
#
# Sensitive outputs (db_password) are marked `sensitive = true` so they
# aren't echoed to stdout. The non-sensitive outputs (rds_endpoint,
# redis_endpoint, s3_bucket_arn, eks_cluster_name) are the standard
# surface a user hands to their k8s config-map.
# =============================================================================

output "rds_endpoint" {
  description = "The RDS Postgres endpoint. Pass as DATABASE_URL=postgresql://risk:<password>@${rds_endpoint}:5432/riskdb to the API pods."
  value       = aws_db_instance.postgres.endpoint
}

output "rds_proxy_endpoint" {
  description = "The RDS Proxy endpoint — preferred over the direct rds_endpoint for the API pods (connection pooling + failover)."
  value       = aws_db_proxy.main.endpoint
}

output "rds_engine_version" {
  description = "The actual installed Postgres engine version (after auto_minor_version_upgrade)."
  value       = aws_db_instance.postgres.engine_version
}

output "redis_endpoint" {
  description = "The ElastiCache Redis primary endpoint. Pass as REDIS_URL=redis://${redis_endpoint}:6379 to the API + stream workers."
  value       = aws_elasticache_replication_group.redis.primary_endpoint
}

output "redis_read_endpoints" {
  description = "Read-replica endpoints — the dashboard / read-heavy consumer reads from here."
  value       = aws_elasticache_replication_group.redis.member_clusters
}

output "s3_bucket_arn" {
  description = "S3 artifacts bucket ARN — the API's MODEL_REGISTRY_S3_PREFIX env var points here."
  value       = aws_s3_bucket.rto_artifacts.arn
}

output "s3_bucket_name" {
  description = "S3 artifacts bucket name — used in CLI commands (aws s3 cp ...)."
  value       = aws_s3_bucket.rto_artifacts.id
}

output "eks_cluster_name" {
  description = "EKS cluster name. Use `aws eks update-kubeconfig --name ${eks_cluster_name}` to bind kubectl."
  value       = aws_eks_cluster.main.name
}

output "eks_cluster_endpoint" {
  description = "EKS API server endpoint. Used by kubectl + Helm provider post-apply."
  value       = aws_eks_cluster.main.endpoint
}

output "eks_cluster_ca" {
  description = "EKS CA certificate (base64) — required for kubectl config."
  value       = aws_eks_cluster.main.certificate_authority[0].data
}

output "vpc_id" {
  description = "VPC ID — referenced by the ALB + EKS ingress controllers."
  value       = aws_vpc.main.id
}

output "private_subnet_ids" {
  description = "Private subnet IDs — for the k8s ALB controller ingress annotations."
  value       = aws_subnet.private[*].id
}

output "kms_key_arn" {
  description = "Customer-managed KMS key ARN — grants the security team revocation power across RDS + S3 + ElastiCache."
  value       = aws_kms_key.data.arn
}

output "db_secret_arn" {
  description = "SecretsManager ARN for the DB password — the API pods reference this via k8s ExternalSecrets operator."
  value       = aws_secretsmanager_secret.db.arn
  sensitive   = true
}
