# Resume implementation

1 November 2026: USER-AUTHORIZED PROJECT TEARDOWN COMPLETED. Terraform destroyed
39 resources; no managed resources remain in state. AWS project data/users were
deleted, no backup requested. Local source/configuration/lock/state are retained.
EC2 is no longer running. Current KMS key is PendingDeletion until 1 December.
See TEARDOWN_2026_11_01.txt before any future work. Old .env IDs are stale.
Do not recreate chargeable resources without a new user request to rebuild.
The older deployment notes below are historical, NOT current live AWS status.

Latest 18 October testing-host increment supersedes older AWS restrictions below:
the user approved an additive t4g.micro/ECR/SSM deployment, scoped cleanup IAM,
and migration 027 after tests. Staff remain deactivation-only; whole customer
workspace deletion requires explicit UI confirmation. Re-authentication now
preserves unsaved administrative forms. See `EC2_AND_DELETION_PLAN.md` and
`EC2_TESTING.md`. No existing customer is authorized for development deletion.
Terraform apply: 8 added, 3 changed, zero destroyed. Existing runtime secret,
extra IAM policy and current database ingress rules were reconciled into state.
Live Docker deployment now verified on `i-0994d3821cc72a585`, ARM image tag
`04e74bed69a1c7d7bd525d5e159abb20`. systemd active, container healthy, app listens
only on 127.0.0.1:8000. All 27 migration checksums match, runtime is non-owner,
unscoped reports=0, protected anonymous requests=401. The temporary bootstrap
master-secret permission was removed. A refreshed Terraform plan has no changes.
Validation: 240 Python tests, frontend build, 4 offline Terraform tests and
diff/compile checks pass. Protected RAG contract unchanged. No customer deleted,
no password reset, no Git commit/push. Windows SSM plugin installation and private
browser workflow verification are the next user steps; app tunnel is local 18000.
The user requested instructions after deployment, not an automatic system install.

Latest performance increment: `PERFORMANCE_2026_10_18.md`. SaaS pooling, lease-only
role guard deduplication, single clinical request authentication, non-locking
session reads and a combined platform dashboard are implemented. All 226 tests,
frontend build and multi-role HTTP smoke pass; read-only RDS warm benchmarks
improved. No new migration/AWS/Terraform/RAG changes. Docker must be rebuilt in
the user's terminal (agent engine access is denied); do not claim the running
container has the updated code until rebuilt/verified.

Latest approved live-development increment: see `AWS_ROLLOUT_2026_10_18.md`.
Migrations 001–026 are now applied to real RDS, a dedicated runtime login/secret
and scoped extra IAM policy exist, and the real platform-admin email invitation
was requested. Local `.env` enables SaaS mode; actual app runs at port 8000.
The older notes below predate this approval. Terraform remains unchanged;
subscriptions stay on hold. Await the user's password/email verification and
test real account/workspace workflows before claiming the rollout complete.
Update: real admin password/email verification and login have now succeeded.
The first local launcher mixed incompatible cryptography dependencies; corrected
loading resolved it without resetting the password. Read-only live checks show
one active session, platform-admin enabled, zero clinical memberships. All 217
regression tests pass. Customer/clinical AWS workflows remain to be tested.

Checkpoint: 16 October 2026. Work is saved in local files and has not been committed.

## Current integration checkpoint (supersedes the older inventory below)

### Latest: local-first dashboard, demo fixes and security hardening

User again confirmed LOCAL TESTING FIRST and KEEP TERRAFORM UNCHANGED. Do not
prepare/apply production IAM/network/backup changes or start RDS. No domain was
selected. Subscriptions stay on hold; manual payments remain bookkeeping only.

Implemented: modular professional platform dashboard with sidebar, real summary
metrics, account search/type/status filters, creation/invitation feedback, separate
usage/payments/audit/security pages, and clinical worklist status/modality filters.
Platform views remain business-only; no clinical access or signing is implied.

Fixed demo errors: mock Cognito rejected non-@example.test usernames. Any valid
email now creates a LOCAL identity; no email is sent. Invitations return visible
failure feedback when status=FAILED instead of treating HTTP 200 as delivery.
The demo's AST HTTP harness lacked cloud-library dependencies; an explicitly
injected tests/demo_library.py adapter now validates and stages in local memory.
It never calls AWS or claims indexing; modes/statuses/UI say local-demo/LOCAL_STAGED.
Production API construction never injects this adapter and rejects it in production
mode. Real S3/DynamoDB/KB behavior and protected RAG files remain unchanged.

