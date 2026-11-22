"""Assigned-report AI adapter; the existing RAG and chat-generation logic stay intact."""
import os
from contextlib import contextmanager
from psycopg.types.json import Jsonb
from patient_chat import PatientChat,fresh,ChatConflict
from ingestion.scope import resolve_modality
from .tenants import workspace_transaction
from .permissions import SaaSError
from .locks import require_editable


def authorize(conn,workspace,report_id,editable=False):
    workspace.require('ai.generate')
    row=conn.execute('SELECT patient_id,workflow_status FROM saas.report_controls WHERE tenant_id=%s AND report_id=%s',
                     (workspace.tenant_id,report_id)).fetchone()
    assigned=bool(conn.execute('SELECT 1 FROM saas.report_assignments WHERE tenant_id=%s AND report_id=%s AND user_id=%s',
                               (workspace.tenant_id,report_id,workspace.user_id)).fetchone())
    if not row or (not workspace.allows('reports.read_all') and not assigned):
        raise SaaSError('Report not found.','NotFound',404)
    if editable: require_editable(workspace,{'workflowStatus':row[1]})
    return row[0]


class ReportStore:
    def __init__(self,connect,identity,tenant,report_id,patient_id):
        self.connect,self.identity,self.tenant,self.report_id,self.patient_id=connect,identity,tenant,report_id,patient_id

    @contextmanager
    def transaction(self,tenant,patient,now):
        if tenant!=self.tenant or patient!=self.patient_id: raise SaaSError('Conversation scope mismatch.','NotFound',404)
        with workspace_transaction(self.connect,self.identity,self.tenant) as (conn,workspace):
            if authorize(conn,workspace,self.report_id)!=self.patient_id: raise SaaSError('Report ownership changed.','NotFound',404)
            initial=fresh(now)
            conn.execute('''INSERT INTO saas.report_conversations(tenant_id,report_id,payload,expires_at)
                VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING''',(tenant,self.report_id,Jsonb(initial),initial['expiresAt']))
            row=conn.execute('SELECT payload,expires_at FROM saas.report_conversations WHERE tenant_id=%s AND report_id=%s FOR UPDATE',
                             (tenant,self.report_id)).fetchone()
            if not row: raise SaaSError('Conversation unavailable.','NotFound',404)
            value=row[0] if row[1]>now else initial
            yield value
            conn.execute('UPDATE saas.report_conversations SET payload=%s,expires_at=%s WHERE tenant_id=%s AND report_id=%s',
                         (Jsonb(value),value['expiresAt'],tenant,self.report_id))


def handle(connect,identity,tenant,report_id,method,payload,generate,request_id,dispatch=None):
    with workspace_transaction(connect,identity,tenant) as (conn,workspace):
        patient=authorize(conn,workspace,report_id,editable=method=='POST')
    if method not in ('GET','POST','DELETE'): raise SaaSError('Method unavailable.','MethodNotAllowed',405)
    def guarded(generation):
        if os.getenv('SAAS_AI_ENABLED','false').lower()!='true': raise SaaSError('AI generation is disabled.','AIUnavailable',403)
        with workspace_transaction(connect,identity,tenant) as (conn,workspace):
            if authorize(conn,workspace,report_id,editable=True)!=patient: raise SaaSError('Report ownership changed.')
        # PatientChat builds bounded history solely from this report's store.
        return generate(generation)
    service=PatientChat(ReportStore(connect,identity,tenant,report_id,patient),guarded,dispatch=dispatch)
    if method=='GET': return service.read(tenant,patient)
    if method=='DELETE': return service.clear(tenant,patient)
    if os.getenv('SAAS_AI_ENABLED','false').lower()!='true' or generate is None:
        raise SaaSError('AI generation is disabled. Contact your administrator.','AIUnavailable',403)
    if payload.get('patientId',patient)!=patient or payload.get('reportId',report_id)!=report_id:
        raise SaaSError('Report not found.','NotFound',404)
    value={**payload,'patientId':patient,'reportId':report_id,'_requestId':request_id}
    value['_resolvedModality']=resolve_modality(value)
    try: return service.submit(tenant,patient,value)
    except ChatConflict as exc: raise SaaSError(str(exc),'ChatBusy',409) from None
    except ValueError as exc: raise SaaSError(str(exc),'InvalidChatRequest',400) from None
