# AWS Bedrock RAG configuration — S3 Vectors + DynamoDB

The app runs without AWS using a local demo fallback. To enable real S3 and Bedrock Knowledge Base calls, install the optional dependency and set these environment variables before starting the server:

```powershell
python -m pip install -r requirements.txt
$env:AWS_RAG_ENABLED='true'
$env:AWS_REGION='us-east-1'
$env:RAG_S3_BUCKET='your-private-bucket'
$env:RAG_S3_PREFIX='old-reports/'
$env:BEDROCK_KNOWLEDGE_BASE_ID='your-knowledge-base-id'
$env:BEDROCK_DATA_SOURCE_ID='your-data-source-id'
$env:BEDROCK_MODEL_ARN='arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0'
$env:RAG_KMS_KEY_ID='your-kms-key-id'
python server.py
```

For the infrastructure, use the Terraform in [`terraform/`](terraform/README.md). It creates an encrypted S3 documents bucket, S3 Vectors bucket/index, Bedrock Knowledge Base and S3 data source, one pooled DynamoDB table, KMS, Cognito, and IAM roles. It does not deploy the Python server.

The AWS identity used by the server needs permission to write the configured S3 prefix, use the KMS key, write/read DynamoDB tenant records, start Bedrock ingestion jobs, and call `bedrock:RetrieveAndGenerate` through the Knowledge Base. The app uploads a report plus a matching `.metadata.json` sidecar containing `tenant_id`, `patient_id`, `modality`, `body_part`, `report_type`, and `access_scope`.

Recommended DynamoDB keys:

```text
PK=TENANT#hospital-001, SK=PROFILE
PK=TENANT#hospital-001, SK=USER#cognito-sub
PK=TENANT#hospital-001, SK=PATIENT#patient-123
PK=TENANT#hospital-001, SK=REPORT#report-456
```

The backend must derive `tenant_id` from the Cognito JWT and the DynamoDB membership record. Never trust a tenant ID supplied by the browser. Apply the same tenant ID to S3 object keys and Bedrock retrieval filters.

Implemented API endpoints:

- `POST /api/uploads` — multipart upload of an old report
- `POST /api/uploads/{id}/ingest` — local indexing or AWS ingestion job start
- `GET /api/uploads` — upload/indexing history
- `GET /api/rag/config` — active AWS/demo mode
- `POST /api/rag/chat` — metadata-filtered Bedrock generation with citations

The UI inserts generated content as a draft and keeps finalization manual. In AWS mode, the Knowledge Base query is filtered by modality and, when supplied, patient ID.

## Application database

Terraform also provisions a private PostgreSQL RDS instance. Use PostgreSQL for relational clinical application data:

- patients
- reports and report versions
- templates
- modalities
- upload records
- AI audit records

Use DynamoDB for the SaaS control plane:

- tenant profiles
- hospital subscriptions
- user-to-tenant memberships
- plan limits and usage counters

After Terraform applies, retrieve the database secret ARN:

```powershell
terraform output postgres_secret_arn
terraform output postgres_endpoint
```

The application runtime should read the PostgreSQL username/password from AWS Secrets Manager and construct a connection string such as:

```text
postgresql://radiology_app:PASSWORD@PRIVATE_RDS_ENDPOINT:5432/radiology
```

Do not place the database password in `terraform.tfvars`, frontend code, or a Docker image. The current local demo still uses `data.json`; the production migration must add PostgreSQL migrations and replace those JSON reads/writes.

The backend now supports a transitional PostgreSQL mode. If `DATABASE_URL` is set and `psycopg[binary]` is installed, it creates an `app_entities` JSONB table and reads/writes the application collections there. If `DATABASE_URL` is not set, it continues to use `data.json` for local demo mode.

For ECS, inject `DATABASE_URL` from the RDS-managed Secrets Manager secret, or have the application fetch the secret at startup. For production, replace the transitional `app_entities` table with normalized migrations for `tenants`, `memberships`, `patients`, `reports`, `report_versions`, `templates`, and `audit_events`.

To copy the local demo data into PostgreSQL once:

```powershell
python migrate_json_to_postgres.py
```
