# Radiology OS: additive SaaS implementation plan

Prepared: 11 October 2026. Status: **PLAN ONLY — implementation and live changes require approval.**

Project root: `C:/Users/aa/Documents/Codex/2026-10-01/cr/outputs/radiology-reporting/`.
Paths below are relative to that root. Proposed paths are explicitly identified as new.

The attached master prompt requires this document first. This planning task does not modify application code, `.env`, AWS, database contents, Cognito, models, or deployments. No packages are installed. No production readiness is claimed.

## 1. Executive Summary

Keep the existing application. Add account, workflow, locking and billing modules alongside it, not a replacement backend or frontend. Continue using one Python backend, React/TypeScript frontend, PostgreSQL database, Cognito pool, S3 bucket, DynamoDB table, Bedrock Knowledge Base and S3 Vectors index.

Two workspace types will be supported: a hospital with multiple members and a shared subscription/wallet, and an individual radiologist with a private subscription/wallet. A person can have memberships in several workspaces without transferring reports or balances between them.

**Protected boundary:** do not change `rag_prompt`, the system instructions in `rag_generate`, retrieval ranking/fallbacks, modality conflict handling, chunking, embeddings, model selection/settings, report rendering, or generation/edit behavior. Add authorization and billing around those operations. Recording actual provider usage needs a narrow, approved instrumentation hook; it must leave the model request and generated response unchanged.

The safest implementation starts with additive tenant/membership migrations and isolation tests on a disposable database. Hospital collaboration must not be enabled until workflow permissions and editing locks pass together. Billing must not become enforceable until reservations and actual usage reconciliation pass together.

## 2. Existing Project Assessment

This is a source-level assessment of first-party entry points, schema declarations, frontend, ingestion, infrastructure definitions and tests. Dependencies, secrets, Terraform state, patient documents and generated assets are not reproduced. Live AWS resources and production database contents were not inspected in this planning task; their deployed state is **UNKNOWN**. Repository configuration is not proof of live configuration.

| Area | Actual implementation | SaaS gap |
|---|---|---|
| Backend | `server.py`: Python `ThreadingHTTPServer`, request handlers and CRUD; not FastAPI/Django | Add policy checks and modular helpers without moving existing functions |
| Frontend | `frontend/src/App.tsx`: React/TypeScript view state, editor, patients, reports, templates and modalities | Workspace selection, role-specific views, workflow, billing pages |
| Editor | Existing contentEditable toolbar and `reportTextToHtml` | Version/lock awareness, read-only finalized content; retain renderer |
| Packaging | `Dockerfile` builds frontend and copies it to `/app/web`; legacy `web/` assets also exist | Confirm one tested serving path; do not accidentally deploy stale legacy UI |
| Authentication | `security.py`, `Login.tsx`, server login: Cognito access-token verification and HttpOnly cookie | Multi-membership, workspace sessions, invitation/signup/reset/MFA enrollment |
| Membership | `hospital_memberships`, primary key `(issuer, subject)`, roles `admin`/`radiologist` | One current membership per identity; not multiple hospitals or transcriptionists |
| Application data | `tenant_store.py`: one `hospital_state.payload` JSONB snapshot per tenant, serialized writes | No report-level concurrency control or relational report ownership constraints |
| Versions | `hospital_report_versions`; `server.save_report_version` writes through a separate connection | No atomic signing/version workflow tied to the report save transaction |
| Legacy storage | `server.py` retains global `app_entities`, `report_versions` and JSON bootstrap paths | Never implicitly import or expose these as a new tenant's data |
| Chat | `patient_chat.py`: tenant/patient-keyed PostgreSQL history, 14-day retention, last five messages plus bounded report context | Assignment-aware access, durable jobs and metering integration |
| Background work | In-process daemon threads for chat and library work | Restart-safe billing/job reconciliation; no need for a new queue service initially |
| Historical library | `ingestion/library.py`, `batch.py`, `documents.py`; S3 originals/normalized documents; DynamoDB manifests | Permissions, storage/ingestion entitlements, auditing across all upload paths |
| RAG | `ingestion/retrieval.py`, `scope.py`: mandatory tenant/modality, full-source checks, controlled fallback | Preserve and prove workspace identity reaches every call |
| Generation | `server.rag_generate`: Bedrock Converse, optional existing OpenAI branch; generation and editor-revision paths | Provider usage not returned for billing; no subscription/wallet gate |
| Workflow | Generic report mutation supports `Draft` and `Final` | No enforced clinical role transition, immutable signature, amendment or lease lock |
| Billing | No subscription, invoice, payment, wallet or ledger implementation found | New additive business modules |
| Testing | `tests/test_security.py`, `test_patient_chat.py`, `test_ingestion.py`, `test_library.py` | Add real PostgreSQL concurrency/RLS tests and browser role tests |

Existing safeguards worth preserving: server-resolved tenant identity, token issuer/client validation, tenant-specific state, chat isolation, metadata/path checks, SigV4 uploads, structured upload failures, explicit modality confirmation, loading states and template/modality validation.

Important limits: hospital-row transaction serialization is **not** an editor lock. Current generic report saves can accept `Final` without a dedicated signing permission. Patient-wide chat/state responses need additional restrictions for assigned-only transcriptionists. Current `admin` must not silently become a clinician. `repair_s3_docx_reports` contains broad-prefix legacy repair logic: disable that route for commercial users until an approved ownership-safe maintenance design is verified. Do not modify retrieval to work around it.

## 3. Features to Preserve

- Existing patient registration/search, reporting/editor toolbar, templates, modalities and previous-report sidebar.
- Existing chat appearance, loading/error behavior, generation/edit intent, insertion, five-message context and 14-day patient history policy.
- Existing historical upload validation, modality-specific selection, progress/error reporting and DynamoDB manifest behavior.
- Existing Bedrock/S3 Vectors retrieval, source validation, final-report publication and report terminology/formatting instructions.
- Existing explicit errors and no-substitute behavior when retrieval is empty or modality is unresolved.
- Current API paths where practical; enforce new permissions on old routes too, rather than leaving a bypass.

Security-related changes are deliberate exceptions: unauthorized actions become unavailable, finalized reports become immutable, concurrent saves need preconditions, and hospital editors require leases. The visible editor and its formatting stay intact.

## 4. Modular Monolith Architecture

Add a small `saas/` package. Keep existing `server.py`, `security.py`, `tenant_store.py`, `patient_chat.py`, `ingestion/` and frontend files where they are.

```text
Existing React UI + new workspace/business screens
  -> existing server.py routes
     -> authenticated workspace + permissions
     -> saas tenant / workflow / locks / subscription / wallet / audit helpers
     -> existing tenant_store + patient_chat + RAG/generation functions
        -> existing PostgreSQL / DynamoDB / S3 / Bedrock / S3 Vectors
```

