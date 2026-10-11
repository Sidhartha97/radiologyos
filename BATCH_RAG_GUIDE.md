# RadiologyOS: batch historical-report ingestion and RAG

## What was implemented

Existing React editor → existing Python `/api/rag/chat` → Bedrock Knowledge Base retrieval → complete S3 reference documents → existing Bedrock Converse generation → existing editor.

```text
Local historical_reports folder (recursive DOCX / text PDF / UTF-8 TXT)
    → deterministic tenant + content hash / DynamoDB manifest
    → original preserved in existing S3 bucket
    → complete text extraction + rule-first classification + style metadata
    → normalized TXT and metadata in existing old-reports/ data source
    → Bedrock Knowledge Base chunking + existing Titan embedding model
    → existing S3 Vectors index

Current findings + selected modality
    → prompt modality/body-region/study analysis
    → tenant + modality + preferred study / region filters
    → semantic retrieval / deduplicate / top 3–5
    → fetch COMPLETE selected reports from S3
    → Bedrock: current findings = CONTENT; historical reports = STYLE
    → Markdown draft / existing editor's safe HTML renderer
```

This is retrieval-based style guidance, not model training or fine-tuning. The model does not permanently learn your documents. Your embedding model, vector store, bucket, editor and API names remain unchanged. No EC2 resources were added.

## Files

| File | Purpose |
| --- | --- |
| `ingestion/documents.py` | DOCX/PDF/TXT extraction, modality/body-region/study rules, section/list-style extraction |
| `ingestion/batch.py` | Resumable CLI, deterministic IDs, DynamoDB manifest, unchanged-S3 detection, optional Bedrock classifier, ingestion status |
| `ingestion/retrieval.py` | Strict tenant/modality search, study/region preferences, deduplication, full S3 documents, context limits |
| `ingestion/__init__.py` | Package marker |
| `historical_reports/.gitkeep` | Empty default local input directory |
| `tests/test_ingestion.py` | Offline mocked integration/unit tests; never imports the server's database startup |
| `server.py` | Connects existing upload, finalized-report publication and generation to improved metadata/retrieval |
| `frontend/src/App.tsx` | Prevents failed output from being inserted, handles errors, preserves bullet/numbered list style |
| `Dockerfile`, `requirements.txt` | Include ingestion package and PDF reader in the image |
| `terraform/main.tf` | Add original-storage and document-status permissions to the application role |
| `.env`, `docker-compose.yml` | Use the user-specified `dev` profile; disable unscoped retrieval |
| `.gitignore`, `.dockerignore` | Exclude local reports, temporary dependency/cache directories and test data |
| `frontend/package-lock.json` | Dependency lock generated while verifying the React build |

Existing report CRUD, registration, template and modality UI, final-report PostgreSQL persistence, Cognito and DynamoDB infrastructure are not replaced.

## Where to put historical reports

Default directory: `outputs/radiology-reporting/historical_reports/`, alongside `server.py`.

Alternatively use `E:\RadiologyOS\historical_reports` with `--folder`. Subfolders are discovered automatically. You do not need modality folders. Use **one study/report per file**. Combined multi-study files are flagged FAILED with a split-file instruction; they are not silently assigned a single incorrect modality. Scanned PDFs require OCR first. Originals are preserved; normalized text retains sections and supported DOCX list numbering, not an exact Word page layout.

Do not place patient documents in source control or the Docker build context. This directory is excluded from both. Use deidentified copies for initial testing.

## Existing `.env` settings

Keep your existing `.env`; never paste credentials into chat. Existing settings used:

```dotenv
AWS_PROFILE=radiology-dev
AWS_REGION=us-east-1
AWS_RAG_ENABLED=true
LLM_PROVIDER=bedrock
DEFAULT_TENANT_ID=hospital-001
RAG_S3_BUCKET=<existing documents bucket>
RAG_S3_PREFIX=old-reports/
RAG_FINAL_REPORTS_PREFIX=final-reports/
BEDROCK_KNOWLEDGE_BASE_ID=<knowledge base ID>
BEDROCK_DATA_SOURCE_ID=<old-reports data source ID>
BEDROCK_FINAL_DATA_SOURCE_ID=<final-reports data source ID>
BEDROCK_MODEL_ARN=<existing Converse-compatible model ARN>
RAG_KMS_KEY_ID=<existing KMS ARN>
DYNAMODB_TABLE=radiologyos-dev
```

Use bare data-source IDs, NOT Terraform's `dataSourceId,knowledgeBaseId` composite ID.

