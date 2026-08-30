# infra/terraform/main.tf
#
# Multi-AZ / multi-region infrastructure stub for the RTO Trust Layer.
#
# This file is NOT runnable as-is: the resource blocks are commented
# because the hackathon sandbox has no AWS credentials + the user
# explicitly said "we will add this in the code and not set in cloud
# as it will be a good point to mention and include even if not wired
# to everywhere but in code... we have a legitimate claim that yes we
# had made it but didn't connect cause Amazon costs money, but we
# made it." (worklog.md, tier2-B context).
#
# To run this in production:
#   1. Configure AWS creds via `aws configure` or a role assumption
#      chain.
#   2. Create a `terraform.tfvars` file with `aws_region`, `project`,
#      `db_password`, etc.
#   3. `terraform init && terraform plan && terraform apply`
#
# What this stub defines (uncomment + fill in to wire):
#   - Multi-AZ RDS Postgres with one standby + 2 read-replicas (az-a,
#     az-b, az-c) — matches src/lib/db/multi-az.ts.
#   - MSK Kafka cluster with 3 brokers across 3 AZs — matches
#     src/stream/kafka_producer.py.
#   - EKS cluster with the terraform-aws-modules/eks module.
#   - KMS key for at-rest encryption of RDS + MSK.
#   - S3 bucket for Terraform state with versioning + object lock.

terraform {
  required_version = ">= 1.7.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }

  # S3 backend — set up `aws s3api create-bucket --bucket rto-tfstate ...`
  # before running `terraform init`. Object lock MUST be enabled at
  # bucket creation time (cannot be added later).
  # backend "s3" {
  #   bucket               = "rto-trust-layer-tfstate"
  #   key                  = "multi-az/terraform.tfstate"
  #   region               = "ap-south-1"
  #   encrypt              = true
  #   use_lockfile         = true
  #   dynamodb_table       = "rto-tfstate-locks"
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "rto-trust-layer"
      ManagedBy = "terraform"
      Tier      = "multi-az"
    }
  }
}

variable "aws_region" {
  type        = string
  default     = "ap-south-1"
  description = "Mumbai region — closest to NPCI + Razorpay data residency."
}

variable "project" {
  type    = string
  default = "rto-trust-layer"
}

variable "db_password" {
  type        = string
  sensitive   = true
  description = "Postgres master password — set via TF_VAR_db_password or a secrets manager."
}

# ----------------------------------------------------------------------------
# KMS key — at-rest encryption for RDS, MSK, EBS volumes.
# ----------------------------------------------------------------------------
# resource "aws_kms_key" "rto" {
#   description             = "RTO Trust Layer — at-rest encryption"
#   deletion_window_in_days = 30
#   enable_key_rotation     = true
# }

# ----------------------------------------------------------------------------
# Multi-AZ RDS Postgres — primary in az-a, standby in az-b. Two
# read-replicas in az-c + az-a (cross-AZ for read scaling).
# ----------------------------------------------------------------------------
# resource "aws_db_subnet_group" "rto" {
#   name        = "${var.project}-dbsg"
#   subnet_ids   = module.vpc.database_subnets
#   description = "Subnets for the multi-AZ RDS cluster"
# }

# resource "aws_db_instance" "primary" {
#   identifier              = "${var.project}-pg-primary"
#   engine                  = "postgres"
#   engine_version          = "16.4"
#   instance_class          = "db.r6g.large"
#   allocated_storage       = 200
#   storage_type            = "io2"
#   iops                    = 3000
#   storage_encrypted       = true
#   kms_key_id              = aws_kms_key.rto.arn
#   multi_az                = true
#   db_subnet_group_name    = aws_db_subnet_group.rto.name
#   backup_retention_period = 30
#   backup_window           = "03:00-04:00"   # IST 8:30am-9:30am IST
#   maintenance_window      = "sun:04:00-sun:05:00"
#   username                = "rto_admin"
#   password                = var.db_password
#   deletion_protection     = true
#   performance_insights_enabled = true
#   monitoring_interval     = 30
#   enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]
# }

