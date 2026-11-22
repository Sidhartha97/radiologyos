import unittest
from unittest.mock import Mock,patch
from saas.auth import SaaSAuth,access_token
from saas.permissions import SaaSError
from security import AccessError


class SaaSAuthenticationTests(unittest.TestCase):
    def test_credentials_are_extracted_from_bearer_or_http_only_cookie(self):
        self.assertEqual(access_token({'Authorization':'Bearer test-token'}),'test-token')
        self.assertEqual(access_token({'Cookie':'radiology_session=test-cookie; other=ignored'}),'test-cookie')
        for headers in ({},{'Authorization':'Basic value'},{'Authorization':'Bearer '}):
            with self.assertRaises(SaaSError): access_token(headers)

    def test_failed_verification_never_opens_session_or_database(self):
        connect=Mock(); verifier=Mock(); verifier.verify_claims.side_effect=AccessError()
        auth=SaaSAuth(connect,verifier)
        with patch('saas.auth.sessions.create') as create:
            with self.assertRaises(AccessError): auth.login('invalid')
            create.assert_not_called(); connect.assert_not_called()

    def test_clinical_request_requires_explicit_matching_workspace(self):
        verifier=Mock(); verifier.verify_claims.return_value={'iss':'issuer','sub':'user','exp':12345}
        auth=SaaSAuth(Mock(),verifier)
        with patch('saas.auth.sessions.describe',return_value={'workspace':{'tenantId':'hospital-a'}}) as describe:
            with self.assertRaises(SaaSError): auth.authenticate({'Authorization':'Bearer test'},require_workspace=True)
            auth.authenticate({'Authorization':'Bearer test','X-Workspace-ID':'hospital-a'},require_workspace=True)
            self.assertEqual(describe.call_args.args[3],'hospital-a')


if __name__=='__main__': unittest.main()
