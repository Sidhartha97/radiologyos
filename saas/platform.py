"""Business-only platform queries. Never return clinical bodies or patient data."""
from .permissions import SaaSError
from .tenants import check_runtime_role,resolve_user


def require_platform(conn,identity):
    check_runtime_role(conn)
    user_id=resolve_user(conn,identity)
    row=conn.execute('SELECT 1 FROM saas.platform_roles WHERE user_id=%s AND enabled',(user_id,)).fetchone()
    if not row: raise SaaSError('Platform administrator access required.')
    return user_id


def list_tenants(connect,identity,*,limit=50,after=''):
    if type(limit) is not int or not 1<=limit<=100:
        raise SaaSError('Page size must be between 1 and 100.','InvalidPage',400)
    if not isinstance(after,str) or len(after)>80:
        raise SaaSError('Invalid page cursor.','InvalidPage',400)
    with connect() as conn:
        with conn.transaction():
            require_platform(conn,identity)
            rows=conn.execute('''SELECT t.tenant_id,t.name,t.kind,t.status,t.created_at,
                s.max_users,s.allow_hospital_admin_create_users,s.version
                FROM saas.tenants t LEFT JOIN saas.tenant_settings s USING(tenant_id)
                WHERE t.tenant_id>%s ORDER BY t.tenant_id LIMIT %s''',(after,limit+1)).fetchall()
            return {'tenants':[{'tenantId':r[0],'name':r[1],'kind':r[2],'status':r[3],
                'createdAt':r[4].isoformat(),'maxUsers':r[5],'allowStaffCreation':r[6],'version':r[7]} for r in rows[:limit]],
                'nextCursor':rows[limit-1][0] if len(rows)>limit else None}


def update_tenant(connect,identity,tenant_id,payload,request_id):
    import uuid
    name=payload.get('name'); status=payload.get('status'); seats=payload.get('maxUsers')
    staff=payload.get('allowStaffCreation'); version=payload.get('version'); reason=payload.get('reason')
    if not isinstance(name,str) or not 1<=len(name.strip())<=160 or status not in ('PENDING','ACTIVE','SUSPENDED'):
        raise SaaSError('Enter a valid account name and status.','InvalidTenant',400)
    if type(seats) is not int or not 1<=seats<=100000 or type(staff) is not bool or type(version) is not int:
        raise SaaSError('Enter valid seats, staff permission and revision.','InvalidSettings',400)
    if not isinstance(reason,str) or not 1<=len(reason.strip())<=1000:
        raise SaaSError('Give a reason for this administrative change.','ReasonRequired',400)
    with connect() as conn:
        with conn.transaction():
            actor=require_platform(conn,identity)
            tenant=conn.execute('SELECT kind FROM saas.tenants WHERE tenant_id=%s FOR UPDATE',(tenant_id,)).fetchone()
            row=conn.execute('SELECT version FROM saas.tenant_settings WHERE tenant_id=%s FOR UPDATE',(tenant_id,)).fetchone()
            if not tenant or not row: raise SaaSError('Customer configuration not found.','NotFound',404)
            if row[0]!=version: raise SaaSError('Customer configuration changed. Refresh and retry.','StaleSettings',412)
            if tenant[0]=='INDIVIDUAL' and (seats!=1 or staff):
                raise SaaSError('Individual accounts have one seat and cannot create hospital staff.','InvalidSettings',400)
            occupied=conn.execute('SELECT count(*) FROM saas.tenant_memberships WHERE tenant_id=%s AND enabled',(tenant_id,)).fetchone()[0]
            conn.execute("SELECT set_config('radiology.tenant_id',%s,true)",(tenant_id,))
            reserved=conn.execute("SELECT count(*) FROM saas.invitations WHERE tenant_id=%s AND status IN ('PENDING','PROVISIONING','INVITED','FAILED') AND expires_at>clock_timestamp()",(tenant_id,)).fetchone()[0]
            if seats<occupied+reserved: raise SaaSError('Seat allowance cannot be below active members plus reserved invitations.','SeatLimit',409)
            conn.execute('UPDATE saas.tenants SET name=%s,status=%s WHERE tenant_id=%s',(name.strip(),status,tenant_id))
            conn.execute('UPDATE saas.tenant_settings SET max_users=%s,allow_hospital_admin_create_users=%s,version=version+1 WHERE tenant_id=%s',(seats,staff,tenant_id))
            conn.execute("SELECT set_config('radiology.tenant_id',%s,true)",(tenant_id,))
            conn.execute('''INSERT INTO saas.audit_logs(event_id,tenant_id,actor_id,action,object_id,request_id,outcome,reason)
                VALUES(%s,%s,%s,'tenant.configuration_updated',%s,%s,'SUCCESS',%s)''',
                (uuid.uuid4(),tenant_id,actor,tenant_id,request_id,reason.strip()))
    return {'tenantId':tenant_id,'version':version+1,'status':status}


def overview(connect,identity):
    with connect() as conn:
        require_platform(conn,identity)
        return _overview(conn)


def _overview(conn):
    row=conn.execute('''SELECT coalesce((SELECT jsonb_agg(jsonb_build_object(
        'kind',kind,'status',status,'count',total) ORDER BY kind,status)
        FROM (SELECT kind,status,count(*) total FROM saas.tenants GROUP BY kind,status) totals),'[]'::jsonb),
        (SELECT count(*) FROM saas.tenant_memberships WHERE enabled)''').fetchone()
    return {'accounts':row[0],'activeMemberships':row[1]}


def audit_events(connect,identity):
    with connect() as conn:
        require_platform(conn,identity)
        return _audit_events(conn)


def _audit_events(conn):
    rows=conn.execute('''SELECT created_at,tenant_id,actor_id,action,object_id,request_id,outcome
        FROM saas.audit_logs WHERE action IN ('account.created','tenant.configuration_updated','member.access_changed',
        'member.invitation_created','member.invitation_revoked','member.deactivated','member.reactivated',
        'payment.recorded','payment.verified','payment.rejected')
        ORDER BY created_at DESC,event_id DESC LIMIT 100''').fetchall()
    return {'events':[{'time':r[0].isoformat(),'tenantId':r[1],'actorId':str(r[2]) if r[2] else None,
            'action':r[3],'objectId':r[4],'requestId':r[5],'outcome':r[6]} for r in rows]}


def dashboard(connect,identity):
    """One authorization/connection for summary and bounded business-only audit."""
    with connect() as conn:
        require_platform(conn,identity)
        return {'overview':_overview(conn),**_audit_events(conn)}
