"""Super-admin account invitations. Cognito remains the password authority."""
import uuid
from psycopg.types.json import Jsonb
from .accounts import email_address
from .platform import require_platform
from .permissions import SaaSError


def create(connect,identity,payload,request_id):
    email=email_address(payload.get('email')); kind=payload.get('kind'); name=payload.get('name')
    reason=payload.get('reason'); seats=payload.get('maxUsers',1); staff=payload.get('allowStaffCreation',False)
    status=payload.get('status','PENDING')
    if kind not in ('HOSPITAL','INDIVIDUAL') or not isinstance(name,str) or not 1<=len(name.strip())<=160:
        raise SaaSError('Choose account type and enter a valid workspace name.','InvalidAccount',400)
    if not isinstance(reason,str) or not 1<=len(reason.strip())<=1000:
        raise SaaSError('Give a reason for creating the account.','ReasonRequired',400)
    if type(seats) is not int or not 1<=seats<=100000 or type(staff) is not bool or status not in ('PENDING','ACTIVE'):
        raise SaaSError('Provide valid account settings.','InvalidSettings',400)
    if kind=='INDIVIDUAL' and (seats!=1 or staff): raise SaaSError('Individual accounts have one member and no hospital staff creation.','InvalidSettings',400)
    snapshot={'email':email,'name':name.strip(),'kind':kind,'maxUsers':seats,'allowStaffCreation':staff,'status':status,'reason':reason.strip()}
    try: operation=uuid.UUID(str(payload.get('requestId')))
    except (ValueError,TypeError,AttributeError): raise SaaSError('Provide a unique request identifier.','InvalidRequest',400) from None
    with connect() as conn:
        with conn.transaction():
            actor=require_platform(conn,identity)
            # Serialize duplicate create requests without storing passwords/tokens.
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(actor+str(operation),))
            previous=conn.execute('''SELECT invitation_id,tenant_id,creation_snapshot
                FROM saas.account_invitations WHERE created_by=%s AND request_id=%s''',(actor,operation)).fetchone()
            if previous:
                if previous[2]!=snapshot:
                    raise SaaSError('Request identifier was already used for different account details.','IdempotencyConflict',409)
                return {'invitationId':str(previous[0]),'tenantId':previous[1],'created':False}
            tenant=('hospital-' if kind=='HOSPITAL' else 'personal-')+uuid.uuid4().hex
            invitation=uuid.uuid4()
            conn.execute('INSERT INTO saas.tenants(tenant_id,name,kind,status) VALUES(%s,%s,%s,%s)',(tenant,name.strip(),kind,status))
            conn.execute('INSERT INTO saas.tenant_settings(tenant_id,max_users,allow_hospital_admin_create_users) VALUES(%s,%s,%s)',(tenant,seats,staff))
            conn.execute('''INSERT INTO saas.account_invitations(invitation_id,tenant_id,request_id,created_by,email,reason,creation_snapshot)
                VALUES(%s,%s,%s,%s,%s,%s,%s)''',(invitation,tenant,operation,actor,email,reason.strip(),Jsonb(snapshot)))
            conn.execute("SELECT set_config('radiology.tenant_id',%s,true)",(tenant,))
            conn.execute('''INSERT INTO saas.audit_logs(event_id,tenant_id,actor_id,action,object_id,request_id,outcome,reason)
                VALUES(%s,%s,%s,'account.created',%s,%s,'SUCCESS',%s)''',(uuid.uuid4(),tenant,actor,str(invitation),request_id,reason.strip()))
    return {'tenantId':tenant,'invitationId':str(invitation),'created':True,'status':status}


def list_invitations(connect,identity):
    with connect() as conn:
        require_platform(conn,identity)
        rows=conn.execute('''SELECT invitation_id,tenant_id,email,status,error_code,expires_at
            FROM saas.account_invitations ORDER BY created_at DESC LIMIT 200''').fetchall()
        return {'invitations':[{'id':str(r[0]),'tenantId':r[1],'email':r[2],'status':r[3],
                'errorCode':r[4],'expiresAt':r[5].isoformat()} for r in rows]}


def deliver(connect,identity,invitation_id,client,pool,issuer):
    lease=uuid.uuid4()
    with connect() as conn:
        require_platform(conn,identity)
        row=conn.execute('''UPDATE saas.account_invitations SET status='PROVISIONING',delivery_token=%s,
            lease_until=clock_timestamp()+interval '5 minutes',error_code=NULL WHERE invitation_id=%s
            AND expires_at>clock_timestamp() AND (status IN ('PENDING','FAILED') OR
              (status='PROVISIONING' AND lease_until<clock_timestamp())) RETURNING email''',(lease,invitation_id)).fetchone()
        if not row: raise SaaSError('Invitation unavailable or already being delivered.','InvitationBusy',409)
    error=None; subject=None
    try:
        try:
            response=client.admin_create_user(UserPoolId=pool,Username=row[0],UserAttributes=[{'Name':'email','Value':row[0]}],DesiredDeliveryMediums=['EMAIL'])
            attributes=response['User']['Attributes']
        except Exception as exc:
            if getattr(exc,'response',{}).get('Error',{}).get('Code')!='UsernameExistsException': raise
            response=client.admin_get_user(UserPoolId=pool,Username=row[0])
            if response.get('Enabled') is False: raise ValueError('Identity unavailable')
            attributes=response['UserAttributes']
        values={a['Name']:a['Value'] for a in attributes}
        if email_address(values.get('email'))!=row[0] or not values.get('sub'): raise ValueError('Identity mismatch')
        subject=values['sub']
    except Exception as exc:
        raw=getattr(exc,'response',{}).get('Error',{}).get('Code',type(exc).__name__)
        error=raw if isinstance(raw,str) and raw.isalnum() and len(raw)<=80 else 'ProvisioningFailed'
    with connect() as conn:
        require_platform(conn,identity)
        completed=conn.execute('''UPDATE saas.account_invitations SET status=%s,cognito_issuer=%s,cognito_subject=%s,
            error_code=%s,lease_until=NULL,delivery_token=NULL WHERE invitation_id=%s AND status='PROVISIONING'
            AND delivery_token=%s RETURNING invitation_id''',('FAILED' if error else 'INVITED',issuer,subject,error,invitation_id,lease)).fetchone()
        if not completed: raise SaaSError('Invitation delivery was superseded.','JobLeaseLost',409)
    return {'status':'FAILED' if error else 'INVITED','errorCode':error}