Use short PostgreSQL transactions. Do not hold a database lock while a user edits, a model generates, or an S3 upload completes. Add a small PostgreSQL-backed outbox/job runner in the same application deployment for durable side effects. No Kafka, Redis, Kubernetes, extra database or microservice is required.

Keep report bodies in the existing tenant JSON store initially. Add relational report-control rows for ownership, assignment, workflow and versions. This avoids rewriting clinical persistence now. The coarse tenant-row write lock remains a throughput limitation; measure it before proposing a separately approved normalization project.

## 5. Tenant and Account Hierarchy

- `users`: stable application identity linked uniquely to Cognito `(issuer, sub)`; email is not an authorization key.
- `tenants`: `HOSPITAL` or `INDIVIDUAL`, lifecycle state and display details.
- `tenant_memberships`: unique `(tenant_id, user_id)` with active status and role grants.
- A personal workspace has one owner/member initially. A hospital supports many members and consumes hospital seats.
- Each tenant has its own subscription and wallet. Switching workspace changes permissions, report scope, RAG scope and billing attribution together.
- Platform administration is a separate global grant, not membership in every hospital.

Preserve existing tenant identifiers and S3/DynamoDB prefixes. Never rename `hospital-001` or move historical documents as a side effect of this migration.

## 6. Super Admin Permissions

Allow platform business administration: tenants, activation, users/invitations, plan versions, seats, feature permissions, model allowlists, token grants, payment verification, invoices, aggregates and audit review.

Deny patient/report text, historical document content, patient chat, signed downloads and clinical signing by default. Platform analytics use tenant IDs, counts and money/usage totals, not names, MRNs or prompts. Lock management shows minimal operational identifiers.

Emergency support access, if approved, must be time-limited, explicitly scoped, justified, recorded and revocable; it is not an invisible impersonation button. Require step-up authentication for sensitive platform actions. Bootstrap the first administrator using an explicit operator command against a verified Cognito subject; never a default password, email suffix or browser role field.

## 7. Hospital User Creation Controls

Store `allow_hospital_admin_create_users` in tenant settings, default false until the owner enables it. Define separate `users.create`, `users.invite`, `users.edit`, `users.deactivate` and `users.assign_roles` permissions.

Create/invite requires the flag, corresponding permission, active subscription entitlement and a free seat. Other user-management permissions remain separately configurable. Hospital administrators cannot create platform administrators or grant clinical signing without the approved verification policy.

Serialize seat admission on the tenant entitlement row. Count active members plus unexpired seat-reserving invitations; accept/retry cannot consume a seat twice. A platform administrator cannot accidentally exceed the limit either: explicitly change the entitlement first, with an audit reason. Deactivation releases a seat but preserves authorship/history. Prevent accidental removal of the last active hospital administrator.

## 8. Radiologist and Transcriptionist Permission Matrix

| Capability | Hospital admin alone | Authorized radiologist | Transcriptionist | Individual owner |
|---|---|---|---|---|
| User administration | Configured permissions | No | No | Own profile only |
| Read clinical reports | No automatic access | Authorized scope | Assigned scope | Own tenant |
| Create/edit drafts | No automatic access | Yes, with lease/version | Assigned drafts, with lease/version | Yes, version checked |
| Generate AI draft | No automatic access | Entitlement/wallet checked | Only if explicitly enabled | Entitlement/wallet checked |
| Submit review | No | Yes | Yes | Not required |
| Review/finalize/sign | No | Explicit signing grant | Never | Explicit verified signing grant |
| Modify a signed version | Never | Amendment only | Never | Amendment only |
| View billing | Yes | Optional usage summary | Optional usage summary | Own tenant |
| Override another lock | Separate emergency permission | Not automatic | Never | Not applicable |

A person may hold hospital-admin and radiologist roles together, but the signing capability must be explicitly assigned. Apply checks to bulk/list responses, individual objects, chat history, exports, previous-report views and upload/download APIs—not just buttons.

## 9. Hospital Report Locking Architecture

Suggested starting configuration: 120-second lease, heartbeat every 30 seconds; configurable after testing. Database time is authoritative. A browser tab generates an editing-session ID; the backend issues an opaque lock credential and monotonically increasing fencing number.

1. Persist a new draft ID before starting an editing lease. Read-only open does not acquire one.
2. Atomically acquire the report-control row and create/replace its lock only when absent or expired. Increment a persistent fencing counter; never reset it when deleting/releasing a lock.
3. Renew only for the exact tenant, user, editing session, credential and fencing number, while still unexpired.
4. Every save, deletion/cancellation, submission, finalization and amendment verifies permission, unexpired lease, fencing number and expected report version in the **same transaction** as the report update.
5. On success increment report version. Return `409` for contention, `412` for stale version and a machine-readable error explaining recovery.
6. Release after submission/finalization or editor exit. Browser unload is best effort; expiration provides crash recovery.

Two tabs from the same user are still different editing sessions. An expired tab becomes read-only and must reload/reacquire; no silent overwrite. Network loss disables save and preserves an unsent draft only in memory by default. Emergency override increments the fence, records actor/reason and invalidates the old owner immediately. Any user identity shown in the lock banner requires authorization.

Individuals use optimistic version checking without hospital lease endpoints. AI generation does not hold a database transaction; applying its result requires the current editor version and, for hospitals, a valid lease. A stale result may be previewed but must not replace a newer saved report automatically.

## 10. Hospital Report Workflow

```text
DRAFT -> IN_TRANSCRIPTION -> READY_FOR_REVIEW -> UNDER_REVIEW -> FINALIZED
                       ^                          |
                       +-- RETURNED_FOR_CORRECTION-+
FINALIZED -> new linked amendment draft -> reviewed/signed AMENDED version
Unfinalized work -> CANCELLED (authorized, reason recorded)
```

Allow a radiologist to create and finalize their own reviewed draft without unnecessary transcription steps. A hospital administrator alone cannot sign. Server-owned fields include signer, signed time, workflow version and amendment parent; never accept these from arbitrary request data.

Retain outward `Draft`/`Final` compatibility while adding explicit `workflowStatus`, revision and signature fields. The transition service—not generic JSON merging—decides final status. Update report snapshot, immutable version, workflow history, audit entry and publication outbox atomically on one connection. Existing `save_report_version` uses a separate connection and therefore needs a narrow transaction-integration change.

Do not erase a signed original. An amendment references it and has a new author/reason/signature. Publishing still calls the existing final-report publication logic, after commit, with retry/idempotency tracking. RAG publication failure must not undo a valid clinical signature; display a separate indexing state. Legal meaning of an electronic signature remains subject to professional/legal review.

## 11. Individual Radiologist Subscription Sales

Reuse the editor and library inside a private `INDIVIDUAL` tenant. Keep personal reports, subscriptions and wallets separate from any employer workspace. Start with manual payment verification. Registration alone grants no paid generation or unrestricted signing.

Onboarding states: registered, email verified, professional review if required, payment pending, active, suspended/expired. Do not collect unnecessary identity documents; verification evidence storage/retention needs approval. An individual can later accept a hospital invitation without duplicating their Cognito identity or merging data.

