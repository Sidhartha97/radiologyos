"""Hospital employee invitations and roster. Email is recovery, code is sign-in."""
import uuid
from .accounts import email_address,employee_code
from .permissions import SaaSError,require_user_creation
from .tenants import workspace_transaction


RESERVED=('PENDING','PROVISIONING','INVITED','FAILED')


def expire(conn,tenant):
    conn.execute("UPDATE saas.invitations SET status='EXPIRED' WHERE tenant_id=%s AND status=ANY(%s) AND expires_at<=clock_timestamp()",(tenant,list(RESERVED)))


def roster(conn,workspace):
    workspace.require('users.read')
    rows=conn.execute('SELECT user_id,employee_code,roles,enabled,can_sign,can_use_ai,version FROM saas.tenant_memberships WHERE tenant_id=%s ORDER BY created_at',(workspace.tenant_id,)).fetchall()
    invitations=conn.execute('SELECT invitation_id,employee_code,email,role,status,error_code,expires_at FROM saas.invitations WHERE tenant_id=%s ORDER BY created_at DESC LIMIT 200',(workspace.tenant_id,)).fetchall()
    return {'members':[{'userId':str(r[0]),'employeeCode':r[1],'roles':r[2],'enabled':r[3],'canSign':r[4],'canUseAi':r[5],'version':r[6]} for r in rows],
        'invitations':[{'id':str(r[0]),'employeeCode':r[1],'email':r[2],'role':r[3],'status':r[4],'errorCode':r[5],'expiresAt':r[6].isoformat()} for r in invitations]}


def invite(conn,workspace,payload,request_id):
    email=email_address(payload.get('email')); code=employee_code(payload.get('employeeCode')); role=payload.get('role')
    if role not in ('radiologist','transcriptionist'): raise SaaSError('Choose radiologist or transcriptionist.','InvalidRole',400)
    settings=conn.execute('SELECT max_users,allow_hospital_admin_create_users FROM saas.lock_seats(%s)',(workspace.tenant_id,)).fetchone()
    if not settings: raise SaaSError('User seat configuration is unavailable.','MissingEntitlement',409)
    expire(conn,workspace.tenant_id)
    occupied=conn.execute('SELECT count(*) FROM saas.tenant_memberships WHERE tenant_id=%s AND enabled',(workspace.tenant_id,)).fetchone()[0]
    reserved=conn.execute('SELECT count(*) FROM saas.invitations WHERE tenant_id=%s AND status=ANY(%s) AND expires_at>clock_timestamp()',
                          (workspace.tenant_id,list(RESERVED))).fetchone()[0]
    require_user_creation(workspace,{'max_users':settings[0],'allow_hospital_admin_create_users':settings[1]},occupied,reserved,'users.invite')
    if conn.execute('SELECT 1 FROM saas.tenant_memberships WHERE tenant_id=%s AND employee_code=%s',(workspace.tenant_id,code)).fetchone():
        raise SaaSError('Employee code is already assigned.','EmployeeCodeExists',409)
    if conn.execute('SELECT 1 FROM saas.invitations WHERE tenant_id=%s AND employee_code=%s',(workspace.tenant_id,code)).fetchone():
        raise SaaSError('An invitation already exists for this employee code.','InvitationExists',409)
    invitation=str(uuid.uuid4())
    conn.execute('INSERT INTO saas.invitations(invitation_id,tenant_id,employee_code,email,role,invited_by) VALUES(%s,%s,%s,%s,%s,%s)',
                 (invitation,workspace.tenant_id,code,email,role,workspace.user_id))
    audit(conn,workspace,'member.invitation_created',invitation,request_id)
    return {'id':invitation,'status':'PENDING','employeeCode':code}


def audit(conn,workspace,action,object_id,request_id):
    conn.execute('''INSERT INTO saas.audit_logs(event_id,tenant_id,actor_id,action,object_id,request_id,outcome)
        VALUES(%s,%s,%s,%s,%s,%s,'SUCCESS')''',(uuid.uuid4(),workspace.tenant_id,workspace.user_id,action,object_id,request_id))


def deactivate(conn,workspace,user_id,request_id):
    workspace.require('users.deactivate')
    try: user_id=uuid.UUID(user_id)
    except (ValueError,TypeError): raise SaaSError('Member not found.','NotFound',404) from None
    conn.execute('SELECT max_users FROM saas.lock_seats(%s)',(workspace.tenant_id,))
    member=conn.execute('SELECT roles,enabled FROM saas.tenant_memberships WHERE tenant_id=%s AND user_id=%s FOR UPDATE',(workspace.tenant_id,user_id)).fetchone()
    if not member: raise SaaSError('Member not found.','NotFound',404)
    if member[1] and 'hospital_admin' in member[0]:
        count=conn.execute("SELECT count(*) FROM saas.tenant_memberships WHERE tenant_id=%s AND enabled AND 'hospital_admin'=ANY(roles)",(workspace.tenant_id,)).fetchone()[0]
        if count<=1: raise SaaSError('The last hospital administrator cannot be deactivated.','LastAdministrator',409)
    conn.execute('UPDATE saas.tenant_memberships SET enabled=false WHERE tenant_id=%s AND user_id=%s',(workspace.tenant_id,user_id))
    conn.execute('DELETE FROM saas.report_locks WHERE tenant_id=%s AND user_id=%s',(workspace.tenant_id,user_id))
    audit(conn,workspace,'member.deactivated',str(user_id),request_id)
    return {'ok':True}


