import copy
import unittest
from saas.permissions import Workspace,SaaSError,require_user_creation
from saas.schema import inspect_legacy


class PermissionTests(unittest.TestCase):
    def workspace(self,*roles,**kwargs):
        return Workspace('hospital-a','user','HOSPITAL',frozenset(roles),**kwargs)

    def test_admin_cannot_sign_or_read_clinical_records(self):
        admin=self.workspace('hospital_admin',can_sign=True)
        self.assertTrue(admin.allows('billing.read'))
        for capability in ('reports.sign','reports.read','reports.edit','ai.generate','platform.admin'):
            self.assertFalse(admin.allows(capability))

    def test_signer_requires_radiologist_and_explicit_grant(self):
        self.assertFalse(self.workspace('radiologist').allows('reports.sign'))
        self.assertTrue(self.workspace('radiologist',can_sign=True).allows('reports.sign'))
        self.assertFalse(self.workspace('transcriptionist',can_sign=True).allows('reports.sign'))

    def test_transcriptionist_needs_assignment_and_matching_tenant(self):
        user=self.workspace('transcriptionist')
        user.require_report({'tenant_id':'hospital-a'},assigned=True)
        for report,assigned in (({'tenant_id':'hospital-a'},False),({'tenant_id':'hospital-b'},True)):
            with self.assertRaises(SaaSError): user.require_report(report,assigned)

    def test_personal_owner_does_not_implicitly_gain_signing(self):
        owner=Workspace('personal','user','INDIVIDUAL',frozenset({'individual_owner'}))
        self.assertTrue(owner.allows('billing.read'))
        self.assertFalse(owner.allows('users.create'))
        self.assertFalse(owner.allows('reports.sign'))

    def test_ai_access_is_explicit(self):
        self.assertFalse(self.workspace('radiologist').allows('ai.generate'))
        self.assertTrue(self.workspace('radiologist',can_use_ai=True).allows('ai.generate'))
        transcriptionist=self.workspace('transcriptionist',can_use_ai=True)
        self.assertTrue(transcriptionist.allows('ai.generate'))
        self.assertFalse(transcriptionist.allows('reports.sign'))

    def test_creation_flag_and_reserved_seats(self):
        admin=self.workspace('hospital_admin')
        with self.assertRaises(SaaSError):
            require_user_creation(admin,{'max_users':10,'allow_hospital_admin_create_users':False},1,0)
        with self.assertRaises(SaaSError) as caught:
            require_user_creation(admin,{'max_users':2,'allow_hospital_admin_create_users':True},1,1)
        self.assertEqual(caught.exception.code,'SeatLimit')
        require_user_creation(admin,{'max_users':2,'allow_hospital_admin_create_users':True},1,0)


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.state={'patients':[{'id':'p1','name':'PRIVATE PATIENT'}],
                    'reports':[{'id':'r1','patientId':'p1','status':'Final','body':'PRIVATE REPORT'}]}

    def test_preflight_does_not_mutate_or_disclose_clinical_text(self):
        original=copy.deepcopy(self.state)
        result=inspect_legacy([('issuer','sub','hospital-a','admin',True)],[('hospital-a',self.state)])
        self.assertTrue(result['ready']); self.assertEqual(result['reports'],1)
        self.assertEqual(self.state,original)
        self.assertNotIn('PRIVATE',str(result))

    def test_foreign_ownership_or_missing_patient_blocks_backfill(self):
        self.state['reports'][0]['tenantId']='hospital-b'
        self.state['reports'][0]['patientId']='missing'
        result=inspect_legacy([],[('hospital-a',self.state)])
        self.assertFalse(result['ready']); self.assertEqual(len(result['issues']),2)

    def test_duplicate_report_id_blocks_backfill(self):
        self.state['reports']*=2
        self.assertFalse(inspect_legacy([],[('hospital-a',self.state)])['ready'])

    def test_unknown_status_is_not_silently_converted(self):
        self.state['reports'][0]['status']='Approved'
        self.assertFalse(inspect_legacy([],[('hospital-a',self.state)])['ready'])


if __name__=='__main__': unittest.main()
