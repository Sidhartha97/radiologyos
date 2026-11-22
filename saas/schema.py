"""Explicit migrations and conservative legacy preflight. No application imports."""
import hashlib
import json
import re
import uuid
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / 'migrations'
LOCK_ID = 7262341901


def files():
    return sorted(MIGRATIONS.glob('[0-9][0-9][0-9]_*.sql'))


def digest(path):
    # Git may check out CRLF on Windows; identical SQL must keep its checksum.
    return hashlib.sha256(path.read_text(encoding='utf-8').encode('utf-8')).hexdigest()


def table_exists(conn, name):
    return conn.execute('SELECT to_regclass(%s)', (name,)).fetchone()[0] is not None


def migration_status(conn):
    applied = {}
    if table_exists(conn, 'public.saas_schema_migrations'):
        applied = dict(conn.execute('SELECT version,checksum FROM public.saas_schema_migrations').fetchall())
    known = {p.name for p in files()}
    if set(applied)-known:
        raise ValueError('Database has migrations newer than this application. Use a compatible release.')
    result = []
    for path in files():
        checksum = digest(path)
        if path.name in applied and applied[path.name] != checksum:
            raise ValueError('Applied migration checksum differs: '+path.name)
        result.append({'version': path.name, 'status': 'APPLIED' if path.name in applied else 'PENDING'})
    return result


def apply(conn):
    """All pending migrations commit together; caller supplies an explicit transaction."""
    conn.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK_ID,))
    pending = migration_status(conn)
    conn.execute('''CREATE TABLE IF NOT EXISTS public.saas_schema_migrations (
        version text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())''')
    conn.execute('REVOKE ALL ON public.saas_schema_migrations FROM PUBLIC')
    for entry in pending:
        if entry['status'] == 'APPLIED':
            continue
        path = MIGRATIONS / entry['version']
        conn.execute(path.read_text(encoding='utf-8'))
        conn.execute('INSERT INTO public.saas_schema_migrations(version,checksum) VALUES(%s,%s)',
                     (path.name, digest(path)))
    return migration_status(conn)


def legacy_rows(conn):
    memberships = conn.execute('SELECT issuer,subject,tenant_id,role,enabled FROM public.hospital_memberships').fetchall() if table_exists(conn, 'public.hospital_memberships') else []
    states = conn.execute('SELECT tenant_id,payload FROM public.hospital_state').fetchall() if table_exists(conn, 'public.hospital_state') else []
    return memberships, states


def inspect_legacy(memberships, states):
    """Return structural counts/issues only; never include clinical content."""
    errors, tenant_ids = [], set()
    reports = 0
    for issuer, subject, tenant, role, enabled in memberships:
        tenant_ids.add(tenant)
        if not issuer or not subject or role not in ('admin','radiologist'):
            errors.append('Invalid legacy membership identity or role.')
    for tenant, state in states:
        tenant_ids.add(tenant)
        if not isinstance(state, dict):
            errors.append('Invalid tenant state object.'); continue
        patients = state.get('patients', [])
        report_list = state.get('reports', [])
        if not isinstance(patients, list) or not isinstance(report_list, list):
            errors.append('Invalid patient/report collection.'); continue
        patient_ids, report_ids = set(), set()
        for patient in patients:
            if not isinstance(patient, dict) or not isinstance(patient.get('id'),str) or not patient['id']:
                errors.append('Invalid patient ID.'); continue
            if patient['id'] in patient_ids: errors.append('Duplicate patient ID within tenant.')
            patient_ids.add(patient['id'])
            if patient.get('tenantId',tenant) != tenant: errors.append('Patient tenant ownership mismatch.')
        for report in report_list:
            if not isinstance(report,dict) or not isinstance(report.get('id'),str) or not 1<=len(report['id'])<=128:
                errors.append('Invalid report ID.'); continue
            if report['id'] in report_ids: errors.append('Duplicate report ID within tenant.')
            report_ids.add(report['id']); reports += 1
            if not isinstance(report.get('patientId'),str) or report['patientId'] not in patient_ids:
                errors.append('Report references missing patient.')
            if report.get('tenantId',tenant) != tenant: errors.append('Report tenant ownership mismatch.')
            if report.get('status') not in ('Draft','Final'): errors.append('Unknown legacy report status.')
    if any(not isinstance(t,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',t) for t in tenant_ids):
        errors.append('Invalid tenant ID.')
    return {'tenants':len(tenant_ids), 'memberships':len(memberships), 'reports':reports,
            'issues':sorted(set(errors)), 'ready':not errors}


def backfill(conn):
    """Operator-only import, additive and idempotent. Never invent signing rights."""
    conn.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK_ID,))
    for name in ('hospital_memberships','hospital_state'):
        if table_exists(conn,'public.'+name):
            conn.execute('LOCK TABLE public.'+name+' IN SHARE MODE')
    members, states = legacy_rows(conn)
    report = inspect_legacy(members,states)
    if not report['ready']:
        raise ValueError('Legacy preflight failed: '+'; '.join(report['issues']))
    # Backfill is an operator operation requiring a migration principal that can
    # bypass RLS. Runtime identities are never granted this permission.
    for tenant in sorted({x[2] for x in members} | {x[0] for x in states}):
        conn.execute("INSERT INTO saas.tenants(tenant_id,name,kind,status) VALUES(%s,%s,'HOSPITAL','ACTIVE') ON CONFLICT DO NOTHING",(tenant,tenant))
        conn.execute('INSERT INTO saas.tenant_settings(tenant_id,max_users) VALUES(%s,%s) ON CONFLICT DO NOTHING',
                     (tenant,max(1,sum(1 for x in members if x[2]==tenant and x[4]))))
    for issuer, subject, tenant, role, enabled in members:
        user_id = uuid.uuid5(uuid.NAMESPACE_URL, json.dumps([issuer,subject],separators=(',',':')))
        conn.execute('INSERT INTO saas.users(user_id,issuer,subject) VALUES(%s,%s,%s) ON CONFLICT(issuer,subject) DO NOTHING',(user_id,issuer,subject))
        user_id = conn.execute('SELECT user_id FROM saas.users WHERE issuer=%s AND subject=%s',(issuer,subject)).fetchone()[0]
        conn.execute('''INSERT INTO saas.tenant_memberships(tenant_id,user_id,roles,enabled)
            VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
            (tenant,user_id,['hospital_admin' if role=='admin' else 'radiologist'],enabled))
    for tenant, state in states:
        for item in state.get('reports',[]):
            final = item['status']=='Final'
            existing=conn.execute('SELECT patient_id FROM saas.report_controls WHERE tenant_id=%s AND report_id=%s',(tenant,item['id'])).fetchone()
            if existing and existing[0]!=item['patientId']:
                raise ValueError('Existing report-control ownership conflicts with legacy state.')
            conn.execute('''INSERT INTO saas.report_controls(tenant_id,report_id,patient_id,workflow_status,legacy_final)
                VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                (tenant,item['id'],item['patientId'],'FINALIZED' if final else 'DRAFT',final))
    return report


