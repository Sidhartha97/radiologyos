"""Pending email registration after Cognito verification; operator connection.

This is the transactional provisioning component, not a public signup endpoint.
It does not create Cognito users, activate subscriptions or grant signing/AI.
"""
import uuid
from .accounts import provision_identity
from .permissions import SaaSError


def register(conn,identity,email,kind,name,*,email_verified):
    if kind not in ('HOSPITAL','INDIVIDUAL'):
        raise SaaSError('Choose hospital or individual registration.','InvalidAccountType',400)
    name=name.strip() if isinstance(name,str) else ''
    if not 1<=len(name)<=160:
        raise SaaSError('Enter a name of up to 160 characters.','InvalidName',400)
    # provision_identity locks the user, serializing duplicate registration.
    user=provision_identity(conn,identity,email,email_verified=email_verified)
    existing=conn.execute('SELECT tenant_id FROM saas.registrations WHERE user_id=%s AND kind=%s',(user,kind)).fetchone()
    if existing: return {'tenantId':existing[0],'created':False}
    tenant=('hospital-' if kind=='HOSPITAL' else 'personal-')+uuid.uuid4().hex
    conn.execute('INSERT INTO saas.tenants(tenant_id,name,kind) VALUES(%s,%s,%s)',(tenant,name,kind))
    conn.execute('INSERT INTO saas.tenant_settings(tenant_id) VALUES(%s)',(tenant,))
    roles=['hospital_admin'] if kind=='HOSPITAL' else ['individual_owner','radiologist']
    conn.execute('INSERT INTO saas.tenant_memberships(tenant_id,user_id,roles) VALUES(%s,%s,%s)',(tenant,user,roles))
    conn.execute('INSERT INTO saas.registrations(user_id,kind,tenant_id) VALUES(%s,%s,%s)',(user,kind,tenant))
    return {'tenantId':tenant,'created':True,'status':'PENDING'}