Historical import tracking uses the existing DynamoDB table named by `DYNAMODB_TABLE`; it never connects to PostgreSQL. Keep your application's PostgreSQL settings for patients and saved/finalized reports. A PostgreSQL connectivity problem may still affect the web application, but does not block this batch importer.

Optional controls (defaults apply when omitted):

```dotenv
HISTORICAL_REPORTS_DIR=E:\RadiologyOS\historical_reports
BATCH_SIZE=100
RAG_ORIGINALS_PREFIX=historical-originals/
RAG_TOP_K=5
RAG_MIN_SCORE=0
RAG_REPORT_MAX_CHARS=15000
RAG_CONTEXT_MAX_CHARS=30000
RAG_ALLOW_UNSCOPED_RETRIEVAL=false
```

Do not change the two indexed prefixes unless the Bedrock data sources match. The original prefix must stay outside indexed prefixes. Raising context limits raises generation cost. Score thresholds require calibration on your documents; zero means no numeric cutoff, but tenant/modality filters still apply. Optional classification costs are enabled only by `--classify-with-bedrock`; rules run first and invoke the model only when uncertain.

## S3 and metadata

```text
<existing bucket>/
  historical-originals/<tenant>/<content-hash>.docx   # or PDF / TXT; not indexed
  old-reports/<tenant>/normalized/<content-hash>.txt # complete normalized report
  old-reports/<tenant>/normalized/<content-hash>.txt.metadata.json
  final-reports/<tenant>/<patient>/<report-id>.txt    # existing finalized-report publication
  final-reports/<tenant>/<patient>/<report-id>.txt.metadata.json
```

Compact metadata includes `tenant_id`, `report_id`, `report_type`, `modality`, `body_region`, `study_type`, `study_key`, `classification_method`, `classification_confidence`, `sections`, `findings_style`, `impression_style`, `verbosity`, `tone`, `formatting_pattern`, `style_method`, `s3_key`, `original_s3_key`. Empty values are not published. `tone=report_style` is a lightweight rule label, not a trained tone classifier. The full report gives the LLM its terminology/style context.

Bedrock owns chunking and vector insertion. Existing Terraform uses fixed-size chunks (400 tokens / 15% overlap) and the configured Titan embedding model/dimension; this change does not replace the index or embedding model. One report can legitimately create multiple vectors, one per chunk. Stable report IDs and unchanged-object checks prevent duplicate logical documents on replay. Byte-identical copies are deduplicated per tenant; differently saved DOCX files with identical visible text may have different binary hashes.

Older documents remain supported by tenant/modality retrieval when their metadata exists. For region/study preferences, re-ingest through the new pipeline using the original files. Existing legacy copies may remain alongside new normalized objects; do not mass-delete old keys or reset the KB. Review duplicates separately before a controlled migration.

## DynamoDB manifest

The importer reuses the existing application table with string keys `PK` and `SK`:

```text
PK = TENANT#hospital-001
SK = INGESTION#REPORT#<tenant-and-content-hash>
payload = report ID, file name, modality, S3 keys, status and error

PK = TENANT#hospital-001
SK = INGESTION#LOCK
owner = unique run ID; expires_at = lease expiry
```

Only tracking metadata is in DynamoDB; original reports and complete extracted text remain in S3, and embeddings remain in S3 Vectors. The CLI uses strongly consistent, paginated tenant queries and conditional tenant locks. Locks expire after ten minutes if a process crashes, renew on tracking writes, and are released only by their owner. Import runs do not create or change the table. Optional DynamoDB TTL is not needed for lock expiry. Existing PostgreSQL tracking rows, if any, are retained but not automatically migrated; deterministic S3 keys still prevent duplicate objects. Resume status comes from DynamoDB.

`PENDING` → `PROCESSING` (uploaded/queued) → `COMPLETED` only when Bedrock says the individual document is `INDEXED`. Invalid/partial/ignored documents become `FAILED`. Job `COMPLETE` alone is **not** evidence that every file indexed. `--progress` displays the stored snapshot; `--refresh-status` updates it from AWS without uploading files or starting a new job.

## Commands — run from the directory containing `server.py`

Install dependencies in your normal project virtual environment:

```powershell
python -m pip install -r requirements.txt
$env:AWS_PROFILE = 'radiology-dev'
aws sts get-caller-identity --profile radiology-dev
```

Dry-run (reads files/DynamoDB state; no uploads, tracking writes, model calls or embeddings):

```powershell
python -m ingestion.batch --folder 'E:\RadiologyOS\historical_reports' --dry-run
```

