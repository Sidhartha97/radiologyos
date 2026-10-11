# RadiologyOS RAG and database infrastructure

This Terraform stack provisions the shared AWS foundation only. It does **not** create an EC2 instance and does **not** install or run the web application.

It provisions:

- Encrypted S3 bucket for old reports
- S3 Vectors bucket and vector index
- Bedrock Knowledge Base and S3 data source
- Bedrock IAM role for ingestion
- DynamoDB tenant-scoped application table
- PostgreSQL RDS database with managed master credentials
- KMS encryption key
- Cognito user pool and web client
- Application IAM role and security group for a future backend runtime

## Deploy the infrastructure

From this directory:

```powershell
$env:AWS_PROFILE = 'radiology-dev'
aws sts get-caller-identity --profile radiology-dev
Copy-Item terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with unique bucket names and an approved Bedrock model ARN.
terraform init
terraform fmt -recursive
terraform validate
terraform plan -out tfplan
# Review the plan before deciding to run: terraform apply tfplan
```

Terraform does not read the application's `.env`. Use the existing `radiology-dev` CLI profile, even if its IAM user is named `dev`. This review created no AWS resources. A new deployment will create chargeable resources, especially RDS, Secrets Manager and KMS. The example is a development configuration, not a production or free-of-charge guarantee.

For optional local role assumption, set `local_developer_principal_arn` to your IAM user/role ARN. This does not create credentials or attach administrator permissions. The application role supports later EC2 hosting through `application_instance_profile_name`; no EC2 instance or installation is provisioned. Your CLI user does not automatically receive this role's permissions.

An existing default VPC with subnets in at least two Availability Zones is required. RDS defaults to private: local PC access needs a VPN/tunnel/network route. For explicitly temporary public development access, set `db_publicly_accessible=true` and `dev_client_cidr` to your PC's public IPv4 `/32`. Broad CIDRs and public RDS without a PC address are rejected. Keep RDS private for production.

The KB explicitly uses FLOAT32 embeddings matching the vector index dimensions. Defaults: Titan V2, 1024 dimensions, 400-token fixed chunks, 15% overlap. `AMAZON_BEDROCK_TEXT` and `AMAZON_BEDROCK_METADATA` are non-filterable as required by [AWS](https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base-setup.html). Keep custom metadata within the Bedrock/S3 Vectors limits; do not place clinical document text in sidecars.

When using a generation inference-profile ARN, set `additional_generation_model_arns` to its underlying foundation-model ARNs in each destination Region. The inference-profile ARN alone does not authorize all those model invocations.

Offline tests (Terraform 1.7 or later; mock provider, no AWS resources created):

```powershell
terraform test
```

Data sources are recreated if their parent KB changes. Removing a data source deletes its indexed data; S3 originals remain. Review replacements/re-ingestion before changing embedding models, dimensions or index metadata after deployment. Enable production backups and deletion protection; set a unique `db_final_snapshot_identifier` if `db_skip_final_snapshot=false`.

Save the important values:

```powershell
terraform output -raw documents_bucket
terraform output -raw knowledge_base_id
terraform output -raw data_source_id
terraform output -raw final_data_source_id
terraform output -raw vector_bucket
terraform output -raw vector_index_arn
terraform output -raw dynamodb_table
terraform output -raw postgres_endpoint
terraform output -raw postgres_secret_arn
terraform output -raw cognito_user_pool_id
terraform output -raw cognito_client_id
```

## Local application configuration

The local Python backend needs AWS credentials from the AWS CLI profile and the Terraform outputs. Create a local `.env` or set these variables before starting the app:

```text
AWS_RAG_ENABLED=true
AWS_PROFILE=radiology-dev
AWS_REGION=us-east-1
RAG_S3_BUCKET=<documents_bucket>
RAG_S3_PREFIX=old-reports/
RAG_FINAL_REPORTS_PREFIX=final-reports/
RAG_AUTO_INGEST_FINAL=true
DEFAULT_TENANT_ID=hospital-001
BEDROCK_KNOWLEDGE_BASE_ID=<knowledge_base_id>
BEDROCK_DATA_SOURCE_ID=<data_source_id>
BEDROCK_FINAL_DATA_SOURCE_ID=<final_data_source_id>
BEDROCK_MODEL_ARN=<approved_generation_model_arn>
RAG_KMS_KEY_ID=<kms_key_arn>
DATABASE_URL=<postgres connection string>
```

