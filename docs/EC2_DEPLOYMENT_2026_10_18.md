# Verified testing deployment — 18 October 2026

Approved scope: `EC2_AND_DELETION_PLAN.md`. Operating guide: `EC2_TESTING.md`.
No existing customer data was deleted and no password was reset. No Git commit
or push was performed. Staff deletion remains deactivation-only. Protected RAG
functions/files and model settings passed the invariant tests unchanged.

## Deployment

- EC2 `i-0994d3821cc72a585`, t4g.micro ARM Linux, us-east-1c (same AZ as RDS).
- Encrypted 8-GiB gp3, 1-GiB swap, standard CPU credits, IMDSv2 required.
- Application SG: zero inbound rules. Instance IPv4 is for outbound SSM/ECR.
- ECR `radiologyos-dev-application`, immutable source tag
  `04e74bed69a1c7d7bd525d5e159abb20` matched the allowlisted source bundle at deployment.
- Image built on EC2; no local Docker engine access was required.
- systemd service active, Docker container healthy, listens only 127.0.0.1:8000.
- Existing admin email remains platform-only. No new invitation was needed.
- Bootstrap reused the existing dedicated runtime login/password. All 27
  migrations/checksums match, including newly approved migration 027.
- Temporary master-secret read policy removed after explicit bootstrap.
  Normal host policy contains no master-secret reference.

## Verified gates

- 240 Python tests passed, including local PostgreSQL RLS, subject-bound reauth,
  wrong confirmation rejection, external-cleanup failure handling, shared-user
  retention, and signed-history purge with a non-superuser RDS-style owner.
- React/TypeScript production build, Python compile checks, diff checks passed.
- Terraform validation and four offline/mock tests passed.
- Live approved apply: 8 added, 3 updated, zero destroyed. Runtime secret and
  approved extra policy imported rather than duplicated. Current app/PC DB ingress
  state reconciled without changing the live ingress targets.
- Post-apply live plan: no changes (exit 0).
- Real runtime login: `radiologyos_runtime`; unscoped reports visible: 0.
- HTTP `/` and `/api/health`: 200. Anonymous `/api/session`, `/api/data` and
  `/api/platform/dashboard`: 401. Health is liveness, not full clinical readiness.
- Read-only same-region service timings (milliseconds, three samples):
  dashboard `[313.1, 10.6, 5.4]`, customer list `[6.0, 5.7, 5.1]`. The first call
  includes cold setup. These are server-side DB/service timings, **not** browser
  login or model generation timings. No paid model invocation was made.

## User handover and remaining gates

The user elected to install the Windows SSM plugin after deployment. Installation
instructions are in `EC2_TESTING.md`; the helper opens local **18000**, not the
old local container's 8000. Keep the tunnel command running and sign in privately
with the existing new Cognito password. The updated schema requires current code;
an old local Docker image may need rebuilding if used instead of EC2.

Browser verification of sign-in, reauthentication-preserved access forms, customer
creation/invitation, staff deactivation and clinical workflows remains. No real
customer-deletion button was exercised. Do not remove the 2-hour upload-expiry
quarantine for a quick test on a real customer. No data backup was requested.

This is not production clinical hosting. Public TLS/domain, private database
posture, backups/restore, MFA policy, durable generation/publication, thorough
upload/print/XSS/load tests and clinical review remain separate gates. RDS public
access, zero backup retention and disabled deletion protection were not changed
under this additive hosting approval. Subscriptions remain off.