If DynamoDB/AWS is unavailable, dry-run reports unknown manifest counts. It must not be mistaken for proof that everything is unprocessed.

For your prepared modality directories, run:

```powershell
python -m ingestion.batch --folder 'C:\Users\aa\Documents\Historical reports' --folder-modalities --limit 10
python -m ingestion.batch --refresh-status
python -m ingestion.batch --progress
```

Use `--folder-modalities` on subsequent imports too. This uses your CT/MR/USG directory labels without model classification; MR is stored as MRI.

First 10 new reports:

```powershell
python -m ingestion.batch --folder 'E:\RadiologyOS\historical_reports' --batch-size 10 --limit 10
python -m ingestion.batch --refresh-status
python -m ingestion.batch --progress
```

Next 100, then next 500 (after verifying each stage):

```powershell
python -m ingestion.batch --folder 'E:\RadiologyOS\historical_reports' --batch-size 100 --limit 100 --resume
python -m ingestion.batch --folder 'E:\RadiologyOS\historical_reports' --batch-size 100 --limit 500 --resume
```

Resume/process all remaining; no manual index ranges:

```powershell
python -m ingestion.batch --folder 'E:\RadiologyOS\historical_reports' --resume
```

Retry failures (also processes other pending files, up to your limit):

```powershell
python -m ingestion.batch --folder 'E:\RadiologyOS\historical_reports' --retry-failed --limit 10
```

Optional classification fallback:

```powershell
python -m ingestion.batch --folder 'E:\RadiologyOS\historical_reports' --limit 10 --classify-with-bedrock
```

Batch size controls progress checkpoints; documents are streamed one at a time. Bedrock ingestion is queued once after uploads to avoid starting dozens of competing scans. If a job is active, its ID is returned rather than starting another. After that job finishes, run `--resume` to start a scan for uploads made after its snapshot, then `--refresh-status`. Do not repeatedly call ingestion endpoints as if they were status checks.

## AWS prerequisites and proposed infrastructure change

Reuse the S3 bucket, KMS key, Knowledge Base, both S3 data sources, S3 Vectors index and PostgreSQL instance. No new vector database, bucket or EC2 is required by these code changes. Existing Cognito and DynamoDB resources remain unchanged.

The Terraform edit adds only these application-role capabilities:

- S3 Put/Get/AbortMultipartUpload under `historical-originals/*`.
- Bedrock ingestion-list, document-status and data-source read permissions on the existing KB.

Important: a locally configured IAM user is **not automatically the application role**. If `dev` runs directly as an IAM user, ensure it assumes that role or has equivalent scoped permissions. Do not add administrator access as a workaround.

This session could not access the `dev` profile and the local Terraform state contains zero resources. Therefore **live AWS resources are unverified and Terraform was not applied**. An empty local state might mean resources were destroyed, or that the wrong state/backend is being used. Applying immediately could create chargeable resources or collide with existing names.

Read-only checks you can run in your own PowerShell:

```powershell
aws sts get-caller-identity --profile dev
aws bedrock-agent list-knowledge-bases --profile dev --region us-east-1
aws s3api list-buckets --profile dev
aws rds describe-db-instances --profile dev --region us-east-1 --query 'DBInstances[].{ID:DBInstanceIdentifier,Status:DBInstanceStatus,Public:PubliclyAccessible}'
terraform -chdir=terraform state list
```

Use the correct existing Terraform backend/state before planning. Then run `terraform plan` and review it. Obtain approval before applying. Stop if it replaces/deletes a KB/vector index, changes embedding dimensions, creates resources unexpectedly or changes public access. Do not reuse an old saved `tfplan`.

## Verify RAG with 10 documents before scaling

1. Use deidentified MRI, CT and USG examples. Confirm S3 has original + complete TXT + valid sidecar for each stable report ID.
2. Inspect TXT: study title, TECHNIQUE, complete FINDINGS and IMPRESSION must be present.
3. Run `--refresh-status`; all test documents should be `COMPLETED` / vector `INDEXED`. Inspect FAILED messages instead of repeatedly starting scans.
4. In the existing app select USG and ask: `Hepatomegaly with Grade I fatty liver.` Modality comes from the selected study when the prompt does not explicitly name one; this finding alone is not treated as proof of ultrasound.
5. Ask `USG abdomen: hepatomegaly with Grade I fatty liver`. Retrieval prefers USG+ABDOMEN; it may relax study/region but **never** a known modality or tenant.
6. In browser network tools inspect `/api/rag/chat`: citations show URI, report ID, score and study. Generation uses full documents, not the citation preview or a single returned chunk.
7. Request `MRI knee with moderate effusion` and `CT brain with right MCA territory infarct`. Citation modalities must remain MRI and CT respectively.
8. Check that no unrelated historical measurements, normal organs, history, contrast/sequence details, patient names or management recommendations appear as current findings. A radiologist must review every draft. Prompt constraints are not a medical safety guarantee; evaluate your selected model on deidentified cases before clinical use.
9. Repeat a batch: completed documents should not upload again. For byte-identical files, the manifest count and S3 keys remain stable. Multiple KB chunks per document are expected, not duplicate reports.
10. Only then proceed to 100 → 500 → remainder.

