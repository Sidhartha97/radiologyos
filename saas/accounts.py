"""Explicit account provisioning. Use an operator connection, never public input.

Cognito remains the password authority. Employee codes are identifiers scoped
to one hospital, not passwords or authorization grants. No AWS calls on import.
"""
import re
import uuid

from .permissions import SaaSError


def email_address(value):
    value=value.strip().lower() if isinstance(value,str) else ''
    if len(value)>254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',value):
        raise SaaSError('Enter a valid email address.','InvalidEmail',400)
    return value


def employee_code(value):
    value=value.strip().upper() if isinstance(value,str) else ''
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9_-]{0,39}',value):
        raise SaaSError('Employee code must use 1–40 letters, numbers, hyphens or underscores.','InvalidEmployeeCode',400)
    return value


def provision_identity(conn,identity,email,*,email_verified):
    """Only after verifying the subject and email using Cognito AdminGetUser."""
    from .tenants import VerifiedIdentity
    if not isinstance(identity,VerifiedIdentity) or not identity.issuer or not identity.subject:
        raise SaaSError('Verified Cognito identity required.','Unauthenticated',401)
    email=email_address(email)
    if email_verified is not True:
        raise SaaSError('Verify the account email before provisioning.','EmailNotVerified',400)
    conn.execute('''INSERT INTO saas.users(user_id,issuer,subject,email,email_verified)
        VALUES(%s,%s,%s,%s,true) ON CONFLICT(issuer,subject) DO NOTHING''',
        (uuid.uuid4(),identity.issuer,identity.subject,email))
    row=conn.execute('SELECT user_id,email,email_verified,enabled FROM saas.users WHERE issuer=%s AND subject=%s FOR UPDATE',
                     (identity.issuer,identity.subject)).fetchone()
    if not row[3]: raise SaaSError('Account disabled.')
    if row[1] not in (None,email):
        raise SaaSError('Existing account email differs; use a reviewed email-change operation.','IdentityConflict',409)
    conn.execute('UPDATE saas.users SET email=%s,email_verified=true WHERE user_id=%s',(email,row[0]))
    return str(row[0])


def grant_platform_admin(conn,user_id):
    """Operator bootstrap only. No tenant membership or clinical role is created."""
    row=conn.execute('SELECT enabled,email_verified FROM saas.users WHERE user_id=%s FOR UPDATE',(user_id,)).fetchone()
    if not row or not all(row): raise SaaSError('An enabled email-verified account is required.')
    conn.execute("INSERT INTO saas.platform_roles(user_id,role) VALUES(%s,'platform_admin') ON CONFLICT(user_id) DO UPDATE SET enabled=true",(user_id,))


def add_employee(conn,tenant_id,user_id,code,roles,*,can_use_ai=False):
    """Operator admission; serializes seats. Paid entitlement gate comes at API layer.

    Does not grant signing. Codes stay reserved when a member is deactivated to
    avoid silently transferring the identity of a departed employee.
    """
    code=employee_code(code)
    allowed={'hospital_admin','radiologist','transcriptionist'}
    if not isinstance(roles,(list,tuple,set,frozenset)) or not roles or not set(roles)<=allowed:
        raise SaaSError('Choose valid hospital roles.','InvalidRole',400)
    tenant=conn.execute("SELECT kind FROM saas.tenants WHERE tenant_id=%s FOR UPDATE",(tenant_id,)).fetchone()
    if not tenant or tenant[0]!='HOSPITAL': raise SaaSError('Hospital not found.','NotFound',404)
    settings=conn.execute('SELECT max_users FROM saas.tenant_settings WHERE tenant_id=%s FOR UPDATE',(tenant_id,)).fetchone()
    if not settings: raise SaaSError('Hospital seat configuration is unavailable.','MissingEntitlement',409)
    user=conn.execute('SELECT enabled,email_verified FROM saas.users WHERE user_id=%s',(user_id,)).fetchone()
    if not user or not all(user): raise SaaSError('An enabled email-verified identity is required.')
    existing=conn.execute('SELECT employee_code,roles,enabled FROM saas.tenant_memberships WHERE tenant_id=%s AND user_id=%s',(tenant_id,user_id)).fetchone()
    if existing:
        if existing[0]==code and set(existing[1])==set(roles) and existing[2]: return {'employeeCode':code,'created':False}
        raise SaaSError('Member already exists; use a reviewed membership update.','MemberExists',409)
    if conn.execute('SELECT 1 FROM saas.tenant_memberships WHERE tenant_id=%s AND employee_code=%s',(tenant_id,code)).fetchone():
        raise SaaSError('Employee code is already assigned in this hospital.','EmployeeCodeExists',409)
    occupied=conn.execute('SELECT count(*) FROM saas.tenant_memberships WHERE tenant_id=%s AND enabled',(tenant_id,)).fetchone()[0]
    reserved=conn.execute("SELECT count(*) FROM saas.invitations WHERE tenant_id=%s AND status IN ('PENDING','PROVISIONING','INVITED','FAILED') AND expires_at>clock_timestamp()",(tenant_id,)).fetchone()[0]
    if occupied+reserved>=settings[0]: raise SaaSError('No user seats are available.','SeatLimit',409)
    conn.execute('''INSERT INTO saas.tenant_memberships(tenant_id,user_id,roles,employee_code,can_use_ai)
        VALUES(%s,%s,%s,%s,%s)''',(tenant_id,user_id,sorted(set(roles)),code,bool(can_use_ai)))
    conn.execute('''INSERT INTO saas.employee_directory(tenant_id,employee_code,user_id,issuer,cognito_login)
        SELECT %s,%s,user_id,issuer,email FROM saas.users WHERE user_id=%s''',(tenant_id,code,user_id))
    return {'employeeCode':code,'created':True}


def employee_login_identity(conn,tenant_id,code):
    """Backend-only directory lookup for the email-based Cognito pool.

    Return only to the authentication adapter, never to the public client.
    Public failures must be generic and rate limited before this is exposed.
    """
    code=employee_code(code)
    row=conn.execute('''SELECT u.email FROM saas.tenant_memberships m
        JOIN saas.users u USING(user_id) JOIN saas.tenants t USING(tenant_id)
        WHERE m.tenant_id=%s AND m.employee_code=%s AND m.enabled AND u.enabled
          AND u.email_verified AND t.kind='HOSPITAL' AND t.status='ACTIVE' ''',(tenant_id,code)).fetchone()
    if not row or not row[0]: raise SaaSError('Incorrect hospital, employee code or password.','InvalidCredentials',401)
    return row[0]
