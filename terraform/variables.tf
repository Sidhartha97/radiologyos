variable "aws_region" {
  type        = string
  description = "AWS Region where all RAG resources are created."
  default     = "us-east-1"
}

variable "library_allowed_origins" {
  type        = list(string)
  description = "Browser origins allowed to submit presigned historical-report uploads. Add the deployed HTTPS app origin before hosting."
  default     = ["http://localhost:8000", "http://127.0.0.1:8000", "http://localhost:5173", "http://localhost:18000", "http://127.0.0.1:18000"]
}

variable "project_name" {
  type    = string
  default = "radiologyos"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "documents_bucket_name" {
  type        = string
  description = "Globally unique S3 bucket name for uploaded old reports."
  validation {
    condition     = lower(var.documents_bucket_name) != "yes" && can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.documents_bucket_name))
    error_message = "documents_bucket_name must be a globally unique S3-compatible name and cannot be 'yes'."
  }
}

variable "vector_bucket_name" {
  type        = string
  description = "Globally unique S3 Vectors bucket name."
  validation {
    condition     = var.vector_bucket_name != "yes" && can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", var.vector_bucket_name))
    error_message = "Use a unique 3–63 character lowercase vector bucket name (letters, digits, hyphens), not 'yes'."
  }
}

variable "vector_index_name" {
  type    = string
  default = "radiology-report-index"
}

variable "embedding_model_arn" {
  type        = string
  description = "Embedding model ARN available in the selected Region."
  default     = "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0"
}

variable "generation_model_arn" {
  type        = string
  description = "Approved Bedrock generation model ARN available in the selected Region."
  default     = "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-pro-v1:0"
  validation {
    condition     = can(regex("^arn:[^:]+:bedrock:[^:]+:[^:]*:(foundation-model|inference-profile)/.+$", var.generation_model_arn))
    error_message = "generation_model_arn must be a Bedrock model ARN, for example arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0. Do not use the S3 vector bucket value (such as yes)."
  }
}

variable "vector_dimension" {
  type        = number
  description = "Must match the selected embedding model dimensions. Titan Text Embeddings V2 commonly uses 1024."
  default     = 1024
  validation {
    condition     = contains([256, 512, 1024], var.vector_dimension)
    error_message = "This Titan V2 setup supports 256, 512 or 1024 dimensions; KB and index use the same value."
  }
}

variable "additional_generation_model_arns" {
  type        = list(string)
  description = "Underlying foundation-model ARNs required when generation_model_arn is an inference profile, including each destination Region. Empty for direct Nova Pro invocation."
  default     = []
  validation {
    condition     = alltrue([for arn in var.additional_generation_model_arns : can(regex("^arn:[^:]+:bedrock:[^:]+:[^:]*:foundation-model/.+$", arn))])
    error_message = "Additional generation resources must be Bedrock foundation-model ARNs, not wildcard or bucket values."
  }
}

variable "chunk_max_tokens" {
  type        = number
  description = "Maximum tokens in each Knowledge Base chunk."
  default     = 400
  validation {
    condition     = var.chunk_max_tokens >= 1 && var.chunk_max_tokens <= 8192 && floor(var.chunk_max_tokens) == var.chunk_max_tokens
    error_message = "chunk_max_tokens must be an integer from 1 to 8192."
  }
}

variable "chunk_overlap_percentage" {
  type        = number
  description = "Percentage of overlap between adjacent chunks."
  default     = 15
  validation {
    condition     = var.chunk_overlap_percentage >= 1 && var.chunk_overlap_percentage <= 99 && floor(var.chunk_overlap_percentage) == var.chunk_overlap_percentage
    error_message = "chunk_overlap_percentage must be an integer from 1 to 99."
  }
}

variable "enable_cognito_mfa" {
  type    = bool
  default = false
}

variable "db_name" {
  type    = string
  default = "radiology"
}

variable "db_username" {
  type    = string
  default = "radiology_app"
}

variable "db_instance_class" {
  type        = string
  description = "Use a small class for development; increase for production load."
  default     = "db.t3.micro"
}

variable "db_allocated_storage" {
  type    = number
  default = 20
}

variable "db_multi_az" {
  type    = bool
  default = false
}

variable "db_deletion_protection" {
  type    = bool
  default = false
}

variable "db_skip_final_snapshot" {
  type    = bool
  default = true
}

variable "db_backup_retention_period" {
  type        = number
  description = "RDS backup retention in days. Free-tier development accounts may require 0."
  default     = 0
}

variable "db_publicly_accessible" {
  type        = bool
  description = "Development only. Prefer false and connect through a VPC tunnel in production."
  default     = false
}

variable "dev_client_cidr" {
  type        = string
  description = "Your PC public IP in CIDR notation, for example 203.0.113.10/32. Leave empty in production."
  default     = ""
  validation {
    condition     = var.dev_client_cidr == "" || (can(cidrnetmask(var.dev_client_cidr)) && can(regex("/32$", var.dev_client_cidr)))
    error_message = "dev_client_cidr must be empty or a single IPv4 /32 address; broad database ingress is not allowed."
  }
}

variable "local_developer_principal_arn" {
  type        = string
  description = "Optional IAM user/role ARN allowed to assume the application role for local tests. Never enter access keys here."
  default     = ""
  validation {
    condition     = var.local_developer_principal_arn == "" || can(regex("^arn:[^:]+:iam::[0-9]{12}:(user|role)/.+$", var.local_developer_principal_arn))
    error_message = "Use an IAM user/role ARN, or leave empty."
  }
}

variable "db_final_snapshot_identifier" {
  type        = string
  description = "Unique final snapshot name required if db_skip_final_snapshot is false."
  default     = null
}
