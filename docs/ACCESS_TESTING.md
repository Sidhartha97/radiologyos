# Access-first development checkpoint

Subscriptions are ON HOLD at the user's request. No billing plan/order/payment
tables or UI were deployed. The unfinished manual-billing migration was removed
before it was ever applied. Existing disabled wallet/usage foundations remain.

## Current controls

| Account | Available access |
| --- | --- |
| Super admin (platform_admin) | Business-only customer configuration and member access; no automatic clinical workspace or patient access |
| Hospital admin | Staff roster, employee invitations, deactivate/reactivate members, revoke invitations; no automatic signing or clinical access |
| Radiologist | Full authorized clinical workspace, leases/review; signing requires an explicit grant |
| Transcriptionist | Assigned reports/patients only, draft editing/submission; no signing, historical library, patient registration or modality/template administration |
| Individual | Private owner/radiologist workspace, no hospital invitations; signing remains an explicit grant |

Platform member changes require recent authentication, current member revision
and a reason. They cannot grant platform administration through workspace roles.
They revoke the affected member's editing leases. Membership is rechecked by the
backend on subsequent requests. The last active hospital administrator cannot
be removed. Reactivation respects active members plus reserved invitations.

Permission to use AI is separate from the global generation feature gate. This
checkpoint does not turn on AI, send invitation emails or change RAG behavior.

## Safe local browser previews

Use the isolated test PostgreSQL container, not RDS. From the project directory:

```powershell
$env:SAAS_TEST_DATABASE_URL = 'postgresql://postgres:local-test-only@127.0.0.1:55432/radiology_saas_test'
python tests/preview_saas.py --role platform
```

Open the printed loopback URL. Use `preview@example.test` and any nonempty
**dummy** password. Authentication is mocked: never enter real credentials,
never expose this server to a network and never deploy this preview script.
Type `stop` in its terminal to shut down and delete its generated database/role.

Repeat one at a time with `--role hospital_admin`, `--role radiologist`,
`--role transcriptionist`, and `--role individual`. Each run uses an isolated,
disposable database: changes do NOT transfer between preview runs. This is for
role-specific UI checks, not proof of real Cognito or AWS integration.

Check platform Member access, hospital staff controls, assigned-report navigation,
read-only/finalized reports, signing restrictions and individual workspace rights.
Network errors should display errors rather than falsely claiming success.

## Before the actual application/live browser test

Do NOT simply enable SaaS against the existing database. The remaining rollout
gate requires review/approval of explicit migrations/backfill, a dedicated
non-owner runtime login, verified Cognito users/memberships and feature flags.
Migration 015 adds access-control revisions and database-level grant protection.
RDS can stay stopped until that approved gate. No RDS migration, Cognito user
creation, `.env` edit or Terraform change was performed in this checkpoint.

Public hospital/individual registration, password recovery/MFA enrollment,
assignment administration and full multi-user browser workflow checks remain
separate unfinished work. Do not describe this checkpoint as complete production
account onboarding or a subscription implementation.
