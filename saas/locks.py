"""PostgreSQL editing leases. Call inside the same transaction as any report write."""
import hashlib
import secrets
import uuid
from .permissions import SaaSError

LEASE_SECONDS=120
FINAL_STATES={'FINALIZED','AMENDED','CANCELLED'}


def control(conn,workspace,report_id):
    row=conn.execute('''SELECT patient_id,version,fence,workflow_status FROM saas.report_controls
        WHERE tenant_id=%s AND report_id=%s FOR UPDATE''',(workspace.tenant_id,report_id)).fetchone()
    if not row: raise SaaSError('Report not found.','NotFound',404)
    assigned=bool(conn.execute('SELECT 1 FROM saas.report_assignments WHERE tenant_id=%s AND report_id=%s AND user_id=%s',
                               (workspace.tenant_id,report_id,workspace.user_id)).fetchone())
    workspace.require_report({'tenant_id':workspace.tenant_id},assigned=assigned)
    return {'patientId':row[0],'version':row[1],'fence':row[2],'workflowStatus':row[3]}


def require_editable(workspace,report):
    workspace.require('reports.edit')
    if report['workflowStatus'] in FINAL_STATES:
        raise SaaSError('This report is read-only. Use the amendment workflow.','ReportReadOnly',409)
    if report['workflowStatus'] in ('READY_FOR_REVIEW','UNDER_REVIEW') and not workspace.allows('reports.review'):
        raise SaaSError('The report has been submitted for radiologist review.','ReportUnderReview',409)


def acquire(conn,workspace,report_id,editing_session):
    if workspace.kind!='HOSPITAL':
        raise SaaSError('Personal workspaces use version checks, not editing leases.','LeaseNotRequired',400)
    try: editing_session=uuid.UUID(str(editing_session))
    except (ValueError,TypeError,AttributeError): raise SaaSError('Invalid editing session.','InvalidSession',400) from None
    report=control(conn,workspace,report_id)
    require_editable(workspace,report)
    active=conn.execute('''SELECT user_id::text,editing_session FROM saas.report_locks WHERE tenant_id=%s AND report_id=%s
        AND expires_at>clock_timestamp()''',(workspace.tenant_id,report_id)).fetchone()
    if active and (active[0]!=workspace.user_id or active[1]!=editing_session):
        raise SaaSError('This report is currently being edited by another user or tab.','ReportLocked',409)
    # A retry from the SAME tab may replace a lost response. New credential/fence
    # invalidates the previous one; another tab/user can never take over this way.
    fence=report['fence']+1
    token=secrets.token_urlsafe(32)
    conn.execute('UPDATE saas.report_controls SET fence=%s WHERE tenant_id=%s AND report_id=%s',
                 (fence,workspace.tenant_id,report_id))
    expiry=conn.execute('''INSERT INTO saas.report_locks(tenant_id,report_id,user_id,editing_session,token_hash,fence,expires_at)
        VALUES(%s,%s,%s,%s,%s,%s,clock_timestamp()+make_interval(secs=>%s))
        ON CONFLICT(tenant_id,report_id) DO UPDATE SET user_id=EXCLUDED.user_id,
        editing_session=EXCLUDED.editing_session,token_hash=EXCLUDED.token_hash,
        fence=EXCLUDED.fence,expires_at=EXCLUDED.expires_at RETURNING expires_at''',
        (workspace.tenant_id,report_id,workspace.user_id,editing_session,hashlib.sha256(token.encode()).hexdigest(),fence,LEASE_SECONDS)).fetchone()[0]
    return {'token':token,'fence':fence,'editingSession':str(editing_session),'expiresAt':expiry.isoformat(),'version':report['version']}


def check(conn,workspace,report_id,credential,expected_version=None):
    report=control(conn,workspace,report_id)
    require_editable(workspace,report)
    if expected_version is not None and (type(expected_version) is not int or expected_version!=report['version']):
        raise SaaSError('Report changed. Reload before saving.','StaleReport',412)
    if workspace.kind=='INDIVIDUAL': return report
    credential=credential if isinstance(credential,dict) else {}
    token=credential.get('token')
    if not isinstance(token,str) or len(token)>256:
        raise SaaSError('A valid editing lease is required.','LeaseRequired',409)
    row=conn.execute('''SELECT user_id::text,editing_session::text,token_hash,fence,expires_at>clock_timestamp()
        FROM saas.report_locks WHERE tenant_id=%s AND report_id=%s''',(workspace.tenant_id,report_id)).fetchone()
    if not row or not row[4] or row[0]!=workspace.user_id or row[1]!=credential.get('editingSession') or type(credential.get('fence')) is not int or row[3]!=credential['fence'] or row[3]!=report['fence'] or not secrets.compare_digest(row[2],hashlib.sha256(token.encode()).hexdigest()):
        raise SaaSError('Editing lease expired or belongs to another user. Reload the report.','LeaseLost',409)
    return report


def renew(conn,workspace,report_id,credential):
    if workspace.kind!='HOSPITAL': raise SaaSError('Lease not required.','LeaseNotRequired',400)
    check(conn,workspace,report_id,credential)
    row=conn.execute('''UPDATE saas.report_locks SET expires_at=clock_timestamp()+make_interval(secs=>%s)
        WHERE tenant_id=%s AND report_id=%s AND expires_at>clock_timestamp() RETURNING expires_at''',
        (LEASE_SECONDS,workspace.tenant_id,report_id)).fetchone()
    if not row: raise SaaSError('Editing lease expired.','LeaseLost',409)
    return {'expiresAt':row[0].isoformat()}


def release(conn,workspace,report_id,credential):
    if workspace.kind!='HOSPITAL': return
    check(conn,workspace,report_id,credential)
    conn.execute('DELETE FROM saas.report_locks WHERE tenant_id=%s AND report_id=%s',(workspace.tenant_id,report_id))
    # report_controls.fence survives release; a future holder gets a higher fence.
