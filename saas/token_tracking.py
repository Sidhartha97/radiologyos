"""Actual provider counters only; no customer charging or clinical text storage."""
import logging
import re
import uuid
from .runtime import SCOPE
from .tenants import workspace_transaction
from .platform import require_platform
from .permissions import SaaSError

LOG=logging.getLogger('radiology.tokens')


def count(value):
    return value if type(value) is int and 0<=value<=10**12 else None


def counters(response):
    usage=response.get('usage',{})
    if not isinstance(usage,dict): usage={}
    metadata=response.get('ResponseMetadata',{})
    if not isinstance(metadata,dict): metadata={}
    request_id=metadata.get('RequestId')
    if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',request_id): request_id=None
    values={key:count(usage.get(field)) for key,field in (
        ('input_tokens','inputTokens'),('output_tokens','outputTokens'),('total_tokens','totalTokens'),
        ('cache_read_tokens','cacheReadInputTokens'),('cache_write_tokens','cacheWriteInputTokens'))}
    return {**values,'provider_request_id':request_id,
            'state':'RECORDED' if all(values[k] is not None for k in ('input_tokens','output_tokens','total_tokens')) else 'USAGE_MISSING'}


class Tracker:
    def __init__(self,connect): self.connect=connect

    def begin(self,model):
        scope=SCOPE.get()
        if not scope: raise SaaSError('Authenticated workspace is required for token tracking.','UsageScopeMissing',403)
        if not isinstance(model,str) or not 1<=len(model)<=512: raise SaaSError('Invalid model identifier.','InvalidModel',400)
        event=uuid.uuid4()
        with workspace_transaction(self.connect,scope.identity,scope.tenant_id) as (conn,workspace):
            workspace.require('ai.generate')
            conn.execute('INSERT INTO saas.model_token_events(event_id,tenant_id,actor_id,model_id) VALUES(%s,%s,%s,%s)',
                         (event,scope.tenant_id,workspace.user_id,model))
        return event,scope

    def finish(self,started,values):
        event,scope=started
        with workspace_transaction(self.connect,scope.identity,scope.tenant_id) as (conn,_):
            conn.execute('''UPDATE saas.model_token_events SET state=%s,input_tokens=%s,output_tokens=%s,total_tokens=%s,
                cache_read_tokens=%s,cache_write_tokens=%s,provider_request_id=%s,completed_at=clock_timestamp()
                WHERE event_id=%s AND state='STARTED' ''',
                tuple(values.get(k) for k in ('state','input_tokens','output_tokens','total_tokens','cache_read_tokens','cache_write_tokens','provider_request_id'))+(event,))


class Runtime:
    def __init__(self,client,tracker): self.client,self.tracker=client,tracker
    def __getattr__(self,name): return getattr(self.client,name)

    def converse(self,**kwargs):
        started=self.tracker.begin(kwargs.get('modelId'))
        try: response=self.client.converse(**kwargs)
        except Exception:
            try: self.tracker.finish(started,{'state':'OUTCOME_UNKNOWN'})
            except Exception: LOG.warning('Token outcome unresolved event=%s',started[0])
            raise
        try: self.tracker.finish(started,counters(response))
        except Exception: LOG.warning('Token persistence unresolved event=%s',started[0])
        return response  # Exact provider response, unchanged; never invoke again.


def dashboard(connect,identity,after=''):
    tenant_cursor=None; actor_cursor=None
    if after:
        try:
            tenant_cursor,actor_cursor=after.split('|'); actor_cursor=str(uuid.UUID(actor_cursor))
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',tenant_cursor): raise ValueError()
        except (ValueError,AttributeError): raise SaaSError('Invalid page cursor.','InvalidPage',400) from None
    with connect() as conn:
        require_platform(conn,identity)
        totals=conn.execute('''SELECT coalesce(sum(input_tokens) FILTER(WHERE state='RECORDED'),0),
          coalesce(sum(output_tokens) FILTER(WHERE state='RECORDED'),0),
          coalesce(sum(total_tokens) FILTER(WHERE state='RECORDED'),0),count(*) FILTER(WHERE state<>'RECORDED')
          FROM saas.model_token_events''').fetchone()
        rows=conn.execute('''SELECT m.tenant_id,m.user_id,u.email,m.employee_code,
          coalesce(sum(e.input_tokens) FILTER(WHERE e.state='RECORDED'),0),
          coalesce(sum(e.output_tokens) FILTER(WHERE e.state='RECORDED'),0),
          coalesce(sum(e.total_tokens) FILTER(WHERE e.state='RECORDED'),0),
          count(e.event_id),count(e.event_id) FILTER(WHERE e.state<>'RECORDED')
          FROM saas.tenant_memberships m JOIN saas.users u USING(user_id)
          LEFT JOIN saas.model_token_events e ON e.tenant_id=m.tenant_id AND e.actor_id=m.user_id
          WHERE (%s::text IS NULL OR (m.tenant_id,m.user_id)>(%s,%s::uuid))
          GROUP BY m.tenant_id,m.user_id,u.email,m.employee_code ORDER BY m.tenant_id,m.user_id LIMIT 51''',
          (tenant_cursor,tenant_cursor,actor_cursor)).fetchall()
        return {'totals':{'inputTokens':int(totals[0]),'outputTokens':int(totals[1]),'totalTokens':int(totals[2]),'unresolvedCalls':totals[3]},
            'users':[{'tenantId':r[0],'userId':str(r[1]),'email':r[2],'employeeCode':r[3],
                'inputTokens':int(r[4]),'outputTokens':int(r[5]),'totalTokens':int(r[6]),'calls':r[7],'unresolvedCalls':r[8]} for r in rows[:50]],
            'nextCursor':rows[49][0]+'|'+str(rows[49][1]) if len(rows)>50 else None}
