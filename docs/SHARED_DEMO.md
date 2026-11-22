# Shared browser demo (no AWS)

This is a LOCAL TEST application with mocked Cognito and real PostgreSQL/API/
workflow isolation. It is not the production login and must never be deployed
or exposed outside loopback. No subscription, real email, AWS/RAG invocation or
real password reset/MFA enrollment is enabled in this demo.

Updated 16 October: the super-admin dashboard has separate navigation for
Overview, Customer accounts, Create account, Token usage, Manual payments, Audit
activity and Account security. A visible LOCAL DEMO badge distinguishes mock
identities from a real Cognito deployment. Do not expose the demo to a network.

## Start

From PowerShell in the project folder, with Python project requirements installed:

```powershell
docker start radiologyos-saas-test
$env:SAAS_TEST_DATABASE_URL = 'postgresql://postgres:local-test-only@127.0.0.1:55432/radiology_saas_test'
python tests/demo_saas.py
```

Open the URL printed in the terminal (not your normal localhost:8000 URL).
All accounts below share one disposable database for this demo run.

| Role | Email | Employee sign-in alternative |
| --- | --- | --- |
| Super admin | superadmin@example.test | None |
| Hospital admin | hospitaladmin@example.test | demo-hospital + ADMIN001 |
| Hospital radiologist | radiologist@example.test | demo-hospital + DR001 |
| Hospital transcriptionist | transcriptionist@example.test | demo-hospital + TX001 |
| Individual radiologist | individual@example.test | None |

Password for all: `DemoOnly123!` (synthetic, local-only).
Use Sign out before switching identities. Each account starts with its own
session/workspace selection. Sharing a browser cookie across tabs is not a
substitute for separate users; use separate browser profiles if testing concurrency.

## Test sequence

1. Super admin: review the overview/audit/customer list. Create a HOSPITAL or
   INDIVIDUAL account with any valid email, a reason, and **Active** status
   for this development test. Pending status deliberately prevents workspace use.
2. Click Send invitation. The demo records INVITED locally; no actual email is
   sent. Sign out, then use the newly invited email and the same demo password.
   Hospital owners receive hospital_admin only. Individual owners receive owner
   and radiologist roles, but neither new account receives signing or AI grants.
   No email is sent even when you enter a real address; this is a local identity
   only. The former `@example.test` restriction caused InvalidParameterException
   for Gmail addresses and has been removed from this demo, not from Cognito.
3. Hospital admin: open demo-hospital, inspect staff/occupied/reserved seats;
   invite an employee with a unique employee code and a valid recovery
   email, deliver locally, then sign in as that employee. Test deactivate/reactivate
   and revoke. Hospital admins cannot create new hospitals/individual accounts or
   grant signing/AI/platform access.
4. Radiologist: open demo-hospital's synthetic report; editing is reserved
   automatically. Open it in a second tab to check read-only contention. Change/save
   a draft, and assign/remove TX001 using the assignment controls. A removed
   assignment removes the transcriptionist's report/patient visibility.
5. Transcriptionist: open only the assigned synthetic report, edit it, select
   DR001 under Send to radiologist and click Save & send for review. This saves
   and assigns/submits atomically; the editor becomes read-only after submission.
   Confirm signing and full library/template/modality administration are unavailable.
   AI permission is enabled in this synthetic demo to show the report-scoped
   assistant. It does NOT use patient-wide chat or other-report history. Sending
   a generation request reports AI disabled; this demo never invokes AWS/models.
   In real SaaS the super admin grants AI under Member access, independently of
   signing. Reload the user's session after changing a grant.
6. Radiologist: review and finalize the submitted report. Signed content is
   read-only; changes require a linked amendment, not overwriting the original.
7. Individual: use the private demo-individual workspace without hospital editing
   leases or employee invitations. Its records do not mix with demo-hospital.
8. Platform Member access: changes require current revision/reason; the last active
   hospital administrator is protected. Platform accounts cannot read patient data
   merely because they have platform administration.
9. Super admin: inspect User token usage. The local demo shows zero real model
   tokens because it never calls AWS. Live tracked input includes system/reference/
   conversation context. Missing/unknown usage is shown separately, not as zero.
10. Super admin: record a synthetic INR payment for demo-hospital with a unique
    receipt reference. Verify its received amount or reject it with a reason.
    This only records a decision; it never transfers money, grants credits or
    enables AI. Use synthetic references only in this disposable demo.
11. Open a saved report: use Print saved report to print the authorized persisted
    revision (not unsaved editor changes), and Back to reports to leave the editor.
    Verify print/PDF output manually before production use.
12. Radiologist: Historical library now supports real DOCX/PDF/TXT validation and
    **Stage files locally**. Try `tests/fixtures/demo_usg.txt` with USG selected.
    The batch must finish as LOCAL_STAGED, never INDEXED/COMPLETED. Files stay in
    demo process memory (40 MB per workspace, up to 50 batches) and are deleted
    when it stops. This tests browser validation/staging, not S3 or Bedrock. Do
    not upload real patient documents to a mock-identity environment.
13. Account security: use **Sign out all application sessions** to revoke your
    Radiology OS sessions. It does not globally sign out other Cognito apps.
    Deployment configuration checks intentionally show REVIEW REQUIRED for local
    insecure cookies, missing public domain and mocked identities. This is not
    an authentication/database failure.
14. Reports: filter the worklist by workflow status/modality. Transcriptionists
    still see only assigned records, regardless of filters.

If an older demo is running, type `stop` in its terminal, rebuild the frontend
with `cd frontend; npm run build; cd ..`, then run `python tests/demo_saas.py`
again. Open the newly printed URL. Restarting removes old disposable demo data
and recreates the five demo accounts. Refreshing a browser alone does not reload
the Python backend or the old mock invitation behavior.

## Stop

Type `stop` and press Enter in the demo terminal. The server stops and removes
only its generated `radiology_saas_it_*` database and matching runtime role.
Demo changes are lost. The base test database, main app and AWS are not deleted.
Avoid force-killing the terminal, which can interrupt this cleanup.

For bounded automated browser verification without interactive stdin:

```powershell
python tests/demo_saas.py --serve-seconds 420
```

The demo stops after the requested duration (1–3600 seconds) and removes only
its generated test database/runtime role. It does not stop Docker or the base DB.

For an automated multi-account check without leaving a server running:

```powershell
python tests/demo_saas.py --smoke-test
```

This verifies all five roles plus super-admin account creation, local delivery
and owner acceptance, then removes its own test database/role.

## Real deployment boundary

The user asked to keep Terraform unchanged. No IAM changes or live user creation
were performed. Actual Cognito delivery needs reviewed scoped user-pool permissions;
SaaS requires approved explicit migrations/non-owner runtime setup before enabling
it on RDS. Real recovery and authenticator setup need actual Cognito configuration
and live tests; the demo intentionally returns unavailable rather than pretending
to reset passwords or enable MFA. No production-readiness claim is made.

Implementation references: recovery follows Cognito
[ForgotPassword](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_ForgotPassword.html)
and [ConfirmForgotPassword](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_ConfirmForgotPassword.html).
Authenticator enrollment follows
[AssociateSoftwareToken](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_AssociateSoftwareToken.html),
[VerifySoftwareToken](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_VerifySoftwareToken.html)
and [SetUserMFAPreference](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_SetUserMFAPreference.html).
