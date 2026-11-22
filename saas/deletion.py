"""Confirmed customer cleanup. Never deletes shared infrastructure or staff alone."""
import json
import logging
import os
import re
import threading
from datetime import datetime,timezone
from psycopg.types.json import Jsonb
from .permissions import SaaSError
from .platform import require_platform

LOG=logging.getLogger('radiology.deletion')
_started=False
_lock=threading.Lock()


def call(connect,action,tenant=None,job=None,token=None,payload=None,identity=None):
    try:
        with connect() as conn:
            if identity: require_platform(conn,identity)
            return conn.execute('SELECT saas.customer_deletion(%s,%s,%s,%s,%s)',
                (action,tenant,job,token,Jsonb(payload or {}))).fetchone()[0]
    except Exception as exc:
        if identity and getattr(exc,'sqlstate',None)=='P0001':
            raise SaaSError('Customer deletion state changed or confirmation expired. Reload the preview/status before retrying.','DeletionConflict',409) from None
        raise


def prefixes(tenant):
    if not isinstance(tenant,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',tenant):
        raise ValueError('Invalid cleanup tenant')
    return tuple(root+'/'+tenant+'/' for root in ('historical-originals','old-reports','final-reports'))


def configuration():
    cfg={name:os.getenv(key,'') for name,key in [('bucket','RAG_S3_BUCKET'),('kb','BEDROCK_KNOWLEDGE_BASE_ID'),
        ('old','BEDROCK_DATA_SOURCE_ID'),('final','BEDROCK_FINAL_DATA_SOURCE_ID'),('pool','COGNITO_USER_POOL_ID'),('table','DYNAMODB_TABLE')]}
    cfg['region']=os.getenv('AWS_REGION','us-east-1')
    if not all(cfg.values()): raise ValueError('Customer cleanup AWS configuration incomplete')
    return cfg


class CloudCleanup:
    def __init__(self,cfg,s3,agent,cognito,table):
        self.cfg,self.s3,self.agent,self.cognito,self.table=cfg,s3,agent,cognito,table

    @classmethod
    def configured(cls):
        import boto3
        from botocore.config import Config
        cfg=configuration(); limits=Config(connect_timeout=5,read_timeout=15,retries={'max_attempts':2})
        client=lambda name:boto3.client(name,region_name=cfg['region'],config=limits)
        table=boto3.resource('dynamodb',region_name=cfg['region'],config=limits).Table(cfg['table'])
        return cls(cfg,client('s3'),client('bedrock-agent'),client('cognito-idp'),table)

    def block_imports(self,tenant):
        prefixes(tenant)
        self.table.put_item(Item={'PK':'TENANT#'+tenant,'SK':'ACCOUNT#DELETING','blocked':True})

    def discover(self,tenant):
        owned=prefixes(tenant); bucket=self.cfg['bucket']; documents={self.cfg['old']:set(),self.cfg['final']:set()}
        count=0
        for prefix in owned:
            for page in self.s3.get_paginator('list_object_versions').paginate(Bucket=bucket,Prefix=prefix):
                for item in page.get('Versions',[])+page.get('DeleteMarkers',[]):
                    key=item['Key']
                    if not key.startswith(prefix): raise ValueError('Foreign S3 object returned')
                    count+=1
                    if key.endswith('.metadata.json'): continue
                    source=self.cfg['old'] if key.startswith(owned[1]) else self.cfg['final'] if key.startswith(owned[2]) else None
                    if source: documents[source].add('s3://'+bucket+'/'+key)
        # Include indexed orphans, not only files still present in S3.
        for source in documents:
            cursor=None
            while True:
                params={'knowledgeBaseId':self.cfg['kb'],'dataSourceId':source,'maxResults':100}
                if cursor: params['nextToken']=cursor
                page=self.agent.list_knowledge_base_documents(**params)
                for item in page.get('documentDetails',[]):
                    uri=item.get('identifier',{}).get('s3',{}).get('uri','')
                    if any(uri.startswith('s3://'+bucket+'/'+prefix) for prefix in owned[1:]): documents[source].add(uri)
                cursor=page.get('nextToken')
                if not cursor: break
        return {'documents':{source:sorted(values) for source,values in documents.items()},'objectVersions':count}

    def delete_storage(self,tenant):
        for prefix in prefixes(tenant):
            while True:
                page=self.s3.list_object_versions(Bucket=self.cfg['bucket'],Prefix=prefix,MaxKeys=1000)
                objects=[{'Key':item['Key'],'VersionId':item['VersionId']} for item in page.get('Versions',[])+page.get('DeleteMarkers',[])]
                if not objects: break
                if any(not obj['Key'].startswith(prefix) for obj in objects): raise ValueError('Foreign S3 deletion target')
                result=self.s3.delete_objects(Bucket=self.cfg['bucket'],Delete={'Objects':objects,'Quiet':True})
                if result.get('Errors'): raise RuntimeError('S3VersionDeletionFailed')

    def delete_index(self,tenant,documents,requested=False):
        allowed=tuple('s3://'+self.cfg['bucket']+'/'+prefix for prefix in prefixes(tenant)[1:])
        complete=True
        for source,uris in documents.items():
            if source not in (self.cfg['old'],self.cfg['final']) or any(not uri.startswith(allowed) for uri in uris):
                raise ValueError('Foreign indexed deletion target')
            for offset in range(0,len(uris),10):
                ids=[{'dataSourceType':'S3','s3':{'uri':uri}} for uri in uris[offset:offset+10]]
                params={'knowledgeBaseId':self.cfg['kb'],'dataSourceId':source,'documentIdentifiers':ids}
                if not requested: self.agent.delete_knowledge_base_documents(**params)
                result=self.agent.get_knowledge_base_documents(**params)
                details=result.get('documentDetails',[])
                if any(item.get('status') in ('FAILED','METADATA_UPDATE_FAILED') for item in details):
                    raise RuntimeError('IndexedDocumentDeletionFailed')
                returned={item.get('identifier',{}).get('s3',{}).get('uri') for item in details}
                if len(details)!=len(ids) or returned!=set(uris[offset:offset+10]) or any(item.get('status')!='NOT_FOUND' for item in details): complete=False
        return complete

    def delete_dynamo(self,tenant):
        from boto3.dynamodb.conditions import Key
        while True:
            objects=[]; cursor=None
            while True:
                params={'KeyConditionExpression':Key('PK').eq('TENANT#'+tenant),'ConsistentRead':True,'ProjectionExpression':'PK,SK'}
                if cursor: params['ExclusiveStartKey']=cursor
                response=self.table.query(**params)
                objects.extend(item for item in response.get('Items',[]) if item['SK']!='ACCOUNT#DELETING')
                cursor=response.get('LastEvaluatedKey')
                if not cursor: break
            for item in objects:
                if item['PK']!='TENANT#'+tenant: raise ValueError('Foreign DynamoDB target')
                self.table.delete_item(Key={'PK':item['PK'],'SK':item['SK']})
            if not objects: break

    def delete_identities(self,identities):
        issuer=f'https://cognito-idp.{self.cfg["region"]}.amazonaws.com/{self.cfg["pool"]}'
        for item in identities:
            if item['issuer']!=issuer or not item.get('email'): raise ValueError('IdentityCleanupReviewRequired')
            try: user=self.cognito.admin_get_user(UserPoolId=self.cfg['pool'],Username=item['email'])
            except Exception as exc:
                if getattr(exc,'response',{}).get('Error',{}).get('Code')=='UserNotFoundException': continue
                raise
            values={a['Name']:a['Value'] for a in user.get('UserAttributes',[])}
            if values.get('sub')!=item['subject']: raise ValueError('CognitoIdentityChanged')
            self.cognito.admin_delete_user(UserPoolId=self.cfg['pool'],Username=user['Username'])


def tick(connect,cloud):
    job=call(connect,'NEXT')
    if not job: return False
    tenant=job['tenantId']; manifest=job['manifest']; stage=job['stage']
    def save(state='RUNNING',delay=0,error=None):
        return call(connect,'SAVE',job=job['jobId'],token=job['workerToken'],payload={
            'state':state,'stage':stage,'manifest':manifest,'delaySeconds':delay,'errorCode':error})
    try:
        cloud.block_imports(tenant)
        remaining=(datetime.fromisoformat(job['quarantineUntil'])-datetime.now(timezone.utc)).total_seconds()
        if remaining>0:
            stage='WAITING_FOR_UPLOAD_EXPIRY'; save('CONFIRMED',max(1,int(remaining))); return True
        if 'documents' not in manifest:
            stage='DISCOVERY'; manifest.update(cloud.discover(tenant)); save()
        if not manifest.get('storageDeleted'):
            stage='S3_VERSIONS'; cloud.delete_storage(tenant); manifest['storageDeleted']=True; save()
        if not manifest.get('indexDeleted'):
            stage='BEDROCK_INDEX'
            if manifest.get('indexStartedAt') and (datetime.now(timezone.utc)-datetime.fromisoformat(manifest['indexStartedAt'])).total_seconds()>1800:
                raise RuntimeError('IndexedDeletionTimedOut')
            manifest.setdefault('indexStartedAt',datetime.now(timezone.utc).isoformat())
            done=cloud.delete_index(tenant,manifest['documents'],manifest.get('indexRequested',False))
            manifest['indexRequested']=True
            if not done: save('CONFIRMED',30); return True
            manifest['indexDeleted']=True; save()
        if not manifest.get('dynamoDeleted'):
            stage='DYNAMODB'; cloud.delete_dynamo(tenant); manifest['dynamoDeleted']=True; save()
        if not manifest.get('identitiesDeleted'):
            stage='COGNITO'
            identities=call(connect,'IDENTITIES',job=job['jobId'],token=job['workerToken'])
            cloud.delete_identities(identities); manifest['identitiesDeleted']=True; save()
        # Recheck storage to catch a late upload rather than falsely declaring
        # success; application import workers are blocked by the tombstone.
        cloud.delete_storage(tenant)
        stage='DATABASE'
        call(connect,'COMPLETE',job=job['jobId'],token=job['workerToken'])
        LOG.info(json.dumps({'event':'customer_cleanup_complete','jobId':job['jobId']}))
    except Exception as exc:
        raw=getattr(exc,'response',{}).get('Error',{}).get('Code',str(exc) if type(exc) in (ValueError,RuntimeError) else type(exc).__name__)
        code=raw if isinstance(raw,str) and re.fullmatch(r'[A-Za-z0-9_]{1,80}',raw) else 'CleanupFailed'
        try: save('FAILED',error=code)
        except Exception: pass
        LOG.error(json.dumps({'event':'customer_cleanup_failed','jobId':job['jobId'],'stage':stage,'errorCode':code}))
    return True


def start_worker(connect):
    global _started
    if os.getenv('SAAS_CUSTOMER_DELETION_ENABLED','false').lower()!='true': return
    with _lock:
        if _started: return
        _started=True
    def run():
        while True:
            try: tick(connect,CloudCleanup.configured())
            except Exception: LOG.error('Customer cleanup worker unavailable; retrying without logging private details.')
            threading.Event().wait(30)
    threading.Thread(target=run,daemon=True,name='customer-cleanup').start()
