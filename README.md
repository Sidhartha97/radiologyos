# RadiologyOS

**Multi-tenant radiology reporting with tenant-filtered RAG, clinician-reviewed AI drafts, and AWS-native CI/CD.**

RadiologyOS combines a structured reporting workspace, hospital/user administration, report assignment and review workflows, a historical-report library, and an AI assistant grounded in permitted prior reports. The system is a development portfolio project; it is not clinically validated and must not be used with real patient data.

## Architecture

![RadiologyOS current AWS architecture](docs/aws-architecture.svg)

The application is hosted in a dedicated VPC. A public ALB routes to a private EC2 Auto Scaling Group (configured maximum four); the app tier accesses private PostgreSQL through security-group rules. A NAT Gateway provides private-tier outbound access. Cognito, S3, DynamoDB, Bedrock Knowledge Bases, S3 Vectors, KMS and Secrets Manager provide identity, document, retrieval, encryption and configuration services.

On pushes to `main`, GitHub Actions verifies the code, builds an ARM64 image, publishes a unique immutable tag to ECR using OIDC, and starts an ASG refresh. It waits for a successful refresh and a healthy ALB target.

Detailed materials:

- [How the web app, RAG pipeline and role-based access work](docs/WEB_APP_AND_RAG.md)
- [AWS architecture, trust boundaries and request flows](docs/ARCHITECTURE.md)
- [Implementation sequence, resume bullets, LinkedIn copy and screenshot plan](docs/IMPLEMENTATION_AND_PORTFOLIO.md)
- [GitHub Actions setup and deployment](docs/GITHUB_ACTIONS_DEPLOYMENT.md)
- [SaaS implementation plan](SAAS_IMPLEMENTATION_PLAN.md)

## System capabilities

- Hospital/individual workspaces with server-resolved tenant identity and explicit membership/capability checks.
- Radiology report editing, save/review/finalize, assignment, conversation history and edit locking.
- Historical report ingestion from S3 with modality/study metadata and status visibility.
- Tenant- and modality-filtered retrieval through Bedrock Knowledge Bases and S3 Vectors.
- Bedrock report drafting grounded in retrieved examples; clinician review is required.
- Platform administration, role management, token-usage visibility and manual payment records. Subscription billing is not enabled.

## Stack

| Layer | Stack |
|---|---|
| Web client | React, TypeScript, Vite, CSS |
| API and domain services | Python 3.12, Boto3, Psycopg 3, Psycopg Pool |
| Identity | Amazon Cognito User Pools |
| Tenant data | Amazon RDS for PostgreSQL 16, SQL migrations, row-level security |
| Historical RAG | S3, Bedrock Knowledge Bases, Titan Text Embeddings V2, S3 Vectors |
| Generation | Amazon Bedrock Runtime; current configuration uses Nova Pro |
| Job metadata | Amazon DynamoDB |
| Cloud/security | Dedicated VPC, ALB, EC2 ASG, IAM, KMS, Secrets Manager, SSM |
| Delivery | Docker, ECR, GitHub Actions, GitHub OIDC, Terraform |

## Run locally

Use synthetic records. Configure a local `.env` and AWS CLI profile only if using AWS-backed functionality; never commit `.env`, Terraform state/plans, credentials or reports.

```powershell
docker compose up --build
```

Open `http://localhost:8000/`. Review [docs/SAAS_OPERATIONS.md](docs/SAAS_OPERATIONS.md) for local SaaS/database workflows.

## Security and clinical scope

- The backend verifies Cognito identity and resolves tenant membership; a browser-supplied tenant is not authoritative.
- Tenant and modality filters are mandatory in retrieval, and retrieved metadata is checked before generation.
- PostgreSQL RLS and least-privilege non-owner runtime credentials provide database-layer controls.
- GitHub deployment uses OIDC and short-lived role credentials; EC2 uses instance profiles. No long-lived AWS keys belong in GitHub or the image.
- Reports and databases use KMS-backed encryption according to infrastructure configuration.
- AI output is an unfinalized draft and requires qualified clinician review.
- The current development listener is HTTP and the NAT design has one gateway. Do not represent the deployment as production-ready, highly available or compliant without further work and verification.

## Portfolio screenshots

Use synthetic data. Prioritize: (1) architecture diagram, (2) report editor with fake patient data, (3) assistant draft with retrieval context, (4) modality-aware historical library, (5) role/access workflow, and (6) successful GitHub Actions deployment showing ECR push, ASG refresh and healthy ALB target. See [the screenshot checklist](docs/IMPLEMENTATION_AND_PORTFOLIO.md#screenshot-plan) for sanitization guidance.