## 12. Public Registration and Pricing Flow

Public endpoints expose enabled plan summaries only. Registration validates input and rate limits attempts; Cognito verifies email. After verification, create an idempotent pending personal workspace and direct the person to plan selection/payment instructions. Admin approval activates the paid period and grants credits once.

Add invitation acceptance, password-reset request/confirmation and MFA enrollment/recovery screens. Use generic responses where needed to avoid account enumeration. Never make public registration a route to hospital-admin or platform-admin status. Payment references and evidence are private, not public S3 objects.

## 13. Multi-Tenant Database ER Diagram

Proposed additive relationships; clinical bodies remain in existing storage initially:

```text
users --< tenant_memberships >-- tenants --1 hospital_state [existing JSONB]
  |              |                |--< tenant_settings
  |              +--< member_roles|--< report_controls --< report_assignments
  +--< platform_roles             |         |--1 report_locks
                                 |         |--< report_workflow_history
roles --< role_permissions        |         +--< hospital_report_versions [existing]
                                 |--< patient_conversations [existing]
subscription_plans --< plan_versions --< subscriptions --< subscription_periods
                                 |                    +--< subscription_entitlements
tenants --1 token_wallets --< token_credit_lots
                        |--< token_reservations --< token_usage_events
                        +--< token_ledger
tenants --< orders --< payment_records --< invoices
tenants --< invitations / audit_logs / generation_jobs / outbox_events
users --< workspace_sessions
```

Schema rules:

- Explicit migration files and `schema_migrations` versions; no production schema changes triggered by simply starting the app.
- Unique user `(issuer, subject)`; membership `(tenant_id, user_id)`; report control `(tenant_id, report_id)`; lock `(tenant_id, report_id)`; wallet `tenant_id`; idempotency `(tenant_id, operation, key)`.
- Composite tenant foreign keys for assignments, locks, workflow, usage, payments and versions; constrain positive/nonnegative amounts, valid transitions/types and unique provider payment references.
- A JSON report cannot be a SQL foreign-key target. `report_controls` acts as the relational anchor, backfilled and reconciled against JSON IDs. Creation/deletion updates both in one transaction. Orphans block rollout; do not pretend this is a fully normalized report schema.
- Add relational `report_id` linkage to existing version rows through an audited backfill. Keep original version payloads and legacy tables intact.
- Index memberships by user/status; reports by tenant/workflow/assignee; lease expiry; jobs by status/lease time; invoices by tenant/date; usage by tenant/date; subscription expiry; audit by tenant/time. Retain chat expiry index.
- Store timestamps in UTC, money as integer minor units plus currency, billable units as integers and rates as exact decimals. Never floating-point balances.
- RLS on tenant tables, including existing tenant state/chat/version tables, with `USING` and `WITH CHECK`. Set tenant context transaction-locally; clear between pooled connections. Validate membership before selecting a tenant context.
- Use a non-owner, non-superuser, non-`BYPASSRLS` runtime database role. Separate migration/maintenance credentials. Evaluate `FORCE ROW LEVEL SECURITY`; RLS is not meaningful if the app continues as a bypass-capable owner. Global platform queries use restricted business views, not unrestricted clinical queries.
- Preserve `(issuer, subject)` legacy membership data, backfill the new multi-membership table, compare counts and mappings, then switch reads. Do not dual-write multiple memberships into the old single-membership primary key.

Existing row counts, legacy ownership and orphan records are **UNKNOWN** until an approved read-only migration preflight. Do not auto-adopt legacy demo data.

## 14. Cognito Authentication Design

Keep Cognito JWT verification and the existing pool. Cognito proves identity; PostgreSQL determines active membership and capabilities. Do not use immutable `custom:tenant_id` or a browser-provided role as the source of authority.

Introduce a revocable server session bound to the validated subject and an active workspace. Keep session cookies HttpOnly/Secure/SameSite and add consistent CSRF/Origin protections for mutations. Store refresh material encrypted server-side if refresh sessions are approved; never in localStorage or logs. Revalidate disabled users, membership and tenant status on sensitive operations. Session expiry must not silently lose editor work.

Preserve first-password-change and MFA challenge handling. Add enrollment/reset flows, login throttling, generic public errors and detailed redacted server diagnostics. Passwords and tokens must never be Terraform outputs. Do not enable Cognito features or alter the pool during this planning task.

## 15. Workspace Switching Design

`GET /api/workspaces` returns only active memberships. `POST /api/session/workspace` verifies the chosen membership and sets the server session context; the submitted workspace ID is a selection request, not authorization.

Before switching, warn about unsaved work, release the old editing lease, cancel UI polling and clear old tenant data/chat/error state. Re-fetch permitted data for the selected workspace. Bind each request to the workspace the tab expects; reject a mismatched session workspace rather than accidentally charging another tenant after a second tab switches it.

Capture immutable actor, tenant, request and billing context for each background job. A subsequent browser switch must not retarget an in-flight generation, library worker, outbox publication or wallet charge. Recheck authorization before starting paid work and before applying results.

## 16. Token Wallet and Reservation Architecture

One wallet per tenant; never one shared global balance. Hospital members use that hospital wallet, while the same user's personal work uses the personal wallet.

```text
Authenticated request -> entitlement/model permission -> durable job + reservation
 -> unchanged retrieval/generation -> actual provider usage
 -> atomic settlement + append-only ledger -> draft available
```

At acceptance, calculate a conservative reservation covering the bounded input and existing maximum output. Validate all payload/context size limits. Immediately before invocation, validate against the final request: use supported provider token counting when available, or a tested conservative upper bound for the approved model. If insufficient, increase the reservation atomically or do not invoke. Do not equate character count with exact tokens.

Lock the wallet row briefly. Available units exclude active reservations and expired credits. Reserve only if enough credit exists. Record unique request/idempotency key, model, pricing snapshot, actor and workspace. Never hold this database transaction open during AWS work.

Capture actual input/output/cache counters where the existing provider response supports them. Retain missing counters as unknown, not zero. Record provider request ID and rates without prompt/report text. Settlement debits actual billable units, releases the unused hold and appends ledger entries once. Cached tokens must not be double-counted as both ordinary and cache-priced input. Version the model-specific accounting rules.

Failure cases:

- Rejected before invocation: release all credit; record reason.
- Provider returned output but persistence failed: preserve usage and recover settlement; do not invoke again automatically.
- Network timeout/worker crash after sending: mark `OUTCOME_UNKNOWN`. A missing response does not prove zero provider cost. Reconcile or apply an approved customer refund policy; do not blindly release and retry a paid call.
- Model refusal or unusable draft: provider cost still exists; whether the customer pays requires a defined policy, separate from provider usage.
- Clearing chat does not erase a charge/usage event. A late result cannot recreate cleared history.
- If unexpected actual usage exceeds the reserved bound, flag an accounting incident; never silently make the wallet negative or create a paid overage. Correct the estimator before resuming that path.

