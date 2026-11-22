import unittest
from unittest.mock import patch
from email.message import Message
from saas.http_policy import validate
from saas.readiness import configuration
from saas.permissions import SaaSError


class HttpPolicyTests(unittest.TestCase):
    def headers(self,**values):
        result=Message()
        for key,value in {'Host':'localhost:8000','Content-Type':'application/json',**values}.items(): result[key]=value
        return result

    def test_rejects_negative_duplicate_and_chunked_lengths(self):
        for headers in (self.headers(**{'Content-Length':'-1'}),self.headers(**{'Transfer-Encoding':'chunked'})):
            with self.assertRaises(SaaSError): validate(headers,'POST','/api/reports')
        headers=self.headers(**{'Content-Length':'10'}); headers['Content-Length']='10'
        with self.assertRaises(SaaSError): validate(headers,'POST','/api/reports')

    def test_library_validation_accepts_bounded_large_json_not_general_mutations(self):
        headers=self.headers(**{'Content-Length':'3000000'})
        self.assertEqual(validate(headers,'POST','/api/library/validate'),3000000)
        with self.assertRaises(SaaSError) as caught: validate(headers,'POST','/api/reports')
        self.assertEqual(caught.exception.status,413)

    def test_production_host_origin_and_media_type_are_checked(self):
        with patch.dict('os.environ',{'APP_ENV':'production','APP_ALLOWED_HOSTS':'reports.example.test','APP_PUBLIC_ORIGIN':'https://reports.example.test'}):
            with self.assertRaises(SaaSError): validate(self.headers(),'GET','/api/data')
            headers=self.headers(Host='reports.example.test',Origin='http://reports.example.test')
            with self.assertRaises(SaaSError): validate(headers,'POST','/api/reports')
            self.assertEqual(validate(self.headers(Host='reports.example.test',Origin='https://reports.example.test'),'POST','/api/reports'),0)
        with self.assertRaises(SaaSError): validate(self.headers(**{'Content-Length':'4','Content-Type':'text/plain'}),'POST','/api/reports')

    def test_insecure_configuration_never_reports_ready(self):
        self.assertFalse(all(check['ok'] for check in configuration({})))
        env={'SAAS_ENABLED':'true','APP_COOKIE_SECURE':'true','SAAS_DATABASE_URL':'configured-but-not-returned','APP_PUBLIC_ORIGIN':'https://reports.example.test','APP_ALLOWED_HOSTS':'reports.example.test'}
        self.assertTrue(all(check['ok'] for check in configuration(env)))
        self.assertNotIn('configured-but-not-returned',str(configuration(env)))

    def test_demo_adapter_is_refused_in_production_mode(self):
        from saas.api import API
        with patch.dict('os.environ',{'APP_ENV':'production'}):
            with self.assertRaises(RuntimeError): API(None,None,demo_library=object())
