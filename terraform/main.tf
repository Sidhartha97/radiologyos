data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

locals {
  generation_model_arn = var.generation_model_arn
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_kms_key" "radiology" {
  description             = "RadiologyOS encryption key"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}

resource "aws_kms_alias" "radiology" {
  name          = "alias/${var.project_name}-${var.environment}"
  target_key_id = aws_kms_key.radiology.key_id
}

resource "aws_kms_key_policy" "radiology" {
  key_id = aws_kms_key.radiology.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableRootAccountPermissions"
        Effect    = "Allow"
        Principal = { AWS = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "AllowS3VectorsIndexing"
        Effect    = "Allow"
        Principal = { Service = "indexing.s3vectors.amazonaws.com" }
        Action    = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey*", "kms:DescribeKey", "kms:CreateGrant", "kms:RetireGrant", "kms:ListGrants"]
        Resource  = "*"
        Condition = {
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        }
      }
    ]
  })
}

resource "aws_s3_bucket" "documents" {
  bucket        = var.documents_bucket_name
  force_destroy = false
}

resource "aws_s3_bucket_cors_configuration" "library" {
  bucket = aws_s3_bucket.documents.id
  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["POST"]
    allowed_origins = concat(var.library_allowed_origins, var.enable_testing_host && var.testing_app_client_cidr != "" ? ["http://${aws_eip.testing[0].public_ip}:8000"] : [])
    expose_headers  = ["ETag", "x-amz-request-id"]
    max_age_seconds = 300
  }
}

resource "aws_s3_bucket_public_access_block" "documents" {
  bucket                  = aws_s3_bucket.documents.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "documents" {
  bucket = aws_s3_bucket.documents.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_policy" "documents_tls" {
  bucket = aws_s3_bucket.documents.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.documents.arn, "${aws_s3_bucket.documents.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

resource "aws_s3_bucket_server_side_encryption_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id
  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.radiology.arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id
  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter { prefix = "" }
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
  }
}

resource "aws_dynamodb_table" "app" {
  name         = "${var.project_name}-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"

  # The AWS provider currently requires these legacy arguments for the table's
  # primary key. The GSI below uses the modern key_schema blocks.
  hash_key  = "PK"
  range_key = "SK"

  attribute {
    name = "PK"
    type = "S"
  }
  attribute {
    name = "SK"
    type = "S"
  }
  attribute {
    name = "GSI1PK"
    type = "S"
  }
  attribute {
    name = "GSI1SK"
    type = "S"
  }

  global_secondary_index {
    name            = "GSI1"
    projection_type = "ALL"

    key_schema {
      attribute_name = "GSI1PK"
      key_type       = "HASH"
    }
    key_schema {
      attribute_name = "GSI1SK"
      key_type       = "RANGE"
    }
  }

  point_in_time_recovery { enabled = true }
  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.radiology.arn
  }
}