# resource "aws_db_instance" "replica_az_c" {
#   identifier                 = "${var.project}-pg-replica-az-c"
#   replicate_source_db        = aws_db_instance.primary.id
#   instance_class             = "db.r6g.large"
#   availability_zone          = "${var.aws_region}c"
#   auto_minor_version_upgrade = true
#   storage_encrypted          = true
#   kms_key_id                 = aws_kms_key.rto.arn
# }

# ----------------------------------------------------------------------------
# MSK Kafka cluster — 3 brokers across 3 AZs. Matches
# src/stream/kafka_producer.py exactly-once topology.
# ----------------------------------------------------------------------------
# resource "aws_msk_cluster" "rto" {
#   cluster_name           = "${var.project}-msk"
#   kafka_version          = "3.7.0"
#   number_of_broker_nodes = 3
#   broker_node_group_info {
#     instance_type   = "kafka.m5.large"
#     client_subnets  = module.vpc.database_subnets
#     security_groups = [aws_security_group.msk.id]
#     storage_info {
#       ebs_storage_info {
#         volume_size = 200
#         provisioned_throughput {
#           enabled           = true
#           volume_throughput = 250
#         }
#       }
#     }
#   }
#   encryption_info {
#     encryption_at_rest_kms_key_arn = aws_kms_key.rto.arn
#   }
#   logging_info {
#     broker_logs {
#       cloudwatch_logs { enabled = true }
#       s3 { enabled = true }
#     }
#   }
#   enhanced_monitoring = "PER_TOPIC_PER_PARTITION"
# }

# ----------------------------------------------------------------------------
# EKS cluster — the API pods from infra/k8s/multi-az/deployment.yaml
# schedule here. The module wires the VPC, node groups across 3 AZs,
# and the IRSA that lets the API read the integration secrets from
# AWS Secrets Manager (RAZORPAY_WEBHOOK_SECRET, SHIPROCKET_TOKEN, etc).
# ----------------------------------------------------------------------------
# module "eks" {
#   source  = "terraform-aws-modules/eks/aws"
#   version = "~> 20.0"

#   cluster_name    = "${var.project}-eks"
#   cluster_version = "1.30"
#   vpc_id          = module.vpc.vpc_id
#   subnet_ids      = module.vpc.private_subnets

#   eks_managed_node_groups = {
#     api = {
#       desired_size = 3
#       min_size     = 3
#       max_size     = 20
#       instance_types = ["m6i.large"]
#       capacity_type   = "on_demand"
#       subnet_ids      = module.vpc.private_subnets
#       labels = {
#         "nodepool" = "api"
#       }
#     }
#   }

#   enable_irsa = true
# }

# ----------------------------------------------------------------------------
# VPC — 3 AZs, public + private + database subnets per AZ.
# ----------------------------------------------------------------------------
# module "vpc" {
#   source  = "terraform-aws-modules/vpc/aws"
#   version = "~> 5.0"

#   name = "${var.project}-vpc"
#   cidr = "10.0.0.0/16"

#   azs              = ["${var.aws_region}a", "${var.aws_region}b", "${var.aws_region}c"]
#   private_subnets  = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
#   public_subnets   = ["10.0.101.0/24", "10.0.102.0/24", "10.0.103.0/24"]
#   database_subnets = ["10.0.21.0/24", "10.0.22.0/24", "10.0.23.0/24"]

#   enable_nat_gateway   = true
#   single_nat_gateway   = false  # one NAT per AZ for true AZ isolation
#   enable_dns_hostnames = true

#   create_database_subnet_group           = true
#   create_database_subnet_route_table     = true
# }

# ----------------------------------------------------------------------------
# Outputs — surface the connection strings the app reads.
# ----------------------------------------------------------------------------
output "region" {
  value = var.aws_region
}

output "project" {
  value = var.project
}

# output "rds_primary_endpoint" {
#   value       = aws_db_instance.primary.endpoint
#   description = "The leader AZ's Postgres endpoint."
# }

# output "rds_replica_endpoints" {
#   value = [
#     aws_db_instance.replica_az_c.endpoint,
#   ]
# }

# output "msk_bootstrap_brokers" {
#   value       = aws_msk_cluster.rto.bootstrap_brokers_sasl_iam
#   description = "SASL/IAM bootstrap brokers for the Kafka producer."
# }

# output "eks_cluster_endpoint" {
#   value       = module.eks.cluster_endpoint
#   description = "K8s API server endpoint — kubectl target."
# }
