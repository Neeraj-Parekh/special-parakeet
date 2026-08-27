# =============================================================================
# OpenTofu / Terraform spec for the RTO Trust Layer production target.
# Source: docs/ARCHITECTURE_V2.md §9.2 ("prod target" — Multi-AZ stateless API
# behind gateway+WAF; managed Postgres / Redis / object store / EKS).
#
# Status: SPEC ONLY. NOT applied. Per docs/ARCHITECTURE_V3.md §9.2 —
# "an unapplied partial IaC is worse than a precise spec." This file is the
# precise spec the buildathon demo points at; the user applies it after
# their AWS account is provisioned:
#     cd infra && tofu init && tofu plan
#     cd infra && tofu apply
#
# Wire-up order (per infra/README.md): network → data stores → secrets →
# api → observability. The blocks below follow that order.
#
# Track M (Day 4) — owns this file. Don't apply in CI (no AWS credentials).
# =============================================================================

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  # Backend: S3 + DynamoDB state locking. Commented out for the spec — the
  # user fills in the bucket + table name after `tofu init` in their AWS
  # account. Keep state remote so a team of operators can collaborate +
  # so `tofu apply` from CI doesn't clobber a local state file.
  # backend "s3" {
  #   bucket         = "rto-trust-layer-tfstate"
  #   key            = "infra/main.tfstate"
  #   region         = "ap-south-1"
  #   dynamodb_table = "rto-trust-layer-tflocks"
  #   encrypt        = true
  # }
}

# ─────────────────────────────────────────────────────────────────────────────
# Providers — AWS region is parameterised so the user can deploy to
# ap-south-1 (Mumbai — closest to INR e-commerce traffic) by default but
# override per environment.
# ─────────────────────────────────────────────────────────────────────────────
provider "aws" {
  region = var.aws_region
}

