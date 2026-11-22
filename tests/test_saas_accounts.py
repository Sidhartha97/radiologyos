import unittest
from saas.accounts import email_address,employee_code
from saas.permissions import SaaSError
from saas.sessions import token_digest


class AccountValidationTests(unittest.TestCase):
    def test_email_and_employee_identifiers_are_normalized(self):
        self.assertEqual(email_address('  Doctor@Example.test '),'doctor@example.test')
        self.assertEqual(employee_code(' emp-001 '),'EMP-001')

    def test_ambiguous_or_unbounded_employee_identifiers_are_rejected(self):
        for value in ('','EMP 001','../admin','a@b.test','é001','A'*41,None):
            with self.subTest(value=value),self.assertRaises(SaaSError): employee_code(value)

    def test_invalid_email_rejected(self):
        for value in ('a','a@b','a b@example.test','a@@example.test',None):
            with self.subTest(value=value),self.assertRaises(SaaSError): email_address(value)

    def test_session_digest_never_contains_the_token(self):
        token='synthetic-test-access-token'
        self.assertEqual(len(token_digest(token)),64)
        self.assertNotIn(token,token_digest(token))


if __name__=='__main__': unittest.main()
