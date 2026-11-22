# Implementation and portfolio guide

This guide explains the project in a format suitable for a technical portfolio. It separates implemented architecture and workflows from production gaps; do not describe the current single-instance HTTP test deployment as production-ready.

## 1. Project story

RadiologyOS was built to help radiology teams draft structured reports using their own historical reports as reference material. It combines a React reporting workspace, a Python API, a tenant-aware SaaS foundation, a retrieval-augmented generation path and an AWS deployment pipeline.

The central engineering challenge is to retrieve relevant examples while keeping every search scoped to the authenticated hospital and selected modality. The application requires a human clinician to review AI drafts before finalization.

## 2. Implementation sequence

### Step 1 — Reporting application foundation

- Built a React/TypeScript single-page application and Python API.
- Added report editing, patient/report workflows, modality-specific templates, save/finalize and print-related workflows.
- Added an assistant that accepts instructions and returns an editable draft.

### Step 2 — Historical report library and RAG

- Established S3 prefixes for historical and final reports.
- Added document extraction/normalization and metadata classification for modality, study and body region.
- Added Bedrock Knowledge Base data sources over the S3 prefixes, fixed-size chunking, Amazon Titan embeddings and an S3 Vectors index.
- Added retrieval with tenant and modality filters and server-side checks of retrieved metadata.
- Connected retrieved examples to a configurable Bedrock Runtime text-generation model.

### Step 3 — Tenant and role boundaries

- Added versioned PostgreSQL migrations for tenants, workspaces, members, invitations, audit events and report workflow state.
- Added a non-owner application database login, row-level security and backend membership/permission checks.
- Kept platform administration separate from clinical access. Signing and AI capabilities require explicit grants.
- Added account creation/invitations, hospital employee access, report assignment, editing leases and review handoff.

### Step 4 — AWS infrastructure as code

- Defined AWS resources in Terraform: Cognito, PostgreSQL RDS, S3, S3 Vectors, Bedrock Knowledge Base/data sources, DynamoDB, KMS, Secrets Manager, ECR, IAM and a low-cost EC2 testing host.
- Kept RDS private and stored application/runtime configuration in Secrets Manager.
- Limited the optional web ingress to a single configured IPv4 `/32`.

### Step 5 — Container and CI/CD

- Created a multi-stage Docker image with native frontend build and a Python application runtime.
- Configured GitHub Actions to run backend tests and a frontend production build.
- Used GitHub OIDC instead of long-lived AWS keys; scoped the deploy role to ECR publishing and SSM commands.
- Built ARM64 images tagged with the Git commit SHA, pushed to ECR, then deployed through Systems Manager Run Command.
- Added a post-deployment health check and previous-image restoration attempt if the health check fails.

## 3. Stack summary

- **UI:** React, TypeScript, Vite, CSS
- **API:** Python 3.12, Boto3, Psycopg/connection pool
- **Relational data:** PostgreSQL 16 on Amazon RDS; SQL migrations and row-level security
- **Identity:** Amazon Cognito User Pools
- **RAG:** Amazon Bedrock Knowledge Bases, S3 data sources, Titan Text Embeddings, S3 Vectors
- **Generation:** Amazon Bedrock Runtime Converse API with a configurable model
- **Storage:** Amazon S3, DynamoDB, Secrets Manager
- **Encryption/permissions:** AWS KMS, IAM roles and scoped policies
- **Runtime/deployment:** Docker, EC2 ARM64, ECR, Systems Manager
- **IaC/CI/CD:** Terraform, GitHub Actions, GitHub OIDC

## 4. Reproduce the AWS development deployment

These steps are for a fresh authorized development environment. Existing environments should be inspected before applying Terraform. AWS resources incur charges.

### Prerequisites

- Windows PowerShell (or an equivalent shell), Git, Python 3.12, AWS CLI, Terraform and Docker Desktop only if building/testing locally.
- An AWS CLI profile with only the permissions needed to manage this project. Do not put access keys in the repository or GitHub Actions.
- A GitHub repository and a Dockerfile/workflow committed to `main`.
- Synthetic test users and reports only.

