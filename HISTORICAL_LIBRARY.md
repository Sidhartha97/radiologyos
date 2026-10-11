# Historical Report Library

Rebuild the app to enable the new navigation page:

```powershell
docker compose up -d --build radiologyos
```

Open Historical library, select the modality, choose files or a modality folder,
click Validate selected files, then Upload & index. Each batch accepts up to 100
DOCX/text PDF/TXT files, each at most 10 MB. Folder selection uses the selected
modality for all files. Source CSV/JSON splitter manifests are ignored. Invalid or
combined reports are flagged before upload. Already imported files are skipped.

The browser uploads originals directly to encrypted S3 through one-hour signed
POST forms constrained to a server-chosen tenant/object key and exact file size.
Forms are not stored in logs. The background worker verifies the actual bytes and
checksum and validates the report again before publishing normalized text and metadata.
Originals use historical-originals/<tenant>/browser/<batch>/<file> paths. Indexed
reports use the same deterministic tenant/content IDs as the CLI importer.

DynamoDB stores durable browser batches, per-file errors and the shared import
manifest. PostgreSQL is not used by library endpoints. The existing application
still uses PostgreSQL for patients and saved reports.

The backend checks work every 15 seconds; the page refreshes every 8 seconds.
Ingestion starts after a batch is normalized, not once per file. If a scan is
already running, the batch retains needsSync and starts a follow-up scan after it
finishes. INDEXED is based on Bedrock's individual-document status, not merely
the ingestion job's COMPLETE state. Large/repeated imports should be monitored
through Recent batches. The backend must remain running for automatic processing.

If the browser closes during upload, use Retry failed / resume processing on that
batch: objects that reached S3 are recovered; missing objects show an upload-again
instruction. Choose and validate missing files again to upload them in a new batch.
If the completion API fails, keep the page open and use Retry starting this uploaded
batch. Correct invalid source files locally, then select/validate them again.

## Logs and error reasons

Each failed file shows the step, reason, AWS/error code, suggested action and AWS
request ID when available. Batch failures and ingestion job failureReasons appear
under Recent batches. Download log exports the latest 20 browser batches, their
events/errors, counters and current session activity as JSON. Local browser errors
are session-only until downloaded; backend batch errors are durable in DynamoDB.

Backend structured error logs go to standard error:

```powershell
docker compose logs --tail=200 radiologyos
docker compose logs -f radiologyos
```

Logs intentionally omit full report content, credentials and signed URLs. Batch
logs include filenames and source-service error reasons; treat downloads as private.

## Configuration and deployment

Uses existing DYNAMODB_TABLE, RAG_S3_BUCKET, RAG_KMS_KEY_ID, AWS_PROFILE,
AWS_REGION, DEFAULT_TENANT_ID, BEDROCK_KNOWLEDGE_BASE_ID and BEDROCK_DATA_SOURCE_ID.
This release retains the app's configured development tenant. Its existing demo
login does not establish authenticated hospital isolation for a public deployment.

S3 browser POST CORS has been applied for localhost:8000, 127.0.0.1:8000 and
localhost:5173. Add the actual HTTPS app origin to library_allowed_origins before
hosting. Bucket public access remains blocked. The application role's Terraform
policy includes DynamoDB DescribeTable as well as the existing tracking permissions.
The live radiology-dev profile can access the table.

A general Terraform plan currently also detects an existing inline/separate
database security-group rule conflict that would remove the PC /32 rule. Do not
apply library-upload.tfplan; only the isolated library-cors.tfplan was applied.
The IAM DescribeTable addition remains pending for deployments using the application
role rather than the working local radiology-dev profile. Review the database rule
ownership conflict before a future full infrastructure apply.

Verification: 40 offline tests including worker/active-job recovery, checksum failure,
presign boundaries, tenant scoping and retry; TypeScript/Vite production build;
live read-only library counts and local browser layout. No historical reports were
uploaded as part of implementation. Docker rebuild was blocked by named-pipe access
permissions in the agent session; run the rebuild command above in your terminal.
