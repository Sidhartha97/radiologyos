"""Short, membership-validated transactions for the additive SaaS tables."""
from contextlib import contextmanager
from dataclasses import dataclass
import re

from .permissions import SaaSError, Workspace


@dataclass(frozen=True)
class VerifiedIdentity:
    # Only construct from verified Cognito claims, never request JSON.
    issuer: str
    subject: str


def check_runtime_role(conn):
    if getattr(conn,'_saas_role_checked',False) is True: return
    row=conn.execute('''SELECT r.rolsuper,r.rolbypassrls,EXISTS(
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='saas' AND pg_has_role(current_user,c.relowner,'MEMBER'))
        FROM pg_roles r WHERE r.rolname=current_user''').fetchone()
    if not row or any(row):
        raise SaaSError('Use a non-owner database runtime role without RLS bypass.','UnsafeDatabaseRole',503)


def resolve_user(conn, identity):
    if not isinstance(identity,VerifiedIdentity) or not identity.issuer or not identity.subject:
        raise SaaSError('Verified identity required.','Unauthenticated',401)
    conn.execute("SELECT set_config('radiology.issuer',%s,true),set_config('radiology.subject',%s,true)",
                 (identity.issuer,identity.subject))
    row=conn.execute("SELECT set_config('radiology.user_id',user_id::text,true) FROM saas.users WHERE issuer=%s AND subject=%s AND enabled",
                     (identity.issuer,identity.subject)).fetchone()
    if not row: raise SaaSError('Account is not provisioned or has been disabled.')
    user_id=str(row[0])
    return user_id


@contextmanager
def workspace_transaction(connect,identity,tenant_id):
    if not isinstance(tenant_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',tenant_id):
        raise SaaSError('Invalid workspace.','InvalidWorkspace',400)
    with connect() as conn:
        with conn.transaction():
            check_runtime_role(conn)
            user_id=resolve_user(conn,identity)
            member=conn.execute('''SELECT roles,can_sign,can_use_ai FROM saas.tenant_memberships
                WHERE tenant_id=%s AND user_id=%s AND enabled''',(tenant_id,user_id)).fetchone()
            if not member: raise SaaSError('Workspace membership is unavailable.')
            conn.execute("SELECT set_config('radiology.tenant_id',%s,true)",(tenant_id,))
            tenant=conn.execute("SELECT kind FROM saas.tenants WHERE tenant_id=%s AND status='ACTIVE'",(tenant_id,)).fetchone()
            if not tenant: raise SaaSError('Workspace is not active.')
            workspace=Workspace(tenant_id,user_id,tenant[0],frozenset(member[0]),member[1],member[2])
            yield conn,workspace


def list_workspaces(connect,identity):
    with connect() as conn:
        with conn.transaction():
            check_runtime_role(conn)
            user_id=resolve_user(conn,identity)
            rows=conn.execute('SELECT tenant_id,roles FROM saas.tenant_memberships WHERE user_id=%s AND enabled ORDER BY tenant_id',(user_id,)).fetchall()
            result=[]
            for tenant_id,roles in rows:
                conn.execute("SELECT set_config('radiology.tenant_id',%s,true)",(tenant_id,))
                tenant=conn.execute("SELECT name,kind FROM saas.tenants WHERE tenant_id=%s AND status='ACTIVE'",(tenant_id,)).fetchone()
                if tenant: result.append({'tenantId':tenant_id,'name':tenant[0],'kind':tenant[1],'roles':roles})
            return result
