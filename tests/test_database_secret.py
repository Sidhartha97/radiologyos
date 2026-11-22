import json
import os
import unittest
from unittest.mock import Mock,patch
from saas import database_secret
from saas.permissions import SaaSError


class DatabaseSecretTests(unittest.TestCase):
    def setUp(self): database_secret._cache.clear()

    def test_runtime_secret_is_cached_and_copy_isolated(self):
        value={'host':'database.example.test','port':5432,'dbname':'radiology','username':'runtime','password':'synthetic-only'}
        client=Mock(); client.get_secret_value.return_value={'SecretString':json.dumps(value)}
        with patch.dict(os.environ,{'SAAS_DATABASE_SECRET_ARN':'runtime-secret'}),patch('boto3.client',return_value=client):
            first=database_secret.credentials(); first['password']='modified'
            self.assertEqual(database_secret.credentials()['password'],'synthetic-only')
        client.get_secret_value.assert_called_once_with(SecretId='runtime-secret')

    def test_secret_errors_never_leak_provider_details(self):
        client=Mock(); client.get_secret_value.side_effect=RuntimeError('private-password-value')
        with patch.dict(os.environ,{'SAAS_DATABASE_SECRET_ARN':'runtime-secret'}),patch('boto3.client',return_value=client):
            with self.assertRaises(SaaSError) as caught: database_secret.credentials()
        self.assertNotIn('private-password-value',str(caught.exception))

    def test_no_configuration_fails_closed(self):
        with patch.dict(os.environ,{'SAAS_DATABASE_SECRET_ARN':''}):
            with self.assertRaises(SaaSError): database_secret.credentials()