resource "aws_security_group" "application" {
  name        = "${var.project_name}-${var.environment}-application"
  description = "Application runtime security group"
  vpc_id      = data.aws_vpc.default.id
  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "database" {
  name        = "${var.project_name}-${var.environment}-postgres"
  description = "Private PostgreSQL security group"
  vpc_id      = data.aws_vpc.default.id
  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# Manage both database rules independently; mixing inline ingress with a
# separate PC rule causes Terraform to remove/recreate the other rule.
resource "aws_security_group_rule" "database_application" {
  type                     = "ingress"
  security_group_id        = aws_security_group.database.id
  source_security_group_id = aws_security_group.application.id
  protocol                 = "tcp"
  from_port                = 5432
  to_port                  = 5432
  description              = "PostgreSQL from application runtime only"
}

resource "aws_security_group_rule" "database_dev_pc" {
  count             = var.db_publicly_accessible && var.dev_client_cidr != "" ? 1 : 0
  type              = "ingress"
  security_group_id = aws_security_group.database.id
  protocol          = "tcp"
  from_port         = 5432
  to_port           = 5432
  cidr_blocks       = [var.dev_client_cidr]
  description       = "Temporary development access from the configured PC IP"
}

resource "aws_db_subnet_group" "postgres" {
  name       = "${var.project_name}-${var.environment}-postgres"
  subnet_ids = data.aws_subnets.default.ids
}

resource "aws_db_instance" "postgres" {
  identifier                    = "${var.project_name}-${var.environment}-postgres"
  engine                        = "postgres"
  engine_version                = "16"
  instance_class                = var.db_instance_class
  allocated_storage             = var.db_allocated_storage
  max_allocated_storage         = 200
  storage_type                  = "gp3"
  storage_encrypted             = true
  kms_key_id                    = aws_kms_key.radiology.arn
  db_name                       = var.db_name
  username                      = var.db_username
  manage_master_user_password   = true
  master_user_secret_kms_key_id = aws_kms_key.radiology.arn
  port                          = 5432
  db_subnet_group_name          = aws_db_subnet_group.postgres.name
  vpc_security_group_ids        = [aws_security_group.database.id]
  publicly_accessible           = var.db_publicly_accessible
  multi_az                      = var.db_multi_az
  backup_retention_period       = var.db_backup_retention_period
  copy_tags_to_snapshot         = true
  deletion_protection           = var.db_deletion_protection
  skip_final_snapshot           = var.db_skip_final_snapshot
  apply_immediately             = false
  auto_minor_version_upgrade    = true
  final_snapshot_identifier     = var.db_skip_final_snapshot ? null : var.db_final_snapshot_identifier

  lifecycle {
    precondition {
      condition     = !var.db_publicly_accessible || var.dev_client_cidr != ""
      error_message = "Public development RDS requires dev_client_cidr set to your PC's public IPv4 /32 address."
    }
    precondition {
      condition     = var.db_skip_final_snapshot || var.db_final_snapshot_identifier != null
      error_message = "Set a unique db_final_snapshot_identifier when db_skip_final_snapshot is false."
    }
  }
}

resource "aws_cognito_user_pool" "radiology" {
  name                     = "${var.project_name}-${var.environment}"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  mfa_configuration        = var.enable_cognito_mfa ? "ON" : "OFF"

  dynamic "software_token_mfa_configuration" {
    for_each = var.enable_cognito_mfa ? [1] : []
    content { enabled = true }
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  schema {
    name                     = "tenant_id"
    attribute_data_type      = "String"
    developer_only_attribute = false
    mutable                  = false
    required                 = false
    string_attribute_constraints { max_length = 128 }
  }
}

resource "aws_cognito_user_pool_client" "web" {
  name                          = "${var.project_name}-web"
  user_pool_id                  = aws_cognito_user_pool.radiology.id
  generate_secret               = false
  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true
  explicit_auth_flows           = ["ALLOW_USER_SRP_AUTH", "ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
}

resource "aws_iam_role" "bedrock_kb" {
  name = "${var.project_name}-${var.environment}-bedrock-kb"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        ArnLike      = { "aws:SourceArn" = "arn:${data.aws_partition.current.partition}:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:knowledge-base/*" }
      }
    }]
  })
}

resource "aws_iam_role_policy" "bedrock_kb" {
  role = aws_iam_role.bedrock_kb.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.documents.arn
      },
      {
        Effect = "Allow"
        Action = ["s3:GetObject"]
        Resource = [
          "${aws_s3_bucket.documents.arn}/old-reports/*",
          "${aws_s3_bucket.documents.arn}/final-reports/*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:DescribeKey", "kms:GenerateDataKey"]
        Resource = aws_kms_key.radiology.arn
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = var.embedding_model_arn
      },
      {
        Effect = "Allow"
        Action = [
          "s3vectors:GetIndex",
          "s3vectors:PutVectors",
          "s3vectors:GetVectors",
          "s3vectors:DeleteVectors",
          "s3vectors:QueryVectors"
        ]
        Resource = aws_s3vectors_index.radiology.index_arn
      }
    ]
  })
}

resource "aws_s3vectors_vector_bucket" "radiology" {
  vector_bucket_name = var.vector_bucket_name
  depends_on         = [aws_kms_key_policy.radiology]
  encryption_configuration {
    sse_type    = "aws:kms"
    kms_key_arn = aws_kms_key.radiology.arn
  }
}