Added Cognito required TOTP MFA_SETUP flow with private single-use provider
sessions; only the setup key/code UI is returned to the authenticating user. It
is mock-tested, not enabled in the demo or validated against live Cognito. See
AWS RespondToAuthChallenge/VerifySoftwareToken API docs; no IAM/pool edits made.

Migration 024 adds authenticated_at and a monotonic per-user session revocation
cutoff. Self-service logout-all revokes all application sessions and rejects old
authentication replay, including delayed login continuations. Authentication
time/cutoff cannot be rewritten backwards. This is NOT Cognito global sign-out,
and automatic password-reset reconciliation is still outstanding.

HTTP policy now rejects duplicate/negative lengths, chunked JSON, wrong media
types, cross-site mutations and excessive bodies. Library validation permits the
advertised 10 MB file/base64 envelope within a 20 MB request bound. Production-only
host/origin checks and startup configuration/schema/role preflight were added.
Read-only /api/platform/readiness is platform-only and exposes no credentials.
It checks configuration, NOT overall production readiness. Docker uses npm ci,
has a liveness healthcheck, and request sockets have 30-second timeouts.

Final validation: 210 tests passed (37 seconds); frontend production build and
shared multi-role HTTP smoke test passed; git diff --check passed. Browser verified
new dashboard/navigation/customer list, a Gmail-domain synthetic invitation ->
INVITED, historical validation -> LOCAL_STAGED, and security/readiness controls.
Temporary QA server shut down and cleaned its own disposable database/role.
The user's older demo
at 127.0.0.1:63054 was not stopped or modified; restart it to load the new backend.
Our temporary bounded QA demo runs on a separate loopback port and cleans its own
disposable database. No .env/Terraform/AWS edits or commit/push.

Still pending: restart-safe generation, connected S3 publication worker, automated
password-reset revocation reconciliation, MFA emergency recovery, full print/XSS/
upload-security/load/restore gates, and approved real Cognito/RDS/AWS rollout.
Do not describe this increment as the finished production SaaS application.

### Latest: report workflow, actual tokens and manual payment records

User approved actual token tracking, without charging, and INR manual payment
recording/verification only. Subscriptions remain on hold. Terraform, `.env` and
live AWS were not changed; RDS stayed stopped. The local test DB was initially
unavailable; after the user started it, all 196 tests and the frontend build passed.
Shared multi-role HTTP smoke also passed, including platform-only usage/payment
access and recording/verifying a synthetic payment without wallet grants.

Migrations 021–023 add a narrow same-hospital reviewer directory, actor-scoped
model token events and immutable manual payment decisions. Explicit operator
migration/runtime grants are still required for a live rollout; never enable
startup DDL or run this against RDS without reviewed approval.

Hospital editors automatically acquire a 120-second lease on open; 30-second
heartbeats and version/fencing checks remain enforced. Same-tab retry is allowed;
another tab is denied. Transcriptionists can atomically save, assign an active
hospital radiologist and submit for review, but cannot sign. New authorized saved
report GET supports printing the persisted revision, independently of unsaved
editor content. Editor Back, navigation warnings and workflow status labels were
added; cancellation remains historical/soft rather than deleting signed records.

Synthetic browser checks passed: automatic lock, competing-tab denial, edited
transcription saved/handed to DR001, transcriptionist read-only after handoff,
radiologist review/finalization, signed read-only view, Back navigation and the
platform token/payment screens. The browser print dialog/PDF output was not
manually tested. Handoff freezes editing and report navigation while saving.
Final regression run: all 196 tests passed again; the production build passed.

`saas/token_tracking.py` wraps only `bedrock-runtime.converse` via `aws_client` in
SaaS mode. It forwards the original request and response unchanged and records
actual provider counters against the frozen authenticated tenant/user. Input
includes system instructions, references and conversation context, not just the
typed prompt. Missing usage/unknown outcomes stay unresolved, never invented as
zero. It stores no clinical text, credentials or prices, and never retries a
model call on telemetry failure. Dashboard totals cover newly tracked Bedrock
Converse report calls only: no historical backfill, OpenAI branch, embeddings,
ingestion or all-AWS usage claim. Protected RAG contracts still pass.

Manual records do not move money, grant credits, activate AI, create a subscription
or issue tax invoices. Verified received amount must match the recorded amount;
settled decisions are immutable and mutations require recent authentication.
The UI lists the latest 100 payments (not a complete paginated payment ledger).

