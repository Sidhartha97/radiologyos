import unittest
from unittest.mock import Mock
from saas import recovery
from saas.permissions import SaaSError


class RecoveryTests(unittest.TestCase):
    def test_unknown_accounts_have_identical_request_response(self):
        client=Mock(); expected=recovery.request(client,'client',{'email':'known@example.test'})
        error=Exception('private user detail'); error.response={'Error':{'Code':'UserNotFoundException'}}
        client.forgot_password.side_effect=error
        self.assertEqual(expected,recovery.request(client,'client',{'email':'unknown@example.test'}))

    def test_confirmation_does_not_return_password_or_provider_details(self):
        client=Mock(); payload={'email':'test@example.test','code':'123456','password':'LongDummyPassword!'}
        self.assertNotIn(payload['password'],str(recovery.confirm(client,'client',payload)))
        client.confirm_forgot_password.side_effect=Exception('private sensitive provider detail')
        with self.assertRaises(SaaSError) as caught: recovery.confirm(client,'client',payload)
        self.assertNotIn('sensitive',str(caught.exception))