resource "aws_s3vectors_index" "radiology" {
  index_name         = var.vector_index_name
  vector_bucket_name = aws_s3vectors_vector_bucket.radiology.vector_bucket_name
  depends_on         = [aws_kms_key_policy.radiology]
  data_type          = "float32"
  dimension          = var.vector_dimension
  distance_metric    = "cosine"

  encryption_configuration {
    sse_type    = "aws:kms"
    kms_key_arn = aws_kms_key.radiology.arn
  }

  metadata_configuration {
    # Bedrock stores the chunk text and source metadata in these fields.
    # They must not be filterable because chunk text can exceed S3 Vectors'
    # 2048-byte filterable metadata limit.
    non_filterable_metadata_keys = [
      "AMAZON_BEDROCK_TEXT",
      "AMAZON_BEDROCK_METADATA",
      "source_uri",
      "report_title"
    ]
  }
}

resource "aws_bedrockagent_knowledge_base" "radiology" {
  name       = "${var.project_name}-${var.environment}-kb"
  role_arn   = aws_iam_role.bedrock_kb.arn
  depends_on = [aws_iam_role_policy.bedrock_kb, aws_s3_bucket_server_side_encryption_configuration.documents]

  knowledge_base_configuration {
    type = "VECTOR"
    vector_knowledge_base_configuration {
      embedding_model_arn = var.embedding_model_arn
      embedding_model_configuration {
        bedrock_embedding_model_configuration {
          dimensions          = var.vector_dimension
          embedding_data_type = "FLOAT32"
        }
      }
    }
  }

  storage_configuration {
    type = "S3_VECTORS"
    s3_vectors_configuration {
      index_arn = aws_s3vectors_index.radiology.index_arn
    }
  }
}

resource "aws_bedrockagent_data_source" "reports" {
  knowledge_base_id    = aws_bedrockagent_knowledge_base.radiology.id
  name                 = "${var.project_name}-${var.environment}-s3"
  data_deletion_policy = "DELETE"

  lifecycle {
    replace_triggered_by = [aws_bedrockagent_knowledge_base.radiology]
  }

  data_source_configuration {
    type = "S3"
    s3_configuration {
      bucket_arn         = aws_s3_bucket.documents.arn
      inclusion_prefixes = ["old-reports/"]
    }
  }

  vector_ingestion_configuration {
    chunking_configuration {
      chunking_strategy = "FIXED_SIZE"
      fixed_size_chunking_configuration {
        max_tokens         = var.chunk_max_tokens
        overlap_percentage = var.chunk_overlap_percentage
      }
    }
  }
}

resource "aws_bedrockagent_data_source" "final_reports" {
  knowledge_base_id    = aws_bedrockagent_knowledge_base.radiology.id
  name                 = "${var.project_name}-${var.environment}-final-reports"
  data_deletion_policy = "DELETE"

  lifecycle {
    replace_triggered_by = [aws_bedrockagent_knowledge_base.radiology]
  }

  data_source_configuration {
    type = "S3"
    s3_configuration {
      bucket_arn         = aws_s3_bucket.documents.arn
      inclusion_prefixes = ["final-reports/"]
    }
  }

  vector_ingestion_configuration {
    chunking_configuration {
      chunking_strategy = "FIXED_SIZE"
      fixed_size_chunking_configuration {
        max_tokens         = var.chunk_max_tokens
        overlap_percentage = var.chunk_overlap_percentage
      }
    }
  }
}

resource "aws_iam_role" "application" {
  name = "${var.project_name}-${var.environment}-application"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([{
      Effect    = "Allow"
      Principal = { Service = ["ec2.amazonaws.com", "ecs-tasks.amazonaws.com"] }
      Action    = "sts:AssumeRole"
      }], var.local_developer_principal_arn == "" ? [] : [{
      Effect    = "Allow"
      Principal = { AWS = var.local_developer_principal_arn }
      Action    = "sts:AssumeRole"
    }])
  })
}

resource "aws_iam_instance_profile" "application" {
  name = "${var.project_name}-${var.environment}-application"
  role = aws_iam_role.application.name
}

