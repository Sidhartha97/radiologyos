"""No live AWS writes: exact ownership, verification and failure behavior."""
import io
import tarfile
import unittest
from unittest.mock import Mock,patch
from saas.deletion import CloudCleanup,prefixes,tick
from scripts.deploy_aws import source_bundle,environment


class CleanupTests(unittest.TestCase):
    def cloud(self):
        cfg={'bucket':'reports','kb':'kb','old':'old','final':'final','pool':'pool','region':'us-east-1'}
        return CloudCleanup(cfg,Mock(),Mock(),Mock(),Mock())

    def test_prefixes_are_exact_and_reject_path_traversal(self):
        self.assertEqual(prefixes('hospital-a')[1],'old-reports/hospital-a/')
        for value in ('../b','a/b','','a?','a%2Fb',None):
            with self.assertRaises(ValueError): prefixes(value)

    def test_s3_versions_and_markers_verified_until_empty(self):
        cloud=self.cloud()
        cloud.s3.list_object_versions.side_effect=[{'Versions':[{'Key':'historical-originals/a/r','VersionId':'1'}],
            'DeleteMarkers':[{'Key':'historical-originals/a/r','VersionId':'2'}]}, {},{},{}]
        cloud.s3.delete_objects.return_value={}
        cloud.delete_storage('a')
        objects=cloud.s3.delete_objects.call_args.kwargs['Delete']['Objects']
        self.assertEqual({x['VersionId'] for x in objects},{'1','2'})
        self.assertEqual(cloud.s3.list_object_versions.call_count,4)

    def test_foreign_s3_object_or_error_never_counts_as_deleted(self):
        cloud=self.cloud()
        cloud.s3.list_object_versions.return_value={'Versions':[{'Key':'old-reports/b/r','VersionId':'v'}]}
        with self.assertRaises(ValueError): cloud.delete_storage('a')
        cloud.s3.delete_objects.assert_not_called()
        cloud.s3.list_object_versions.return_value={'Versions':[{'Key':'historical-originals/a/r','VersionId':'v'}]}
        cloud.s3.delete_objects.return_value={'Errors':[{'Code':'AccessDenied'}]}
        with self.assertRaises(RuntimeError): cloud.delete_storage('a')

    def test_index_requires_matching_identifiers_and_not_found_status(self):
        cloud=self.cloud(); uri='s3://reports/old-reports/a/r.txt'; docs={'old':[uri]}
        def result(uri,status): return {'documentDetails':[{'identifier':{'dataSourceType':'S3','s3':{'uri':uri}},'status':status}]}
        cloud.agent.get_knowledge_base_documents.return_value=result(uri,'DELETE_IN_PROGRESS')
        self.assertFalse(cloud.delete_index('a',docs))
        cloud.agent.delete_knowledge_base_documents.assert_called_once()
        cloud.agent.get_knowledge_base_documents.return_value=result('s3://reports/old-reports/b/r.txt','NOT_FOUND')
        self.assertFalse(cloud.delete_index('a',docs,True))
        cloud.agent.get_knowledge_base_documents.return_value=result(uri,'NOT_FOUND')
        self.assertTrue(cloud.delete_index('a',docs,True))
        cloud.agent.get_knowledge_base_documents.return_value=result(uri,'FAILED')
        with self.assertRaises(RuntimeError): cloud.delete_index('a',docs,True)
        with self.assertRaises(ValueError): cloud.delete_index('a',{'old':['s3://reports/old-reports/ab/r.txt']})

    def test_discovery_keeps_only_tenant_index_orphans(self):
        cloud=self.cloud()
        cloud.s3.get_paginator.return_value.paginate.return_value=[]
        cloud.agent.list_knowledge_base_documents.return_value={'documentDetails':[
            {'identifier':{'s3':{'uri':'s3://reports/old-reports/a/orphan.txt'}}},
            {'identifier':{'s3':{'uri':'s3://reports/old-reports/ab/foreign.txt'}}}]}
        result=cloud.discover('a')
        self.assertEqual(result['documents']['old'],['s3://reports/old-reports/a/orphan.txt'])
        self.assertNotIn('foreign',str(result))

    def test_identity_must_match_pool_and_subject(self):
        cloud=self.cloud(); item={'issuer':'https://cognito-idp.us-east-1.amazonaws.com/pool','subject':'expected','email':'a@example.test'}
        cloud.cognito.admin_get_user.return_value={'Username':'canonical','UserAttributes':[{'Name':'sub','Value':'other'}]}
        with self.assertRaises(ValueError): cloud.delete_identities([item])
        cloud.cognito.admin_delete_user.assert_not_called()
        cloud.cognito.admin_get_user.return_value['UserAttributes'][0]['Value']='expected'
        cloud.delete_identities([item])
        cloud.cognito.admin_delete_user.assert_called_once_with(UserPoolId='pool',Username='canonical')

    def test_worker_failure_persists_safe_reason_without_marking_complete(self):
        cloud=Mock(); cloud.delete_storage.side_effect=RuntimeError('S3VersionDeletionFailed')
        job={'jobId':'job','tenantId':'a','workerToken':'private','stage':'QUARANTINE','manifest':{},'quarantineUntil':'2020-01-01T00:00:00+00:00'}
        cloud.discover.return_value={'documents':{},'objectVersions':0}
        with patch('saas.deletion.call',side_effect=lambda connect,action,**kw:job if action=='NEXT' else {} ) as call, self.assertLogs('radiology.deletion'):
            tick(Mock(),cloud)
        self.assertNotIn('COMPLETE',[c.args[1] for c in call.call_args_list])
        saved=call.call_args.kwargs['payload']
        self.assertEqual(saved['state'],'FAILED'); self.assertEqual(saved['stage'],'S3_VERSIONS')
        self.assertEqual(saved['errorCode'],'S3VersionDeletionFailed')
        cloud.delete_identities.assert_not_called()

    def test_source_bundle_contains_no_environment_state_or_reports(self):
        digest,bundle=source_bundle()
        self.assertEqual(len(digest),32)
        with tarfile.open(fileobj=io.BytesIO(bundle),mode='r:gz') as archive:
            names=archive.getnames()
        self.assertIn('migrations/027_customer_deletion.sql',names)
        self.assertIn('frontend/src/main.tsx',names)
        self.assertFalse(any(name.startswith(('terraform/','.git/','historical_reports/','uploads/')) or '.env' in name for name in names))


if __name__=='__main__': unittest.main()
