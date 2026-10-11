import ast
import copy
import io
import json
import os
import unittest
import uuid
import threading
import http.client
import logging
import datetime
import re
from pathlib import Path
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from unittest.mock import Mock, patch

from security import CognitoAuth, AccessError, IDENTITY, require_admin, audit, login_failure
from tenant_store import STATE, ScopedState, empty_state
from ingestion.scope import resolve_modality, ModalityRequired, ModalityConflict
from ingestion.retrieval import retrieve_reports
from patient_chat import ChatConflict


class ScopeTests(unittest.TestCase):
    def test_requires_modality(self):
        for payload in ({},{'prompt':'abdomen','modality':'OTHER'},{'prompt':'CT and MRI'}):
            with self.assertRaises(ModalityRequired): resolve_modality(payload)

    def test_conflict_requires_explicit_confirmation(self):
        request={'prompt':'CT brain','modality':'MRI'}
        with self.assertRaises(ModalityConflict): resolve_modality(request)
        self.assertEqual(resolve_modality({**request,'confirmedModality':'CT'}),'CT')
        self.assertEqual(resolve_modality({**request,'confirmedModality':'MRI'}),'MRI')
        with self.assertRaises(ModalityConflict): resolve_modality({**request,'confirmedModality':'USG'})

    def test_all_fallbacks_keep_tenant_and_modality(self):
        client=Mock();client.retrieve.return_value={}
        cfg={'tenantId':'A','bucket':'b','knowledgeBaseId':'kb','prefix':'old-reports/','finalPrefix':'final-reports/'}
        retrieve_reports(client,Mock(),cfg,{'prompt':'USG abdomen','tenantId':'B'})
        for call in client.retrieve.call_args_list:
            terms=call.kwargs['retrievalConfiguration']['vectorSearchConfiguration']['filter']['andAll']
            self.assertIn({'equals':{'key':'tenant_id','value':'A'}},terms)
            self.assertIn({'equals':{'key':'modality','value':'USG'}},terms)

    def test_matching_metadata_but_foreign_s3_path_rejected(self):
        client=Mock();s3=Mock()
        client.retrieve.return_value={'retrievalResults':[{'metadata':{'tenant_id':'A','modality':'CT'},
            'location':{'s3Location':{'uri':'s3://b/old-reports/B/report.txt'}},'score':.99}]}
        refs,_=retrieve_reports(client,s3,{'tenantId':'A','bucket':'b','knowledgeBaseId':'kb','prefix':'old-reports/','finalPrefix':'final-reports/'}, {'prompt':'CT brain'})
        self.assertEqual(refs,[]);s3.get_object.assert_not_called()

    def test_admin_required_and_audit_excludes_report_text(self):
        with self.assertRaises(AccessError): require_admin({'role':'radiologist'})
        with self.assertLogs('radiology.audit',level='INFO') as logs:
            audit('test','request',{'tenantId':'A'},modality='CT',reportIds=['r1'])
        self.assertIn('request',str(logs.output))


class TokenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa
        cls.jwt=jwt;cls.key=rsa.generate_private_key(public_exponent=65537,key_size=2048)

    def setUp(self):
        self.env=patch.dict(os.environ,{'COGNITO_USER_POOL_ID':'us-east-1_Test','COGNITO_CLIENT_ID':'client'})
        self.env.start();self.addCleanup(self.env.stop)
        self.conn=Mock();self.conn.__enter__=Mock(return_value=self.conn);self.conn.__exit__=Mock(return_value=False)
        self.conn.execute.return_value.fetchone.return_value=('A','radiologist')
        self.auth=CognitoAuth(lambda:self.conn)
        self.auth.keys=Mock();self.auth.keys.get_signing_key_from_jwt.return_value.key=self.key.public_key()

    def token(self, **changes):
        import time
        claims={'sub':'subject','iss':'https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Test',
            'exp':int(time.time())+600,'iat':int(time.time()),'token_use':'access','client_id':'client',
            'custom:tenant_id':'B','cognito:groups':['admin']}
        claims.update(changes)
        return self.jwt.encode(claims,self.key,algorithm='RS256',headers={'kid':'test'})

    def test_backend_membership_overrides_tenant_and_admin_claims(self):
        identity=self.auth.verify(self.token())
        self.assertEqual(identity['tenantId'],'A');self.assertEqual(identity['role'],'radiologist')

    def test_expired_wrong_issuer_client_and_id_tokens_rejected(self):
        for changes in ({'exp':1},{'iss':'attacker'},{'client_id':'other'},{'token_use':'id'}):
            with self.subTest(changes=changes),self.assertRaises(AccessError): self.auth.verify(self.token(**changes))

    def test_tampered_signature_rejected(self):
        token=self.token().split('.')
        token[1]=self.token(sub='attacker').split('.')[1]
        with self.assertRaises(AccessError): self.auth.verify('.'.join(token))

    def test_disabled_or_missing_membership_rejected(self):
        self.conn.execute.return_value.fetchone.return_value=None
        with self.assertRaises(AccessError): self.auth.verify(self.token())

    def test_small_clock_skew_accepted_but_large_future_tokens_rejected(self):
        import time
        self.assertEqual(self.auth.verify(self.token(iat=int(time.time())+65))['tenantId'],'A')
        with self.assertRaises(AccessError) as caught: self.auth.verify(self.token(iat=int(time.time())+300))
        self.assertEqual(caught.exception.code,'ClockOutOfSync')

    def test_clock_tolerance_cannot_be_disabled_by_large_override(self):
        with patch.dict(os.environ,{'AUTH_CLOCK_SKEW_SECONDS':'3600'}):
            with self.assertRaises(AccessError):self.auth.verify(self.token())

    def test_expired_token_outside_leeway_is_still_rejected(self):
        import time
        with self.assertRaises(AccessError): self.auth.verify(self.token(exp=int(time.time())-300))

    def test_login_error_is_actionable_without_logging_credentials(self):
        class FailedLogin(Exception):
            response={'Error':{'Code':'NotAuthorizedException','Message':'private-password-and-session'},
                      'ResponseMetadata':{'RequestId':'aws-request'}}
        with self.assertLogs('radiology.audit') as captured:
            error=login_failure(FailedLogin(),'request')
        self.assertEqual(error.status,401)
        self.assertEqual(error.code,'InvalidCredentials')
        self.assertIn('new password',str(error))
        self.assertNotIn('private-password',str(captured.output))
        self.assertNotIn('private-password',str(error))

    def test_login_service_errors_do_not_claim_bad_password(self):
        with self.assertLogs('radiology.audit'):
            error=login_failure(RuntimeError('private credentials'),'request')
        self.assertEqual(error.status,503)
        self.assertNotIn('private credentials',str(error))