def grant_runtime(conn, role):
    """Grant new-table rights to an existing dedicated login, never owner credentials."""
    from psycopg import sql
    if not re.fullmatch(r'[a-z][a-z0-9_]{1,62}',role):
        raise ValueError('Invalid runtime role name.')
    row=conn.execute('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=%s',(role,)).fetchone()
    if not row or any(row): raise ValueError('Runtime role must exist without superuser or BYPASSRLS.')
    owned=conn.execute("SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='saas' AND pg_has_role(%s,c.relowner,'MEMBER') LIMIT 1",(role,)).fetchone()
    if owned: raise ValueError('Runtime role must not own or inherit ownership of SaaS tables.')
    identifier=sql.Identifier(role)
    conn.execute(sql.SQL('GRANT USAGE ON SCHEMA saas TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT SELECT ON saas.tenants,saas.users,saas.tenant_memberships,saas.tenant_settings,saas.platform_roles TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE ON saas.report_controls,saas.report_assignments TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE ON saas.report_locks TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT SELECT,INSERT ON saas.audit_logs TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE ON saas.sessions TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT SELECT,INSERT ON saas.report_events,saas.signed_reports TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE ON saas.publication_jobs TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT SELECT ON saas.registrations TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT SELECT ON public.saas_schema_migrations TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE ON public.hospital_state TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT SELECT ON public.hospital_report_versions TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE ON public.patient_conversations TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT EXECUTE ON FUNCTION saas.employee_login(text,text,text) TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT UPDATE(name,status) ON saas.tenants TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT UPDATE(max_users,allow_hospital_admin_create_users,version) ON saas.tenant_settings TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE ON saas.invitations TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT UPDATE(enabled) ON saas.tenant_memberships TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT EXECUTE ON FUNCTION saas.hospital_admin(text,text),saas.accept_verified_invitations(text,text,text) TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT EXECUTE ON FUNCTION saas.lock_seats(text) TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE ON saas.wallets,saas.credit_lots,saas.reservations TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT SELECT,INSERT ON saas.reservation_lots,saas.wallet_ledger TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT SELECT,INSERT ON saas.usage_receipts TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT UPDATE(roles,can_sign,can_use_ai) ON saas.tenant_memberships TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT INSERT ON saas.tenants,saas.tenant_settings TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE ON saas.account_invitations TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT EXECUTE ON FUNCTION saas.accept_account_invitation(text,text,text) TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT EXECUTE ON FUNCTION saas.accept_platform_invitation(text,text,text) TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT EXECUTE ON FUNCTION saas.clinical_assignees(text) TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE ON saas.report_conversations TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT EXECUTE ON FUNCTION saas.report_chat_allowed(text,text) TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT EXECUTE ON FUNCTION saas.review_radiologists(text) TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE ON saas.model_token_events TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE ON saas.manual_payments TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE ON saas.session_security TO {}').format(identifier))
    conn.execute(sql.SQL('GRANT EXECUTE ON FUNCTION saas.customer_deletion(text,text,uuid,uuid,jsonb) TO {}').format(identifier))
    return {'runtimeRole':role,'scope':'SaaS and tenant-scoped clinical tables'}