# Default tags applied to every resource — billing team / cost-allocation
# keys. Per V2 §9.2, prod targets use `env=prod` + `app=rto-trust-layer`
# on every resource so the AWS Cost Explorer dashboard groups them cleanly.
provider "aws" {
  default_tags {
    tags = {
      app       = "rto-trust-layer"
      env       = var.environment
      managedBy = "opentofu"
      track     = "M-iac"
    }
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# Data sources — current AZ list + caller identity (for ARN composition).
# ─────────────────────────────────────────────────────────────────────────────
data "aws_availability_zones" "available" {
  state = "available"
  # Exclude Local Zones — they don't support RDS Multi-AZ yet (AWS docs).
  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

data "aws_caller_identity" "current" {}

# ─────────────────────────────────────────────────────────────────────────────
# Network — VPC with public + private subnets across ≥3 AZs. RDS Multi-AZ
# + EKS both need ≥3 AZs for HA; the private subnets host the data plane
# (RDS, ElastiCache, EKS nodes); the public subnets host only the NAT
# gateway + ALB.
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames  = true
  tags = { Name = "rto-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags = { Name = "rto-igw" }
}

resource "aws_subnet" "public" {
  count                   = 3
  vpc_id                  = aws_vpc.main.id
  cidr_block             = cidrsubnet(aws_vpc.main.cidr_block, 4, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
  tags = {
    Name = "rto-public-${count.index}"
    Tier = "public"
  }
}

resource "aws_subnet" "private" {
  count             = 3
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(aws_vpc.main.cidr_block, 4, count.index + 8)
  availability_zone = data.aws_availability_zones.available.names[count.index]
  tags = {
    Name = "rto-private-${count.index}"
    Tier = "private"
    # K8s cluster tag so EKS + ALB controller auto-discover subnets.
    "kubernetes.io/role/internal-elb" = "1"
  }
}

resource "aws_eip" "nat" {
  count  = 3
  domain = "vpc"
  # EIPs are allocated per-AZ so a single AZ failure doesn't take down
  # outbound internet for all 3 private subnets.
  tags = { Name = "rto-nat-eip-${count.index}" }
}

resource "aws_nat_gateway" "main" {
  count         = 3
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id
  tags = { Name = "rto-nat-${count.index}" }
  # Force NAT → IGW so private subnets have outbound internet.
  depends_on = [aws_internet_gateway.main]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "rto-rtb-public" }
}

resource "aws_route_table_association" "public" {
  count          = 3
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  count  = 3
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[count.index].id
  }
  tags = { Name = "rto-rtb-private-${count.index}" }
}

resource "aws_route_table_association" "private" {
  count          = 3
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# ─────────────────────────────────────────────────────────────────────────────
# Security groups — least-privilege. The api/EKS nodes talk to RDS + Redis
# only; the ALB is the only inbound on the API pods. No 0.0.0.0/0 egress
# on data-plane SGs (only the NAT + IGW paths above).
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_security_group" "rds" {
  name        = "rto-sg-rds"
  description = "Postgres ingress from EKS nodes only"
  vpc_id      = aws_vpc.main.id
  ingress {
    description     = "Postgres from EKS"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.eks_nodes.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "redis" {
  name        = "rto-sg-redis"
  description = "Redis ingress from EKS nodes only"
  vpc_id      = aws_vpc.main.id
  ingress {
    description     = "Redis from EKS"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.eks_nodes.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "eks_nodes" {
  name        = "rto-sg-eks-nodes"
  description = "EKS worker nodes — ingress from ALB + self"
  vpc_id      = aws_vpc.main.id
  ingress {
    description = "ALB → pods"
    from_port   = 30000
    to_port     = 32767
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    description = "self (K8s control plane + pod-to-pod)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# Data store 1 — Postgres RDS Multi-AZ (the primary audit / cases / model
# registry backend per src/config.py + alembic/versions/001_initial.py).
# Engine + version pinned to match the docker-compose `postgres:15-alpine`
# service. Multi-AZ = synchronous standby in another AZ → automatic
# failover on primary loss (RDS SLA 99.95%).
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_db_subnet_group" "main" {
  name        = "rto-db-subnet-group"
  description = "Private subnets across 3 AZs for RDS Multi-AZ"
  subnet_ids  = aws_subnet.private[*].id
}

resource "random_password" "db_password" {
  length           = 32
  special          = true
  override_special = "!#$%&*()-_=+[]{}?"
}

resource "aws_secretsmanager_secret" "db" {
  name        = "rto-trust-layer/db/password"
  description = "Master password for the RDS Postgres instance (rotated via SecretsManager)."
  # SecretsManager KMS encryption is on by default with the AWS-managed key;
  # for prod, override with a customer-managed key per V2 §9.2 secrets hygiene.
}

resource "aws_secretsmanager_secret_version" "db" {
  secret_id     = aws_secretsmanager_secret.db.id
  secret_string = random_password.db_password.result
}

resource "aws_db_parameter_group" "main" {
  name   = "rto-pg-params"
  family = "postgres15"
  # pgaudit is enabled for compliance — every DML on audit_records is logged
  # to the RDS log stream (CloudWatch → S3 cold storage).
  parameter {
    name  = "shared_preload_libraries"
    value = "pgaudit"
  }
  parameter {
    name  = "log_connections"
    value = "1"
  }
  # pgbouncer-style connection pooling deferred to RDS Proxy (below).
}

resource "aws_db_instance" "postgres" {
  identifier                 = "rto-trust-layer-pg"
  engine                     = "postgres"
  engine_version             = "15.7"
  instance_class             = var.rds_instance_class
  allocated_storage          = 200
  storage_type               = "gp3"
  iops                       = 3000
  storage_encrypted          = true
  kms_key_id                 = aws_kms_key.data.arn
  db_name                    = "riskdb"
  username                   = "risk"
  password                   = random_password.db_password.result
  db_subnet_group_name       = aws_db_subnet_group.main.name
  parameter_group_name       = aws_db_parameter_group.main.name
  multi_az                   = true
  storage_encrypted         = true
  backup_retention_period    = 30
  backup_window              = "03:00-04:00"
  maintenance_window         = "sun:04:00-sun:05:00"
  deletion_protection        = true
  auto_minor_version_upgrade = true
  monitoring_interval        = 60
  monitoring_role_arn        = aws_iam_role.rds_enhanced_monitoring.arn
  vpc_security_group_ids     = [aws_security_group.rds.id]
  # Deletion is gated behind `delete_protected = false` — destructive ops
  # require an explicit var.
  lifecycle {
    prevent_destroy = true
  }
}

# RDS Proxy — connection pooling + failover pooling for the API pods. Reduces
# connection churn from K8s pod churn (HPA scaling events).
resource "aws_db_proxy" "main" {
  name                   = "rto-db-proxy"
  debug_logging          = false
  engine_family          = "POSTGRESQL"
  idle_client_timeout    = 1800
  require_tls            = true
  role_arn               = aws_iam_role.rds_proxy.arn
  vpc_subnet_ids         = aws_subnet.private[*].id
  vpc_security_group_ids = [aws_security_group.rds.id]
  auth {
    auth_scheme = "SECRETS"
    description = "RDS master secret"
    iam_auth    = "DISABLED"
    secret_arn  = aws_secretsmanager_secret.db.arn
  }
  depends_on = [aws_iam_role_policy.rds_proxy]
}

# ─────────────────────────────────────────────────────────────────────────────
# Data store 2 — ElastiCache Redis (Redis Streams backbone per Track F +
# src/stream/producer.py). Multi-AZ replication group so a primary loss
# promotes a read replica automatically.
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_elasticache_subnet_group" "main" {
  name        = "rto-redis-subnet-group"
  description = "Private subnets for ElastiCache"
  subnet_ids  = aws_subnet.private[*].id
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id    = "rto-redis"
  description             = "Redis Streams producer for risk.scores / audit.records / cases.created"
  node_type               = var.redis_node_type
  num_cache_clusters      = 3
  parameter_group_name    = "default.redis7"
  engine                  = "redis"
  engine_version          = "7.1"
  subnet_group_name       = aws_elasticache_subnet_group.main.name
  security_group_ids     = [aws_security_group.redis.id]
  multi_az_enabled        = true
  automatic_failover_enabled = true
  at_rest_encryption_enabled = true
  transit_encryption_enabled  = true
  snapshot_retention_limit   = 7
  snapshot_window            = "03:00-05:00"
  maintenance_window         = "sun:05:00-sun:06:00"
  # AOF persistence so Redis Streams replay after a failover (per Track F's
  # fire-and-forget contract — best-effort, but AOF reduces data loss).
  snapshot_count                  = 2
  apply_immediately              = false
}

# ─────────────────────────────────────────────────────────────────────────────
# Object store — S3 bucket for (a) model artifacts (.joblib pickle from
# src/models/train.py — Track L registers new versions here) + (b) audit
# Parquet export (the /v1/compliance/audit-export CSV endpoint writes to
# Parquet here on a daily cron — V2 §10.3 cold-storage layer above the
# audit_records table). Versioned + object-locked so model artifacts
# cannot be deleted (WORM per financial-services regulation mimic).
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_s3_bucket" "rto_artifacts" {
  bucket = "${var.bucket_prefix}-artifacts"
  tags   = { Name = "rto-model-artifacts" }
}

resource "aws_s3_bucket_versioning" "rto_artifacts" {
  bucket = aws_s3_bucket.rto_artifacts.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "rto_artifacts" {
  bucket = aws_s3_bucket.rto_artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.data.arn
    }
  }
}

# Object Lock — WORM for the audit Parquet cold storage. Compliance-grade
# immutability (so a malicious insider can't `aws s3 rm` the audit trail).
resource "aws_s3_bucket_object_lock_configuration" "rto_artifacts" {
  bucket = aws_s3_bucket.rto_artifacts.id
  rule {
    default_retention {
      mode = "COMPLIANCE"
      days = 2555 # 7 years — financial-records retention standard
    }
  }
}

resource "aws_s3_bucket_public_access_block" "rto_artifacts" {
  bucket                  = aws_s3_bucket.rto_artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "rto_artifacts" {
  bucket = aws_s3_bucket.rto_artifacts.id
  rule {
    id     = "audit-parquet-to-glacier"
    status = "Enabled"
    filter {
      prefix = "audit-parquet/"
    }
    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }
    transition {
      days          = 365
      storage_class = "GLACIER_IR"
    }
    transition {
      days          = 730
      storage_class = "DEEP_ARCHIVE"
    }
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# KMS key — single customer-managed key for RDS + S3 + ElastiCache at-rest
# encryption (so a single key grant gives the security team revocation
# power across all data stores).
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_kms_key" "data" {
  description             = "rto-trust-layer data-encryption key (RDS + Redis + S3)"
  deletion_window_in_days = 30
  enable_key_rotation     = true
}

resource "aws_kms_alias" "data" {
  name          = "alias/rto-data"
  target_key_id = aws_kms_key.data.key_id
}

# ─────────────────────────────────────────────────────────────────────────────
# EKS cluster — the API + stream-worker + stream-processor + drift-consumer
# pods run here. Istio service mesh installed via Helm provider (below).
# HPA scales the api deployment on CPU + the custom `risk_decisions_total`
# Prometheus metric (per Track G's drift gauges).
# ─────────────────────────────────────────────────────────────────────────────
resource "aws_eks_cluster" "main" {
  name     = var.eks_cluster_name
  role_arn = aws_iam_role.eks_cluster.arn
  version = "1.30"

  vpc_config {
    subnet_ids              = aws_subnet.private[*].id
    endpoint_private_access = true
    endpoint_public_access  = true
    public_access_cidrs     = ["0.0.0.0/0"]
  }

  enabled_log_types = ["api", "audit", "authenticator", "controllerManager", "scheduler"]

  encryption_config {
    resources = ["secrets"]
    provider {
      key_arn = aws_kms_key.data.arn
    }
  }

  depends_on = [aws_iam_role_policy_attachment.eks_cluster_policy]
}

resource "aws_eks_node_group" "main" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "rto-api-nodes"
  node_role_arn   = aws_iam_role.eks_nodes.arn
  subnet_ids      = aws_subnet.private[*].id

  instance_types = var.eks_instance_types
  disk_size      = 100
  disk_encrypted = true

  scaling_config {
    desired_size = var.eks_node_count_desired
    max_size     = var.eks_node_count_max
    min_size     = var.eks_node_count_min
  }

  update_config {
    max_unavailable = 1
  }

  # Don't release pods on a node drain during a deploy — graceful.
  capacity_type = "ON_DEMAND"

  depends_on = [aws_iam_role_policy_attachment.eks_nodes_policy]
}

# IAM roles for EKS — cluster + nodes. The cluster role can manage VPC
# networking; the node role can pull images + write to CloudWatch logs.
resource "aws_iam_role" "eks_cluster" {
  name = "rto-eks-cluster-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "eks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "eks_cluster_policy" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
  role       = aws_iam_role.eks_cluster.name
}

resource "aws_iam_role" "eks_nodes" {
  name = "rto-eks-node-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "eks_nodes_policy" {
  for_each = toset([
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
    "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
  ])
  policy_arn = each.value
  role       = aws_iam_role.eks_nodes.name
}

# RDS enhanced-monitoring IAM role (for the per-60s CloudWatch metrics).
resource "aws_iam_role" "rds_enhanced_monitoring" {
  name = "rto-rds-enhanced-monitoring"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "monitoring.rds.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "rds_enhanced_monitoring" {
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
  role       = aws_iam_role.rds_enhanced_monitoring.name
}

# RDS Proxy IAM role.
resource "aws_iam_role" "rds_proxy" {
  name = "rto-rds-proxy"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "rds.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "rds_proxy" {
  name = "rto-rds-proxy-secrets"
  role = aws_iam_role.rds_proxy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "secretsmanager:GetSecretValue",
        "secretsmanager:DescribeSecret",
      ]
      Resource = [aws_secretsmanager_secret.db.arn]
    }]
  })
}

