"""Shared local demo, synthetic identities ONLY. Never deploy this mock login.

Uses real scoped SQL/API/workflow and a disposable local database. No AWS/.env.
Type stop on stdin for exact test database/role cleanup.
"""
import os
import sys
import time
import uuid
import threading
import argparse
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
PASSWORD='DemoOnly123!'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--smoke-test',action='store_true',help='Run multi-account HTTP checks, then clean up and exit.')
    parser.add_argument('--serve-seconds',type=int,default=0,help='Keep the loopback demo open for 1–3600 seconds, then clean up automatically.')
    args=parser.parse_args()
    if args.serve_seconds and not 1<=args.serve_seconds<=3600: parser.error('--serve-seconds must be between 1 and 3600')
    if not os.getenv('SAAS_TEST_DATABASE_URL'): raise SystemExit('Set SAAS_TEST_DATABASE_URL to the isolated local test database first.')
    from test_saas_postgres import PostgresFoundationTests
    from saas import accounts
    from saas.tenants import VerifiedIdentity
    from tenant_store import empty_state
    from psycopg.types.json import Jsonb
    from demo_library import DemoLibrary
    PostgresFoundationTests.setUpClass(); case=PostgresFoundationTests()
    directory={}; tokens={}; mutex=threading.Lock()
    class DemoError(Exception):
        def __init__(self,code): self.response={'Error':{'Code':code}}
    def add(email):
        subject=uuid.uuid4().hex; directory[email]=subject; return VerifiedIdentity('issuer',subject)
    def attributes(email):
        return [{'Name':'sub','Value':directory[email]},{'Name':'email','Value':email},{'Name':'email_verified','Value':'true'}]
    try:
        identities={name:add(name+'@example.test') for name in ('superadmin','hospitaladmin','radiologist','transcriptionist','individual')}
        with case.owner() as conn:
            users={name:accounts.provision_identity(conn,identity,name+'@example.test',email_verified=True) for name,identity in identities.items()}
            accounts.grant_platform_admin(conn,users['superadmin'])
            for tenant,name,kind,seats in [('demo-hospital','Demo Hospital','HOSPITAL',10),('demo-individual','Demo Individual','INDIVIDUAL',1)]:
                conn.execute("INSERT INTO saas.tenants(tenant_id,name,kind,status) VALUES(%s,%s,%s,'ACTIVE')",(tenant,name,kind))
                conn.execute('INSERT INTO saas.tenant_settings(tenant_id,max_users,allow_hospital_admin_create_users) VALUES(%s,%s,%s)',(tenant,seats,kind=='HOSPITAL'))
                state=empty_state(); state['patients']=[{'id':'demo-patient','name':'Synthetic Demo Patient','mrn':'DEMO-001','dob':'1990-01-01','sex':'M','phone':''}]
                state['reports']=[{'id':'demo-report','patientId':'demo-patient','modality':'CT','body':'<h3>FINDINGS</h3><p>Synthetic report for workflow testing only.</p>','status':'Draft','updatedAt':'2026-10-13T00:00:00'}]
                conn.execute('INSERT INTO public.hospital_state(tenant_id,payload) VALUES(%s,%s)',(tenant,Jsonb(state)))
                conn.execute("INSERT INTO saas.report_controls(tenant_id,report_id,patient_id) VALUES(%s,'demo-report','demo-patient')",(tenant,))
            for name,code,role in [('hospitaladmin','ADMIN001','hospital_admin'),('radiologist','DR001','radiologist'),('transcriptionist','TX001','transcriptionist')]:
                accounts.add_employee(conn,'demo-hospital',users[name],code,[role])
            conn.execute("UPDATE saas.tenant_memberships SET can_sign=true WHERE tenant_id='demo-hospital' AND user_id=%s",(users['radiologist'],))
            conn.execute("UPDATE saas.tenant_memberships SET can_use_ai=true WHERE tenant_id='demo-hospital' AND user_id IN (%s,%s)",(users['radiologist'],users['transcriptionist']))
            conn.execute("INSERT INTO saas.tenant_memberships(tenant_id,user_id,roles,can_sign) VALUES('demo-individual',%s,ARRAY['individual_owner','radiologist'],true)",(users['individual'],))
            conn.execute("INSERT INTO saas.report_assignments(tenant_id,report_id,user_id) VALUES('demo-hospital','demo-report',%s)",(users['transcriptionist'],))
        request,gateway,provider=case._http(identities['superadmin'],prelogin=False,demo_library=DemoLibrary())
        def initiate(**kwargs):
            email=kwargs['AuthParameters']['USERNAME']; password=kwargs['AuthParameters']['PASSWORD']
            with mutex:
                if email not in directory or password!=PASSWORD: raise DemoError('NotAuthorizedException')
                token='demo-'+uuid.uuid4().hex; tokens[token]=email
            return {'AuthenticationResult':{'AccessToken':token}}
        def verify(token):
            with mutex:
                email=tokens.get(token)
                if not email: raise DemoError('NotAuthorizedException')
                return {'iss':'issuer','sub':directory[email],'exp':int(time.time())+3600,'auth_time':int(time.time())}
        def get_user(**kwargs):
            with mutex: return {'UserAttributes':attributes(tokens[kwargs['AccessToken']])}
        def create_user(**kwargs):
            email=kwargs['Username']
            email=accounts.email_address(email)  # Local identity only; no email/provider call.
            with mutex:
                if email in directory: raise DemoError('UsernameExistsException')
                add(email)
                return {'User':{'Attributes':attributes(email)}}
        def existing_user(**kwargs):
            with mutex: return {'Enabled':True,'UserAttributes':attributes(kwargs['Username'])}
        provider.initiate_auth.side_effect=initiate; provider.get_user.side_effect=get_user
        provider.admin_create_user.side_effect=create_user; provider.admin_get_user.side_effect=existing_user
        provider.forgot_password.side_effect=DemoError('OperationNotEnabledException')
        provider.confirm_forgot_password.side_effect=DemoError('OperationNotEnabledException')
        provider.associate_software_token.side_effect=DemoError('OperationNotEnabledException')
        provider.verify_software_token.side_effect=DemoError('OperationNotEnabledException')
        gateway.auth.verifier.verify_claims.side_effect=verify
        with patch('saas.api.cognito_config',return_value={'issuer':'issuer','client':'client','pool':'demo-pool'}),patch.dict(os.environ,{'APP_COOKIE_SECURE':'false','SAAS_ENABLED':'true','SAAS_USER_PROVISIONING_ENABLED':'true','SAAS_AI_ENABLED':'false'}):
            if args.smoke_test:
                def login(email):
                    status,_,cookie=request('POST','/api/auth/login',{'username':email,'password':PASSWORD},anonymous=True)
                    assert status==200,(email,status)
                    return {'Cookie':cookie.split(';')[0]}
                admin_headers=login('superadmin@example.test')
                assert request('GET','/api/platform/overview',headers=admin_headers,anonymous=True)[0]==200
                assert request('GET','/api/platform/token-usage',headers=admin_headers,anonymous=True)[0]==200
                payment={'tenantId':'demo-hospital','amount':'100.25','method':'CASH','reference':'DEMO-RECEIPT-001','reason':'Synthetic receipt only'}
                status,record,_=request('POST','/api/platform/manual-payments',payment,headers=admin_headers,anonymous=True)
                assert status==201,(status,record)
                assert request('POST','/api/platform/manual-payments/'+record['paymentId']+'/decision',{'state':'VERIFIED','version':1,'receivedAmount':'100.25','reason':'Synthetic verification only'},headers=admin_headers,anonymous=True)[0]==200
                new={'kind':'HOSPITAL','name':'Smoke Hospital','email':'synthetic-smoke-owner@gmail.com','status':'ACTIVE',
                     'maxUsers':3,'allowStaffCreation':True,'reason':'Synthetic smoke test','requestId':str(uuid.uuid4())}
                status,created,_=request('POST','/api/platform/tenants',new,headers=admin_headers,anonymous=True)
                assert status==201,(status,created)
                assert request('POST','/api/platform/account-invitations/'+created['invitationId']+'/send',{},headers=admin_headers,anonymous=True)[0]==200
                owner=login(new['email'])
                assert request('POST','/api/session/workspace',{'tenantId':created['tenantId']},headers=owner,anonymous=True)[0]==200
                assert request('GET','/api/tenant/members',workspace=created['tenantId'],headers=owner,anonymous=True)[0]==200
                for name,tenant,clinical in [('hospitaladmin','demo-hospital',False),('radiologist','demo-hospital',True),('transcriptionist','demo-hospital',True),('individual','demo-individual',True)]:
                    headers=login(name+'@example.test')
                    assert request('POST','/api/session/workspace',{'tenantId':tenant},headers=headers,anonymous=True)[0]==200
                    assert request('GET','/api/data',workspace=tenant,headers=headers,anonymous=True)[0]==(200 if clinical else 403)
                    assert request('GET','/api/platform/overview',headers=headers,anonymous=True)[0]==403
                    assert request('GET','/api/platform/token-usage',headers=headers,anonymous=True)[0]==403
                    assert request('GET','/api/platform/manual-payments',headers=headers,anonymous=True)[0]==403
                    if name=='radiologist':
                        status,library,_=request('GET','/api/library',workspace=tenant,headers=headers,anonymous=True)
                        assert status==200 and library['mode']=='local-demo',(status,library)
                    elif name=='transcriptionist':
                        assert request('GET','/api/library',workspace=tenant,headers=headers,anonymous=True)[0]==403
                print('SHARED DEMO SMOKE TEST PASSED: all roles + super-admin account creation/owner acceptance.',flush=True)
                return
            print('LOCAL SYNTHETIC DEMO — mocked Cognito, NO AWS/AI calls. Never deploy.',flush=True)
            print('URL: '+request.server_url,flush=True)
            print('Password for all demo accounts: '+PASSWORD,flush=True)
            print('Emails: '+', '.join(name+'@example.test' for name in identities),flush=True)
            print('Hospital ID: demo-hospital; employee codes: ADMIN001 / DR001 / TX001',flush=True)
            print('Any valid email can be used for local test accounts. No email is sent; use the same demo password after Send invitation.',flush=True)
            if args.serve_seconds:
                print(f'Automatic shutdown and exact disposable database cleanup in {args.serve_seconds} seconds.',flush=True)
                threading.Event().wait(args.serve_seconds)
            else:
                print('Type stop to shut down and remove this disposable database.',flush=True)
                for line in sys.stdin:
                    if line.strip().lower()=='stop': break
    finally:
        case.doCleanups(); PostgresFoundationTests.doClassCleanups()


if __name__=='__main__': main()
