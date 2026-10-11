import copy
import base64
import hashlib
import io
import json
import unittest
from unittest.mock import Mock, patch
from ingestion import library

TEXT=b'USG ABDOMEN\nFINDINGS\nExample finding.\nIMPRESSION\nExample impression.'


class Tracking:
    values={}
    def __init__(self, table): pass
    def acquire(self, tenant): pass
    def renew(self): pass
    def release(self): pass
    def records(self, tenant): return copy.deepcopy(self.values)
    def put(self, tenant, identity, payload): self.values[identity]=copy.deepcopy(payload)


class LibraryTests(unittest.TestCase):
    def service(self):
        service=object.__new__(library.Library)
        service.cfg={'tenant':'t1','bucket':'b','prefix':'old-reports/','originalPrefix':'historical-originals/','kb':'kb','ds':'ds'}
        service.tenant='t1'; service.table=Mock();service.s3=Mock();service.agent=Mock()
        return service

    def test_validation_rejects_combined_and_unsupported(self):
        for text,name in [(TEXT+b'\n'+TEXT,'report.txt'),(TEXT,'report.doc'),(b'','empty.txt')]:
            with self.subTest(name=name),self.assertRaises(ValueError): library.validate(text,name,'USG')

    def test_signed_upload_is_tenant_scoped_and_size_bounded(self):
        service=self.service();service.save=Mock();service.s3.generate_presigned_post.return_value={'url':'https://s3.example','fields':{}}
        result=service.create({'modality':'USG','files':[{'name':'../../file.txt','size':len(TEXT),'sha256':hashlib.sha256(TEXT).hexdigest()}]})
        call=service.s3.generate_presigned_post.call_args
        self.assertTrue(call.args[1].startswith('historical-originals/t1/browser/'))
        self.assertIn(['content-length-range',len(TEXT),len(TEXT)],call.kwargs['Conditions'])
        self.assertEqual(service.save.call_args.args[0]['files'][0]['name'],'file.txt')
        self.assertNotIn('url',service.save.call_args.args[0])
        self.assertEqual(len(result['uploads']),1)

    def test_batch_read_cannot_cross_tenant(self):
        service=self.service();service.table.get_item.return_value={}
        with self.assertRaisesRegex(ValueError,'hospital'): service.get('a'*32)
        self.assertEqual(service.table.get_item.call_args.kwargs['Key']['PK'],'TENANT#t1')

    def test_real_kms_upload_form_uses_sigv4_in_us_east_1(self):
        import boto3
        session = boto3.Session(aws_access_key_id='test-key',
                               aws_secret_access_key='test-secret', region_name='us-east-1')
        cfg = {**self.service().cfg, 'region':'us-east-1', 'bucket':'test-reports'}
        def client(service, **kwargs):
            return session.client(service, **kwargs) if service == 's3' else Mock()
        kms = 'arn:aws:kms:us-east-1:123456789012:key/test-key'
        with patch.object(library, 'settings', return_value=cfg), \
             patch.object(library, 'connect_manifest', return_value=Mock()), \
             patch.object(boto3, 'client', side_effect=client), \
             patch.dict('os.environ', {'RAG_KMS_KEY_ID':kms}):
            service = library.Library()
            service.save = Mock()
            result = service.create({'modality':'USG', 'files':[
                {'name':'report.txt', 'size':len(TEXT), 'sha256':hashlib.sha256(TEXT).hexdigest()}]})
        fields = result['uploads'][0]['fields']
        self.assertEqual(fields['x-amz-algorithm'], 'AWS4-HMAC-SHA256')
        self.assertIn('/us-east-1/s3/aws4_request', fields['x-amz-credential'])
        self.assertIn('x-amz-signature', fields)
        self.assertNotIn('AWSAccessKeyId', fields)
        self.assertNotIn('signature', fields)
        policy = json.loads(base64.b64decode(fields['policy']))
        self.assertIn({'x-amz-server-side-encryption':'aws:kms'}, policy['conditions'])
        self.assertIn({'x-amz-server-side-encryption-aws-kms-key-id':kms}, policy['conditions'])

    def test_error_detail_excludes_credentials_and_document_text(self):
        error=RuntimeError('private patient text SECRET_PASSWORD')
        with self.assertLogs('radiology.library') as logs: result=library.error_detail(error,'indexing')
        self.assertNotIn('SECRET_PASSWORD',str(result)+str(logs.output))
        self.assertEqual(result['stage'],'indexing')

    def test_worker_indexing_and_active_job_followup(self):
        service=self.service();Tracking.values={}
        batch={'id':'a'*32,'status':'READY','modality':'USG','events':[],
               'files':[{'id':'f','name':'report.txt','status':'UPLOADED','size':len(TEXT),'sha256':hashlib.sha256(TEXT).hexdigest(),'originalKey':'historical-originals/t1/browser/test.txt'}]}
        service.batches=lambda:[copy.deepcopy(batch)]
        service.get=lambda _:copy.deepcopy(batch)
        def save(value): batch.clear();batch.update(copy.deepcopy(value))
        service.save=save
        service.s3.get_object.return_value={'Body':io.BytesIO(TEXT)}
        service.agent.get_ingestion_job.return_value={'ingestionJob':{'status':'IN_PROGRESS'}}
        jobs=[{'status':'WAIT_FOR_ACTIVE_JOB','job_id':'previous'},{'status':'STARTING','job_id':'new'}]
        with patch.object(library,'Manifest',Tracking),patch.object(library,'put_if_changed'),patch.object(library,'queue_ingestion',side_effect=jobs) as queue,patch.object(library,'sync_status'):
            service.tick()
            self.assertTrue(batch['needsSync']);self.assertEqual(batch['status'],'INDEXING')
            self.assertEqual(batch['files'][0]['status'],'INDEXING')
            service.tick()
            self.assertFalse(batch['needsSync']);self.assertEqual(queue.call_count,2)
            for item in Tracking.values.values(): item['status']='COMPLETED'
            service.agent.get_ingestion_job.return_value={'ingestionJob':{'status':'COMPLETE'}}
            service.tick()
            self.assertEqual(batch['status'],'COMPLETED');self.assertEqual(batch['files'][0]['status'],'INDEXED')
            self.assertEqual(queue.call_count,2)

    def test_checksum_failure_is_not_indexed(self):
        service=self.service();Tracking.values={}
        batch={'id':'a'*32,'status':'READY','modality':'USG','events':[], 'files':[{'id':'f','name':'report.txt','status':'UPLOADED','size':len(TEXT),'sha256':'0'*64,'originalKey':'key'}]}
        service.batches=lambda:[batch];service.get=lambda _:batch;service.save=Mock()
        service.s3.get_object.return_value={'Body':io.BytesIO(TEXT)}
        with patch.object(library,'Manifest',Tracking),patch.object(library,'put_if_changed') as upload,patch.object(library,'sync_status'),patch.object(library,'queue_ingestion') as queue,self.assertLogs('radiology.library'):
            service.tick()
        upload.assert_not_called();queue.assert_not_called()
        self.assertEqual(batch['files'][0]['error']['stage'],'read_upload')
        self.assertEqual(batch['status'],'COMPLETED_WITH_ERRORS')

    def test_retry_recovers_interrupted_browser_upload(self):
        service=self.service()
        batch={'id':'a'*32,'status':'UPLOADING','files':[{'status':'UPLOADING'},{'status':'INDEXED'}]}
        service.get=lambda _:batch;service.save=Mock()
        with patch.object(library,'Manifest',Tracking): service.retry('a'*32)
        self.assertEqual(batch['status'],'READY')
        self.assertEqual(batch['files'][0]['status'],'UPLOADED')
        self.assertEqual(batch['files'][1]['status'],'INDEXED')