Still NOT production ready: live Cognito/RDS rollout, password-reset session
revocation, required-MFA first login/emergency recovery, durable generation and
publication workers, deployment/security/load/retention/recovery gates. Super
admin customer provisioning uses Cognito invitations; it does not reveal or store
permanent passwords. Real provisioning and generation remain separately disabled
by default. No new AWS users, resources, Terraform apply, commit or push occurred.

For local browser tests use `docs/SHARED_DEMO.md`. The demo's optional
`--serve-seconds 420` provides bounded loopback-only testing with automatic cleanup
of its own disposable DB/role. Never deploy mock Cognito or demo credentials.

### Latest: transcriptionist AI, never sign-off

User clarified transcriptionists need AI access but NOT signing. No request to
delegate platform-admin status was approved; no such delegation was added.
Platform access controls now accept canUseAi for radiologist OR transcriptionist;
canSign still requires radiologist. Frontend AI checkbox/launcher follow that rule.

Migration 020 adds report_conversations with report FK and assignment/grant-aware
RLS. saas/report_chat.py adapts the existing PatientChat to a report-specific store,
keeping actual patient identity for generation but never reading patient-wide or
other-report history. /api/report-chat/{id} GET/POST/DELETE validates current
membership, AI grant, report/patient ownership and assignment. Worker rechecks
before generation and persistence. Submitted transcriptionist work is read-only.
Legacy /api/rag/chat and /api/patient-chat remain denied to transcriptionists.
PatientChat internals and protected RAG functions/files were not modified.

Report generation is supplied to the new API through an injected callback;
the raw existing rag_generate is invoked only behind the scoped adapter's checks.
The global SAAS_AI_ENABLED gate remains false by default; no AWS calls or enabling
occurred. Shared demo grants radiologist/transcriptionist AI permission to show
the UI, but real generation remains disabled and no fake reports are generated.

Validation: 188 tests passed, frontend production build passed, shared-demo smoke
passed. Tests cover grant-vs-signing, report isolation and RLS, revoked AI before
worker invocation, forged patient IDs, disabled global gate and patient-wide denial.
No live rollout, Terraform/.env edits or commit. Latest tiny change only clarifies
the disabled-AI message (no requirement to implement subscriptions first).

### Latest: super-admin-only account creation and shared demo

User now requires ONLY super admin to create hospital-admin and individual
accounts; public self-registration is no longer a next step. Hospital staff
invitations remain delegated under the configured creation flag. Subscriptions
remain held. User explicitly said KEEP TERRAFORM UNCHANGED when scoped Cognito
user-management IAM was proposed; do not add it or apply AWS changes.

Added migrations 016–019: platform-only account provisioning/invitations, verified
owner acceptance, clinical assignee directory, immutable idempotency snapshot and
business-only platform audit read policy. Added provisioning.py, assignments.py,
recovery.py, mfa.py, account creation/assignment/recovery/security/overview UI.
Owner grants are fixed by workspace type and never imply signing/AI/platform access.
API acceptance requires verified JWT and Cognito GetUser verified email plus exact
issuer/subject binding established during invitation delivery. Real delivery remains
SAAS_USER_PROVISIONING_ENABLED=false by default. No application passwords stored.

Shared demo: tests/demo_saas.py creates all five synthetic roles in one disposable
DB with a mock provider, no .env or AWS. See SHARED_DEMO.md. Browser verified:
super-admin login -> create active hospital -> local invitation INVITED -> new owner
login -> hospital-admin dashboard with no clinical/signing grant. Demo server was
closed after verification; its exact DB radiology_saas_it_d16e09575e8d and matching
role were verified synthetic and removed. Another pre-existing fixture DB
radiology_saas_it_ae17595f0847 was NOT removed (ownership not established this turn).

Real Cognito MFA/recovery and RDS rollout are unverified. MFA only enables TOTP after
successful code verification; enabled factors cannot be replaced through setup.
Forgot-password confirmation does not yet reconcile all existing application sessions;
review global sign-out/revocation before production. Required-MFA first-login setup
challenges and production emergency recovery are not complete. Publication worker,
durable generation, operational readiness/load/recovery and live access tests remain.
Do not claim the entire non-subscription plan is finished or production ready.

Final validation: all 184 tests passed (30s), frontend production build passed
(1916 modules), shared-demo multi-account smoke test passed, git diff --check
passed. An earlier run hit Windows socket abort 10053; the full rerun passed.
Terraform diff is empty. Protected RAG contracts remain unchanged. No commit/push.

### Access-first update — subscriptions held

