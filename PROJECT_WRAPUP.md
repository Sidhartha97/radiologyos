# RadiologyOS project wrap-up

The complete local source is preserved in this folder:

- `server.py` — Python backend, PostgreSQL persistence, S3 upload, Bedrock Knowledge Base retrieval, and Bedrock Nova report generation.
- `frontend/` — React/TypeScript application and report editor.
- `Dockerfile` and `docker-compose.yml` — local/container runtime.
- `terraform/` — AWS RAG and database infrastructure definition.
- `AWS_RAG_SETUP.md` and `terraform/README.md` — setup and verification instructions.

## AWS cleanup

The AWS stack was intentionally stopped to avoid ongoing charges. The local Terraform configuration remains available for a future rebuild.

Before rebuilding later, update the generated AWS IDs in `.env` after Terraform creates the resources again.

## Local restart later

```powershell
docker compose up -d --build
Invoke-RestMethod http://localhost:8000/api/health | ConvertTo-Json -Depth 8
```

## Recreate AWS later

```powershell
cd terraform
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

Upload old reports, wait for the Bedrock ingestion job to reach `COMPLETE`, then verify `/api/rag/debug` before generating drafts.

All generated reports remain drafts until a radiologist reviews and finalizes them.