resource "aws_iam_role_policy" "application" {
  role = aws_iam_role.application.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["s3:PutObject", "s3:GetObject", "s3:AbortMultipartUpload"]
        Resource = [
          "${aws_s3_bucket.documents.arn}/historical-originals/*",
          "${aws_s3_bucket.documents.arn}/old-reports/*",
          "${aws_s3_bucket.documents.arn}/final-reports/*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.documents.arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:DescribeTable", "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:TransactWriteItems"]
        Resource = [aws_dynamodb_table.app.arn, "${aws_dynamodb_table.app.arn}/index/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = aws_db_instance.postgres.master_user_secret[0].secret_arn
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:Retrieve", "bedrock:RetrieveAndGenerate"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = concat([local.generation_model_arn], var.additional_generation_model_arns)
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:StartIngestionJob", "bedrock:GetIngestionJob", "bedrock:ListIngestionJobs", "bedrock:ListKnowledgeBaseDocuments", "bedrock:GetKnowledgeBaseDocuments", "bedrock:GetDataSource", "bedrock:ListDataSources"]
        Resource = aws_bedrockagent_knowledge_base.radiology.arn
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey"]
        Resource = aws_kms_key.radiology.arn
      }
    ]
  })
}







/*
resource "aws_instance" "radiology" {
  ami                         = data.aws_ami.amazon_linux.id
  instance_type               = var.ec2_instance_type
  subnet_id                   = data.aws_subnets.default.ids[0]
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.application.id]
  iam_instance_profile        = aws_iam_instance_profile.ec2.name

  user_data = <<-USERDATA
    #!/bin/bash
    set -euxo pipefail
    dnf update -y
    dnf install -y python3 python3-pip unzip nginx awscli
    mkdir -p /opt/radiologyos
    aws s3 cp s3://${aws_s3_bucket.documents.bucket}/${var.app_bundle_s3_key} /tmp/radiologyos.zip --region ${var.aws_region}
    unzip -o /tmp/radiologyos.zip -d /opt/radiologyos
    python3 -m pip install -r /opt/radiologyos/requirements.txt

    SECRET_JSON=$(aws secretsmanager get-secret-value --secret-id ${aws_db_instance.postgres.master_user_secret[0].secret_arn} --query SecretString --output text --region ${var.aws_region})
    DATABASE_URL=$(echo "$SECRET_JSON" | python3 -c 'import json,sys,urllib.parse; x=json.load(sys.stdin); print("postgresql://%s:%s@%s:%s/%s" % (x["username"], urllib.parse.quote(x["password"], safe=""), x["host"], x["port"], x.get("dbname", "${var.db_name}")))')

    cat > /etc/radiologyos.env <<EOF
    APP_HOST=127.0.0.1
    APP_PORT=8000
    DATABASE_URL=$DATABASE_URL
    AWS_RAG_ENABLED=true
    AWS_REGION=${var.aws_region}
    RAG_S3_BUCKET=${aws_s3_bucket.documents.bucket}
    RAG_S3_PREFIX=old-reports/
    BEDROCK_KNOWLEDGE_BASE_ID=${aws_bedrockagent_knowledge_base.radiology.id}
    BEDROCK_DATA_SOURCE_ID=${aws_bedrockagent_data_source.reports.id}
    BEDROCK_MODEL_ARN=${local.generation_model_arn}
    RAG_KMS_KEY_ID=${aws_kms_key.radiology.key_id}
    EOF

    cat > /etc/systemd/system/radiologyos.service <<'SERVICE'
    [Unit]
    Description=RadiologyOS Python application
    After=network-online.target
    Wants=network-online.target
    [Service]
    WorkingDirectory=/opt/radiologyos
    EnvironmentFile=/etc/radiologyos.env
    ExecStart=/usr/bin/python3 /opt/radiologyos/server.py
    Restart=always
    RestartSec=5
    [Install]
    WantedBy=multi-user.target
    SERVICE

    cat > /etc/nginx/conf.d/radiologyos.conf <<'NGINX'
    server {
      listen 80 default_server;
      server_name _;
      location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
      }
    }
    NGINX

    systemctl daemon-reload
    systemctl enable --now radiologyos
    systemctl enable --now nginx
  USERDATA

  depends_on = [aws_db_instance.postgres, aws_bedrockagent_data_source.reports]
}
*/
