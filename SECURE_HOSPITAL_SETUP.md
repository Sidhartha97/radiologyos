# Reliable retrieval and authenticated hospital isolation

## What changed

- Every data API now requires a verified Cognito **access token** or an HttpOnly
  session cookie. The old demo-login/sessionStorage bypass is removed.
- JWT signature, expiry, issuer, token type and app client are checked. A backend
  PostgreSQL membership maps the verified subject to one hospital and role.
  Browser tenant fields, Cognito custom tenant fields and group names cannot
  assign hospital access or grant admin rights.
- No implicit migration: untagged legacy application data is not shared with new
  hospitals. Authenticated requests never fall back to JSON on database failures.
- PostgreSQL application state uses hospital_state keyed by tenant_id, with row
  locking for mutations. Report versions use hospital_report_versions keyed by
  tenant_id and version_id. Patient chat remains tenant/patient keyed.
- Browser-upload DynamoDB partitions and original/normalized S3 keys use the
  resolved hospital. Workers retain that hospital identity.
- Retrieval always filters tenant_id AND modality. Study/body-region filters can
  be relaxed, never those two mandatory filters. Returned metadata is checked
  again, and source S3 paths must be inside the authenticated hospital prefix.
- Prompt/dropdown conflicts return HTTP 409 before starting work. The UI asks
  permission to use the prompt modality. Unknown/ambiguous modalities return 400.
- /api/rag/debug requires a backend admin membership and a valid modality.
  It returns IDs, matching metadata and scores, never report text or signed URLs.
  Legacy bucket-wide repair is disabled; use the hospital historical library.
- Structured audit logs contain request IDs, hospital, modality, filter keys and
  retrieved IDs. Optional study/body-region filter values are redacted.

## Start locally

1. Install dependencies or rebuild: docker compose up -d --build radiologyos.
2. The current local .env sets APP_COOKIE_SECURE=false for localhost HTTP only.
   For hosted HTTPS set APP_COOKIE_SECURE=true.
3. Sign in using your Cognito account. New temporary-password accounts must set
   a new password on first sign-in. Existing SMS/software MFA challenges are
   supported; initial MFA enrollment must be completed through the identity provider.
4. Register a patient, then test chat. Patient/report creation is scoped to your
   signed-in hospital, even when a request contains a forged tenantId.
5. Changing hospital/user requires signing out, then signing in to that account.

The approved Cognito client setting ALLOW_USER_PASSWORD_AUTH has also been
added to Terraform. A targeted live client update preserved the other settings.
Review a fresh Terraform plan before applying; do not reuse an old saved plan.

## Provision another hospital user

Create a user in the configured Cognito pool, then copy their sub attribute.
Only a trusted operator with database access should run:

    python manage_hospitals.py --subject "COGNITO_SUB" --tenant hospital-002 --role admin

For radiologists use --role radiologist. To disable access:

    python manage_hospitals.py --subject "COGNITO_SUB" --tenant hospital-002 --disable

Membership is checked on every API request, so disabling membership does not
wait for token expiration. Passwords do not belong in .env or source control.
This version maps one Cognito subject to one hospital; multi-hospital membership
selection/billing is not implemented.

## Existing data

Existing S3 reports already indexed with tenant_id=hospital-001 and stored under
old-reports/hospital-001/ or final-reports/hospital-001/ remain usable by that hospital.
Files outside a tenant path are rejected even if their metadata claims that tenant.
Re-upload those through the historical library rather than removing the path check.

Existing global PostgreSQL application data and local JSON are preserved, not
automatically shown. After explicitly confirming ALL legacy records belong to
hospital-001, an operator can import them BEFORE creating that hospital's first
new application record:

    python manage_hospitals.py --tenant hospital-001 --import-legacy

Import refuses to overwrite an existing hospital_state row or silently reassign
records tagged to another hospital. Back up the database before importing.

## Verify

Automated checks:

    python -m unittest discover -s tests
    cd frontend
    npm run build

Read-only AWS retrieval checks (no LLM calls, report text or signed URLs printed):

    python verify_live_retrieval.py --tenant hospital-001 --profile radiology-dev

PASS means at least one full reference was loaded with matching tenant/modality
and a valid tenant-specific S3 path. NO_MATCH is safe but does not establish that
the modality's ingestion is complete. Inspect ingestion/indexing before uploading
more data or changing the model.

Administrator diagnostics after signing in:

    /api/rag/debug?modality=USG&query=USG%20abdomen
    /api/rag/debug?modality=CT&query=CT%20brain
    /api/rag/debug?modality=MRI&query=MRI%20spine

For PowerShell/CLI use Authorization: Bearer <Cognito access token>, not an ID
token. Never paste tokens into screenshots or logs. Non-admin users receive 403.
/api/health is now public liveness only, not a database/RAG readiness claim.

Two-hospital acceptance check: create a patient/report under A and record its IDs.
Sign in as B and confirm A's records are absent. Directly request A's chat IDs and
PUT/DELETE A's patient/report IDs: these must fail. Try linking B's report/upload to
A's patient; that must fail. B cannot read A's DynamoDB batch IDs, and presigned
upload keys must always start with historical-originals/B/. Change tenantId in
request JSON: it must not change any of these outcomes.

## Deployment limits

JWT validation tolerates at most 120 seconds of clock skew (AUTH_CLOCK_SKEW_SECONDS,
default 120, allowed 0–120). Signatures, issuer, client, token type and time claims
remain checked. Keep Windows/EC2/Docker clocks synchronized; larger clock drift
is rejected with a specific clock error. If Cognito accepts a new password but
session verification fails, the UI explains that the password already changed
and returns to normal sign-in rather than reusing a consumed challenge session.

Use HTTPS, secure cookies, short sessions and MFA enrollment before real hospital
use. Sessions currently expire after at most an hour and require sign-in again;
automatic refresh, federated login and login rate limiting are separate work.
Apply network/WAF controls and configure safe audit-log retention/export.
Audit logs currently go to application stdout, not an immutable audit store.

Hospital snapshots are isolated but serialize writes within a hospital; migrate
to normalized tenant-keyed entities/RLS for high concurrency. Background ingestion
workers resume when that hospital opens its library after server restart.
The server IAM role can access all hospital prefixes; application checks enforce
end-user isolation, not per-tenant IAM credentials. Do not expose AWS credentials.
Live two-Cognito-user end-to-end acceptance still requires a second provisioned
hospital account; automated tests cover those boundary cases with simulated identities.
These changes alone are not a production-readiness or compliance certification.

References: [AWS Cognito JWT verification](https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-using-tokens-verifying-a-jwt.html)
and [Bedrock retrieval filters](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_RetrievalFilter.html).

## Verification results, 11 October 2026

- 78 automated tests passed, including actual RSA signature/claim checks and
  HTTP-level two-hospital boundary tests with simulated authenticated identities.
- Frontend production build and Terraform validation passed.
- Read-only AWS test for hospital-001: USG returned 5 matching full references;
  CT returned 1; MRI spine returned NO_MATCH. No cross-modality substitution.
- Live PostgreSQL tenant-state round trips passed for two synthetic hospitals;
  only those synthetic rows were deleted afterward.
- A demo hospital-001 admin membership was provisioned. The Cognito user has a
  temporary password that must be changed on first sign-in. No password was
  added to source files or .env.
- Existing legacy application records were not imported without ownership confirmation.
- Docker restart and interactive first-login verification remain pending because
  this Codex session cannot access the Docker Desktop engine.