Use `generation_jobs`, durable reservations and a PostgreSQL-leased worker/reconciler in the same deployment. No automatic retry of an ambiguous paid generation. Streaming is a future UI change, not part of this protected generation setup; if later approved, settle only from final usage or an explicit unknown state.

## 17. Token Pricing and Subscription Design

Plan versions contain tenant type, duration, currency, price, seats, feature/model permissions and included credits. Snapshot entitlements/rates for each purchased period; editing a plan affects new periods, not historical invoices.

Distinguish five quantities: provider input/output/cache tokens; weighted customer units; estimated AWS cost; actual customer money paid; estimated margin. A prepaid credit is not a rupee, and model tokens do not all have equal cost. Any INR/USD conversion must record its source/date; estimates are not reconciled AWS invoices.

Support monthly/annual periods, trials, renewals, explicit top-ups and effective-dated upgrades/downgrades. Start without automatic proration unless approved. Never downgrade below current occupied/reserved seats without a resolution workflow. Inactive plans cannot be purchased, but existing paid periods remain valid.

Credit lots track source, expiry and rollover. Expire only unreserved eligible balances; define settlement for reservations crossing expiry. Use compensating ledger transactions for refunds/adjustments, not editing ledger history. No automatic paid overage. Token exhaustion blocks new AI calls, not authorized reading of saved clinical reports. Subscription expiry and account suspension have separate, explicit access policies.

## 18. Manual Billing Workflow

1. Create an order with a plan/price snapshot and unique ID.
2. Customer submits a payment reference/evidence; mark `PENDING_VERIFICATION`.
3. Platform administrator independently checks the payment externally.
4. Record verifier, amount/currency, reference, verified time and decision.
5. In one transaction, activate the subscription period, grant credits once, create invoice/receipt records and append audit/outbox entries.
6. Notify the user after commit. Retry notification without granting credit again.

Reject duplicate references and duplicate activation. Never accept client-submitted paid status, amount or balance. Refunds and mistaken activations use explicit correction records. Invoice numbering, tax/GST treatment and required legal invoice content need accountant/legal approval.

## 19. Future Payment Gateway Integration

Define a payment-provider interface now but activate no gateway. A later implementation must verify webhook signatures, validate amount/currency/order ownership against server records, reject replay, handle out-of-order events and support refunds/reconciliation. Browser redirects are not proof of payment. Feed verified events into the same idempotent activation service used for manual payments. Provider choice, credentials, fees and live activation require separate approval.

## 20. Super Admin Dashboard Plan

Add business-only overview, hospitals, individuals, users, plans/versions, packages, entitlements, usage, payments/invoices, estimated margin, settings and audit screens. Show pending verification, failed provisioning, stale reservations and publication jobs as actionable queues.

Reuse existing cards, dialogs and loading buttons. Dangerous actions require a reason and confirmation. Do not include a general patient search or report preview. Distinguish provider-cost estimates from financial revenue and reconciled expenses.

## 21. Hospital Admin Dashboard Plan

Show hospital identity, members/invitations, occupied/reserved seats, enabled creation permissions, subscription, shared credit balance, aggregate usage and billing. Show report workflow counts and minimal lock status only within approved access rights. An admin with a separate clinical role can switch to clinical views; administrative membership alone exposes no patient report content.

## 22. Radiologist and Transcriptionist UI Plan

Add authorized worklists and review queues around the existing editor. Radiologists get save, review, return, finalize and amendment actions according to state. Transcriptionists get assigned drafts and submit-for-review, never signing controls.

Add a compact lease banner, heartbeat/recovery states and version conflict dialog. Preserve formatting tools, template loading, chat and insertion. Retain unsaved content when a lock is lost; never silently merge clinical findings. Disable insertion/save when stale or unauthorized with an explicit recovery action.

Patient-wide history is a special risk: a transcriptionist assigned one report must not receive another report's findings through the previous-report sidebar or last-five-message context. Filter history by authorized reports, or deny patient-wide chat unless the user has full patient access. Do not solve this by changing the generation prompt.

## 23. Individual Radiologist Dashboard Plan

Reuse current patient/report/editor/library views with personal workspace labeling. Add plan, renewal date, credit balance, top-up request, usage, invoices and profile/security settings. No collaborative lease UI. Keep optimistic version warnings for two tabs. Switching to a hospital never copies personal report data or spends personal credit on hospital work.

## 24. Backend API Design

Proposed additive endpoints, implemented through existing handlers plus `saas/api.py`:

| API family | Purpose | Mandatory policy |
|---|---|---|
| `/api/public/plans`, `/api/auth/register`, verification/reset endpoints | Onboarding | Public-safe schema, throttling, no privileged grants |
| `/api/workspaces`, `/api/session/workspace` | Membership selection | Verified subject and active membership |
| `/api/platform/tenants`, `/users`, `/plans`, `/payments`, `/audit` | Platform business control | Platform capability, no automatic PHI access |
| `/api/tenant/settings`, `/members`, `/invitations` | Hospital administration | Tenant-scoped permission, flag and seats |
| `/api/reports/{id}/lock`, `/lock/renew`, `/lock/release` | Editing lease | Hospital membership, assigned/clinical access |
| `/api/reports/{id}/submit`, `/review`, `/return`, `/finalize`, `/amend` | Workflow transition | Role, allowed transition, version, lease where required |
| `/api/subscription`, `/wallet`, `/usage`, `/invoices`, `/orders` | Customer business data | Active workspace and business visibility permission |
| Existing `/api/rag/chat`, `/api/patient-chat/*` | Existing AI workflow | Authorized report context, entitlement and reservation |
| Existing `/api/library/*`, `/api/uploads*` | Historical library | Tenant permissions, quota, validated keys/content |
| Existing `/api/rag/debug` and maintenance routes | Diagnostics | Narrow administrative permission, tenant/modality; no global documents |

All existing generic report `POST/PUT/DELETE` paths must call the same policy/transition service. Reject attempts to directly set signature, workflow, tenant, wallet or billing fields. Enforce body limits and validate IDs before storage access. Resource ownership mismatch normally returns 404; permission denial 403; conflict 409; stale version 412; quota/entitlement errors have stable application codes; rate limits 429; service failure 503 with a safe request ID.

Mutation responses are returned only after commit. Provider errors must not leak credentials, report text or signed URLs. Add authenticated readiness diagnostics separately from public liveness; current liveness does not prove database or RAG readiness.

## 25. Tenant-Aware RAG Integration

Existing tenant/modality filters and full-source checks already provide the starting point. Do not rebuild them. Feed the authorized active tenant into the existing request `IDENTITY` context and worker context. Preserve `retrieve_reports`, `resolve_modality`, extraction/classification, prompts and formatting.