### Step 4.1 — Configure infrastructure inputs

Review `terraform/historical-reports.tfvars` for unique S3 names, AWS region, `github_actions_repository` and the current testing client IP as a single `/32`. Review `terraform/testing-host.auto.tfvars` to understand whether the EC2 test host is enabled. Keep account-specific `.tfvars`, state and plans private; replace account-specific values with examples before a public portfolio release.

### Step 4.2 — Plan and provision

From PowerShell, in the Terraform directory:

```powershell
$env:AWS_PROFILE = "YOUR_AWS_PROFILE"
Set-Location "C:\path\to\radiology-reporting\terraform"
terraform init
terraform plan -var-file="historical-reports.tfvars" -out="infra.tfplan"
terraform show "infra.tfplan"
```

Review every create, update, replace and delete. Only apply a plan that matches the intended environment:

```powershell
terraform apply "infra.tfplan"
```

Terraform outputs provide generated resource identifiers. Do not paste secret values or full state output into public documentation.

### Step 4.3 — Bootstrap the application and database

Start RDS for the one-time bootstrap. From the project root, install the Python dependencies required by the operator script and run the explicit reviewed bootstrap:

```powershell
Set-Location "C:\path\to\radiology-reporting"
py -3.12 -m pip install boto3
py -3.12 scripts/deploy_aws.py deploy --profile YOUR_AWS_PROFILE --approve-schema --admin-email admin@example.com
```

The deploy utility reads Terraform outputs, uploads a temporary source bundle, builds/publishes the initial image to ECR through the host workflow, configures the application secret, applies explicitly approved schema changes and starts the service. It does not run `terraform apply`.

### Step 4.4 — Configure GitHub Actions variables

In **GitHub → repository Settings → Secrets and variables → Actions → Variables**, set repository variables from the Terraform outputs:

| Variable | Source/value |
|---|---|
| `AWS_REGION` | AWS deployment region |
| `AWS_ROLE_ARN` | `terraform output -raw github_actions_deploy_role_arn` |
| `EC2_INSTANCE_ID` | `terraform output -raw testing_instance_id` |
| `ECR_REPOSITORY_URI` | `terraform output -raw application_repository_url` |

The ECR URI is a repository URI without a tag. No Docker Hub credential or long-lived AWS access key is required for this workflow.

### Step 4.5 — Deploy code changes

Commit and push to `main`:

```powershell
git add .
git commit -m "Describe the change"
git push origin main
```

In GitHub Actions, verify that the **Verify backend and frontend** and **Build ARM image and deploy to EC2** jobs succeed. The deployment job should push a commit-SHA image to ECR, invoke SSM, restart the app and report a healthy `/api/health` response.

For manual deployment, choose **Actions → CI and EC2 deployment → Run workflow** on `main`.

### Step 4.6 — Open and smoke-check the test site

Use the current public IP from `terraform output -raw testing_instance_public_ip` and open `http://<IP>:8000/` only from the configured source IP. Check sign-in using the intended test account and verify `/api/health`. Use synthetic records only. A local tunnel is an alternative when configured; see `docs/EC2_TESTING.md`.

## 5. RAG workflow in one pass

1. Prepare each historical report as its own document, or use the project's report splitter before ingestion.
2. Assign correct tenant, modality and study metadata. Avoid mixing tenants in one ingestion path.
3. Upload to the tenant-scoped historical S3 prefix using the application library or reviewed ingestion tooling.
4. Run Bedrock Knowledge Base ingestion and inspect status/failure reasons and metadata.
5. Submit a prompt that identifies the study/modality and positive findings.
6. The backend searches with mandatory tenant and modality filters and checks returned metadata.
7. The generation model writes a proposed report using retrieved examples for reference. The clinician validates every fact and signs only after review.

