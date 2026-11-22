mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  mock_data "aws_subnets" {
    defaults = { ids = ["subnet-12345678", "subnet-87654321"] }
  }
  mock_resource "aws_db_instance" {
    defaults = {
      master_user_secret = [{ secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:test", secret_status = "active", kms_key_id = "test-key" }]
    }
  }
}

variables {
  enable_testing_host   = false
  documents_bucket_name = "radiologyos-offline-test-documents"
  vector_bucket_name    = "radiologyos-offline-test-vectors"
}

run "private_rag_plan" {
  command = plan

  assert {
    condition     = contains(aws_s3vectors_index.radiology.metadata_configuration[0].non_filterable_metadata_keys, "AMAZON_BEDROCK_TEXT")
    error_message = "Bedrock chunk text must not be filterable."
  }
  assert {
    condition     = aws_bedrockagent_knowledge_base.radiology.knowledge_base_configuration[0].vector_knowledge_base_configuration[0].embedding_model_configuration[0].bedrock_embedding_model_configuration[0].dimensions == aws_s3vectors_index.radiology.dimension
    error_message = "KB embeddings and vector index dimensions must agree."
  }
  assert {
    condition     = !aws_db_instance.postgres.publicly_accessible && length(aws_security_group_rule.database_dev_pc) == 0
    error_message = "RDS must default to private with no PC ingress."
  }
  assert {
    condition     = contains(jsondecode(aws_iam_role.application.assume_role_policy).Statement[0].Principal.Service, "ec2.amazonaws.com")
    error_message = "The application role must support later EC2 instance-profile use."
  }
}

run "reject_wide_database_access" {
  command = plan
  variables {
    dev_client_cidr = "0.0.0.0/0"
  }
  expect_failures = [var.dev_client_cidr]
}

run "reject_public_database_without_pc_ip" {
  command = plan
  variables {
    db_publicly_accessible = true
  }
  expect_failures = [aws_db_instance.postgres]
}