Do not put AWS access keys in the application source or Docker image. Configure the AWS CLI profile locally, for example:

```powershell
aws configure --profile radiology-dev
$env:AWS_PROFILE = "radiology-dev"
```

## Upload and ingest an old report manually

Upload a text or supported document to the configured prefix:

```powershell
$bucket = terraform output -raw documents_bucket
aws s3 cp .\sample-old-report.txt "s3://$bucket/old-reports/tenant-hospital-001/CT/sample-old-report.txt"
```

Start a Knowledge Base ingestion job:

```powershell
$kb = terraform output -raw knowledge_base_id
$ds = terraform output -raw data_source_id
aws bedrock-agent start-ingestion-job --knowledge-base-id $kb --data-source-id $ds --region us-east-1
```

Check the job:

```powershell
aws bedrock-agent list-ingestion-jobs --knowledge-base-id $kb --data-source-id $ds --region us-east-1
```

Job `COMPLETE` is not enough: inspect document failure counts and confirm individual documents are indexed. The outputs now return bare data-source IDs rather than composite Terraform IDs.

For upload/classification/valid tenant sidecars, use the application upload or the batch commands in `../BATCH_RAG_GUIDE.md`. A direct `aws s3 cp` of a report without its `.metadata.json` sidecar will not match tenant-filtered application retrieval.

## Manual RAG verification

1. Confirm the source object exists in S3 under `old-reports/`.
2. Confirm the ingestion job reaches `COMPLETE`.
3. In the AWS Bedrock console, open the Knowledge Base and use the test query area. Ask a question whose answer is present in the uploaded report.
4. Confirm the response contains a relevant retrieved reference and does not return unrelated hospital data.
5. Ask a question about a nonexistent finding. The system should say that the information is unavailable instead of inventing a finding.
6. Run the local backend with RAG enabled and call the application chat endpoint:

```powershell
docker compose up --build
```

Then use the report editor copilot to generate a draft. Confirm that the draft is marked for radiologist review and that the retrieved old reports are used only as references.

7. Verify the application can save and reload the report in PostgreSQL.
8. Finalize a new report and confirm it appears under `final-reports/` in S3. Confirm its metadata sidecar has `report_type=final_report`.
9. Wait for the automatic ingestion job to complete, then ask the copilot for a prompt that should use the finalized report.
10. Confirm draft reports are not uploaded to `final-reports/`.
11. Upload a second report with a different tenant prefix and test tenant isolation. A tenant must never retrieve another tenant's documents.
12. Inspect CloudTrail, S3 access logs, RDS metrics, and Bedrock invocation/ingestion errors before using real PHI.

## Database verification

By default RDS is private. For a short development test only, set `db_publicly_accessible = true` and `dev_client_cidr = "YOUR.PUBLIC.IP/32"`, apply Terraform, and connect with a PostgreSQL client. Revert these settings afterward. Production should keep RDS private and run the backend inside the same VPC or through a controlled network connection.

The RDS master password is stored in the AWS-managed Secrets Manager secret exposed by `postgres_secret_arn`. Retrieve it only for controlled development setup; do not commit it.

## Tenant isolation

Use a pooled DynamoDB table with keys such as:

```text
PK = TENANT#hospital-001
SK = PATIENT#patient-123
PK = TENANT#hospital-001
SK = REPORT#report-456
```

Store old reports under a tenant prefix, for example `old-reports/hospital-001/...`, and finalized reports under `final-reports/hospital-001/...`. Both sources include tenant metadata in their Knowledge Base sidecars. The backend must derive the tenant from the validated Cognito JWT and membership record. Never trust a tenant ID sent by the browser.

## Later EC2 hosting

After local RAG testing succeeds, create the EC2 host separately. Attach an IAM instance profile that can access the S3 bucket, DynamoDB table, RDS secret, KMS key, and Bedrock Knowledge Base. Put the instance in the same VPC/security-group path as RDS. Deploy the Docker image or application bundle there and set the same environment variables. This Terraform stack intentionally does not perform that hosting step.
