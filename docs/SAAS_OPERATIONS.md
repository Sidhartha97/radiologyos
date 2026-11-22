# SaaS foundation: local development

The optional SaaS API/UI is wired but disabled by default. Follow `SAAS_CONTINUE.md` for the current checkpoint. Existing AWS settings and RAG behavior are preserved. These local instructions are not permission to enable SaaS on RDS.

## Start the isolated test database

From the project folder in your own PowerShell terminal, use ONE of these options.

Option A (the command already shared in chat):

```powershell
docker run -d --name radiologyos-saas-test -e POSTGRES_PASSWORD=local-test-only -e POSTGRES_DB=radiology_saas_test -p 127.0.0.1:55432:5432 postgres:16
docker exec radiologyos-saas-test pg_isready -U postgres -d radiology_saas_test
```

Option B (separate Compose file; do not start both options on the same port):

```powershell
docker compose -f compose.saas-test.yml up -d --wait
```

This is a local PostgreSQL 16 test database on port 55432. The fixed password is only a disposable test credential. It is not used for the application or AWS. Option B keeps its data in memory and loses it when stopped.

## Run tests

Use a Python environment with the project's existing requirements installed. The tests do not load `.env` or fetch AWS secrets.

```powershell
python -m unittest discover -s tests
$env:SAAS_TEST_DATABASE_URL = 'postgresql://postgres:local-test-only@127.0.0.1:55432/radiology_saas_test'
python -m unittest discover -s tests -p 'test_saas_postgres.py' -v
```

The second command creates a uniquely named `radiology_saas_it_*` database and `saas_test_*` runtime role and removes only those generated test targets afterward. It refuses non-loopback hosts and requires the connection database name to begin with `radiology_saas_test`. It never connects to RDS. All fixtures are synthetic.

If Windows prevents temporary test files from being written, use a writable project-local temporary directory:

```powershell
New-Item -ItemType Directory -Force .testdeps\tmp | Out-Null
$env:TEMP = (Resolve-Path .testdeps\tmp).Path
$env:TMP = $env:TEMP
```

## Inspect and apply the foundation locally

Use a separate variable for migrations. The command deliberately ignores `DATABASE_URL`, `.env` and AWS secrets to prevent selecting the live database accidentally.

```powershell
$env:SAAS_MIGRATION_DATABASE_URL = 'postgresql://postgres:local-test-only@127.0.0.1:55432/radiology_saas_test'
python scripts/manage_schema.py status
python scripts/manage_schema.py preflight
python scripts/manage_schema.py apply --confirm-database radiology_saas_test
python scripts/manage_schema.py backfill --confirm-database radiology_saas_test
```

On an empty test database, backfill legitimately imports zero records. In the integration suite, synthetic legacy records prove the backfill behavior. It rejects mismatched ownership, missing patient links, duplicate report IDs and unknown statuses rather than guessing.

The foundation import does not grant signing/AI privileges to legacy admins. Approved role assignment is part of the upcoming authentication integration.

`grant-runtime --runtime-role <existing_role> --confirm-database <database>` grants SaaS tables, tenant-scoped legacy clinical tables and narrowly scoped directory/seat/invitation functions to a dedicated existing runtime login. It rejects superuser/BYPASSRLS roles and inherited SaaS table ownership. It does not create a login or expose its password. Review function ownership/RLS behavior before RDS rollout.

## Troubleshooting

- `postgres:16.` not found: remove the trailing full stop; the image is `postgres:16`.
- Container name exists: inspect `docker ps -a --filter name=radiologyos-saas-test`. If it is the test container above and stopped, use `docker start radiologyos-saas-test`.
- Connection refused/timeout: verify the test container is ready and port 55432 is published. Do not change the application RDS security group for this local test.
- `UnsafeDatabaseRole`: the new runtime service requires a dedicated non-owner role. Migration credentials and runtime credentials have different purposes.
- Checksum mismatch: an already-applied migration changed. Restore the original file and create a new numbered migration; do not overwrite the recorded checksum.
- Preflight fails: resolve ownership/structure explicitly before backfilling. Preflight output intentionally omits patient names and report bodies.

## Stop local tests

For option A:

```powershell
docker stop radiologyos-saas-test
```

For option B:

```powershell
docker compose -f compose.saas-test.yml down
```

These commands refer only to the isolated test setup, not `docker-compose.yml` or AWS.

## Deployment and rollback

No live migration is authorized or executed by these local instructions. Remote database commands require `--allow-remote` plus exact database confirmation. Obtain rollout approval and review the target/runtime role before using them. There is intentionally no automatic startup migration and no destructive down-migration command.

Migration 006 activates RLS on the legacy clinical tables. After applying it, reverting to baseline code or disabling SaaS is not a supported rollback: those legacy connections do not establish the new scope. Use a compatible context-aware release/forward migration. Never use owner credentials to bypass the new protections. Application rollback does not reverse memberships, signed history or ledger entries.