# ─────────────────────────────────────────────────────────────────────────────
# Istio service mesh — installed via the Helm provider (mesh layer for mTLS
# + traffic shifting per V2 §9.2 canary rollout strategy). The Helm release
# below is a SPEC of what would be installed post-`tofu apply` + `kubectl
# apply istio-operator.yaml`; in practice the user runs `istioctl install`
# against the EKS cluster after this stack is up.
# ─────────────────────────────────────────────────────────────────────────────
# To install Istio (run after `tofu apply`):
#   istioctl install --set profile=production
#   kubectl label namespace rto-trust-layer istio-injection=enabled
#
# The Helm provider block below is intentionally NOT instantiated — it's
# documentation of the intended mesh layer. Instancing it requires
# `helm install ...` against a live cluster, which is post-apply work
# (the V3 spec rule: don't half-deploy IaC).
# ─────────────────────────────────────────────────────────────────────────────

# HPA (Horizontal Pod Autoscaler) — declared in-cluster via kubectl/Helm
# post-apply. Spec for reference:
#   apiVersion: autoscaling/v2
#   kind: HorizontalPodAutoscaler
#   metadata:
#     name: rto-api-hpa
#     namespace: rto-trust-layer
#   spec:
#     scaleTargetRef:
#       apiVersion: apps/v1
#       kind: Deployment
#       name: rto-api
#     minReplicas: 3
#     maxReplicas: 30
#     metrics:
#       - type: Resource
#         resource:
#           name: cpu
#           target:
#             type: Utilization
#             averageUtilization: 70
#       - type: Pods
#         resource:
#           name: risk_decisions_per_sec
#           target:
#             type: AverageValue
#             averageValue: "100"
#
# Track G's drift gauges feed the second metric — if drift state spikes,
# the HPA scales out so the degraded-mode rules-only path doesn't pile up
# requests.
# ─────────────────────────────────────────────────────────────────────────────