Place entitlement/model permission checks before generation, and a metered Bedrock client decorator at the existing `aws_client('bedrock-runtime')` boundary. The decorator forwards the **identical** model ID, system text, messages and inference config and records usage separately. Contract tests compare request dictionaries before/after. Do not silently switch a disallowed model to another model. Existing optional providers must either be metered under a separately approved adapter or be denied for billed SaaS use; never permit an unmetered bypass.

Generation, ingestion/classification and embedding charges are distinct. Default folder-modality classification remains non-LLM. Decide whether storage/indexing is included or separately limited; do not deduct invented generation tokens for embedding jobs without a defined accounting source.

No generated-report cache was identified in the inspected path. Do not introduce one now. Infrastructure/JWKS caches are not report caches. If a content cache is later approved, key by tenant, permissions/context version, report revision, modality, study, prompt/model version and reference-library version, with deletion/invalidation tests. Never share clinical cached responses across tenants.

Preserve negative findings such as “no focal lesion” in fixtures. Historical reference text remains untrusted formatting evidence, not evidence of the current patient's condition. This plan does not certify generated clinical accuracy or alter your prompt's normal-template policy; mandatory radiologist review remains necessary.

## 26. AWS Infrastructure and Cost Considerations

Terraform source already defines RDS PostgreSQL, Cognito, encrypted S3, DynamoDB, KMS, Bedrock KB/data sources, S3 Vectors and application IAM/instance profile. The EC2 resource block in `terraform/main.tf` is commented out: it is not a working application deployment to enable casually.

Source defaults include `db.t3.micro`, 20 GB allocated storage, single AZ, zero-day backup retention, deletion protection off, skipped final snapshot and private RDS by default. A development tfvars file can override public access. These are development settings, not an approved production posture. Embedding defaults are Titan v2/1024 dimensions, chunk size 400 with 15% overlap; generation default is Nova Pro. **Leave all RAG settings unchanged.**

SaaS tables add storage/IO to existing PostgreSQL, not a new database service. Main variable costs remain model usage, indexing/embeddings/vector queries/storage, S3 requests/storage, database size/compute, authentication/email, DynamoDB and logs. Additional Cognito messaging, backups, runtime hosting/TLS and monitoring may increase costs. Current monthly price is **UNKNOWN** without workload and current pricing verification; no illustrative subscription price guarantees profitability.

Later approved infrastructure work: runtime non-owner DB credentials, narrowly scoped Cognito user-management IAM, production HTTPS/allowed origins, backups/recovery, deletion protection, budget alarms, bounded logging and production network access. Keep DB off the public Internet for hospital production; use private application-to-RDS connectivity. No OpenSearch, NAT gateway, ALB or new paid service is added by default. Choose deployment topology explicitly before provisioning.

Terraform must not publish demo passwords, application tokens or database secrets. Review plans for unintended KB/vector/database replacement. SaaS migrations do not require re-embedding historical reports.

## 27. Security and Compliance Requirements

Enforce deny-by-default object authorization, scoped list responses, RLS, lease/version checks, server-owned fields, CSRF controls, sanitized rendered content, bounded uploads, rate limits and short-lived signed access. Validate file type/content, DOCX decompression limits and tenant-controlled keys. Quarantine/scan policy for production uploads needs an approved implementation and cost assessment.

Use safe structured audit events: request ID, actor ID, tenant, capability/action, object ID, timestamp and result. Exclude patient names, reports, prompts, passwords, tokens, raw provider bodies and signed URLs. Make ledger/audit append-only to the runtime role; document that this is not absolute protection against a database owner. Define retention and controlled administrative export.

Review both browser and operator paths: CLI import credentials must not be available to hospital users; shared-KB ingestion jobs can scan multiple tenants even when a tenant triggers the run, so never expose other tenants' job details or failures. Restrict broad legacy repair and migration helpers to explicitly authorized operations.

Production requires restore drills, incident response, account suspension/revocation, documented data deletion including backups/indexes, and professional validation of signing/amendments. Earlier permission to discard development data is not permission to discard future hospital records.

Obtain qualified legal review for applicable Indian healthcare/privacy and DPDP-related obligations, notices/consent or other lawful processing basis, roles/contracts, retention, patient rights, breach handling, professional registration, electronic signatures and cross-border transfers. Existing `us-east-1` processing needs a deliberate customer/legal decision; this document does not assert compliance or propose a region migration. Tax/payment rules also need review. Legal requirements and production customer policies are **UNKNOWN** here.

## 28. Actual File-by-File Change Plan

These are future changes only; no files below are modified by this planning task.

| Existing path | Narrow future integration |
|---|---|
| `server.py` | Route dispatch, capability gates, shared transaction hooks, session selection, metered-client boundary; protect prompt/generation bodies |
| `security.py` | Separate verified identity from selected membership; capabilities/session validation; preserve JWT checks |
| `tenant_store.py` | Explicit tenant context/RLS and shared connection access; preserve JSON payload representation |
| `patient_chat.py` | Authorized history, durable job identity, reservation integration; preserve context/retention/intent rules |
| `manage_hospitals.py` | Multi-membership-safe provisioning; stop overwriting a user's only membership |
| `migrate_json_to_postgres.py` | Approval/preflight guard if used; never implicit production migration |
| `frontend/src/App.tsx`, `types.ts` | Add workspace/worklist/workflow hooks and server capabilities; retain editor/layout |
| `frontend/src/Login.tsx`, `SessionBadge.tsx`, `apiFetch.ts` | Signup/reset/MFA/session/workspace behavior, consistent errors |
| `frontend/src/PatientChat.tsx` | Wallet/permission/lease errors and authorized context; unchanged draft renderer |
| `frontend/src/HistoricalLibrary.tsx` | Feature/quota permissions and safe scope display; keep upload pipeline |
| `frontend/src/Loading.tsx`, `loading.css`, existing styles | Reuse existing busy-state primitives; small additive styles only |
| `Dockerfile`, `docker-compose.yml` | Copy new modules/migrations and explicit startup checks only when approved; no automatic live migration |
| `terraform/main.tf`, `variables.tf`, `outputs.tf`, `historical-reports.tfvars` | Separately approved IAM/network/backup/runtime-secret changes; do not replace KB/vector resources |
| `tests/test_security.py`, `test_patient_chat.py`, `test_ingestion.py`, `test_library.py` | Preserve and extend regression coverage |
| `SECURE_HOSPITAL_SETUP.md`, `PATIENT_CHAT.md`, `AWS_RAG_SETUP.md` | Update operational instructions after each implemented phase |

New files proposed, created only in approved phases:

