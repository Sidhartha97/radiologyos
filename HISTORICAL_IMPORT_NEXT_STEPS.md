# Historical report import — 8 October 2026

Source: `C:\Users\aa\Documents\Historical reports`

Local validation found 371 DOCX files: CT 121, MR 50, USG 200.
There are 363 unique binary files: 361 pass extraction/single-report checks;
2 require review. These checks do not establish clinical quality or complete de-identification.

Review these source files before ingestion (both contain multiple report sections):

- `MR/MRI_MR LS SPINE SCR WHOLE SPINE_00039.docx`
- `MR/MRI_MR LS SPINE SCR WHOLE SPINE_00060.docx`

Eight byte-identical duplicates are automatically deduplicated. No source files were changed.
Use `--folder-modalities`: CT, MR (mapped to MRI), and USG folders are authoritative.
This resolves 100 CT filenames containing an incorrect USG prefix without renaming originals.
CSV/JSON splitter manifests are not ingested. No LLM classification is needed.

## Repeat local validation

Run from the directory containing server.py:

```powershell
python -m ingestion.batch --folder 'C:\Users\aa\Documents\Historical reports' --folder-modalities --preflight --validation-output historical_validation.json
```

This command does not access AWS or PostgreSQL. The JSON lists each unique file and its result.

## Infrastructure gate

Read-only checks with AWS profile `radiology-dev` found no Knowledge Bases or RDS instances in
us-east-1 and no Radiology OS report bucket. Terraform state was empty.
A fresh plan was saved at `terraform/historical-reports.tfplan`, using
`terraform/historical-reports.tfvars`: 26 additions, zero changes, zero deletions.
It includes S3, S3 Vectors, Bedrock KB/data sources, RDS PostgreSQL, DynamoDB,
Cognito, KMS and IAM. No EC2 server. The plan has NOT been applied.

Applying creates chargeable services and requires approval. RDS remains private in this
proposal, so a VPN/tunnel/private connection is required to ingest from the PC.
If choosing temporary public development access restricted to the PC's /32 address,
update and review a NEW plan before applying. Do not reuse the old `tfplan`.

Update: infrastructure has now been deployed and .env updated with new outputs.
Historical-import tracking now uses `DYNAMODB_TABLE=radiologyos-dev`, and a live
DynamoDB read-only dry run succeeded. PostgreSQL connectivity is only required by
the web application's patient/report database, not by historical ingestion.

## First live batch using DynamoDB tracking

```powershell
$env:AWS_PROFILE = 'radiology-dev'
python -m ingestion.batch --folder 'C:\Users\aa\Documents\Historical reports' --folder-modalities --limit 10
python -m ingestion.batch --refresh-status
```

Check that all ten documents are COMPLETED/INDEXED, then test retrieval in Radiology OS.
The report originals are preserved in S3; searchable text and modality/tenant metadata are
indexed. Check sample citations and complete reference text before uploading the remainder:

```powershell
python -m ingestion.batch --folder 'C:\Users\aa\Documents\Historical reports' --folder-modalities --resume --limit 100
```

Files failing the single-report check are rejected before S3 upload. Correct the two MRI
files separately and revalidate before importing them. No live reports were uploaded during preparation.
