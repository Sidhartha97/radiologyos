import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from saas.api import API
from saas.reauth import confirm
from saas.permissions import SaaSError
from saas.tenants import VerifiedIdentity


class ReauthenticationTests(unittest.TestCase):
    def setup_identity(self):
        client=Mock(); api=API(Mock(),lambda name:client); api.auth=Mock()
        identity=VerifiedIdentity('issuer','subject')
        api.auth.verified.return_value=(identity,{'auth_time':int(time.time())})
        client.get_user.return_value={'Username':'server-resolved-user','UserAttributes':[
            {'Name':'sub','Value':'subject'},{'Name':'email','Value':'admin@example.test'},
            {'Name':'email_verified','Value':'true'}]}
        client.initiate_auth.return_value={'AuthenticationResult':{'AccessToken':'new-synthetic-token'}}
        req=SimpleNamespace(headers={'Authorization':'Bearer old-synthetic-token'},client_address=('local-test',1),request_id='test')
        return api,client,identity,req

    @patch('saas.reauth.cognito_config',return_value={'client':'client'})
    def test_same_subject_confirmation_preserves_workspace_and_inflight_requests(self,_):
        api,client,identity,req=self.setup_identity()
        result,token=confirm(api,req,identity,{'workspace':{'tenantId':'hospital-a'}},{'password':'synthetic-password','username':'attacker@example.test'})
        self.assertEqual(result,{'ok':True}); self.assertEqual(token,'new-synthetic-token')
        self.assertEqual(client.initiate_auth.call_args.kwargs['AuthParameters']['USERNAME'],'server-resolved-user')
        api.auth.switch.assert_called_once_with({'Authorization':'Bearer new-synthetic-token'},'hospital-a')
        api.auth.logout.assert_not_called()

    @patch('saas.reauth.cognito_config',return_value={'client':'client'})
    def test_another_subject_or_old_authentication_is_rejected(self,_):
        for actual,authenticated in [(VerifiedIdentity('issuer','other'),int(time.time())),(VerifiedIdentity('issuer','subject'),int(time.time())-301)]:
            api,client,identity,req=self.setup_identity()
            api.auth.verified.return_value=(actual,{'auth_time':authenticated})
            with self.assertRaises(SaaSError): confirm(api,req,identity,{}, {'password':'synthetic-password'})
            api.auth.login.assert_not_called(); api.auth.logout.assert_not_called()

    @patch('saas.reauth.cognito_config',return_value={'client':'client'})
    def test_mfa_is_bound_to_current_subject_and_not_exposed(self,_):
        api,client,identity,req=self.setup_identity()
        client.initiate_auth.return_value={'ChallengeName':'SOFTWARE_TOKEN_MFA','Session':'private-provider-session'}
        result,token=confirm(api,req,identity,{}, {'password':'synthetic-password'})
        self.assertIsNone(token); self.assertNotIn('private-provider-session',str(result))
        with self.assertRaises(SaaSError):
            confirm(api,req,VerifiedIdentity('issuer','other'),{}, {'challengeId':result['challengeId'],'answer':'123456'})
        client.respond_to_auth_challenge.assert_not_called()

    @patch('saas.reauth.cognito_config',return_value={'client':'client'})
    def test_successful_mfa_continuation_completes_step_up(self,_):
        api,client,identity,req=self.setup_identity()
        client.initiate_auth.return_value={'ChallengeName':'SOFTWARE_TOKEN_MFA','Session':'private-provider-session'}
        result,_=confirm(api,req,identity,{}, {'password':'synthetic-password'})
        client.respond_to_auth_challenge.return_value={'AuthenticationResult':{'AccessToken':'new-synthetic-token'}}
        public,token=confirm(api,req,identity,{}, {'challengeId':result['challengeId'],'answer':'123456'})
        self.assertTrue(public['ok']); self.assertEqual(token,'new-synthetic-token')
        with self.assertRaises(SaaSError):
            confirm(api,req,identity,{}, {'challengeId':result['challengeId'],'answer':'123456'})
