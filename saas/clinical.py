"""Shared clinical snapshot transaction and assignment-filtered API views."""
from contextlib import contextmanager
from copy import deepcopy
from psycopg.types.json import Jsonb
from tenant_store import STATE,empty_state
from .tenants import workspace_transaction
from .permissions import SaaSError


@contextmanager
def transaction(connect,identity,tenant_id,writable=False):
    with workspace_transaction(connect,identity,tenant_id) as (conn,workspace):
        workspace.require('reports.read')
        if writable:
            conn.execute('INSERT INTO public.hospital_state(tenant_id,payload) VALUES(%s,%s) ON CONFLICT DO NOTHING',
                         (tenant_id,Jsonb(empty_state())))
        row=conn.execute('SELECT payload FROM public.hospital_state WHERE tenant_id=%s'+(' FOR UPDATE' if writable else ''),(tenant_id,)).fetchone()
        state=row[0] if row else empty_state()
        token=STATE.set(state)
        try:
            yield conn,workspace,state
            if writable:
                conn.execute('UPDATE public.hospital_state SET payload=%s WHERE tenant_id=%s',(Jsonb(state),tenant_id))
        finally: STATE.reset(token)


def view(conn,workspace,state):
    workspace.require('reports.read')
    controls={row[0]:row for row in conn.execute('''SELECT report_id,patient_id,version,workflow_status,signed_by,signed_at
        FROM saas.report_controls WHERE tenant_id=%s''',(workspace.tenant_id,)).fetchall()}
    assigned={row[0] for row in conn.execute('SELECT report_id FROM saas.report_assignments WHERE tenant_id=%s AND user_id=%s',
                                           (workspace.tenant_id,workspace.user_id)).fetchall()}
    result={'reports':[],'patients':[],'templates':deepcopy(state.get('templates',[])),
            'modalities':deepcopy(state.get('modalities',[])),'saas':True}
    for original in state.get('reports',[]):
        control=controls.get(original.get('id'))
        if not control or original.get('patientId')!=control[1]: continue
        if original.get('tenantId',workspace.tenant_id)!=workspace.tenant_id: continue
        if not workspace.allows('reports.read_all') and original['id'] not in assigned: continue
        report=deepcopy(original)
        report.update(version=control[2],workflowStatus=control[3],signedBy=str(control[4]) if control[4] else None,
                      signedAt=control[5].isoformat() if control[5] else None)
        result['reports'].append(report)
    patients={report['patientId'] for report in result['reports']}
    result['patients']=[deepcopy(p) for p in state.get('patients',[])
        if p.get('tenantId',workspace.tenant_id)==workspace.tenant_id and (workspace.allows('reports.read_all') or p['id'] in patients)]
    return result


def create_report(conn,workspace,state,payload):
    import uuid
    from datetime import datetime,timezone
    workspace.require('reports.create')
    patient_id=payload.get('patientId'); modality=payload.get('modality')
    if not any(p.get('id')==patient_id and p.get('tenantId',workspace.tenant_id)==workspace.tenant_id for p in state.get('patients',[])):
        raise SaaSError('Patient not found.','NotFound',404)
    # Transcriptionists can create a new report only for an already assigned patient.
    if not workspace.allows('reports.read_all') and patient_id not in {p['id'] for p in view(conn,workspace,state)['patients']}:
        raise SaaSError('Patient not found.','NotFound',404)
    if not isinstance(modality,str) or modality not in state.get('modalities',[]):
        raise SaaSError('Select a workspace modality.','InvalidModality',400)
    if payload.get('status','Draft')!='Draft':
        raise SaaSError('Create a draft before signing.','DraftRequired',409)
    body=payload.get('body','')
    if not isinstance(body,str) or len(body.encode())>1_000_000: raise SaaSError('Invalid report body.','InvalidReport',400)
    report={'id':uuid.uuid4().hex,'patientId':patient_id,'modality':modality,'body':body,
            'tenantId':workspace.tenant_id,'status':'Draft','workflowStatus':'DRAFT','version':1,
            'updatedAt':datetime.now(timezone.utc).isoformat()}
    conn.execute('INSERT INTO saas.report_controls(tenant_id,report_id,patient_id) VALUES(%s,%s,%s)',
                 (workspace.tenant_id,report['id'],patient_id))
    conn.execute('INSERT INTO saas.report_assignments(tenant_id,report_id,user_id) VALUES(%s,%s,%s)',
                 (workspace.tenant_id,report['id'],workspace.user_id))
    state.setdefault('reports',[]).append(report)
    return report
