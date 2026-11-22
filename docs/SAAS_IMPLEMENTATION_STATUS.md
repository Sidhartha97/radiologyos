# SaaS implementation status

Updated 16 October 2026. Baseline: local commit `289f544`, tag `before-saas`.

## Current status — supersedes the historical sections below

Latest local-first increment: redesigned platform dashboard, fixed mock invitation
email restriction and local historical validation/staging, required TOTP first-login
flow, app-wide session revocation (migration 024), stricter HTTP JSON/body/origin
policy, platform configuration checks and workflow worklist filters. Final 210 tests,
frontend production build, shared multi-role smoke and git diff --check passed.
Synthetic browser checks passed for dashboard, invitation and staging.
No cloud-library/indexing success is claimed by the demo. User re-confirmed keeping
Terraform unchanged and testing locally first; no domain/deployment was configured.
Durable workers, password-reset revocation reconciliation and live production gates
remain outstanding. This is not a completed production-ready release.

New non-subscription increment (migrations 021–023): automatic hospital editor
locking, atomic transcriptionist-to-radiologist save/handoff, authorized saved
report GET/print, Back/navigation warnings, workflow labels, actual Bedrock
Converse input/output token dashboard and recorded/verified INR manual payments.
No charges, credits, automatic AI activation or subscriptions were added.

Validation: 196 local tests passed; React production build and shared multi-role
HTTP smoke passed. Synthetic browser checks verified automatic lock, competing
tab denial, handoff/read-only, radiologist finalization and platform screens.
Actual print/PDF output and live AWS/Cognito/RDS were not tested. RAG requests,
prompts/retrieval/renderer remain protected and regression-tested; the approved
usage wrapper forwards the original model request/response unchanged.

Usage totals cover newly tracked SaaS Bedrock Converse report calls only, not
historical AWS usage or embedding/ingestion/OpenAI calls. Manual payment records
are bookkeeping only, with recent-auth/version checks and immutable decisions.
Terraform and `.env` are unchanged. Explicit reviewed migrations/runtime grants
and production recovery, durable workers and operational/security gates remain.
See SAAS_CONTINUE.md for authoritative current scope. No production-ready claim.

Transcriptionist AI update: super admin can grant AI to transcriptionists, never
sign-off. Separate assignment-scoped report conversation and RLS are implemented
in migration 020/report_chat.py. Patient-wide chat remains denied. 188 tests,
frontend build and shared-demo smoke passed; generation remains globally gated.

Latest non-subscription increment: super-admin-only hospital/individual account
creation, bound verified owner invitations, shared multi-role local demo, report
assignment, recovery/TOTP enrollment UI/services, overview and business-only audit.
Migrations now extend through 019. No live deployment or user creation occurred.
Terraform remains unchanged at the user's explicit request. See SHARED_DEMO.md.
Validation: 184 local tests passed; frontend build and shared multi-account smoke
test passed. Browser creation/delivery/new-owner login was verified using synthetic
data only. Real Cognito/RDS, durable generation/publication and production recovery
gates remain outstanding; this is not the completed production SaaS plan.

Subscriptions have been put ON HOLD by the user. The access-first checkpoint adds
migration 015_access_controls, super-admin member controls, audited/revisioned
role and clinical grants, hospital reactivation/invitation revocation and role-aware
UI. See ACCESS_TESTING.md for safe synthetic previews and the remaining live gate.
The unfinished billing migration was never applied and has been removed.
This checkpoint passes 174 local tests and the frontend production build. New
preview roles still need manual browser QA; no live AWS verification is claimed.

Optional SaaS API/React integration is implemented locally, disabled by default.
Migrations 001–014 cover scoped clinical RLS, auth sessions, employee invitations,
platform business controls, leases/workflow/amendments and expiring atomic wallets.
The full local suite now passes 168 tests; the frontend build also passed earlier.
Disposable browser checks verified platform/clinical separation
and editing/signing. No live AWS changes or production rollout has occurred.

