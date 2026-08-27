# =============================================================================
# OpenTofu input variables for the RTO Trust Layer prod target.
# Each variable has a sane default EXCEPT db_password (which is generated
# via random_password in main.tf — there's no plain-text default; the
# user overrides via -var or a *.tfvars file in their AWS account).
# =============================================================================

variable "aws_region" {
  description = "AWS region for all resources. ap-south-1 (Mumbai) is closest to INR e-commerce traffic; us-east-1 for global prod."
  type        = string
  default     = "ap-south-1"
}

variable "environment" {
  description = "Deployment environment tag (dev|staging|prod). Drives the default_tags block on every resource so Cost Explorer groups by env."
  type        = string
  default     = "prod"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod."
  }
}

variable "vpc_cidr" {
  description = "CIDR block for the prod VPC. 10.0.0.0/16 gives 65k IPs across the 3 public + 3 private subnets."
  type        = string
  default     = "10.0.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0))
    error_message = "vpc_cidr must be a valid IPv4 CIDR."
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# RDS — instance class + DB password. The password is generated via the
# random_password resource in main.tf + stored in SecretsManager; the
# variable below is the override path for users who want to import an
# existing secret (e.g. from their enterprise KMS rotation pipeline).
# ─────────────────────────────────────────────────────────────────────────────
variable "rds_instance_class" {
  description = "RDS instance class. db.t4g.medium for dev/staging; db.r6g.xlarge for prod (4 vCPU / 32 GB RAM — handles the audit hash-chain write throughput)."
  type        = string
  default     = "db.r6g.xlarge"
}

variable "db_password_override" {
  description = "Optional: override the auto-generated random_password. Leave empty (default) to let Terraform generate + store in SecretsManager. Set this only if your org has an external password-rotation pipeline."
  type        = string
  default     = ""
  sensitive   = true
}

# ─────────────────────────────────────────────────────────────────────────────
# ElastiCache — node type + shard count.
# ─────────────────────────────────────────────────────────────────────────────
variable "redis_node_type" {
  description = "ElastiCache node type. cache.t4g.small for dev; cache.r6g.2xlarge for prod (13.5 GB RAM — comfortably above the projected Redis Streams backlog at 5x peak)."
  type        = string
  default     = "cache.r6g.2xlarge"
}

# ─────────────────────────────────────────────────────────────────────────────
# EKS — cluster name + node sizing.
# ─────────────────────────────────────────────────────────────────────────────
variable "eks_cluster_name" {
  description = "EKS cluster name. Used in kubectl contexts + the aws_eks_cluster resource identifier."
  type        = string
  default     = "rto-trust-layer-prod"
}

variable "eks_instance_types" {
  description = "EKS node group instance types. m6i.xlarge (4 vCPU / 16 GB) is the cost-effective baseline; mix m6i.2xlarge for the stream-processor nodes (heavier CPU)."
  type        = list(string)
  default     = ["m6i.xlarge", "m6i.2xlarge"]
}

variable "eks_node_count_min" {
  description = "Minimum node count — drives the HPA floor. 3 nodes for HA across AZs."
  type        = number
  default     = 3
}

variable "eks_node_count_desired" {
  description = "Desired node count at apply time. 3 baseline."
  type        = number
  default     = 3
}

variable "eks_node_count_max" {
  description = "Maximum node count for the cluster autoscaler. 12 covers ~3x peak QPS."
  type        = number
  default     = 12
}

# ─────────────────────────────────────────────────────────────────────────────
# S3 — bucket name prefix. The bucket name is `${var.bucket_prefix}-artifacts`
# (must be globally unique across all AWS accounts).
# ─────────────────────────────────────────────────────────────────────────────
variable "bucket_prefix" {
  description = "Prefix for the S3 artifacts bucket. Suffix '-artifacts' is appended. Must be globally unique — include an org prefix."
  type        = string
  default     = "rto-trust-layer"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", var.bucket_prefix))
    error_message = "bucket_prefix must be 3-63 chars, lowercase alphanumeric + hyphens only, no leading/trailing hyphen."
  }
}
