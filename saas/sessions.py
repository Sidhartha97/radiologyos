"""Server-owned workspace selection tied to a verified Cognito access token.

Only token digests are persisted. Callers must verify signature/issuer/client/
expiry before constructing VerifiedIdentity or calling these functions.
"""
from datetime import datetime,timezone
import hashlib

from .permissions import SaaSError
from .tenants import check_runtime_role,resolve_user


def token_digest(token):
    if not isinstance(token,str) or not 16<=len(token)<=16384:
        raise SaaSError('Invalid session.','Unauthenticated',401)
    return hashlib.sha256(token.encode()).hexdigest()


def _session(conn,identity,token,*,lock=True):
    check_runtime_role(conn)
    user_id=resolve_user(conn,identity)
    row=conn.execute('''SELECT s.active_tenant_id FROM saas.sessions s
        LEFT JOIN saas.session_security r ON r.user_id=s.user_id
        WHERE s.token_hash=%s AND s.user_id=%s AND s.revoked_at IS NULL
          AND s.expires_at>clock_timestamp() AND (r.valid_after IS NULL OR s.authenticated_at>r.valid_after)
          '''+(' FOR UPDATE OF s' if lock else ''),(token_digest(token),user_id)).fetchone()
    if not row: raise SaaSError('Sign in again.','SessionExpired',401)
    return user_id,row[0]


def _workspace(conn,user_id,tenant_id):
    # Tenant selection is only accepted after the membership check.
    member=conn.execute('''SELECT roles,can_sign,can_use_ai FROM saas.tenant_memberships
        WHERE tenant_id=%s AND user_id=%s AND enabled''',(tenant_id,user_id)).fetchone()
    if not member: raise SaaSError('Workspace membership is unavailable.')
    conn.execute("SELECT set_config('radiology.tenant_id',%s,true)",(tenant_id,))
    tenant=conn.execute("SELECT name,kind FROM saas.tenants WHERE tenant_id=%s AND status='ACTIVE'",(tenant_id,)).fetchone()
    if not tenant: raise SaaSError('Workspace is not active.')
    return {'tenantId':tenant_id,'name':tenant[0],'kind':tenant[1],
            'roles':member[0],'canSign':member[1],'canUseAi':member[2]}


def create(connect,identity,token,expires_at,authenticated_at=None):
    if type(expires_at) is not int:
        raise SaaSError('Verified token expiry required.','Unauthenticated',401)
    expiry=datetime.fromtimestamp(expires_at,timezone.utc)
    authenticated=datetime.fromtimestamp(authenticated_at,timezone.utc) if type(authenticated_at) is int else None
    with connect() as conn:
        with conn.transaction():
            check_runtime_role(conn)
            user_id=resolve_user(conn,identity)
            if not conn.execute('SELECT %s>clock_timestamp()',(expiry,)).fetchone()[0]:
                raise SaaSError('Token expired.','Unauthenticated',401)
            # Lock the user's security row to serialize login with sign-out-all.
            row=conn.execute('SELECT valid_after FROM saas.session_security WHERE user_id=%s FOR UPDATE',(user_id,)).fetchone()
            if row and (authenticated is None or authenticated<=row[0]):
                raise SaaSError('Sign in again with a fresh password authentication.','SessionExpired',401)
            conn.execute('''INSERT INTO saas.sessions(token_hash,user_id,expires_at,authenticated_at)
                VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING''',(token_digest(token),user_id,expiry,authenticated))
            _session(conn,identity,token)  # A revoked token cannot be recreated.
    return describe(connect,identity,token)


def describe(connect,identity,token,expected_tenant=None):
    with connect() as conn:
        with conn.transaction():
            # Reads still validate revocation/expiry/user on every request.
            # They need not serialize all tabs on the same session row.
            user_id,tenant_id=_session(conn,identity,token,lock=False)
            if expected_tenant is not None and expected_tenant!=tenant_id:
                raise SaaSError('Workspace changed in another tab. Reload before continuing.','WorkspaceChanged',409)
            platform=conn.execute("SELECT 1 FROM saas.platform_roles WHERE user_id=%s AND enabled",(user_id,)).fetchone() is not None
            workspace=None; unavailable=False
            if tenant_id:
                try: workspace=_workspace(conn,user_id,tenant_id)
                except SaaSError as exc:
                    if exc.status!=403: raise
                    unavailable=True
            return {'userId':user_id,'platformAdmin':platform,'workspace':workspace,'workspaceUnavailable':unavailable}


def select_workspace(connect,identity,token,tenant_id):
    if not isinstance(tenant_id,str) or not 1<=len(tenant_id)<=80:
        raise SaaSError('Invalid workspace.','InvalidWorkspace',400)
    with connect() as conn:
        with conn.transaction():
            user_id,_=_session(conn,identity,token)
            result=_workspace(conn,user_id,tenant_id)
            conn.execute('UPDATE saas.sessions SET active_tenant_id=%s WHERE token_hash=%s',(tenant_id,token_digest(token)))
            return result


def revoke(connect,identity,token):
    with connect() as conn:
        with conn.transaction():
            check_runtime_role(conn)
            resolve_user(conn,identity)
            conn.execute('''UPDATE saas.sessions SET revoked_at=clock_timestamp()
                WHERE token_hash=%s AND revoked_at IS NULL''',(token_digest(token),))


def revoke_all(connect,identity):
    with connect() as conn:
        check_runtime_role(conn); user_id=resolve_user(conn,identity)
        conn.execute('''INSERT INTO saas.session_security(user_id,valid_after) VALUES(%s,date_trunc('second',clock_timestamp()))
            ON CONFLICT(user_id) DO UPDATE SET valid_after=greatest(saas.session_security.valid_after,EXCLUDED.valid_after)''',(user_id,))
        conn.execute('UPDATE saas.sessions SET revoked_at=clock_timestamp() WHERE user_id=%s AND revoked_at IS NULL',(user_id,))
