# Approved implementation plan — 18 October 2026

## Scope and approvals

The user requested in-place permission-save recovery, confirmed permanent
customer-workspace deletion, cheap EC2 Docker hosting, and easy infrastructure
recreation. Protected RAG generation/retrieval remains unchanged. Subscriptions
remain off. No existing customer is authorized for deletion during development.

Approved: lowest-cost practical testing, no high availability, no domain, secure
SSM tunnel. t4g.micro Linux in us-east-1 is $0.0084/hour (queried AWS Pricing API).
At 730 hours, compute $6.13 + IPv4 $3.65 + 8 GiB gp3 about $0.64, plus small ECR /
configuration-secret costs: estimate $10.50–12/month, excluding existing RDS,
Bedrock/storage, taxes and extra traffic. No inbound web/SSH ports, ALB or NAT.
User approved preparing/applying an additive Terraform plan after checking it for
destructive changes. Existing Terraform-managed resource replacements are not
approved by that scope.

Deletion decision: **deactivate staff only**. Permanent deletion applies to an
entire hospital/individual customer workspace. Delete only its PostgreSQL,
DynamoDB, S3 versions and indexed documents. Keep shared AWS services and users
with another workspace or platform role. Present a preview and explicit Yes/No
confirmation, require fresh same-subject authentication, block tenant activity
before cleanup, persist progress/errors for safe retry, and never claim completion
while any external cleanup fails. Delete signed/history rows only through the
reviewed privileged purge operation, never general runtime delete rights.

## Work sequence

1. Subject-bound Cognito re-authentication dialog; preserve unsaved forms and
   retry rejected sensitive actions once after password/MFA confirmation.
2. Workspace-deletion schema/job and narrow privileged operations; exact tenant
   path checks, all S3 versions, DynamoDB partition, Bedrock deletion verification,
   exclusive identity cleanup, SQL purge. Unit/integration tests, no live deletes.
3. Optional Terraform EC2/ECR/SSM/configuration resources; import approved existing
   runtime secret/policy instead of duplicating unmanaged resources. Review plan.
4. Explicit deployment/bootstrap script reading current Terraform outputs. Upload
   an allowlisted source bundle (never .env, reports or AWS keys), build/pull Docker
   image on EC2, bootstrap reviewed schema/runtime credentials when explicitly
   approved, render configuration from current infrastructure outputs, run with
   instance-role credentials and bind the container only to host loopback.
5. SSM tunnel helper; repeatable fresh-infrastructure procedure and teardown notes.
   New Cognito pools require a new operator-approved admin invitation; old
   passwords cannot survive infrastructure destruction. No automatic default
   credentials or migration DDL at application startup.
6. Full test/build/Terraform validation, reviewed live additive apply/deployment,
   dependency/health checks; user completes private password/MFA/browser steps.

## Gates

Account purge requires reviewed live schema + IAM changes before enabling it.
Existing accounts remain untouched. RDS private/public/backup changes outside the
additive testing-host plan must be surfaced before applying. This remains a
single-node testing deployment, not a production clinical deployment.
