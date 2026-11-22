"""Radiologist-managed report assignment with existing lease/version rules."""
import uuid
from . import locks,memberships
from .permissions import SaaSError


def directory(conn,workspace):
    workspace.require('reports.read_all')
    rows=conn.execute('SELECT user_id,employee_code,roles FROM saas.clinical_assignees(%s)',(workspace.tenant_id,)).fetchall()
    return {'members':[{'userId':str(row[0]),'employeeCode':row[1],'roles':row[2]} for row in rows]}


def change(conn,workspace,report_id,payload,request_id):
    workspace.require('reports.read_all')
    if workspace.kind!='HOSPITAL': raise SaaSError('Personal workspaces do not assign reports to hospital employees.','NotHospital',400)
    try: target=uuid.UUID(str(payload.get('userId')))
    except (ValueError,TypeError,AttributeError): raise SaaSError('Choose an active clinical member.','InvalidMember',400) from None
    expected=payload.get('version'); assign=payload.get('assigned')
    if type(expected) is not int or type(assign) is not bool: raise SaaSError('Provide report revision and assignment action.','InvalidRequest',400)
    control=locks.check(conn,workspace,report_id,payload.get('lease'),expected)
    available={m['userId'] for m in directory(conn,workspace)['members']}
    if str(target) not in available: raise SaaSError('Member not found in this hospital.','NotFound',404)
    if assign:
        conn.execute('INSERT INTO saas.report_assignments(tenant_id,report_id,user_id) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',(workspace.tenant_id,report_id,target))
    else:
        conn.execute('DELETE FROM saas.report_assignments WHERE tenant_id=%s AND report_id=%s AND user_id=%s',(workspace.tenant_id,report_id,target))
        if str(target)!=workspace.user_id:
            conn.execute('DELETE FROM saas.report_locks WHERE tenant_id=%s AND report_id=%s AND user_id=%s',(workspace.tenant_id,report_id,target))
    conn.execute('UPDATE saas.report_controls SET version=version+1 WHERE tenant_id=%s AND report_id=%s',(workspace.tenant_id,report_id))
    memberships.audit(conn,workspace,'report.assignment_added' if assign else 'report.assignment_removed',report_id,request_id)
    return {'version':control['version']+1}


def reviewers(conn,workspace):
    workspace.require('reports.submit')
    return {'members':[{'userId':str(r[0]),'employeeCode':r[1]} for r in conn.execute(
        'SELECT user_id,employee_code FROM saas.review_radiologists(%s)',(workspace.tenant_id,)).fetchall()]}


def handoff(conn,workspace,report_id,payload,request_id):
    """Save the current text, assign reviewer and submit atomically; never sign."""
    from . import workflow
    workspace.require('reports.submit')
    if workspace.kind!='HOSPITAL': raise SaaSError('Review handoff is for hospital workspaces.','NotHospital',400)
    try: reviewer=uuid.UUID(str(payload.get('reviewerId')))
    except (ValueError,TypeError,AttributeError): raise SaaSError('Choose a radiologist.','InvalidReviewer',400) from None
    if str(reviewer) not in {r['userId'] for r in reviewers(conn,workspace)['members']}:
        raise SaaSError('Radiologist not found in this hospital.','NotFound',404)
    saved=workflow.save(conn,workspace,report_id,payload.get('body'),payload.get('version'),payload.get('lease'),
                        target='READY_FOR_REVIEW',request_id=request_id)
    conn.execute('INSERT INTO saas.report_assignments(tenant_id,report_id,user_id) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',
                 (workspace.tenant_id,report_id,reviewer))
    memberships.audit(conn,workspace,'report.handed_to_radiologist',report_id+':'+str(reviewer),request_id)
    return saved
