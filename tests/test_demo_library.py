import base64
import unittest
from demo_library import DemoLibrary
from saas.permissions import SaaSError

TEXT=b'USG ABDOMEN\nFINDINGS\nSynthetic finding.\nIMPRESSION\nSynthetic impression.'


class DemoLibraryTests(unittest.TestCase):
    def setUp(self): self.library=DemoLibrary()
    def preview(self):
        return self.library.handle('hospital-a','POST','/api/library/validate',{'name':'demo.txt','modality':'USG','content':base64.b64encode(TEXT).decode()})
    def batch(self):
        return self.library.handle('hospital-a','POST','/api/library/batches',{'modality':'USG','files':[self.preview()]})

    def test_local_staging_never_claims_indexing(self):
        batch=self.batch(); fields=batch['uploads'][0]['fields']
        self.assertEqual(batch['uploads'][0]['transport'],'local-demo')
        self.library.handle('hospital-a','POST','/api/library/demo-upload',{**fields,'content':base64.b64encode(TEXT).decode()})
        result=self.library.handle('hospital-a','POST',f"/api/library/batches/{batch['batchId']}/complete",{})
        self.assertEqual(result['status'],'LOCAL_STAGED')
        status=self.library.handle('hospital-a','GET','/api/library',{})
        self.assertEqual(status['counts']['USG'],{'LOCAL_STAGED':1}); self.assertNotIn('COMPLETED',str(status))
        self.assertTrue(self.preview()['duplicate']); self.assertNotIn(TEXT.decode(),str(status))

    def test_other_hospital_cannot_stage_or_read_batch(self):
        batch=self.batch()
        with self.assertRaises(SaaSError) as caught:
            self.library.handle('hospital-b','POST','/api/library/demo-upload',{**batch['uploads'][0]['fields'],'content':base64.b64encode(TEXT).decode()})
        self.assertEqual(caught.exception.status,404)
        self.assertEqual(self.library.handle('hospital-b','GET','/api/library',{})['batches'],[])

    def test_checksum_rejection_and_missing_upload_are_explicit(self):
        batch=self.batch()
        with self.assertRaises(SaaSError) as caught:
            self.library.handle('hospital-a','POST','/api/library/demo-upload',{**batch['uploads'][0]['fields'],'content':base64.b64encode(b'changed').decode()})
        self.assertEqual(caught.exception.code,'ChecksumMismatch')
        self.library.handle('hospital-a','POST',f"/api/library/batches/{batch['batchId']}/complete",{})
        self.assertEqual(self.library.handle('hospital-a','GET','/api/library',{})['batches'][0]['files'][0]['error']['code'],'FileNotStaged')

    def test_invalid_file_does_not_leak_document_text(self):
        with self.assertRaises(SaaSError) as caught:
            self.library.handle('hospital-a','POST','/api/library/validate',{'name':'unsupported.doc','modality':'USG','content':base64.b64encode(TEXT).decode()})
        self.assertNotIn('Synthetic finding',str(caught.exception)); self.assertEqual(caught.exception.code,'InvalidFile')