- `saas/__init__.py`, `api.py`, `tenants.py`, `memberships.py`, `permissions.py`, `sessions.py`.
- `saas/reports.py`, `locks.py`, `audit.py`, `jobs.py`, `outbox.py`.
- `saas/subscriptions.py`, `wallets.py`, `metering.py`, `billing.py`.
- `migrations/001_tenants.sql` through subsequent numbered migrations; `scripts/manage_schema.py`, `scripts/bootstrap_platform_admin.py`, `scripts/reconcile_usage.py`.
- `frontend/src/saas/WorkspaceSwitcher.tsx`, `PlatformDashboard.tsx`, `HospitalDashboard.tsx`, `IndividualDashboard.tsx`, `Pricing.tsx`, `Registration.tsx`, `Worklist.tsx`, `LockBanner.tsx`, `Billing.tsx` and small shared types/styles.
- `tests/test_saas_*.py`, PostgreSQL integration fixtures and browser test specifications; `docs/SAAS_OPERATIONS.md`, `docs/SAAS_ROLLBACK.md`.

Protected unchanged files/functions: `ingestion/retrieval.py`, `ingestion/scope.py`, `ingestion/documents.py`, retrieval/normalization algorithms, `server.rag_prompt`, model/system/inference settings and `App.tsx` report rendering logic. If a verified safety defect requires touching these, stop and request a separate scoped approval instead of hiding it inside this project.

## 29. Detailed Phased Implementation Roadmap

Suggested order: **1 → 2 → 3 → 4 → 5 → 7 → 6 → 10 → 9 → 8 → 11 → 12**. This puts locks before hospital collaboration, and billing definitions before paid public onboarding. Work can be built behind flags, but security checks must not be optional once commercial tenants exist.

### Phase 1 — Existing project architecture audit

- **Objective/why:** establish a truthful baseline and protect current behavior. This document completes the source-planning portion; runtime baseline is still pending approval.
- **Files:** inspect existing source/tests/Docker/Terraform; new contract tests under `tests/test_saas_regression.py` and audit notes after approval.
- **DB/backend/frontend:** read-only schema preflight on an approved test database; inventory every route and current UI flow; no behavior changes.
- **AWS/cost:** no provisioning; optional later synthetic retrieval tests incur small request/model costs only with approval.
- **Security/dependencies:** use synthetic documents; do not dump patient data or secrets. No prerequisites.
- **Tests/acceptance:** repeat existing tests/build in a disposable environment; snapshot exact Bedrock request and response/render contracts; record current failures rather than attributing them to SaaS changes.
- **Complexity:** low–medium. **Troubleshoot:** identify source/build mismatch and database reachability separately from authentication.
- **Rollback:** remove only new test/docs artifacts if requested; no live state changed.

### Phase 2 — Tenant foundation and database isolation

- **Objective/why:** represent hospital and personal tenants without replacing JSON clinical storage.
- **Files:** modify `tenant_store.py` integration only; new `saas/tenants.py`, migrations, `scripts/manage_schema.py`, PostgreSQL fixtures.
- **DB:** tenants/users/memberships/report-control anchors, indexes, migration journal, RLS/runtime grants; approved backfill of existing IDs with count/checksum verification.
- **Backend/frontend:** transaction-local tenant/connection context; no new business UI yet.
- **AWS/cost:** separate runtime DB secret/role only after approval; same RDS, modest storage/IO growth.
- **Security/dependencies:** Phase 1; fail closed on missing context; no implicit global-data adoption or owner-role RLS bypass.
- **Tests/acceptance:** two hospitals and two individuals isolated under actual runtime DB role; missing context denied; JSON/control-row consistency verified; current tenant data unchanged.
- **Complexity:** high. **Troubleshoot:** check migration version, runtime grants, context and backfill exceptions—not disabling RLS.
- **Rollback:** before new memberships/writes, switch to tested legacy reads and leave additive tables intact. After commercial data exists, use a forward fix or approved export/restore; never blindly drop tables/policies.

### Phase 3 — Authentication, memberships and RBAC

- **Objective/why:** one identity, several authorized workspaces and explicit capabilities.
- **Files:** `security.py`, `server.py`, `manage_hospitals.py`, frontend login/session/API helpers; new `saas/memberships.py`, `permissions.py`, `sessions.py`, workspace UI and tests.
- **DB:** role grants, revocable sessions, invitations foundation and permission versions.
- **Backend/frontend:** workspace listing/switching, server-side role checks, secure signup/reset/MFA scaffolding; clear old workspace UI state.
- **AWS/cost:** Cognito configuration/IAM only after reviewing current settings; messaging/MAU charges depend on use.
- **Security/dependencies:** Phase 2; no roles/tenant from browser claims; resolve current `admin` migration explicitly; clinical signing is not automatic.
- **Tests/acceptance:** tampered/expired tokens rejected; multi-tab workspace mismatch denied; disabled membership loses access; no automatic clinical privilege from admin.
- **Complexity:** high. **Troubleshoot:** separate identity success, session state and membership denial using redacted request IDs.
- **Rollback:** revoke new sessions and return only to a compatibility release that understands migrated memberships; do not revert to a single-membership lookup after multi-workspace enrollment.

### Phase 4 — Super Admin dashboard

- **Objective/why:** business control without patient-data access.
- **Files:** new `saas/api.py`, `audit.py`, platform bootstrap script and `PlatformDashboard.tsx`; narrow navigation/routes in `server.py`/`App.tsx`.
- **DB:** platform grants and append-only audit records; use existing tenant/account tables.
- **Backend/frontend:** tenant/user overview and audited administrative actions; billing tabs remain inactive until Phase 10.
- **AWS/cost:** no new service; small DB/log volume.
- **Security/dependencies:** Phase 3; explicit owner bootstrap, step-up policy, platform APIs separated from clinical APIs.
- **Tests/acceptance:** ordinary members receive 403; platform user cannot retrieve reports/chat/library content through IDs or aggregate APIs.
- **Complexity:** medium. **Troubleshoot:** inspect capability grants rather than adding unrestricted admin bypasses.
- **Rollback:** disable new platform navigation/routes while retaining audit records and a tested operator management path.

### Phase 5 — Hospital accounts and user management

- **Objective/why:** safely provision hospitals and enforce delegated user management.
- **Files:** extend `saas/tenants.py`, `memberships.py`, `api.py`; new `HospitalDashboard.tsx`; update `manage_hospitals.py` and tests.
- **DB:** tenant settings, invitations, seat reservations, provisioning operation IDs.
- **Backend/frontend:** create/invite/edit/deactivate/assign roles independently; configurable creation flag and seat display.
- **AWS/cost:** approved Cognito invitation/user-management calls; email and storage costs, no new service by default.
- **Security/dependencies:** Phase 4 and seat entitlement contract; provisional development entitlements only until Phase 10, not production paid activation. Cognito and SQL provisioning are retryable steps, not a claimed distributed transaction.
- **Tests/acceptance:** flag false blocks direct create/invite API; concurrent invitations cannot oversubscribe; retries do not duplicate users/seats; partial Cognito failures recover visibly.
- **Complexity:** medium–high. **Troubleshoot:** inspect invitation/provisioning state and reserved seats before retrying.
- **Rollback:** stop new invitations, retain existing memberships/audit, reconcile pending operations; never delete Cognito users as a blanket rollback.

### Phase 6 — Radiologist and transcriptionist workflows

