"""Report workflow service; caller owns a single workspace transaction.

Clinical snapshot, workflow event, signed version and publication job commit
together. There are no network/provider calls inside this transaction.
"""
import uuid
from . import locks
from .permissions import SaaSError

TRANSITIONS={
    'IN_TRANSCRIPTION':({'DRAFT','RETURNED_FOR_CORRECTION'},'reports.edit'),
    'READY_FOR_REVIEW':({'DRAFT','IN_TRANSCRIPTION','RETURNED_FOR_CORRECTION'},'reports.submit'),
    'UNDER_REVIEW':({'DRAFT','READY_FOR_REVIEW'},'reports.review'),
    'RETURNED_FOR_CORRECTION':({'READY_FOR_REVIEW','UNDER_REVIEW'},'reports.review'),
    'FINALIZED':({'DRAFT','READY_FOR_REVIEW','UNDER_REVIEW'},'reports.sign'),
    'CANCELLED':({'DRAFT','IN_TRANSCRIPTION','RETURNED_FOR_CORRECTION','READY_FOR_REVIEW','UNDER_REVIEW'},'reports.review'),
}


def save(conn,workspace,report_id,body,expected_version,credential=None,*,target=None,reason=None,request_id=None,modality=None):
    """Update the existing hospital JSON store and controls on this connection.

    Old generic report routes must be wired to this service before enabling
    commercial collaboration. A signed record cannot be edited in place.
    """
    from psycopg.types.json import Jsonb
    if type(expected_version) is not int or expected_version<1:
        raise SaaSError('Send the report version before saving.','VersionRequired',428)
    if not isinstance(body,str) or len(body.encode('utf-8'))>1_000_000:
        raise SaaSError('Report text is invalid or too large.','InvalidReport',400)
    if target is not None and target not in TRANSITIONS:
        raise SaaSError('Invalid workflow transition.','InvalidTransition',400)
    if reason is not None and (not isinstance(reason,str) or len(reason)>1000):
        raise SaaSError('Reason must be at most 1000 characters.','InvalidReason',400)
    if target in ('RETURNED_FOR_CORRECTION','CANCELLED') and not (reason or '').strip():
        raise SaaSError('Give a reason for returning or cancelling this report.','ReasonRequired',400)
    # All writers must take locks in this order: tenant JSON, then report control.
    # Existing hospital_state storage serializes all writes in the tenant.
    row=conn.execute('SELECT payload FROM public.hospital_state WHERE tenant_id=%s FOR UPDATE',(workspace.tenant_id,)).fetchone()
    if not row or not isinstance(row[0],dict): raise SaaSError('Report not found.','NotFound',404)
    state=row[0]
    report=next((r for r in state.get('reports',[]) if r.get('id')==report_id),None)
    if not report: raise SaaSError('Report not found.','NotFound',404)
    control=locks.check(conn,workspace,report_id,credential,expected_version)
    if report.get('patientId')!=control['patientId'] or report.get('tenantId',workspace.tenant_id)!=workspace.tenant_id:
        raise SaaSError('Report ownership is inconsistent.','OwnershipMismatch',409)
    current=control['workflowStatus']; desired=target or current
    if target:
        allowed,capability=TRANSITIONS[target]; workspace.require(capability)
        if current not in allowed: raise SaaSError('Transition is unavailable from the current report state.','InvalidTransition',409)
    parent=conn.execute('SELECT amends_report_id FROM saas.report_controls WHERE tenant_id=%s AND report_id=%s',(workspace.tenant_id,report_id)).fetchone()[0]
    if desired=='FINALIZED' and parent: desired='AMENDED'
    signing=desired in ('FINALIZED','AMENDED')
    if signing and not body.strip(): raise SaaSError('An empty report cannot be signed.','EmptyReport',400)
    version=control['version']+1
    signed_at=conn.execute('SELECT clock_timestamp()').fetchone()[0]
    report.update(body=body,version=version,workflowStatus=desired,
                  status='Final' if signing else 'Draft',updatedAt=signed_at.isoformat())
    if modality is not None: report['modality']=modality
    if signing: report.update(signedBy=workspace.user_id,signedAt=signed_at.isoformat())
    conn.execute('''UPDATE saas.report_controls SET version=%s,workflow_status=%s,signed_by=%s,signed_at=%s
        WHERE tenant_id=%s AND report_id=%s''',(version,desired,workspace.user_id if signing else None,
        signed_at if signing else None,workspace.tenant_id,report_id))
    conn.execute('UPDATE public.hospital_state SET payload=%s WHERE tenant_id=%s',(Jsonb(state),workspace.tenant_id))
    conn.execute('''INSERT INTO saas.report_events(event_id,tenant_id,report_id,version,actor_id,from_status,to_status,reason)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',(uuid.uuid4(),workspace.tenant_id,report_id,version,workspace.user_id,current,desired,reason))
    conn.execute('''INSERT INTO saas.audit_logs(event_id,tenant_id,actor_id,action,object_id,request_id,outcome)
        VALUES(%s,%s,%s,%s,%s,%s,'SUCCESS')''',(uuid.uuid4(),workspace.tenant_id,workspace.user_id,
        'report.'+(target.lower() if target else 'saved'),report_id,request_id))
    if signing:
        conn.execute('''INSERT INTO saas.signed_reports(tenant_id,report_id,version,snapshot,signed_by,signed_at)
            VALUES(%s,%s,%s,%s,%s,%s)''',(workspace.tenant_id,report_id,version,Jsonb(report),workspace.user_id,signed_at))
        conn.execute('INSERT INTO saas.publication_jobs(job_id,tenant_id,report_id,version) VALUES(%s,%s,%s,%s)',
                     (uuid.uuid4(),workspace.tenant_id,report_id,version))
    if target in ('READY_FOR_REVIEW','RETURNED_FOR_CORRECTION','FINALIZED','CANCELLED'):
        conn.execute('DELETE FROM saas.report_locks WHERE tenant_id=%s AND report_id=%s',(workspace.tenant_id,report_id))
    return report


def amend(conn,workspace,report_id,expected_version,reason):
    """Create a linked draft without touching the signed original or its version."""
    from psycopg.types.json import Jsonb
    workspace.require('reports.amend')
    if not isinstance(reason,str) or not 1<=len(reason.strip())<=1000:
        raise SaaSError('Give an amendment reason of up to 1000 characters.','ReasonRequired',400)
    row=conn.execute('SELECT payload FROM public.hospital_state WHERE tenant_id=%s FOR UPDATE',(workspace.tenant_id,)).fetchone()
    if not row: raise SaaSError('Report not found.','NotFound',404)
    control=locks.control(conn,workspace,report_id)
    if type(expected_version) is not int or expected_version!=control['version']:
        raise SaaSError('Reload the signed report before creating an amendment.','StaleReport',412)
    if control['workflowStatus'] not in ('FINALIZED','AMENDED'):
        raise SaaSError('Only a signed report can be amended.','InvalidTransition',409)
    original=next((r for r in row[0].get('reports',[]) if r.get('id')==report_id),None)
    if not original or original.get('patientId')!=control['patientId']:
        raise SaaSError('Report not found.','NotFound',404)
    report={'id':uuid.uuid4().hex,'patientId':control['patientId'],'modality':original['modality'],'body':original['body'],
        'status':'Draft','workflowStatus':'DRAFT','version':1,'tenantId':workspace.tenant_id,
        'amendsReportId':report_id,'amendmentReason':reason.strip()}
    conn.execute('''INSERT INTO saas.report_controls(tenant_id,report_id,patient_id,amends_report_id,amendment_reason)
        VALUES(%s,%s,%s,%s,%s)''',(workspace.tenant_id,report['id'],control['patientId'],report_id,reason.strip()))
    conn.execute('INSERT INTO saas.report_assignments(tenant_id,report_id,user_id) VALUES(%s,%s,%s)',
                 (workspace.tenant_id,report['id'],workspace.user_id))
    row[0]['reports'].append(report)
    conn.execute('UPDATE public.hospital_state SET payload=%s WHERE tenant_id=%s',(Jsonb(row[0]),workspace.tenant_id))
    conn.execute('''INSERT INTO saas.audit_logs(event_id,tenant_id,actor_id,action,object_id,outcome)
        VALUES(%s,%s,%s,'report.amendment_created',%s,'SUCCESS')''',(uuid.uuid4(),workspace.tenant_id,workspace.user_id,report['id']))
    return report
