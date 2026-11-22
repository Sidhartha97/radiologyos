"""Optional SaaS HTTP gateway around existing reporting and generation code."""
import os
import re
import uuid
from urllib.parse import urlparse,parse_qs
from security import IDENTITY,cognito_config,login_failure
from .auth import SaaSAuth,access_token
from .accounts import email_address,employee_code
from .login import LoginState
from .permissions import SaaSError
from .runtime import scoped
from .tenants import list_workspaces,workspace_transaction
from .http_policy import validate as validate_http
from . import readiness
from . import clinical,locks,workflow,platform,memberships,access,provisioning,assignments,recovery,mfa,report_chat,token_tracking,manual_payments,sessions,reauth,deletion


class RollbackResponse(Exception): pass


class API:
    def __init__(self,connect,provider,verifier=None,report_generate=None,*,demo_library=None):
        self.connect=connect; self.provider=provider
        self.report_generate=report_generate
        # Explicit test injection only; normal application startup never supplies this.
        if demo_library is not None and os.getenv('APP_ENV','development').lower()=='production':
            raise RuntimeError('Demo adapters cannot run in production mode.')
        self.demo_library=demo_library
        self.auth=SaaSAuth(connect,verifier); self.login_state=LoginState()

    def login(self,req,payload):
        self.login_state.throttle(req.client_address[0])
        cfg=cognito_config(); client=self.provider('cognito-idp')
        if payload.get('challengeId'):
            previous=self.login_state.consume(payload['challengeId'])
            if previous.get('purpose')=='reauth':
                raise SaaSError('Use the identity confirmation form for this challenge.','ChallengeExpired',401)
            if not isinstance(payload.get('answer'),str) or not 1<=len(payload['answer'])<=256:
                raise SaaSError('Enter a valid challenge answer.','InvalidAnswer',400)
            if previous['challenge']=='VERIFY_EMAIL':
                try:
                    client.verify_user_attribute(AccessToken=previous['accessToken'],AttributeName='email',Code=payload['answer'])
                except Exception as exc: raise login_failure(exc,req.request_id) from None
                return self.complete_login(previous['accessToken'],client,previous['hospital'])
            username,hospital=previous['username'],previous['hospital']
            if previous['challenge']=='MFA_SETUP':
                response=mfa.finish_required(client,cfg['client'],previous,payload['answer'])
            else:
                field={'NEW_PASSWORD_REQUIRED':'NEW_PASSWORD','SOFTWARE_TOKEN_MFA':'SOFTWARE_TOKEN_MFA_CODE','SMS_MFA':'SMS_MFA_CODE'}[previous['challenge']]
                try:
                    response=client.respond_to_auth_challenge(ClientId=cfg['client'],ChallengeName=previous['challenge'],
                        Session=previous['session'],ChallengeResponses={'USERNAME':username,field:payload['answer']})
                except Exception as exc: raise login_failure(exc,req.request_id) from None
        else:
            hospital=None
            if payload.get('loginType')=='employee':
                hospital=payload.get('hospitalId',''); code=employee_code(payload.get('employeeCode'))
                if not isinstance(hospital,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',hospital):
                    raise SaaSError('Incorrect hospital, employee code or password.','InvalidCredentials',401)
                with self.connect() as conn:
                    username=conn.execute('SELECT saas.employee_login(%s,%s,%s)',(cfg['issuer'],hospital,code)).fetchone()[0]
                if not username: raise SaaSError('Incorrect hospital, employee code or password.','InvalidCredentials',401)
            else: username=email_address(payload.get('username'))
            password=payload.get('password')
            if not isinstance(password,str) or not 1<=len(password)<=256: raise SaaSError('Enter a password.','InvalidPassword',400)
            try:
                response=client.initiate_auth(ClientId=cfg['client'],AuthFlow='USER_PASSWORD_AUTH',AuthParameters={'USERNAME':username,'PASSWORD':password})
            except Exception as exc: raise login_failure(exc,req.request_id) from None
        if response.get('ChallengeName')=='MFA_SETUP':
            session,secret=mfa.begin_required(client,response)
            public=self.login_state.remember({**response,'Session':session},username,hospital)
            return {**public,'setupSecret':secret},None
        if response.get('ChallengeName'): return self.login_state.remember(response,username,hospital),None
        token=response.get('AuthenticationResult',{}).get('AccessToken','')
        return self.complete_login(token,client,hospital)

    def complete_login(self,token,client,hospital):
        identity,_=self.auth.verified(token)
        try: user=client.get_user(AccessToken=token)
        except Exception as exc: raise login_failure(exc,'cognito-user-verification') from None
        values={a['Name']:a['Value'] for a in user.get('UserAttributes',[])}
        if values.get('sub')!=identity.subject:
            raise SaaSError('Identity verification failed.','Unauthenticated',401)
        email=email_address(values.get('email'))
        if values.get('email_verified')!='true':
            try:
                client.get_user_attribute_verification_code(AccessToken=token,AttributeName='email')
            except Exception as exc:
                raise login_failure(exc,'cognito-email-verification-delivery') from None
            return self.login_state.verify_email(token,hospital),None
        with self.connect() as conn:
            conn.execute('SELECT saas.accept_platform_invitation(%s,%s,%s)',(identity.issuer,identity.subject,email))
            conn.execute('SELECT saas.accept_account_invitation(%s,%s,%s)',(identity.issuer,identity.subject,email))
            conn.execute('SELECT saas.accept_verified_invitations(%s,%s,%s)',(identity.issuer,identity.subject,email))
        result=self.auth.login(token)
        if hospital:
            self.auth.switch({'Authorization':'Bearer '+token},hospital)
            _,result=self.auth.authenticate({'Authorization':'Bearer '+token})
            result['workspaces']=list_workspaces(self.connect,identity)
        return {'ok':True,'saas':True,'demoMode':self.demo_library is not None,**result},token

    def handle(self,req,method,read,send,legacy):
        path=urlparse(req.path).path
        if method not in ('GET','POST','PUT','DELETE'): raise SaaSError('Method unavailable.','MethodNotAllowed',405)
        validate_http(req.headers,method,path)
        payload=None
        def data():
            nonlocal payload
            if payload is None:
                payload=read(req)
                if not isinstance(payload,dict): raise SaaSError('Send a JSON object.','InvalidRequest',400)
            return payload
        if path=='/api/auth/login' and method=='POST':
            if not 0<int(req.headers.get('Content-Length','0') or 0)<20000:
                raise SaaSError('Invalid login request.','InvalidRequest',400)
            result,token=self.login(req,data())
            if token:
                # The caller writes the same-origin HttpOnly cookie after successful provisioning.
                req.saas_cookie=token
            send(req,200,result); return
        if path in ('/api/auth/password-reset','/api/auth/password-reset/confirm') and method=='POST':
            self.login_state.throttle(req.client_address[0])
            if not 0<int(req.headers.get('Content-Length','0') or 0)<20000:
                raise SaaSError('Invalid recovery request.','InvalidRequest',400)
            config=cognito_config(); client=self.provider('cognito-idp')
            operation=recovery.confirm if path.endswith('/confirm') else recovery.request
            send(req,200,operation(client,config['client'],data())); return
        identity,session=self.auth.authenticate(req.headers)
        req.audit_identity={'tenantId':(session.get('workspace') or {}).get('tenantId')}
        if path=='/api/auth/reauth' and method=='POST':
            if not 0<int(req.headers.get('Content-Length','0') or 0)<20000:
                raise SaaSError('Invalid identity confirmation.','InvalidRequest',400)
            result,token=reauth.confirm(self,req,identity,session,data())
            if token: req.saas_cookie=token
            send(req,200,result); return
        if path=='/api/auth/me' and method=='GET':
            send(req,200,{'saas':True,'demoMode':self.demo_library is not None,**session,'workspaces':list_workspaces(self.connect,identity)}); return
        if path=='/api/auth/logout' and method=='POST':
            self.auth.logout(req.headers); req.saas_cookie=''; send(req,200,{'ok':True}); return
        if path=='/api/auth/logout-all' and method=='POST':
            self.auth.require_recent(req.headers)
            sessions.revoke_all(self.connect,identity); req.saas_cookie=''; send(req,200,{'ok':True}); return
        if path in ('/api/auth/mfa/setup','/api/auth/mfa/confirm') and method=='POST':
            self.auth.require_recent(req.headers)
            self.login_state.throttle(req.client_address[0])
            client=self.provider('cognito-idp'); token=access_token(req.headers)
            result=mfa.setup(client,token) if path.endswith('/setup') else mfa.confirm(client,token,data())
            send(req,200,result); return
        if path=='/api/workspaces' and method=='GET':
            send(req,200,{'workspaces':list_workspaces(self.connect,identity)}); return
        if path=='/api/session/workspace' and method=='POST':
            send(req,200,self.auth.switch(req.headers,data().get('tenantId'))); return
        if path=='/api/platform/tenants' and method=='GET':
            params=parse_qs(urlparse(req.path).query)
            send(req,200,platform.list_tenants(self.connect,identity,after=params.get('after',[''])[0])); return
        if path=='/api/platform/overview' and method=='GET': send(req,200,platform.overview(self.connect,identity)); return
        if path=='/api/platform/dashboard' and method=='GET': send(req,200,platform.dashboard(self.connect,identity)); return
        if path=='/api/platform/readiness' and method=='GET': send(req,200,readiness.inspect(self.connect,identity,self.demo_library is not None)); return
        if path=='/api/platform/token-usage' and method=='GET':
            send(req,200,token_tracking.dashboard(self.connect,identity,parse_qs(urlparse(req.path).query).get('after',[''])[0])); return
        if path=='/api/platform/manual-payments' and method=='GET': send(req,200,manual_payments.listing(self.connect,identity)); return
        if path=='/api/platform/manual-payments' and method=='POST':
            self.auth.require_recent(req.headers)
            send(req,201,manual_payments.create(self.connect,identity,data(),req.request_id)); return
        payment_decision=re.fullmatch(r'/api/platform/manual-payments/([0-9a-f-]{36})/decision',path)
        if payment_decision and method=='POST':
            self.auth.require_recent(req.headers)
            send(req,200,manual_payments.decide(self.connect,identity,payment_decision[1],data(),req.request_id)); return
        if path=='/api/platform/audit' and method=='GET': send(req,200,platform.audit_events(self.connect,identity)); return
        if path=='/api/platform/tenants' and method=='POST':
            self.auth.require_recent(req.headers)
            send(req,201,provisioning.create(self.connect,identity,data(),req.request_id)); return
        if path=='/api/platform/account-invitations' and method=='GET':
            send(req,200,provisioning.list_invitations(self.connect,identity)); return
        account_delivery=re.fullmatch(r'/api/platform/account-invitations/([0-9a-f-]{36})/send',path)
        if account_delivery and method=='POST':
            self.auth.require_recent(req.headers)
            with self.connect() as conn: platform.require_platform(conn,identity)
            if os.getenv('SAAS_USER_PROVISIONING_ENABLED','false').lower()!='true':
                raise SaaSError('Cognito account invitation delivery is not enabled.','ProvisioningUnavailable',403)
            config=cognito_config()
            send(req,200,provisioning.deliver(self.connect,identity,account_delivery[1],self.provider('cognito-idp'),config['pool'],config['issuer'])); return
        platform_tenant=re.fullmatch(r'/api/platform/tenants/([A-Za-z0-9_-]{1,80})',path)
        if platform_tenant and method=='PUT':
            self.auth.require_recent(req.headers)
            send(req,200,platform.update_tenant(self.connect,identity,platform_tenant[1],data(),req.request_id)); return
        if path.startswith('/api/platform/'):
            purge=re.fullmatch(r'/api/platform/tenants/([A-Za-z0-9_-]{1,80})/deletion(?:/(preview|confirm|retry))?',path)
            if purge:
                if os.getenv('SAAS_CUSTOMER_DELETION_ENABLED','false').lower()!='true':
                    raise SaaSError('Customer deletion is not configured yet.','DeletionUnavailable',403)
                tenant,operation=purge.groups()
                if method=='GET' and not operation:
                    send(req,200,deletion.call(self.connect,'STATUS',tenant,identity=identity)); return
                if method!='POST' or operation not in ('preview','confirm','retry'):
                    raise SaaSError('Endpoint unavailable.','MethodNotAllowed',405)
                if operation=='preview':
                    result=deletion.call(self.connect,'PREVIEW',tenant,identity=identity)
                    cloud=deletion.CloudCleanup.configured().discover(tenant)
                    result['cloudCounts']={'objectVersions':cloud['objectVersions'],'indexedDocuments':sum(len(items) for items in cloud['documents'].values())}
                    send(req,200,result); return
                self.auth.require_recent(req.headers)
                value=data()
                try:
                    job=str(uuid.UUID(str(value.get('jobId'))))
                    confirmation=str(uuid.UUID(str(value.get('confirmationToken')))) if operation=='confirm' else None
                except ValueError: raise SaaSError('Reload the deletion preview.','InvalidConfirmation',400) from None
                if operation=='confirm' and (value.get('confirm') is not True or not isinstance(value.get('reason'),str) or not 1<=len(value['reason'].strip())<=1000):
                    raise SaaSError('Explicit confirmation and a reason are required.','InvalidConfirmation',400)
                result=deletion.call(self.connect,'CONFIRM' if operation=='confirm' else 'RETRY',tenant,job,confirmation,value,identity)
                send(req,202,result); return
            member_access=re.fullmatch(r'/api/platform/tenants/([A-Za-z0-9_-]{1,80})/members(?:/([0-9a-f-]{36}))?',path)
            if member_access and method=='GET' and not member_access[2]:
                send(req,200,access.roster(self.connect,identity,member_access[1])); return
            if member_access and method=='PUT' and member_access[2]:
                self.auth.require_recent(req.headers)
                send(req,200,access.update(self.connect,identity,member_access[1],member_access[2],data(),req.request_id)); return
            raise SaaSError('Endpoint unavailable.','NotFound',404)
        self.auth.require_workspace(req.headers,session)
        selection=session['workspace']; tenant=selection['tenantId']
        mapped={'tenantId':tenant,'subject':identity.subject,'role':'admin' if 'hospital_admin' in selection['roles'] else 'radiologist'}
        token=IDENTITY.set(mapped)
        try:
            with scoped(identity,tenant):
                report_conversation=re.fullmatch(r'/api/report-chat/([A-Za-z0-9_-]{1,128})',path)
                if report_conversation:
                    result=report_chat.handle(self.connect,identity,tenant,report_conversation[1],method,
                        data() if method=='POST' else {},self.report_generate,req.request_id)
                    send(req,202 if method=='POST' else 200,result); return
                if path.startswith('/api/tenant/'):
                    return self.tenant(req,method,path,data,send,identity,tenant)
                self.clinical(req,method,path,data,send,legacy,identity,tenant)
        finally: IDENTITY.reset(token)

    def tenant(self,req,method,path,data,send,identity,tenant):
        delivery=re.fullmatch(r'/api/tenant/invitations/([0-9a-f-]{36})/send',path)
        if delivery and method=='POST':
            if os.getenv('SAAS_USER_PROVISIONING_ENABLED','false').lower()!='true':
                raise SaaSError('Cognito invitation delivery is not enabled yet.','ProvisioningUnavailable',403)
            self.auth.require_recent(req.headers)
            config=cognito_config()
            send(req,200,memberships.deliver(self.connect,identity,tenant,delivery[1],self.provider('cognito-idp'),config['pool'],config['issuer'])); return
        with workspace_transaction(self.connect,identity,tenant) as (conn,workspace):
            if path=='/api/tenant/settings' and method=='GET':
                workspace.require('tenant.settings.read')
                row=conn.execute('SELECT max_users,allow_hospital_admin_create_users FROM saas.tenant_settings WHERE tenant_id=%s',(tenant,)).fetchone()
                occupied=conn.execute('SELECT count(*) FROM saas.tenant_memberships WHERE tenant_id=%s AND enabled',(tenant,)).fetchone()[0]
                reserved=conn.execute("SELECT count(*) FROM saas.invitations WHERE tenant_id=%s AND status IN ('PENDING','PROVISIONING','INVITED','FAILED') AND expires_at>clock_timestamp()",(tenant,)).fetchone()[0]
                send(req,200,{'maxUsers':row[0],'allowStaffCreation':row[1],'activeMembers':occupied,'reservedSeats':reserved} if row else {}); return
            if path=='/api/tenant/members' and method=='GET': send(req,200,memberships.roster(conn,workspace)); return
            if path=='/api/tenant/invitations' and method=='POST': send(req,201,memberships.invite(conn,workspace,data(),req.request_id)); return
            member=re.fullmatch(r'/api/tenant/members/([0-9a-f-]{36})/deactivate',path)
            if member and method=='POST': send(req,200,memberships.deactivate(conn,workspace,member[1],req.request_id)); return
            reactivate=re.fullmatch(r'/api/tenant/members/([0-9a-f-]{36})/reactivate',path)
            if reactivate and method=='POST': send(req,200,memberships.reactivate(conn,workspace,reactivate[1],data().get('version'),req.request_id)); return
            revoke=re.fullmatch(r'/api/tenant/invitations/([0-9a-f-]{36})/revoke',path)
            if revoke and method=='POST': send(req,200,memberships.revoke_invitation(conn,workspace,revoke[1],req.request_id)); return
            raise SaaSError('Endpoint unavailable.','NotFound',404)

    def clinical(self,req,method,path,data,send,legacy,identity,tenant):
        # Network operations use read-only snapshot transactions; report writes
        # and generic CRUD use the same connection as controls and audit records.
        writable=method!='GET' and (path.startswith('/api/reports') or path.startswith('/api/patients') or
            path.startswith('/api/templates') or path.startswith('/api/modalities'))
        with clinical.transaction(self.connect,identity,tenant,writable) as (conn,workspace,state):
            workspace.require('reports.read')
            visible=clinical.view(conn,workspace,state)
            if path=='/api/data' and method=='GET': send(req,200,visible); return
            if path=='/api/summary' and method=='GET':
                send(req,200,{'patients':len(visible['patients']),'reports':len(visible['reports']),
                    'drafts':sum(r['status']=='Draft' for r in visible['reports']),
                    'finalized':sum(r['status']=='Final' for r in visible['reports'])}); return
            if path=='/api/clinical/members' and method=='GET': send(req,200,assignments.directory(conn,workspace)); return
            if path=='/api/clinical/reviewers' and method=='GET': send(req,200,assignments.reviewers(conn,workspace)); return
            if path=='/api/reports' and method=='POST':
                send(req,201,clinical.create_report(conn,workspace,state,data())); return
            match=re.fullmatch(r'/api/reports/([A-Za-z0-9_-]{1,128})(?:/(lock(?:/renew|/release)?|submit|review|return|finalize|amend|assign|handoff))?',path)
            if match:
                report_id,action=match.groups()
                if report_id not in {r['id'] for r in visible['reports']}: raise SaaSError('Report not found.','NotFound',404)
                if not action and method=='GET':
                    send(req,200,next(r for r in visible['reports'] if r['id']==report_id)); return
                if action and method!='POST': raise SaaSError('Use POST for report actions.','MethodNotAllowed',405)
                value=data()
                if action=='handoff':
                    result=assignments.handoff(conn,workspace,report_id,value,req.request_id)
                    state['reports']=[result if r['id']==report_id else r for r in state['reports']]
                elif action=='assign':
                    revision=assignments.change(conn,workspace,report_id,value,req.request_id)
                    result={**next(r for r in visible['reports'] if r['id']==report_id),**revision}
                    state['reports']=[result if r['id']==report_id else r for r in state['reports']]
                elif action=='amend':
                    result=workflow.amend(conn,workspace,report_id,value.get('version'),value.get('reason'))
                    state['reports'].append(result)
                elif action=='lock': result=locks.acquire(conn,workspace,report_id,value.get('editingSession'))
                elif action=='lock/renew': result=locks.renew(conn,workspace,report_id,value.get('lease'))
                elif action=='lock/release': locks.release(conn,workspace,report_id,value.get('lease')); result={'ok':True}
                elif method in ('PUT','DELETE','POST'):
                    existing=next(r for r in state['reports'] if r['id']==report_id)
                    if value.get('patientId',existing['patientId'])!=existing['patientId']:
                        raise SaaSError('A report cannot be moved to another patient.','OwnershipMismatch',409)
                    target={'submit':'READY_FOR_REVIEW','review':'UNDER_REVIEW','return':'RETURNED_FOR_CORRECTION','finalize':'FINALIZED'}.get(action)
                    if method=='DELETE': target='CANCELLED'
                    if value.get('status')=='Final': target='FINALIZED'
                    modality=value.get('modality',existing['modality'])
                    if modality not in state['modalities']: raise SaaSError('Select a workspace modality.','InvalidModality',400)
                    result=workflow.save(conn,workspace,report_id,value.get('body',existing['body']),value.get('version'),
                        value.get('lease'),target=target,reason=value.get('reason'),request_id=req.request_id,modality=modality)
                    state['reports']=[result if r['id']==report_id else r for r in state['reports']]
                else: raise SaaSError('Method unavailable.','MethodNotAllowed',405)
                send(req,200,result); return
            if path.startswith('/api/rag/') or path.startswith('/api/patient-chat/'):
                workspace.require('reports.read_all')
                if path=='/api/rag/chat':
                    workspace.require('ai.generate')
                    if os.getenv('SAAS_AI_ENABLED','false').lower()!='true':
                        raise SaaSError('AI generation is disabled. Contact your administrator.','AIUnavailable',403)
                if path in ('/api/rag/repair-docx','/api/rag/ingest'):
                    raise SaaSError('Use the historical library for tenant-scoped ingestion.')
            elif path.startswith('/api/library'):
                workspace.require('reports.read_all')
                if self.demo_library is not None:
                    send(req,200,self.demo_library.handle(tenant,method,path,data() if method!='GET' else {})); return
            elif path.startswith('/api/uploads'):
                raise SaaSError('Use the historical library to upload reports.','UseLibrary',409)
            elif path.startswith('/api/patients'):
                workspace.require('reports.read_all')
                if method=='DELETE' and any(r.get('patientId')==path.rsplit('/',1)[-1] for r in state.get('reports',[])):
                    raise SaaSError('Patients with reports cannot be deleted.','PatientHasReports',409)
            elif path.startswith('/api/templates') or path.startswith('/api/modalities'):
                workspace.require('reports.read_all')
            else: raise SaaSError('Endpoint unavailable.','NotFound',404)
            # The existing handlers retain formatting, chat history and upload logic.
            legacy(method)
            if req.pending_response and req.pending_response[0]>=400: raise RollbackResponse()