- **Objective/why:** authorized drafting, review, signing and amendments.
- **Files:** `server.py`, `tenant_store.py`, `App.tsx`, `types.ts`, `PatientChat.tsx`; new `saas/reports.py`, `outbox.py`, `Worklist.tsx` and workflow tests.
- **DB:** assignments, workflow history, signature/version columns, transactional publication outbox.
- **Backend/frontend:** transition service guards old/new routes; assigned-only projections, review queue and finalized read-only UI.
- **AWS/cost:** same existing S3 publication/indexing calls after commit; retries are bounded and visible.
- **Security/dependencies:** Phases 3, 5 and 7 before hospital editing is enabled. Preserve immutable signed versions; assignment-aware patient chat.
- **Tests/acceptance:** transcriptionist denied finalization through every route; authorized radiologist can sign; failures cannot leave half-signed versions; old editor features still work.
- **Complexity:** high. **Troubleshoot:** inspect workflow/assignment/version/lease and publication status separately.
- **Rollback:** disable new transitions/read-only if necessary; preserve signed history. Never roll back to a release allowing generic edits of signed reports.

### Phase 7 — Hospital report locking

- **Objective/why:** prevent two users overwriting the same report.
- **Files:** new `saas/locks.py`, lock migration, `LockBanner.tsx`, concurrency tests; hooks in report/API/editor paths.
- **DB:** report fencing counter, lease owner/session/token hash/expiry and expected version; short atomic transactions.
- **Backend/frontend:** acquire/renew/release/override, read-only contention UI, heartbeat and stale-result insertion checks.
- **AWS/cost:** no service added; periodic DB/API traffic must be load tested.
- **Security/dependencies:** Phases 2–3, report-control anchors and Phase 6 contract. No endpoint may bypass leases or reset fencing numbers.
- **Tests/acceptance:** two users/tabs cannot hold a valid edit lease simultaneously; expiry/override fences old writes; individuals still edit without hospital locks.
- **Complexity:** high. **Troubleshoot:** compare database time, lease expiry, fence and version; do not forcibly overwrite another draft.
- **Rollback:** stop hospital editing or use a prior lock-capable release. Do not disable backend lock checks while collaboration is active.

### Phase 8 — Individual radiologist registration and subscriptions

- **Objective/why:** sell personal workspaces without mixing hospital data.
- **Files:** registration/pricing/individual dashboard components; `Login.tsx`, `saas/memberships.py`, `subscriptions.py`, API routes and onboarding tests.
- **DB:** pending personal tenant, professional-review state and plan order references.
- **Backend/frontend:** verified signup, personal workspace, payment-pending/activation flow; reuse existing editor/library.
- **AWS/cost:** Cognito signup/verification messaging; use existing storage and identity infrastructure.
- **Security/dependencies:** Phases 3, 9, 10; public rate limits, no privileges from signup fields; never enable unpaid generation accidentally.
- **Tests/acceptance:** same subject can use hospital and personal workspaces with separate wallets; pending registration cannot use paid AI; duplicate callbacks don't duplicate workspaces.
- **Complexity:** medium–high. **Troubleshoot:** distinguish email/professional/payment approval stages.
- **Rollback:** close new registrations, preserve paid users and existing access; do not remove customer identity/data.

### Phase 9 — Token wallets, reservations and usage tracking

- **Objective/why:** prevent overspending and attribute every paid call correctly.
- **Files:** new `saas/wallets.py`, `metering.py`, `jobs.py`, reconciliation script and concurrency tests; narrow hooks in `server.aws_client` and chat dispatch.
- **DB:** wallets, credit lots, append-only ledger, reservations, usage events, durable generation jobs/idempotency constraints.
- **Backend/frontend:** atomic reserve/settle/refund, unknown-outcome handling; balance/blocked/request-status UI; exact existing generation request preserved.
- **AWS/cost:** no new service; token-count calls if supported and approved; provider usage costs stay with existing calls. Add IAM actions only when actually required.
- **Security/dependencies:** Phase 10 pricing snapshots plus Phase 3 identity; approve the usage-only client wrapper before touching invocation boundaries.
- **Tests/acceptance:** parallel calls cannot over-reserve; duplicate settlement debits once; crash-after-response reconciles without another model call; actual counters and request equality verified.
- **Complexity:** very high. **Troubleshoot:** reservation/job/provider request IDs, not raw prompt logging; unresolved usage enters an operator queue.
- **Rollback:** stop new AI work, reconcile outstanding reservations, retain ledger; do not revert to unmetered paid calls. Begin in shadow mode with synthetic credits before enforcing balances.

### Phase 10 — Subscription plans and manual billing

- **Objective/why:** make prices/entitlements configurable and payments auditable.
- **Files:** new `saas/subscriptions.py`, `billing.py`, migrations and `Billing.tsx`; extend platform/hospital dashboards.
- **DB:** plan versions, periods/entitlements, orders, verified payment records, invoices and token-grant outbox entries.
- **Backend/frontend:** plan administration, manual verification, activation/renewal/top-up/cancellation and history; Phase 9 consumes grant events once available.
- **AWS/cost:** no live payment gateway or new service; storage and optional notification costs.
- **Security/dependencies:** Phases 3–5; atomic/idempotent activation, no customer-controlled paid state; tax/price policies approved before sales.
- **Tests/acceptance:** duplicate payment verification cannot grant twice; plan edits preserve existing period price; expired plans block new paid generation under defined policy.
- **Complexity:** high. **Troubleshoot:** inspect order, verified payment, period and grant state independently.
- **Rollback:** pause sales/activation, keep invoices/verified payments; reverse mistakes with compensating records, not deleting financial history.

### Phase 11 — Tenant-aware RAG and caching verification

- **Objective/why:** prove new workspace/billing wrappers do not change current retrieval or generation.
- **Files:** new contract/integration tests; `verify_live_retrieval.py` test harness only if necessary; no planned changes to retrieval, prompts, chunking or renderer.
- **DB/backend/frontend:** validate job attribution, permissions, model allowlist and source metadata; show no-match/permission/quota errors distinctly. No content cache introduced.
- **AWS/cost:** approved synthetic USG/CT/MRI retrieval/generation tests on the existing services; explicit limited test budget.
- **Security/dependencies:** Phases 6–10; tenant/modality invariant even in fallbacks; unmetered alternate provider disabled for paid mode.
- **Tests/acceptance:** Hospital A/B and personal sources remain separate; request payload snapshots unchanged; negation/style fixtures pass; no-match never substitutes another modality.
- **Complexity:** medium–high. **Troubleshoot:** membership scope, metadata, indexing and entitlement separately; do not relax mandatory filters.
- **Rollback:** disable the failing SaaS feature or paid generation, preserve original pipeline; no vector/index replacement.

### Phase 12 — Security testing and deployment preparation

