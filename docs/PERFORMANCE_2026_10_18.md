# Database and dashboard performance — 18 October 2026

No Terraform, AWS infrastructure, schema migration, RAG prompt, retrieval, model
request or generation change is required for this increment.

## Implemented

- The SaaS runtime uses a lazy, process-local `psycopg_pool.ConnectionPool`.
  Default minimum 1 / maximum 4 physical connections, 10-second checkout limit,
  maximum 32 waiting borrowers, 15-minute connection lifetime and 60-second idle
  shrink interval. Limits apply per application process, not per entire cluster.
- Pool connections are checked for connectivity on checkout. The non-owner /
  non-superuser / no-BYPASSRLS guard is checked once per lease. Nested helpers
  skip only the duplicate physical-role guard within that lease. Identity,
  membership, tenant status, session revocation and report permissions are NOT
  cached between requests.
- Borrowers commit/rollback through the connection context. Before reuse,
  `DISCARD ALL` clears session role, identity/tenant settings, temp objects,
  prepared statements and session advisory locks. Auto-preparing statements is
  disabled to keep driver/server state consistent. Reset failures discard the
  connection. Secret/DSN changes replace the pool without logging credentials.
- Unsafe database-role checks now use one SQL round trip instead of two.
  Resolving a user sets transaction-local user context in the result query,
  avoiding a separate round trip. Tenant filtering and RLS remain enforced.
- Clinical routes reuse the authentication result already obtained by the HTTP
  gateway. They still require an explicit matching workspace header. JWTs and
  sessions are checked anew for every request; administrative fresh-login gates
  and the downstream clinical authorization remain intact.
- Session descriptions no longer lock the session row for update. Read requests
  across tabs need not serialize on that row. Creation/workspace-selection locks
  remain, as do expiry, revocation and logout-all checks.
- `/api/platform/dashboard` returns summary + bounded business audit through one
  platform authorization and database connection. It returns no clinical data.
  Overview counts are calculated in one query. Existing overview/audit endpoints
  remain compatible.
- The frontend uses the combined dashboard endpoint. The audit page fetches only
  audit; customer lists are loaded when the customer page is opened, rather than
  eagerly on every dashboard mount. No client-side authorization cache added.

## Validation

All 226 tests passed, including disposable local PostgreSQL tests for connection
reuse, cross-hospital concurrent borrowers, committed SESSION settings being
cleared, rollback cleanup, membership revocation, unsafe owners, closed sockets,
configuration rollover and non-blocking session reads. HTTP tests confirm one
clinical authentication, mandatory matching workspace headers, and platform-only
business dashboard access. Protected RAG fingerprints still pass. Frontend build
and the multi-role account/workflow smoke test passed.

Read-only tests against the existing US-East-1 RDS, excluding HTTP authentication:

| Operation | Before | After, warmed pool |
|---|---:|---:|
| Overview database work | 4.418 s | 2.052–2.088 s |
| Customer-list database work | 4.820 s | 2.506–2.521 s |
| Combined overview + audit database work | Not previously combined | 2.275–2.291 s |

Nine benchmark operations used two physical connections rather than nine. The
first overview took 4.951 s including pool/credential startup. These are measured
database-operation timings, NOT complete page or generation timings. Local-to-
AWS network latency remains; no instant-response or production-readiness claim.
The benchmark made no report reads, model calls, session writes or schema edits.

## Load the changes in Docker

From the project directory:

```powershell
docker compose up -d --build radiologyos
docker compose ps
docker compose logs --tail=100 radiologyos
```

This installs the new pooling dependency inside the image. Existing `.env`, AWS
settings and the uploads volume are preserved. Do not use `down -v`. Refresh
`http://127.0.0.1:8000/` afterward; an expired session may require signing in again.
The agent's Windows session still cannot access the Docker engine, so the
container rebuild/start and browser verification of this image require the user's
PowerShell. The currently running container does not hot-reload source changes.

Optional existing-environment settings: `SAAS_DB_POOL_MAX_SIZE` (1–16, default 4),
`SAAS_DB_POOL_TIMEOUT` (1–30 seconds, default 10). Keep a small limit for the
development RDS; more connections do not remove network round-trip latency.