Do not claim RAG makes the report clinically correct. Retrieval relevance, source metadata quality and human review remain essential.

## 6. Screenshot checklist for GitHub and LinkedIn

Capture with synthetic users/reports only. Use browser profiles with no saved real patient data. Crop or blur email addresses, patient names/IDs, Cognito IDs, account IDs, ARNs, public IPs, secret values, job command IDs and URLs that expose private infrastructure.

| Screenshot | What it demonstrates | Suggested caption |
|---|---|---|
| Architecture diagram from `docs/ARCHITECTURE.md` | AWS data flow, storage, RAG and CI/CD | “Tenant-aware RAG architecture on AWS” |
| Platform overview with synthetic accounts | Administration dashboard and account separation | “Business administration separated from clinical access” |
| Report editor with fictional patient and synthetic findings | Structured editor and clinician-reviewed draft workflow | “Structured radiology report drafting” |
| Assistant response with synthetic prompt and source labels | RAG-assisted drafting and human review boundary | “Historical examples provide context; clinician reviews the draft” |
| Historical library with fake file names and ingestion statuses | Document ingestion lifecycle | “Modality-aware historical report library” |
| Access/assignment workflow with demo roles | Role-based capabilities and handoff | “Radiologist/transcriptionist collaboration with explicit permissions” |
| GitHub Actions run with both jobs green | Automated CI/CD evidence | “Test, build, publish to ECR, deploy through SSM” |
| ECR repository showing SHA-tagged image | Immutable image publishing | “Versioned ARM64 container image in ECR” |
| Terraform code or sanitized plan | Infrastructure as code and review workflow | “AWS resources provisioned and reviewed with Terraform” |

Recommended LinkedIn carousel: 1) product/editor, 2) architecture, 3) tenant/role controls, 4) CI/CD green run, 5) lessons and current limitations. Avoid screenshots of real AWS emails, login credentials, customer accounts or unredacted Terraform outputs.

## 7. Resume bullets (edit to match what you personally did)

- Built a multi-tenant radiology reporting application with React/TypeScript and Python, integrating PostgreSQL tenant isolation, Cognito authentication and role-specific workflows.
- Implemented retrieval-assisted report drafting using Amazon Bedrock Knowledge Bases, S3 historical documents, metadata filters and an S3 Vectors index.
- Provisioned AWS development infrastructure with Terraform and deployed a Dockerized ARM64 application to EC2 using GitHub Actions, ECR, GitHub OIDC and Systems Manager.
- Designed clinician-review workflows with report assignment, editing leases, auditability and separate AI/signing permissions.

Do not add performance, cost-savings, user-count or clinical-accuracy metrics unless you have measured and can substantiate them.

## 8. LinkedIn project description

> I built RadiologyOS, a radiology-reporting workspace that combines structured editing with historical-report retrieval. The system uses Amazon Bedrock Knowledge Bases and S3 Vectors for tenant- and modality-scoped references, with AI output kept as a clinician-reviewed draft. I provisioned the AWS development stack with Terraform and set up GitHub Actions to test, build an ARM64 Docker image, publish it to ECR and deploy to EC2 through Systems Manager using OIDC. The current deployment is a single-instance test environment; it is not intended for real patient data or clinical use.

## 9. Before publishing the repository

- Review the tracked files and commit history for API keys, AWS credentials, `.env` values, database passwords, tokens, signed URLs and private email addresses.
- Replace account-specific bucket names, client CIDRs, ARNs, resource IDs, public IPs and sample emails in tracked Terraform files with clearly marked placeholders.
- Confirm no patient documents, exports, screenshots or identifiable metadata are tracked.
- Keep `terraform.tfstate`, plans, `.env`, test database dumps and historical reports out of Git.
- Rotate any credential that was ever committed or pasted into an issue/chat.
- Clearly label the deployment as a development/test topology and list the HTTPS, availability, backup/restore and security-review work that remains.