- **Objective/why:** prove isolation/recovery before admitting hospitals.
- **Files:** security/load/browser tests, `docs/SAAS_OPERATIONS.md`, `docs/SAAS_ROLLBACK.md`; separately approved Docker/Terraform production adjustments.
- **DB/backend/frontend:** restore drill, production grants, retention/cleanup, readiness, CSRF/rate limits, hardened hosting and safe error handling.
- **AWS/cost:** backup/network/HTTPS/monitoring/IAM changes require their own reviewed Terraform plan; hosting/backup/log costs explicitly estimated before apply.
- **Security/dependencies:** all prior gates, legal/professional review and bounded upload controls. No live payment gateway as part of this phase.
- **Tests/acceptance:** required matrix below, load/concurrency targets, restart recovery, no PHI in business analytics/logs, rollback rehearsal; operator sign-off.
- **Complexity:** high. **Troubleshoot:** use runbooks/request IDs and readiness metrics; never bypass auth/RLS to restore availability.
- **Rollback:** deploy a compatible prior release, disable affected writes, restore only after an approved recovery decision; retain accounting/signature auditability.

## 30. Testing and Rollback Strategy

Use mocked provider calls for fast tests, real disposable PostgreSQL for constraints/RLS/races, and synthetic reports for approved AWS checks. Existing tests are not a substitute for concurrency tests against the actual runtime database role. Do not run migrations or provision resources just to create this plan.

Required acceptance matrix:

| Test | Required result |
|---|---|
| 1. Hospital A vs B | Direct IDs, list queries, exports, chat, downloads and writes cannot cross tenants |
| 2. Individual isolation | Personal tenants cannot see each other or an employer outside selected membership |
| 3. User-creation flag | False denies both UI and direct create/invite requests |
| 4. Seat race | Parallel creates/invitations never exceed reserved + occupied allowance |
| 5. Transcription finalization | Denied through transition and legacy generic save paths |
| 6. Radiologist finalization | Authorized signer/lease/version succeeds once with immutable version |
| 7. Concurrent editors | Only one valid hospital lease, including same-user tabs |
| 8. Expired owner | Old fence cannot renew/save over new owner |
| 9. Direct lock bypass | Missing/forged/foreign lock rejected on every mutation |
| 10. Hospital wallet | Every member's hospital request debits that shared wallet only |
| 11. Personal wallet | Workspace switch does not charge an employer or another personal account |
| 12. Wallet race | Many concurrent calls never overspend available credits |
| 13. Failure settlement | Before-send, after-send, refusal, timeout and persistence failure have distinct tested outcomes |
| 14. Subscription expiry | New AI work blocked under policy; authorized historical read preserved |
| 15. Manual activation | Verifier/reason recorded and duplicate request grants once |
| 16. RAG isolation | All searches/sources remain tenant/modality matched; foreign paths rejected |
| 17. Cache isolation | No tenant-shared clinical content cache; future cache requires scope/invalidation tests |
| 18. Regression | Registration/editor/save/templates/modalities/upload/chat/insertion unchanged where authorized |
| 19. Migration recovery | Backfill verified; expand-only rollback rehearsed; no silent data/signature/ledger loss |
| 20. Platform access | Non-platform denied; platform business role still denied patient content |

Additional gates: identical Bedrock request fixture, historical negation retained, RLS pooled-context isolation, CSRF/Origin rejection, assignment-filtered chat, background workspace immutability, idempotent final publication, worker restart, upload bounds, no secret/PHI logging and production-role privilege checks.

Use expand/backfill/verify/switch migrations. Keep old columns/tables during the compatibility window; never automatically run a destructive down migration. Introduce new features disabled for production until their gates pass. A feature flag must not re-enable insecure writes. After new signed reports, memberships or financial events exist, rollback generally means a compatible release or forward correction—not returning to the pre-SaaS application.

## 31. Beginner-Friendly Maintenance Guide

Business settings belong in the dashboard: plans/prices, package credits, seats, allowed models, invitations, activation, payment verification and reasoned ledger adjustments. Infrastructure settings remain in reviewed Terraform/runtime configuration: network access, RDS backups, KMS, Cognito pool/client, IAM, domains and deployment. A dashboard must not run arbitrary Terraform or SQL.

For each release: review the small file diff, run offline tests, run disposable-database tests, build the frontend, inspect migration preflight, approve live changes separately, deploy one compatible release and check readiness with a synthetic account. Record the release and schema version. Do not edit `.env` to grant roles or manually update wallet totals.

Troubleshooting order:

1. Cannot sign in: check Cognito challenge/error code, runtime/database readiness and membership separately.
2. Wrong workspace/data: stop work; inspect verified subject, selected membership and request tenant. Never relax filters.
3. Cannot save: inspect assignment, workflow state, version and lease. Preserve unsaved content; reload safely.
4. Cannot generate: inspect entitlement/model permission, available/reserved credit, indexing and matching modality in that order.
5. Stuck generation: inspect durable job/reservation state; do not blindly retry an unknown paid invocation.
6. Final report saved but not searchable: inspect publication outbox and per-document indexing, not just ingestion job status.
7. Payment not activated: inspect verified payment and idempotent period/credit grant records.

Use synthetic data during development. Before commercial use, define backups, restore targets, retention and incident ownership. Terraform destroy remains a destructive development-only operation, not a subscription pause. Recreating Cognito/database resources loses identities/memberships/business state unless restored or explicitly reprovisioned; infrastructure code alone cannot restore a hospital's records.

## 32. Open Questions Requiring My Approval

Implementation approval is separate from approval to change live AWS or production data. Please decide these at the relevant phase:

1. Approve Phase 1 regression contracts and Phase 2 additive schema/migration development on a **disposable local/test database**, with no live migration yet?
2. Confirm preserving current tenant IDs/data and the current RAG pipeline exactly. Approve the narrow usage-only Bedrock client wrapper later; without it, trustworthy actual-token billing cannot be completed.
3. Who is the initial platform administrator (verified Cognito identity, never a password in chat)? Which existing admins should also receive an explicitly verified radiologist role?
4. Should radiologists see all hospital reports or only assigned departments/patients? Should transcriptionists have AI access and what exact patient-history scope?
5. Confirm seat counting includes pending invitations and whether hospital admins may grant clinical roles after verification.
6. Approve initial lease/heartbeat timings, emergency override roles, signing/return/amendment rules and final report retention.
7. Choose actual prices, currency/tax treatment, token weighting, top-up expiry/rollover, proration and refusal/timeout customer charging policy. Attachment prices are examples only.
8. Define trial allowances, subscription-expiry manual editing/export rights and suspension policy without blocking legally required record access.
9. Approve verification requirements, payment evidence handling, audit/financial retention and privacy/legal review; confirm suitability of the current AWS region.
10. Choose production deployment, backup/recovery targets and budget. Review live infrastructure changes separately; no new services are assumed.

**Recommended next approval:** implement the regression baseline and additive tenant foundation in a disposable database first. Review that diff and migration preflight before authentication/workspace rollout. This document stops here; no implementation, AWS change or production migration is authorized by its creation.
