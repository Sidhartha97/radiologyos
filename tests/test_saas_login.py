import unittest
from unittest.mock import Mock,MagicMock
from saas.api import API
from saas.tenants import VerifiedIdentity
from saas.login import LoginState
from saas.permissions import SaaSError
from security import AccessError


class SignInStateTests(unittest.TestCase):
    def test_email_delivery_failure_is_safe_and_does_not_provision(self):
        from botocore.exceptions import ClientError
        connect=Mock(); client=Mock()
        api=API(connect,Mock()); api.auth=Mock()
        api.auth.verified.return_value=(VerifiedIdentity('trusted-issuer','trusted-subject'),{})
        client.get_user.return_value={'UserAttributes':[
            {'Name':'sub','Value':'trusted-subject'},{'Name':'email','Value':'owner@example.test'},
            {'Name':'email_verified','Value':'false'}]}
        client.get_user_attribute_verification_code.side_effect=ClientError(
            {'Error':{'Code':'CodeDeliveryFailureException','Message':'private-provider-details'}},
            'GetUserAttributeVerificationCode')
        with self.assertRaises(AccessError) as caught:
            api.complete_login('synthetic-token',client,None)
        self.assertEqual(caught.exception.status,503)
        self.assertIn('email verification',str(caught.exception))
        self.assertNotIn('private-provider-details',str(caught.exception))
        connect.assert_not_called()

    def test_platform_invitation_not_accepted_before_email_verification(self):
        connect=Mock(); client=Mock()
        api=API(connect,Mock()); api.auth=Mock()
        api.auth.verified.return_value=(VerifiedIdentity('trusted-issuer','trusted-subject'),{})
        client.get_user.return_value={'UserAttributes':[
            {'Name':'sub','Value':'trusted-subject'},{'Name':'email','Value':'owner@example.test'},
            {'Name':'email_verified','Value':'false'}]}
        result,token=api.complete_login('synthetic-token',client,None)
        self.assertEqual(result['challenge'],'VERIFY_EMAIL'); self.assertIsNone(token)
        connect.assert_not_called(); api.auth.login.assert_not_called()

    def test_verified_identity_accepts_only_backend_resolved_invitation(self):
        connect=MagicMock(); client=Mock()
        api=API(connect,Mock()); api.auth=Mock()
        api.auth.verified.return_value=(VerifiedIdentity('trusted-issuer','trusted-subject'),{})
        api.auth.login.return_value={}
        client.get_user.return_value={'UserAttributes':[
            {'Name':'sub','Value':'trusted-subject'},{'Name':'email','Value':'owner@example.test'},
            {'Name':'email_verified','Value':'true'}]}
        api.complete_login('synthetic-token',client,None)
        connect.return_value.__enter__.return_value.execute.assert_any_call(
            'SELECT saas.accept_platform_invitation(%s,%s,%s)',
            ('trusted-issuer','trusted-subject','owner@example.test'))

    def test_throttling_expires_without_unbounded_address_growth(self):
        now=[0.0]; state=LoginState(clock=lambda:now[0])
        for _ in range(15): state.throttle('local-test')
        with self.assertRaises(SaaSError) as caught: state.throttle('local-test')
        self.assertEqual(caught.exception.status,429)
        now[0]=301; state.throttle('new-address')
        self.assertNotIn('local-test',state.attempts)

    def test_challenge_is_private_single_use_and_expires(self):
        now=[0.0]; state=LoginState(clock=lambda:now[0])
        public=state.remember({'ChallengeName':'NEW_PASSWORD_REQUIRED','Session':'provider-secret'},'private-login@example.test','hospital-a')
        self.assertEqual(set(public),{'challenge','challengeId'})
        self.assertNotIn('provider-secret',str(public)); self.assertNotIn('private-login',str(public))
        private=state.consume(public['challengeId'])
        self.assertEqual(private['hospital'],'hospital-a')
        with self.assertRaises(SaaSError): state.consume(public['challengeId'])
        public=state.remember({'ChallengeName':'SMS_MFA','Session':'provider-secret'},'user')
        now[0]=601
        with self.assertRaises(SaaSError): state.consume(public['challengeId'])


if __name__=='__main__': unittest.main()