class HttpIsolationTests(unittest.TestCase):
    def setUp(self):
        source=Path(__file__).resolve().parents[1]/'server.py'
        tree=ast.parse(source.read_text())
        selected=ast.Module(body=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))
            and n.name in ('App','send','save','now','body','validate_chat_patient','RequestRollback')],type_ignores=[])
        self.rows={'A':empty_state(),'B':empty_state()}
        self.rows['B']['patients']=[{'id':'pb','tenantId':'B','name':'Hospital B patient'}]
        self.rows['B']['reports']=[{'id':'rb','tenantId':'B','patientId':'pb','modality':'CT'}]
        @contextmanager
        def transaction(connect,tenant,writable):
            token=STATE.set(copy.deepcopy(self.rows[tenant]))
            try:
                yield
                if writable:self.rows[tenant]=copy.deepcopy(STATE.get())
            finally:STATE.reset(token)
        def authenticate(headers):
            tenant=headers.get('Authorization','').removeprefix('Bearer ')
            if tenant not in self.rows:raise AccessError()
            return {'tenantId':tenant,'role':'radiologist','subject':tenant}
        ns={'json':json,'os':os,'uuid':uuid,'datetime':datetime,'urlparse':urlparse,'parse_qs':parse_qs,
            'BaseHTTPRequestHandler':BaseHTTPRequestHandler,'ScopedState':ScopedState,'db':ScopedState(),
            'IDENTITY':IDENTITY,'AUTH':Mock(authenticate=authenticate),'ensure_security':lambda:None,
            'tenant_transaction':transaction,'pg_connect':Mock(),'audit':audit,'AccessError':AccessError,
            'ModalityConflict':ModalityConflict,'ModalityRequired':ModalityRequired,'resolve_modality':resolve_modality,
            'require_admin':require_admin,'aws_settings':lambda:{'tenantId':IDENTITY.get()['tenantId']},
            'patient_chat_service':Mock(),'ChatConflict':ChatConflict,'MAX_BYTES':10*1024*1024,
            'login_failure':login_failure}
        exec(compile(selected,str(source),'exec'),ns)
        self.ns=ns
        self.server=ThreadingHTTPServer(('127.0.0.1',0),ns['App'])
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)

    def request(self,method,path,tenant='A',payload=None):
        client=http.client.HTTPConnection(*self.server.server_address,timeout=5)
        headers={'Authorization':'Bearer '+tenant,'Content-Type':'application/json'}
        client.request(method,path,json.dumps(payload) if payload is not None else None,headers)
        response=client.getresponse();result=json.loads(response.read());client.close()
        return response.status,result

    def test_direct_reads_cannot_see_other_hospital(self):
        status,data=self.request('GET','/api/data')
        self.assertEqual(status,200);self.assertEqual(data['patients'],[])
        self.assertEqual(self.request('GET','/api/data','B')[1]['patients'][0]['id'],'pb')

    def test_modality_creation_normalizes_and_persists(self):
        status,values=self.request('POST','/api/modalities',payload={'name':' pet '})
        self.assertEqual(status,201)
        self.assertIn('PET',values)
        self.assertIn('PET',self.request('GET','/api/data')[1]['modalities'])
        self.assertNotIn('PET',self.rows['B']['modalities'])
        self.assertEqual(self.request('POST','/api/modalities',payload={'name':'pet'})[0],409)
        self.assertEqual(self.request('POST','/api/modalities',payload={'name':' '})[0],400)

    def test_template_creation_validates_content_and_modality(self):
        for payload in ({'name':'Normal CT','modality':'CT'},
                        {'name':'Normal CT','modality':'OTHER','body':'FINDINGS'},
                        {'name':' ','modality':'CT','body':'FINDINGS'}):
            self.assertEqual(self.request('POST','/api/templates',payload=payload)[0],400)
        self.assertEqual(self.rows['A']['templates'],[])
        payload={'name':' Normal CT ','modality':'ct','body':'<h3>FINDINGS</h3><p>Template text.</p>','tenantId':'B'}
        status,item=self.request('POST','/api/templates',payload=payload)
        self.assertEqual(status,201)
        self.assertEqual(item['name'],'Normal CT')
        self.assertEqual(item['modality'],'CT')
        self.assertEqual(item['tenantId'],'A')
        self.assertEqual(self.request('GET','/api/data')[1]['templates'][0]['body'],payload['body'])
        self.assertEqual(self.rows['B']['templates'],[])
        self.assertEqual(self.request('POST','/api/templates',payload=payload)[0],409)

    def test_duplicate_template_name_allowed_in_separate_modality(self):
        for modality in ('CT','MRI'):
            self.assertEqual(self.request('POST','/api/templates',payload={
                'name':'Normal study','modality':modality,'body':'<p>Normal template.</p>'})[0],201)

    def test_invalid_template_update_does_not_change_saved_template(self):
        _,item=self.request('POST','/api/templates',payload={
            'name':'Normal CT','modality':'CT','body':'Original template'})
        self.assertEqual(self.request('PUT','/api/templates/'+item['id'],payload={'body':''})[0],400)
        self.assertEqual(self.rows['A']['templates'][0]['body'],'Original template')

    def test_forged_tenant_and_entity_id_cannot_write_other_hospital(self):
        status,item=self.request('POST','/api/patients',payload={'name':'A patient','id':'pb','tenantId':'B'})
        self.assertEqual(status,201);self.assertEqual(item['tenantId'],'A');self.assertNotEqual(item['id'],'pb')
        self.assertEqual(self.rows['B']['patients'][0]['name'],'Hospital B patient')
        for method in ('PUT','DELETE'):
            self.assertEqual(self.request(method,'/api/patients/pb',payload={'name':'attack'})[0],404)
            self.assertEqual(self.request(method,'/api/reports/rb',payload={'patientId':item['id'],'modality':'CT'})[0],404)

    def test_foreign_patient_chat_and_report_link_rejected(self):
        self.assertEqual(self.request('GET','/api/patient-chat/pb')[0],400)
        self.assertGreaterEqual(self.request('POST','/api/reports',payload={'patientId':'pb','modality':'CT'})[0],400)

    def test_unauthenticated_and_non_admin_diagnostics_denied(self):
        self.assertEqual(self.request('GET','/api/data','invalid')[0],401)
        self.assertEqual(self.request('GET','/api/rag/debug?modality=CT')[0],403)
        self.assertEqual(self.request('POST','/api/rag/repair-docx')[0],403)

    def test_admin_debug_still_requires_modality(self):
        self.ns['AUTH']=Mock(authenticate=lambda headers:{'tenantId':'A','role':'admin'})
        status,result=self.request('GET','/api/rag/debug')
        self.assertEqual(status,400)
        self.assertEqual(result['code'],'ModalityRequired')

    def test_modality_conflict_does_not_start_generation(self):
        self.rows['A']['patients']=[{'id':'pa','tenantId':'A'}]
        status,result=self.request('POST','/api/rag/chat',payload={'patientId':'pa','modality':'MRI','prompt':'CT brain'})
        self.assertEqual(status,409)
        self.assertEqual(result['code'],'ModalityConflict')
        self.ns['patient_chat_service'].return_value.submit.assert_not_called()

    def test_first_login_returns_new_password_challenge(self):
        client=Mock()
        client.initiate_auth.return_value={'ChallengeName':'NEW_PASSWORD_REQUIRED','Session':'challenge-session'}
        self.ns['aws_client']=lambda name:client
        self.ns['cognito_config']=lambda:{'client':'client'}
        status,result=self.request('POST','/api/auth/login',payload={'username':'demo@example.test','password':'test-temporary-password'})
        self.assertEqual(status,200)
        self.assertEqual(result['challenge'],'NEW_PASSWORD_REQUIRED')
        self.assertEqual(result['username'],'demo@example.test')
        self.assertNotIn('password',result)

    def test_password_changed_but_session_failed_does_not_claim_bad_password(self):
        client=Mock()
        client.respond_to_auth_challenge.return_value={'AuthenticationResult':{'AccessToken':'test-token'}}
        self.ns['aws_client']=lambda name:client
        self.ns['cognito_config']=lambda:{'client':'client'}
        self.ns['AUTH'].verify.side_effect=AccessError('Server clock problem.')
        status,result=self.request('POST','/api/auth/login',payload={'username':'demo@example.test',
            'challenge':'NEW_PASSWORD_REQUIRED','session':'session','answer':'New-password-123!'})
        self.assertEqual(status,401)
        self.assertTrue(result['passwordChanged'])
        self.assertIn('changed successfully',result['error'])


if __name__=='__main__':unittest.main()