The user explicitly deferred subscriptions until admin/user/access workflows
have been tested in the browser. Do not resume subscription implementation
without a new request. The unfinished 015_manual_billing.sql and its grants were
removed before any migration/test application. Migration 015 is now
015_access_controls.sql: membership revisions, platform update policy and a
trigger preventing hospital administrators from using shared column grants to
escalate roles/signing/AI. Existing applied migrations 001–014 are unchanged.

New: saas/access.py, platform member GET/PUT with recent auth/revision/reason,
MemberAccess.tsx; hospital reactivation and invitation revocation; roster grant
visibility; role-aware navigation/patient-registration controls. No subscription
check was introduced into these development access flows. Signing stays explicit.
Preview roles now include platform, hospital_admin, radiologist, transcriptionist
and individual; previews are synthetic, isolated and NOT deployment servers.
See ACCESS_TESTING.md. Live Cognito/RDS rollout is still unperformed.

Validation for this access checkpoint: full local suite 174 tests passed (26.4s),
React/TypeScript production build passed, git diff --check passed. Initial test
socket failures were sandbox permissions; rerun after the explicit network grant
passed. The new preview roles have not yet been manually browser-tested this turn.

Next priority: complete multi-user access/browser tests, production authentication
recovery/session handling, durable jobs/publication and then approved live rollout.
Billing/paid generation work is deferred rather than the next implementation step.

The optional SaaS HTTP and React paths are now wired, default OFF (`SAAS_ENABLED=false`).
Migrations 006–014 add tenant RLS on legacy clinical tables, linked amendments,
platform controls, reserved staff invitations/private employee login mapping,
seat serialization, expiring credit lots, reservations and immutable wallet ledger.
The runtime uses a dedicated non-owner `SAAS_DATABASE_URL`; it never migrates on startup.
After migration 006, disabling SaaS and reverting to the old clinical connection
path is NOT a supported rollback. Deploy a compatible context-aware release.

Implemented locally: email/employee login, opaque first-password/MFA/email-verification
challenges, workspace selection, platform-only business configuration, staff roster,
invitation reservation/delivery fencing, report leases/version checks, review/signing,
immutable signed history and linked amendments. Invitation delivery is separately
disabled by default (`SAAS_USER_PROVISIONING_ENABLED=false`). No live user was created.

User-approved currency: INR, stored as integer paise (`saas/money.py`). Prices
have not been chosen: keep commercial plans unpublished; no assumed prices.

User-approved billing policy: credits expire at period end; charge actual model
usage even for unusable drafts. Unknown invocation outcomes retain their holds
for reconciliation; never automatically retry a possibly charged invocation.
`saas/usage.py` adds exact integer rate calculations with synthetic tests, NOT
production rates or an activated provider gateway. Migration 014 and usage.record
commit immutable counter/rate receipts with wallet settlement; repeat settlements
are idempotent and receipts contain no clinical text. Unsupported cache counters,
missing usage and model mismatches fail closed. `SAAS_AI_ENABLED=false` stays off.

Local validation after usage receipts, INR boundaries and unknown-usage/receipt
isolation: full suite passed 168 tests (25 seconds). `git diff --check` passed.
Earlier: 69 targeted SaaS tests passed;
an earlier full suite passed 135 tests; React production build passed. Browser
checks used disposable synthetic platform/radiologist accounts, and verified
workspace routing, edit/save/finalize and amendment affordances. Preview servers
are stopped and their exact disposable databases/roles removed. Future preview
scripts accept `stop` on stdin for graceful cleanup. No RDS/AWS test was performed.

Deferred (not next): immutable plan versions/manual payment approval and subscription entitlements;
durable generation plus metering wrapper with unchanged model requests; publication
worker integration; public registration/password recovery/MFA enrollment; complete
cross-tenant/browser/security gates. Commercial prices remain unchosen: no invented
paid defaults. Provider counter handling and an input reservation bound for Nova Pro
need validation before activating metered generation. RDS function-owner/RLS behavior
needs explicit deployment testing (local superuser tests are not sufficient).

No `.env`, Terraform, AWS or RAG generation/prompt/renderer changes this checkpoint.
No commit/push. Last five-hour usage check: 39% used; continue until user limit.

## Earlier foundation inventory (historical, not current rollout status)

## User decisions

