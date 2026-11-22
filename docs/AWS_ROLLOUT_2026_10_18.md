# Live AWS development rollout — 18 October 2026

## Later approved EC2 increment (supersedes local-only status below)

The low-cost EC2 Docker test deployment is now running and verified. See
`EC2_TESTING.md` for access/recreation steps and `EC2_DEPLOYMENT_2026_10_18.md`
for completed checks. The existing runtime secret/extra IAM policy and current
DB ingress have been reconciled into Terraform. Migration 027 adds the approved
private customer-deletion queue/procedure; **no customer was deleted**. Earlier
statements below about absent EC2 or unchanged Terraform describe the earlier
local rollout, not the present configuration. Production gates still apply.

This is an AWS-backed **local development** rollout, not a production-readiness
certificate. Subscriptions remain on hold. Protected retrieval, prompts, model
requests, chunking and generation settings were not changed.

## Completed with explicit approval

- RDS security group `sg-0f801e21be7a6825c`: changed only the development
  PostgreSQL ingress from `49.37.115.84/32` to `49.37.115.74/32`. Application-SG
  access was preserved. This IP can change again.
- Applied migrations 001–026 together to database `radiology`. Preflight found
  no existing tenants, memberships or clinical reports. No existing data deleted.
- Created `radiologyos_runtime`: LOGIN, NOINHERIT, NOSUPERUSER, NOBYPASSRLS,
  NOCREATEDB, NOCREATEROLE; no SaaS table ownership. Application connections use
  this login, not the RDS master login.
- Created a separately billed Secrets Manager secret
  `radiologyos-dev/application-postgres`, encrypted with the project's existing
  KMS key. The RDS-managed master secret was not modified. No database password
  is printed, returned to the browser or stored in the source/configuration.
- Added inline IAM policy `radiologyos-approved-runtime-access` to application
  role `radiologyos-dev-application`: GetSecretValue on that runtime secret,
  AdminCreateUser/AdminGetUser only on pool `us-east-1_tuda9vR8q`. The existing
  Terraform-managed inline policy was preserved.
- Created Cognito account `sidharthachintu1997@gmail.com` and requested email
  delivery of its temporary-password invitation. AWS accepted the request;
  mailbox delivery is not independently confirmed. It was not auto-verified.
- A private, issuer/subject/email-bound invitation grants platform-admin access
  only after JWT and Cognito email verification. It does not create hospital
  membership or grant patient access, signing or AI permissions. Consumed
  invitations cannot restore subsequently revoked access.
- Enabled the SaaS runtime in local `.env`, using only the runtime secret ARN.
  The active Cognito pool/client are `us-east-1_tuda9vR8q` /
  `46bkl9mh70thafolq7godds27n`. Local HTTP uses development cookies.

## Test the real application

Use `http://127.0.0.1:8000/`, not the older disposable demo port. The older demo
was not stopped or altered. Sign in by email using the invitation, set your own
password, then enter the emailed verification code. Do not send credentials or
verification codes to an assistant. Password setup/login must still be completed
by the account owner before the live dashboard workflow can be verified.

For a later restart from the project directory (with project dependencies installed):

```powershell
python server.py
```

Use one Python environment with `pip install -r requirements.txt`. Do not prepend
another Python/Anaconda installation's site-packages through `PYTHONPATH`:
incompatible native cryptography extensions can prevent JWT verification even
after Cognito accepts the password. This occurred in the agent's first local
launcher; its dependency-loading order was corrected and JWT/cryptography imports
were checked before restarting. The Cognito password did not need resetting.
The user subsequently completed email verification and real sign-in: read-only
checks confirmed a verified Cognito/application identity, an enabled platform-admin
grant, one active application session, and zero clinical memberships. The login
failure is resolved. Email-delivery failures now have safe actionable errors;
generic API failures log only exception type/SQLSTATE/provider code, never raw
exception text or tracebacks. The updated regression suite passed all 217 tests.

For the Docker workflow:

```powershell
docker compose up -d --build
docker compose logs --tail=100 radiologyos
```

Use `http://localhost:8000/` with Docker. Avoid running both servers on port 8000.
AWS profile `radiology-dev` must remain available to the process. Docker mounts
your AWS configuration read-only. Do not copy AWS keys into the application.

After sign-in, create customer accounts in the super-admin dashboard. Customer
invitations are real emails. Explicitly grant clinical and AI capabilities only
to appropriate verified customer accounts. Platform administration alone never
provides clinical access. Historical uploads and model calls require a clinical
workspace/account and must still be tested end-to-end under that identity.

## Verification and limits

- Final regression: all 216 tests passed, including real disposable local
  PostgreSQL isolation tests and protected RAG contract checks. Frontend build
  and `git diff --check` passed. The live server serves the built React assets.
- Live runtime login/schema check passed: all 26 migration checksums match;
  unsafe-owner check passed; zero unscoped reports visible.
- Real local server returned SaaS mode enabled. Anonymous session, clinical data
  and platform overview requests returned 401. `/api/health` is liveness only,
  not evidence that all AWS dependencies or clinical workflows are healthy.
- Verified RDS, Cognito, KB/data sources, encrypted/private S3 and DynamoDB
  availability. No report text read or new paid model call made for these checks.
- The new function-owner regression simulates non-superuser RDS ownership with
  FORCE RLS, rather than relying solely on a local PostgreSQL superuser fixture.

## Explicitly outstanding production gates

RDS remains publicly accessible, backup retention is zero, and deletion
protection is off. Terraform was not changed. No public domain/HTTPS deployment
or EC2 instance exists. Current database transport uses TLS `require`; configure
and validate a trusted RDS CA with `SAAS_DATABASE_SSLROOTCERT` for `verify-full`.
MFA remains off in the live pool. Restart-safe AI generation, the connected
publication worker, password-reset revocation reconciliation, emergency MFA
recovery, full XSS/print/upload security, load testing and backup/restore drills
remain separate unfinished gates. Do not process real patient data yet.

## Infrastructure drift / teardown

Terraform source and state were not changed. The approved SG edit, separate
runtime secret, additional inline IAM policy and Cognito account are AWS-only
changes. **Do not assume Terraform destroy removes them.** Review/reconcile
these resources before applying/destroying infrastructure; the additional IAM
policy can also block Terraform role deletion unless removed/reconciled. The
runtime database login disappears with the database; the separate secret can
continue to be billed after RDS teardown. No teardown is authorized by this work.

`scripts/configure_aws_runtime.py` records the explicit operator workflow. It
requires an AWS account confirmation and never runs on app startup. `database`
refuses an existing runtime role/secret instead of silently rotating credentials;
`verify` is read-only. Inspect completed stages before retrying partial failures.