def reactivate(conn,workspace,user_id,version,request_id):
    workspace.require('users.edit')
    from .access import member_id
    user_id=member_id(user_id)
    if type(version) is not int or version<1: raise SaaSError('Provide the current member revision.','InvalidRevision',400)
    settings=conn.execute('SELECT max_users FROM saas.lock_seats(%s)',(workspace.tenant_id,)).fetchone()
    row=conn.execute('SELECT enabled,version FROM saas.tenant_memberships WHERE tenant_id=%s AND user_id=%s FOR UPDATE',(workspace.tenant_id,user_id)).fetchone()
    if not row: raise SaaSError('Member not found.','NotFound',404)
    if row[1]!=version: raise SaaSError('Member changed. Refresh and retry.','StaleMembership',412)
    if not row[0]:
        expire(conn,workspace.tenant_id)
        occupied=conn.execute('SELECT count(*) FROM saas.tenant_memberships WHERE tenant_id=%s AND enabled',(workspace.tenant_id,)).fetchone()[0]
        reserved=conn.execute('SELECT count(*) FROM saas.invitations WHERE tenant_id=%s AND status=ANY(%s) AND expires_at>clock_timestamp()',
                              (workspace.tenant_id,list(RESERVED))).fetchone()[0]
        if not settings or occupied+reserved>=settings[0]: raise SaaSError('No staff seats are available.','SeatLimit',409)
        conn.execute('UPDATE saas.tenant_memberships SET enabled=true WHERE tenant_id=%s AND user_id=%s',(workspace.tenant_id,user_id))
        audit(conn,workspace,'member.reactivated',str(user_id),request_id)
    return {'ok':True}


def revoke_invitation(conn,workspace,invitation_id,request_id):
    workspace.require('users.invite')
    from .access import member_id
    invitation_id=member_id(invitation_id)
    conn.execute('SELECT max_users FROM saas.lock_seats(%s)',(workspace.tenant_id,))
    row=conn.execute('SELECT status FROM saas.invitations WHERE tenant_id=%s AND invitation_id=%s FOR UPDATE',
                     (workspace.tenant_id,invitation_id)).fetchone()
    if not row: raise SaaSError('Invitation not found.','NotFound',404)
    if row[0]=='ACCEPTED': raise SaaSError('Deactivate the member instead of revoking an accepted invitation.','AlreadyAccepted',409)
    if row[0]!='REVOKED':
        conn.execute("UPDATE saas.invitations SET status='REVOKED',delivery_token=NULL,lease_until=NULL WHERE tenant_id=%s AND invitation_id=%s",(workspace.tenant_id,invitation_id))
        audit(conn,workspace,'member.invitation_revoked',str(invitation_id),request_id)
    return {'ok':True}


def deliver(connect,identity,tenant,invitation_id,client,pool,issuer):
    """Persist delivery intent, call Cognito outside transaction, then record outcome."""
    delivery_token=uuid.uuid4()
    with workspace_transaction(connect,identity,tenant) as (conn,workspace):
        workspace.require('users.invite')
        settings=conn.execute('SELECT allow_hospital_admin_create_users FROM saas.lock_seats(%s)',(tenant,)).fetchone()
        if not settings or not settings[0]: raise SaaSError('Staff creation is disabled.')
        row=conn.execute('''UPDATE saas.invitations SET status='PROVISIONING',lease_until=clock_timestamp()+interval '5 minutes',error_code=NULL,delivery_token=%s
            WHERE tenant_id=%s AND invitation_id=%s AND expires_at>clock_timestamp()
              AND (status IN ('PENDING','FAILED') OR (status='PROVISIONING' AND lease_until<clock_timestamp()))
            RETURNING email''',(delivery_token,tenant,invitation_id)).fetchone()
        if not row: raise SaaSError('Invitation is unavailable or already being delivered.','InvitationBusy',409)
    error=None; subject=None; existing=False
    try:
        try:
            response=client.admin_create_user(UserPoolId=pool,Username=row[0],UserAttributes=[{'Name':'email','Value':row[0]}],DesiredDeliveryMediums=['EMAIL'])
            attributes=response['User']['Attributes']
        except Exception as exc:
            if getattr(exc,'response',{}).get('Error',{}).get('Code')!='UsernameExistsException': raise
            existing=True
            attributes=client.admin_get_user(UserPoolId=pool,Username=row[0])['UserAttributes']
        values={a['Name']:a['Value'] for a in attributes}
        if email_address(values.get('email'))!=row[0] or not values.get('sub'): raise ValueError('Identity mismatch')
        subject=values['sub']
    except Exception as exc:
        raw=getattr(exc,'response',{}).get('Error',{}).get('Code',type(exc).__name__)
        error=raw if isinstance(raw,str) and raw.isalnum() and len(raw)<=80 else 'ProvisioningFailed'
    with workspace_transaction(connect,identity,tenant) as (conn,workspace):
        completed=conn.execute('''UPDATE saas.invitations SET status=%s,cognito_subject=%s,cognito_issuer=%s,error_code=%s,lease_until=NULL,delivery_token=NULL
            WHERE tenant_id=%s AND invitation_id=%s AND status='PROVISIONING' AND delivery_token=%s RETURNING invitation_id''',
            ('FAILED' if error else 'INVITED',subject,issuer,error,tenant,invitation_id,delivery_token)).fetchone()
        if not completed: raise SaaSError('Invitation delivery was superseded.','JobLeaseLost',409)
    return {'status':'FAILED' if error else 'INVITED','errorCode':error,'existingAccount':existing}