- Continue implementing the approved SaaS plan without restructuring the application.
- Preserve existing RAG retrieval, system prompts, model requests, chunking and rendering.
- `sidharthachintu1997@gmail.com` is platform admin only. No automatic clinical membership or signing grant. No live grant has been performed.
- Hospital customers and independent radiologists register using email.
- Hospital staff are created with employee codes unique within a hospital. Login needs hospital ID + employee code + password. The existing Cognito pool is email-based; a backend-only directory maps employee codes to verified Cognito email identities. Do not replace the pool or create a second password database.
- RDS is stopped. Use the isolated local PostgreSQL container until live deployment is ready, then notify the user before it is needed.
- Stop work when the five-hour usage window reaches 99% used (1% remaining). Query the account usage tool periodically; do not consume a reset credit automatically.

## Implemented services

`migrations/001` through `005`: tenant/membership/RLS, leases, email and employee identity fields, token-digest sessions, immutable signed snapshots/history, publication jobs, pending registrations and business-only platform read policies.

`saas/accounts.py`: verified identity provisioning, explicit platform-only bootstrap, hospital employee admission serialized by seat configuration, employee-code directory lookup. Operator-only today; no public route or privileged account connection exposed. Employee codes remain reserved after deactivation.

`saas/onboarding.py`: verified email registration creates a PENDING hospital/personal tenant idempotently. No paid AI or signing privilege. Future public endpoint must validate Cognito verification server-side and rate limit requests.

`saas/sessions.py`, `saas/auth.py`: shared Cognito claim verification, hashed access-token sessions, permanent token logout, selected membership validation, explicit workspace preconditions for clinical requests. `security.py` only extracts `verify_claims`; legacy `verify` behavior remains compatible.

`saas/platform.py`: bounded business-only tenant listing; no report or patient payloads.

`saas/locks.py`, `saas/workflow.py`: report version and hospital lease checks; submission/review/finalization/cancellation; submitted transcriptionist work is read-only until returned; signed snapshots, state, audit and publication job persist on the same transaction. The service writes the existing `hospital_state` JSON store. Amendment creation is still outstanding.

`saas/publication.py`: lease-based claims, expired-worker recovery, safe error codes and stale-worker completion rejection. The existing publisher is not yet connected; retries require a deterministic report/version S3 key. No AWS calls were made.

## Integration still needed

These services are NOT wired into the existing HTTP routes or React UI. Do not claim signup, employee login, workspace selection or signing are available in the running app yet. Existing report routes still use legacy behavior. Do not enable SaaS production mode prematurely.

1. Add a complete optional SaaS HTTP path using `SaaSAuth`, trusted workspace context, safe exception mapping, CSRF/rate limiting and business-only routes for platform users. Public hospital/individual email signup and employee login adapters must not expose internal directory emails or trust browser roles/verification flags.
2. Connect tenant JSON and relational controls using one connection and consistent lock order: tenant JSON row before report controls. Enforce role/assignment checks on every existing route, chat/history/export/library endpoint and background job; do not leave generic saves as a bypass.
3. Add legacy-table RLS and explicit non-owner runtime grants only after every legacy connection path sets context. Current migrations cover NEW SaaS tables only. The test fixture grants legacy state access solely inside its isolated database.
4. Add frontend role-specific shell, workspace selector, email signup and hospital employee login; editing leases/version banners, transitions and signed read-only state. Keep existing editor and RAG behavior.
5. Add immutable linked amendments, invitation/seat reservations, membership administration, step-up security, password reset and MFA flows.
6. Implement plans/manual payments, atomic wallets/reservations, actual provider usage accounting and durable generation jobs; choose/approve commercial policies before activation.
7. Connect publication worker after commit and test deterministic retries, then complete cross-tenant end-to-end/browser tests.
8. Only after local gates pass, ask to start RDS and approve reviewed migration/backfill/runtime role and Cognito changes. No live AWS, Terraform or `.env` changes in this checkpoint.

## Local verification

Disposable PostgreSQL: `127.0.0.1:55432/radiology_saas_test`, user postgres, password `local-test-only`, container `radiologyos-saas-test`. Tests create/drop only their uniquely generated database and role, never this supplied base database. No RDS is used.

Python runtime available at `C:/Users/aa/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe`; append `E:/Anaconda/Lib/site-packages` for installed project dependencies. Set TEMP/TMP to `.testdeps/tmp` if the sandbox temp directory fails. See `SAAS_OPERATIONS.md` for commands.

RAG invariant tests fingerprint protected functions and files. Keep them passing. Do not change fingerprints to disguise pipeline changes.

Validation: full suite passed 133 tests; after adding the submitted-transcriptionist guard and inherited-owner runtime check, the targeted SaaS suite passed all 45 tests. Dockerfile now packages the new modules/migrations/operator scripts; no image rebuild occurred. The status document records the integration boundary. No automatic commit/push, deployment, migration against RDS or live user provisioning has occurred.
