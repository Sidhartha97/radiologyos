# RadiologyOS — implementation and portfolio guide

## Project summary

RadiologyOS is a multi-tenant radiology reporting workspace with historical-report retrieval and AI-assisted drafting. It combines report editing and clinical workflow, tenant-aware access control, a metadata-filtered RAG pipeline, PostgreSQL-backed SaaS data, and an AWS-hosted container platform with GitHub Actions CI/CD.

The core system-design challenge is to provide useful historical examples while preserving hospital and modality boundaries. The backend determines identity, tenant, permissions and retrieval filters. Generated text remains a draft for clinician review.

For the end-to-end browser/API flow, historical document ingestion, RAG retrieval and generation, and the implemented role/capability matrix, see [WEB_APP_AND_RAG.md](WEB_APP_AND_RAG.md).

## Resume-ready content

### Project heading

**RadiologyOS | Multi-tenant AI Radiology Reporting Platform**
React · TypeScript · Python · PostgreSQL · AWS · Bedrock · Terraform · Docker · GitHub Actions

### Resume bullets

- Designed a tenant-aware radiology reporting platform with role/capability separation for administrators, radiologists and transcriptionists, plus report assignment, review handoff and editing locks.
- Built a retrieval-augmented drafting path using Amazon S3, Bedrock Knowledge Bases, Titan Text Embeddings V2 and S3 Vectors; enforced server-side tenant and modality filters and validated retrieved metadata before generation.
- Implemented a PostgreSQL SaaS data layer with versioned SQL migrations, non-owner runtime credentials and row-level security for tenant-scoped records.
- Provisioned a dedicated AWS VPC with public ALB subnets, private app and database subnets, NAT egress, private PostgreSQL, and an EC2 Auto Scaling Group capped at four instances.
- Automated verification, ARM64 Docker image builds, immutable ECR publishing and rolling EC2 deployments through GitHub Actions OIDC, scoped IAM, SSM, ASG refresh and ALB health checks.
- Integrated Cognito authentication, Secrets Manager runtime configuration, KMS encryption, S3 document storage and DynamoDB ingestion/job metadata.

Use only bullets you can explain and demonstrate. Replace technical claims with measured results only when you have real measurements and reproducible evidence.

## GitHub repository presentation

1. Make the repository description concise: **Multi-tenant radiology reporting with filtered RAG and AWS CI/CD**.
2. Put the current architecture diagram near the top of the README.
3. Keep setup, architecture, implementation, security boundaries and screenshots in separate linked docs.
4. Add screenshots under `docs/screenshots/` only after sanitizing them. Prefer synthetic users and reports.
5. Before making the repository public, inspect current files, ignored files, Git history, Terraform plans/state, workflow logs and screenshots for account identifiers, public IPs, resource ARNs, credentials, real names or clinical data. `.env`, Terraform state/plans, report samples and AWS CLI credentials must remain private.
6. Never include passwords, access keys, Cognito verification codes, connection strings, signed URLs, real patient information or raw CloudWatch/SSM logs.

## LinkedIn post draft

> I built RadiologyOS, a multi-tenant radiology reporting workspace that combines structured report workflows with retrieval-augmented AI drafting.
>
> The system uses tenant- and modality-filtered retrieval over historical reports stored in Amazon S3, indexed through Amazon Bedrock Knowledge Bases and S3 Vectors. Cognito handles identity, PostgreSQL stores tenant/workflow state with row-level security, and clinicians review generated drafts before finalization.
>
> I also implemented the AWS delivery platform with Terraform: a dedicated VPC, ALB, private EC2 Auto Scaling tier, private RDS, ECR and GitHub Actions deployments using OIDC and immutable ARM64 images.
>
> This project helped me work through system design trade-offs around tenant isolation, least-privilege IAM, RAG metadata quality, deployment safety and cost-aware scaling. It is a development portfolio project and is not clinically validated.
>
> Architecture and implementation notes: [add your public repository link]

Attach one clear architecture image and two or three sanitized product/pipeline screenshots, not a collage of console pages.

## Recommended implementation walkthrough

### 1. Build and run the app locally

- Frontend: React, TypeScript and Vite.
- Backend: Python 3.12 HTTP service with AWS SDK and Psycopg.
- Package the app in Docker; use synthetic identities and reports locally.
- Keep environment files and local AWS credentials out of Git.

### 2. Establish the tenant data model

- Add schema changes as ordered SQL migrations under `migrations/`.
- Store tenants, users, memberships, report workflow, locks, audit records, conversations and usage events in PostgreSQL.
- Authenticate with Cognito, then resolve tenant membership on the server.
- Use a dedicated non-owner DB role and RLS policies to enforce tenant context at the database layer.
- Separate AI capability from signing permission; a role name alone does not grant clinical authority.

### 3. Build the historical-report RAG path

