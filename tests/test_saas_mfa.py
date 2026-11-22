import unittest
from unittest.mock import Mock
from saas import mfa
from saas.permissions import SaaSError


class MFATests(unittest.TestCase):
    def test_existing_factor_is_not_replaced(self):
        client=Mock(); client.get_user.return_value={'UserMFASettingList':['SOFTWARE_TOKEN_MFA']}
        with self.assertRaises(SaaSError): mfa.setup(client,'private-token')
        client.associate_software_token.assert_not_called()

    def test_only_key_not_session_is_returned(self):
        client=Mock(); client.get_user.return_value={}
        client.associate_software_token.return_value={'SecretCode':'ABCDEFGHIJKLMNOP','Session':'provider-private'}
        self.assertEqual(mfa.setup(client,'private-token'),{'secret':'ABCDEFGHIJKLMNOP'})

    def test_factor_enabled_only_after_successful_code_verification(self):
        client=Mock(); client.verify_software_token.return_value={'Status':'ERROR'}
        with self.assertRaises(SaaSError): mfa.confirm(client,'private-token',{'code':'123456'})
        client.set_user_mfa_preference.assert_not_called()
        client.verify_software_token.return_value={'Status':'SUCCESS'}
        self.assertTrue(mfa.confirm(client,'private-token',{'code':'123456'})['ok'])
        client.set_user_mfa_preference.assert_called_once_with(AccessToken='private-token',SoftwareTokenMfaSettings={'Enabled':True,'PreferredMfa':True})
class RequiredMfaTests(unittest.TestCase):
    def test_required_setup_uses_provider_session(self):
        client=Mock(); client.associate_software_token.return_value={'Session':'associated-private','SecretCode':'ABCDEFGHIJKLMNOP'}
        session,secret=mfa.begin_required(client,{'Session':'first-private','ChallengeParameters':{'MFAS_CAN_SETUP':'["SOFTWARE_TOKEN_MFA"]'}})
        self.assertEqual(session,'associated-private'); self.assertEqual(secret,'ABCDEFGHIJKLMNOP')
        client.associate_software_token.assert_called_once_with(Session='first-private')
        client.verify_software_token.return_value={'Status':'SUCCESS','Session':'verified-private'}
        mfa.finish_required(client,'client',{'session':session,'username':'verified-user'},'123456')
        client.respond_to_auth_challenge.assert_called_once_with(ClientId='client',ChallengeName='MFA_SETUP',Session='verified-private',ChallengeResponses={'USERNAME':'verified-user'})

    def test_failed_verification_cannot_complete_signin(self):
        client=Mock(); client.verify_software_token.return_value={'Status':'ERROR','Session':'private'}
        with self.assertRaises(SaaSError): mfa.finish_required(client,'client',{'session':'private','username':'u'},'123456')
        client.respond_to_auth_challenge.assert_not_called()

