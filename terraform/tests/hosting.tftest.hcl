mock_provider "aws" {
  mock_data "aws_caller_identity" { defaults = { account_id = "123456789012" } }
  mock_data "aws_partition" { defaults = { partition = "aws" } }
  mock_data "aws_subnets" { defaults = { ids = ["subnet-12345678"] } }
  mock_data "aws_subnet" { defaults = { availability_zone = "us-east-1c" } }
  mock_resource "aws_db_instance" {
    defaults = {
      availability_zone  = "us-east-1c"
      master_user_secret = [{ secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:master", secret_status = "active", kms_key_id = "test-key" }]
    }
  }
}

variables {
  enable_testing_host   = true
  documents_bucket_name = "radiologyos-offline-test-documents"
  vector_bucket_name    = "radiologyos-offline-test-vectors"
}

run "private_testing_host" {
  command = plan
  assert {
    condition     = aws_instance.testing[0].instance_type == "t4g.micro" && aws_instance.testing[0].root_block_device[0].volume_size == 8
    error_message = "Keep the approved testing size and root-disk budget."
  }
  assert {
    condition     = aws_instance.testing[0].metadata_options[0].http_tokens == "required" && aws_instance.testing[0].credit_specification[0].cpu_credits == "standard"
    error_message = "Require IMDSv2 and prevent unlimited CPU credit surcharges."
  }
}