- Upload one report per document where possible; keep original, normalized and final report prefixes distinguishable in S3.
- Attach metadata including tenant, modality and study. Treat metadata quality as a prerequisite for relevant retrieval.
- Ingest the S3 prefixes through Bedrock Knowledge Base data sources.
- Use Titan Text Embeddings V2 with a vector dimension matching the S3 Vectors index. Current Terraform defaults are 1024 dimensions, 400-token chunks and 15% overlap.
- Require modality and tenant on every retrieval request, re-check returned source metadata, and avoid cross-modality fallback.
- Send retrieved references and the current user instruction to the Bedrock generation model; return a clearly marked draft for human review.

### 4. Provision cloud infrastructure

- Review Terraform variables and AWS region/account before planning.
- The target network uses two AZs with ALB public subnets, private application subnets and isolated database subnets.
- One NAT Gateway provides private-tier egress for the cost-conscious development configuration.
- The ALB routes to an EC2 ASG; target tracking scales the group with a maximum size of four.
- RDS PostgreSQL is private and separate from EC2 storage. S3, DynamoDB, RDS and vector resources are protected/encrypted according to Terraform configuration.
- Run `terraform plan`, inspect resource replacements/deletes and only apply a reviewed plan.

### 5. Bootstrap and deploy

- Run the explicit deployment/bootstrap helper after Terraform outputs exist and RDS is available.
- The helper configures runtime secrets, applies reviewed migrations and establishes the non-owner runtime login.
- GitHub Actions runs backend verification and the frontend build for pull requests and pushes.
- On `main`, GitHub OIDC assumes a scoped deployment role, Buildx creates an ARM64 Docker image, and ECR stores the immutable image.
- The workflow updates the image parameter, triggers ASG instance refresh and checks target health through the ALB.

### 6. Verify the system

- Test tenant boundaries with two synthetic tenants and direct API requests.
- Verify modality filters and source metadata for CT, MRI and USG queries.
- Confirm unauthorized users cannot edit, sign or invoke AI without the matching capability.
- Confirm the workflow reports the new image tag, successful instance refresh and healthy target.
- Inspect safe audit events and model token counters without exposing prompt/report content.

## Tech stack

| Layer | Tools |
|---|---|
| User interface | React, TypeScript, Vite, CSS |
| Backend | Python 3.12, Boto3, Psycopg 3 and Psycopg connection pool |
| Relational data | Amazon RDS for PostgreSQL 16, SQL migrations, row-level security |
| Authentication | Amazon Cognito User Pools, server-side token and membership validation |
| RAG | Amazon Bedrock Knowledge Bases, S3 data sources, Titan Text Embeddings V2, Amazon S3 Vectors |
| Generation | Amazon Bedrock Runtime; current configured model Amazon Nova Pro |
| Object/key-value storage | Amazon S3 and Amazon DynamoDB |
| Cloud security | IAM roles, GitHub OIDC, KMS, Secrets Manager, VPC security groups |
| Runtime | Docker on Amazon Linux EC2 ARM64 behind an ALB and EC2 Auto Scaling Group |
| Delivery/IaC | GitHub Actions, Docker Buildx, ECR, SSM, Terraform |

## Screenshot plan

Take screenshots with synthetic accounts and data. Prefer the browser app and GitHub Actions; use AWS Console screenshots only when they prove an infrastructure capability that the other images do not.

| Priority | Screenshot | Evidence to capture | Sanitize |
|---|---|---|---|
| 1 | Architecture diagram | Dedicated VPC, ALB, ASG, private RDS, CI/CD, Cognito, S3, Bedrock RAG and trust boundaries | Remove account ID/resource IDs if present |
| 2 | Report editor | Synthetic report with study heading, findings, impression, save/review status | Fictional patient and findings only |
| 3 | RAG assistant | Synthetic prompt, draft and visible reference/source metadata | Remove tenant/patient identifiers and any real report text |
| 4 | Historical library | Modality upload and ingestion statuses | Fake file names; hide bucket names and keys |
| 5 | Admin/role workflow | Hospital membership, role and distinct AI/signing capabilities | Hide emails, employee codes and tenant IDs |
| 6 | GitHub Actions successful run | Verify, image build/push, instance refresh and healthy ALB target | Hide repository secrets and account-specific log values |
| 7 | ECR image list (optional) | Immutable ARM64 image tags and scan status | Hide registry/account URL if desired |
| 8 | AWS VPC/target health view (optional) | Private subnets and healthy ALB target | Hide IDs, IPs, account number and DNS if desired |

### Capture order

1. Capture and export the architecture diagram as SVG or PNG.
2. Seed a demo hospital with fictional users and reports; capture the editor and assistant.
3. Capture historical ingestion and role/access workflow.
4. Trigger a GitHub Actions run and capture it only after it succeeds end-to-end.
5. Inspect every image at full size for PHI, email addresses, user names, access codes, IDs, AWS account numbers, IP addresses, ARNs, secret names and browser autofill.

Do not post screenshots showing failed authentication, service errors, actual CloudWatch/SSM command output, or unfiltered report documents.