Billing decision: period-end credit expiry; actual model usage is charged even for
an unusable draft. Currency is INR (integer paise), with plan prices still unchosen.
Versioned integer pricing and atomic immutable usage receipts are tested components;
paid generation, plans/payment approval, durable generation and provider metering
are NOT integrated into generation. AI and live invitation provisioning remain separately OFF.
Public signup/reset/MFA enrollment and publication-worker integration remain pending.
See SAAS_CONTINUE.md for the authoritative handoff and safety boundaries.

The following is retained as the earlier foundation inventory, not a claim that
the current API/UI remain unwired.

## Implemented in this checkpoint

- Additive `saas/` package, with no import-time database/AWS access.
- Explicit checksummed, transactionally applied SQL migration runner.
- Hospital/individual tenants, multiple memberships, roles and explicit clinical/AI grants.
- Tenant settings, report controls/assignments, platform grants and audit schema.
- PostgreSQL RLS on the new tables, and a dedicated non-owner runtime-role grant command.
- Membership-validated workspace transactions and workspace listing service.
- Deny-by-default capability rules; hospital administration does not imply signing.
- Legacy structural preflight and idempotent backfill, preserving tenant/report IDs and existing clinical JSON.
- Existing finalized records remain marked as legacy finals; no signer identity is invented.
- Protected RAG fingerprints and new policy/preflight tests.
- Opt-in PostgreSQL integration suite using an isolated database/role with synthetic data.
- Report editing leases with expiry, fencing, optimistic version checks and concurrent-access tests.
- Verified-email onboarding and hospital-scoped employee-code provisioning services.
- Hashed-token sessions, workspace selection, logout replay prevention and Cognito authentication adapter.
- Business-only platform tenant listing with no clinical read grant.
- Workflow save/sign service with atomic snapshots, immutable signed history and publication outbox.
- Publication worker claim/recovery helpers; the existing AWS publisher is not yet connected.

## Approved account role

The user selected `sidharthachintu1997@gmail.com` as platform administrator only. Do not automatically grant this account hospital-admin, radiologist, clinical report access or signing permission. Clinical users will be chosen later. This records the intended provisioning policy; no live Cognito or database role grant has been performed.

## Validation

The full suite passed 133 tests before the final submitted-report permission test was added. The final targeted SaaS run passed all 45 tests, including that additional permission check. Local PostgreSQL checks include RLS, membership isolation, employee-code/seat races, session isolation, platform-only access, atomic signing and publication recovery. The earlier frontend build passed; frontend source has not changed in this checkpoint. These checks do not constitute live AWS or complete SaaS end-to-end verification. See the continuation note for the exact integration boundary.

## Rollout boundary

These modules are deliberately not connected to live login, reporting or billing yet. No migration, backfill or role grant has been run against RDS. `security.py` now exposes its existing Cognito claim verification as a reusable method; legacy authentication behavior is preserved. The Dockerfile includes the new modules/migrations/operator scripts but does not activate them or run migrations. The image has not been rebuilt in this session. No `.env`, RAG, model or Terraform setting is changed by this checkpoint.

Phase 1 source audit/regression guards are implemented. Phase 2 foundation code has passed its local PostgreSQL validation gate. Phase 3 authentication/session services and parts of workflow/platform services are implemented; HTTP and UI integration remain pending. Editing leases and workflow are not yet enforced by existing report routes.

Remaining plan work includes live schema rollout after approval, authentication integration, platform/hospital/personal dashboards, invitation/signup/reset/MFA routes, report lease/workflow integration and amendments, subscription/manual payments, prepaid reservations/usage, durable generation and publication-worker integration, and production verification. The application is not yet a completed commercial SaaS.

Do not enable new tenant roles in the existing application until the corresponding API authorization and UI integration phases pass. `saas.permissions` is a policy component, not enforcement on old routes by itself.

## Next gate

Continue authentication/workspace and workflow integration locally, enforcing the platform-only account decision above. Live RDS/Cognito/IAM deployment still needs separate approval. Keep each completed feature reviewable as its own local commit when requested; no commits or pushes were made by this implementation checkpoint.

Read `SAAS_CONTINUE.md` before resuming. RDS can stay stopped during local implementation.
