"""Restart-safe publication claims. No clinical content in operational results.

The caller sets an authorized tenant transaction and invokes the existing S3
publisher only after claim commit, outside the database transaction. Publication
is at-least-once: use the same deterministic report/version key on retries.
"""
import re
import uuid
from .permissions import SaaSError


def claim(conn,tenant_id):
    worker=uuid.uuid4()
    row=conn.execute('''WITH candidate AS (
        SELECT job_id FROM saas.publication_jobs
        WHERE tenant_id=%s AND available_at<=clock_timestamp() AND
          (state='PENDING' OR (state='RUNNING' AND lease_until<clock_timestamp()))
        ORDER BY available_at,created_at FOR UPDATE SKIP LOCKED LIMIT 1)
        UPDATE saas.publication_jobs j SET state='RUNNING',attempts=j.attempts+1,
          worker_token=%s,lease_until=clock_timestamp()+interval '5 minutes',error_code=NULL
        FROM candidate c WHERE j.job_id=c.job_id
        RETURNING j.job_id,j.report_id,j.version,j.attempts''',(tenant_id,worker)).fetchone()
    if not row: return None
    return {'jobId':str(row[0]),'reportId':row[1],'version':row[2],'attempts':row[3],'workerToken':str(worker)}


def finish(conn,tenant_id,job,*,error_code=None,max_attempts=5):
    if type(max_attempts) is not int or not 1<=max_attempts<=20:
        raise SaaSError('Invalid retry configuration.','InvalidRetry',400)
    if error_code is not None and (not isinstance(error_code,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',error_code)):
        raise SaaSError('Use a safe provider error code, never a raw message.','UnsafeError',400)
    # Compare the worker token as well as lease: a stale worker cannot finish a
    # job reclaimed by another worker after a crash or network timeout.
    result=conn.execute('''UPDATE saas.publication_jobs SET
        state=CASE WHEN %s::text IS NULL THEN 'COMPLETE'
                   WHEN attempts>=%s THEN 'FAILED' ELSE 'PENDING' END,
        error_code=%s,available_at=clock_timestamp()+interval '30 seconds',
        lease_until=NULL,worker_token=NULL
        WHERE tenant_id=%s AND job_id=%s AND worker_token=%s AND state='RUNNING'
          AND lease_until>clock_timestamp() RETURNING state''',
        (error_code,max_attempts,error_code,tenant_id,job['jobId'],job['workerToken'])).fetchone()
    if not result: raise SaaSError('Publication claim expired or was replaced.','JobLeaseLost',409)
    return {'state':result[0]}


def snapshot(conn,tenant_id,job):
    """Fetch exactly the signed revision claimed by this worker, never latest draft."""
    row=conn.execute('''SELECT s.snapshot FROM saas.publication_jobs j
        JOIN saas.signed_reports s USING(tenant_id,report_id,version)
        WHERE j.tenant_id=%s AND j.job_id=%s AND j.worker_token=%s
          AND j.state='RUNNING' AND j.lease_until>clock_timestamp()''',
        (tenant_id,job['jobId'],job['workerToken'])).fetchone()
    if not row: raise SaaSError('Publication claim expired or was replaced.','JobLeaseLost',409)
    return row[0]
