import copy
import ast
from pathlib import Path
import threading
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from patient_chat import PatientChat, ChatConflict, bounded_history, fresh, HISTORY_CHARS


class MemoryStore:
    """Transactional test double; production uses PostgreSQL row locks."""
    def __init__(self):
        self.rows = {}
        self.lock = threading.Lock()

    @contextmanager
    def transaction(self, tenant, patient, now):
        with self.lock:
            value = copy.deepcopy(self.rows.get((tenant, patient), fresh(now)))
            if datetime.fromisoformat(value['expiresAt']) <= now:
                value = fresh(now)
            yield value
            self.rows[(tenant, patient)] = copy.deepcopy(value)


class PatientChatTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 11, tzinfo=timezone.utc)
        self.store = MemoryStore()
        self.jobs = []
        self.generate = Mock(return_value={'mode':'aws','text':'**USG ABDOMEN**\nUpdated report'})
        self.chat = PatientChat(self.store, self.generate, clock=lambda:self.now, dispatch=self.jobs.append)

    def submit(self, patient='p1', tenant='t1', **extra):
        return self.chat.submit(tenant, patient, {'prompt':'Generate a report','modality':'USG', **extra})

    def test_reopen_restores_completed_chat_from_store(self):
        self.submit(); self.jobs.pop()()
        reopened = PatientChat(self.store, self.generate, clock=lambda:self.now)
        state = reopened.read('t1','p1')
        self.assertEqual([m['role'] for m in state['messages']], ['user','assistant'])
        self.assertIn('Updated report', state['messages'][1]['draft'])

    def test_patient_and_tenant_isolation(self):
        self.submit(); self.jobs.pop()()
        self.assertEqual(self.chat.read('t1','p2')['messages'], [])
        self.assertEqual(self.chat.read('t2','p1')['messages'], [])
        self.submit(patient='p2'); self.jobs.pop()()
        self.assertEqual(self.generate.call_args.args[0]['_chatHistory'], [])

    def test_read_does_not_extend_retention_and_expired_chat_disappears(self):
        self.submit(); self.jobs.pop()()
        expires = self.chat.read('t1','p1')['expiresAt']
        self.now += timedelta(days=13)
        self.assertEqual(self.chat.read('t1','p1')['expiresAt'], expires)
        self.now += timedelta(days=1)
        self.assertEqual(self.chat.read('t1','p1')['messages'], [])

    def test_message_extends_expiry_and_latest_five_context_is_server_owned(self):
        for index in range(4):
            self.submit(prompt=f'finding {index}'); self.jobs.pop()()
        self.now += timedelta(days=10)
        self.submit(prompt='change size', intent='edit', currentReport='My manually edited report',
                    _chatHistory=[{'role':'user','content':'OTHER PATIENT'}])
        self.jobs.pop()()
        payload = self.generate.call_args.args[0]
        self.assertEqual(len(payload['_chatHistory']), 5)
        self.assertNotIn('OTHER PATIENT',str(payload))
        self.assertEqual(payload['currentReport'],'My manually edited report')
        self.assertEqual(datetime.fromisoformat(self.chat.read('t1','p1')['expiresAt']), self.now+timedelta(days=14))

    def test_duplicate_request_and_concurrent_submission(self):
        request_id = str(uuid.uuid4())
        self.submit(requestId=request_id)
        self.submit(requestId=request_id)
        self.assertEqual(len(self.jobs),1)
        with self.assertRaises(ChatConflict): self.submit()
        self.jobs.pop()()
        self.submit(requestId=request_id)
        self.assertEqual(self.jobs,[])
        self.assertEqual(len(self.chat.read('t1','p1')['messages']),2)

    def test_followup_uses_previous_draft_without_inserting_first(self):
        self.submit(reportId='r1', currentReport='Original editor')
        self.jobs.pop()()
        self.submit(prompt='Change liver size to 165 mm', reportId='r1', currentReport='Original editor')
        self.jobs.pop()()
        payload = self.generate.call_args.args[0]
        self.assertEqual(payload['intent'], 'edit')
        self.assertEqual(payload['currentReport'], '**USG ABDOMEN**\nUpdated report')

    def test_manual_editor_changes_take_priority_over_previous_draft(self):
        self.submit(reportId='r1', currentReport='Original editor'); self.jobs.pop()()
        self.submit(prompt='Change size', reportId='r1', currentReport='Manually revised text')
        self.jobs.pop()()
        self.assertEqual(self.generate.call_args.args[0]['currentReport'], 'Manually revised text')

    def test_explicit_new_study_does_not_edit_old_draft(self):
        self.submit(reportId='r1'); self.jobs.pop()()
        self.submit(prompt='Draft a CT brain report', reportId='r1')
        self.jobs.pop()()
        payload = self.generate.call_args.args[0]
        self.assertEqual(payload['intent'], 'new')
        self.assertEqual(payload['modality'], 'CT')

    def test_prompt_modality_then_followup_uses_generated_modality(self):
        self.submit(prompt='Generate a USG abdomen report', modality='MRI', reportId='r1')
        self.jobs.pop()()
        self.submit(prompt='Change liver size to 165 mm', modality='MRI', reportId='r1')
        self.jobs.pop()()
        payload = self.generate.call_args.args[0]
        self.assertEqual(payload['modality'], 'USG')
        self.assertEqual(payload['intent'], 'edit')

    def test_other_report_draft_is_not_used_as_edit_baseline(self):
        self.submit(reportId='r1'); self.jobs.pop()()
        self.submit(prompt='Change size', reportId='r2', currentReport='Separate report')
        self.jobs.pop()()
        self.assertEqual(self.generate.call_args.args[0]['currentReport'], 'Separate report')

    def test_cleared_chat_not_resurrected_by_running_job(self):
        self.submit()
        self.chat.clear('t1','p1')
        self.jobs.pop()()
        self.assertEqual(self.chat.read('t1','p1')['messages'],[])

    def test_interrupted_generation_times_out_and_late_result_is_ignored(self):
        self.submit()
        self.now += timedelta(minutes=11)
        state = self.chat.read('t1','p1')
        self.assertIsNone(state['pending'])
        self.assertTrue(state['messages'][-1]['error'])
        self.jobs.pop()()
        self.assertEqual(len(self.chat.read('t1','p1')['messages']),2)

    def test_errors_are_durable_but_not_editable_drafts(self):
        self.generate.side_effect = RuntimeError('private secret')
        self.submit()
        with self.assertLogs('radiology.chat'): self.jobs.pop()()
        message = self.chat.read('t1','p1')['messages'][-1]
        self.assertTrue(message['error'])
        self.assertNotIn('draft',message)
        self.assertNotIn('private secret',str(message))

    def test_current_report_required_and_never_silently_truncated(self):
        with self.assertRaises(ValueError): self.submit(intent='edit',currentReport='')
        with self.assertRaises(ValueError): self.submit(intent='edit',currentReport='x'*30001)
        self.assertEqual(self.jobs,[])

    def test_history_bounded_with_latest_messages_first(self):
        messages=[{'role':'assistant','text':str(i),'draft':str(i)*5000} for i in range(9)]
        history=bounded_history(messages)
        self.assertLessEqual(sum(len(m['content']) for m in history),HISTORY_CHARS)
        self.assertEqual(history[-1]['content'],'8'*5000)


class PatientOwnershipTests(unittest.TestCase):
    def setUp(self):
        source=Path(__file__).resolve().parents[1]/'server.py'
        tree=ast.parse(source.read_text())
        selected=ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='validate_chat_patient'],type_ignores=[])
        self.ns={'aws_settings':lambda:{'tenantId':'t1'},'db':{
            'patients':[{'id':'a','tenantId':'t1'},{'id':'b','tenantId':'t1'},{'id':'other','tenantId':'t2'}],
            'reports':[{'id':'rb','patientId':'b','tenantId':'t1'}]}}
        exec(compile(selected,str(source),'exec'),self.ns)

    def test_rejects_other_tenant_and_unknown_patient(self):
        for patient in ('other','missing'):
            with self.assertRaises(ValueError): self.ns['validate_chat_patient'](patient)

    def test_report_must_belong_to_requested_patient(self):
        with self.assertRaises(ValueError): self.ns['validate_chat_patient']('a','rb')
        self.assertEqual(self.ns['validate_chat_patient']('b','rb'),'t1')


if __name__ == '__main__':
    unittest.main()
