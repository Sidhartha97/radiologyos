"""Business-only membership controls. Never returns patient/report content."""
import uuid
from .permissions import SaaSError
from .platform import require_platform


def member_id(value):
    try: return uuid.UUID(str(value))
    except (ValueError,TypeError,AttributeError):
        raise SaaSError('Member not found.','NotFound',404) from None


def roster(connect,identity,tenant):
    with connect() as conn:
        require_platform(conn,identity)
        if not conn.execute('SELECT 1 FROM saas.tenants WHERE tenant_id=%s',(tenant,)).fetchone():
            raise SaaSError('Workspace not found.','NotFound',404)
        rows=conn.execute('''SELECT user_id,employee_code,roles,enabled,can_sign,can_use_ai,version
            FROM saas.tenant_memberships WHERE tenant_id=%s ORDER BY created_at,user_id LIMIT 500''',(tenant,)).fetchall()
        return {'members':[{'userId':str(r[0]),'employeeCode':r[1],'roles':r[2],
                'enabled':r[3],'canSign':r[4],'canUseAi':r[5],'version':r[6]} for r in rows]}


def update(connect,identity,tenant,user,payload,request_id):
    user=member_id(user)
    roles=payload.get('roles'); enabled=payload.get('enabled'); sign=payload.get('canSign'); ai=payload.get('canUseAi')
    version=payload.get('version'); reason=payload.get('reason')
    if not isinstance(roles,list) or not roles or any(not isinstance(r,str) for r in roles):
        raise SaaSError('Choose valid workspace roles.','InvalidRoles',400)
    if len(roles)!=len(set(roles)) or not set(roles)<= {'hospital_admin','radiologist','transcriptionist','individual_owner'}:
        raise SaaSError('Choose valid workspace roles.','InvalidRoles',400)
    if any(type(v) is not bool for v in (enabled,sign,ai)) or type(version) is not int or version<1:
        raise SaaSError('Provide valid access settings and revision.','InvalidAccess',400)
    if not isinstance(reason,str) or not 1<=len(reason.strip())<=1000:
        raise SaaSError('A reason is required for access changes.','ReasonRequired',400)
    if sign and 'radiologist' not in roles:
        raise SaaSError('Signing requires a radiologist role.','InvalidAccess',400)
    if ai and not {'radiologist','transcriptionist'}.intersection(roles):
        raise SaaSError('AI requires a radiologist or transcriptionist role.','InvalidAccess',400)
    if 'radiologist' in roles and 'transcriptionist' in roles:
        raise SaaSError('Choose radiologist or transcriptionist, not both.','InvalidRoles',400)
    with connect() as conn:
        with conn.transaction():
            actor=require_platform(conn,identity)
            kind=conn.execute('SELECT kind FROM saas.tenants WHERE tenant_id=%s',(tenant,)).fetchone()
            settings=conn.execute('SELECT max_users FROM saas.tenant_settings WHERE tenant_id=%s FOR UPDATE',(tenant,)).fetchone()
            if not kind or not settings: raise SaaSError('Workspace not found.','NotFound',404)
            if kind[0]=='INDIVIDUAL' and set(roles)!= {'individual_owner','radiologist'}:
                raise SaaSError('Individual workspaces require owner and radiologist roles.','InvalidRoles',400)
            if kind[0]=='HOSPITAL' and 'individual_owner' in roles:
                raise SaaSError('Individual owner is not a hospital role.','InvalidRoles',400)
            conn.execute("SELECT set_config('radiology.tenant_id',%s,true)",(tenant,))
            previous=conn.execute('''SELECT roles,enabled,version FROM saas.tenant_memberships
                WHERE tenant_id=%s AND user_id=%s FOR UPDATE''',(tenant,user)).fetchone()
            if not previous: raise SaaSError('Member not found.','NotFound',404)
            if previous[2]!=version: raise SaaSError('Access changed. Refresh and retry.','StaleMembership',412)
            if previous[1] and 'hospital_admin' in previous[0] and (not enabled or 'hospital_admin' not in roles):
                count=conn.execute("SELECT count(*) FROM saas.tenant_memberships WHERE tenant_id=%s AND enabled AND 'hospital_admin'=ANY(roles)",(tenant,)).fetchone()[0]
                if count<=1: raise SaaSError('The last hospital administrator must be retained.','LastAdministrator',409)
            if enabled and not previous[1]:
                occupied=conn.execute('SELECT count(*) FROM saas.tenant_memberships WHERE tenant_id=%s AND enabled',(tenant,)).fetchone()[0]
                reserved=conn.execute("SELECT count(*) FROM saas.invitations WHERE tenant_id=%s AND status IN ('PENDING','PROVISIONING','INVITED','FAILED') AND expires_at>clock_timestamp()",(tenant,)).fetchone()[0]
                if occupied+reserved>=settings[0]: raise SaaSError('No staff seats are available.','SeatLimit',409)
            conn.execute('''UPDATE saas.tenant_memberships SET roles=%s,enabled=%s,can_sign=%s,can_use_ai=%s
                WHERE tenant_id=%s AND user_id=%s''',(roles,enabled,sign,ai,tenant,user))
            # Revalidate editing access after any privilege change; preserve report data.
            conn.execute('DELETE FROM saas.report_locks WHERE tenant_id=%s AND user_id=%s',(tenant,user))
            conn.execute('''INSERT INTO saas.audit_logs(event_id,tenant_id,actor_id,action,object_id,request_id,outcome,reason)
                VALUES(%s,%s,%s,'member.access_changed',%s,%s,'SUCCESS',%s)''',
                (uuid.uuid4(),tenant,actor,str(user),request_id,reason.strip()))
    return {'ok':True,'version':version+1}