No-result and AWS failures now return empty report text plus an error warning. The editor does not insert those warnings as clinical reports. An unconfigured local demo scaffold is explicitly labelled and cannot be inserted as a successful RAG draft.

## Local checks performed

26 offline unit/mocked integration tests passed; Python compilation passed; React/TypeScript production build passed. Terraform validation reported a valid configuration, but also an inaccessible CLI-config directory warning. A real empty-folder dry-run succeeded without writes and reported the inaccessible manifest correctly. No historical documents were batch-uploaded, no live Bedrock generation/embedding was tested, and no real PostgreSQL migration was run in this session. Docker daemon was unavailable, so a complete Docker image/runtime check remains pending.

Run the tests locally after dependency installation:

```powershell
python -m unittest discover -s tests -v
```

These tests cover complete DOCX/PDF extraction, classification/style rules, optional fallback behavior, upload idempotency, discovery/deduplication, limit/resume/retry, document-level indexing status, active-job reuse, dry-run, strict tenant/modality filters, MRI/CT/USG retrieval, context/top-K limits and generation prompt/error contracts. S3/Bedrock/PG interactions are mocked; they do not certify live IAM, embeddings or clinical accuracy.

## Docker

Once Docker Desktop and AWS/database connectivity are working:

```powershell
docker compose up -d --build
docker compose logs --tail=100 radiologyos
Invoke-RestMethod http://localhost:8000/api/health | ConvertTo-Json -Depth 8
```

Run batch ingestion on your PC with the Python commands above, or mount a dedicated input directory into a one-off container. Do not bake 5,000 patient reports into the image. `.env` is not copied into the image; Compose supplies it at runtime.

## Production boundary / tenant isolation

The existing backend currently uses a configured development tenant; Cognito is provisioned but verified JWT tenant resolution is not wired into these Python endpoints. This change rejects browser-supplied tenant overrides and keeps retrieval tenant-filtered, but **does not make the entire existing app production-safe or hospital multi-tenant authenticated**. Existing patient/report CRUD also needs an authenticated tenant boundary. Do not expose it publicly with real patient data until that separate security implementation is approved and tested. No authentication replacement was attempted here.

## Troubleshooting and rollback

- `ProfileNotFound`: configure or log in to the `dev` profile on the machine actually running Python/Docker; verify STS. Never commit keys.
- DynamoDB access errors: verify DYNAMODB_TABLE and permission for DescribeTable, Query, PutItem, UpdateItem and DeleteItem. RDS networking is unrelated to batch import.
- `AccessDenied`: check caller identity, application role permissions, KB role permissions and KMS policies separately.
- Invalid metadata: examine the TXT sidecar; empty values and incorrectly tagged combined reports are rejected. Do not remove tenant filters to make retrieval work.
- FAILED or partial documents: read manifest error and Bedrock ingestion details. Correct the source/metadata; then retry a limited batch.
- PROCESSING for a long time: inspect the actual per-document status and latest job in Bedrock. Use `--refresh-status`, not POST-ingestion loops.
- Unknown modality: use an informative filename or the optional Bedrock classifier; mixed-study files must be split.
- Scanned PDF: OCR outside this pipeline before retrying.
- Oversized reference: it is skipped, never silently truncated. Use sensible study-level files or review context limits.
- Formatting mismatch: full references guide the model; the React renderer turns Markdown into headings/lists. Fine-tuning is not required for this setup; model changes still need clinical/style evaluation.

Rollback: stop the batch process first; restore prior source files from your backup/version control, rebuild the app, and retain the manifest/originals for audit. Do not destroy AWS, clear the KB, drop tables or delete patient documents as a rollback shortcut. Any removal of only newly added normalized objects should be separately reviewed and approved; synchronization affects vectors. The additive manifest table can remain unused safely.
