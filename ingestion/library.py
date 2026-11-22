"""Browser batch uploads. Durable DynamoDB batches, S3 originals, background KB sync."""
import base64
import hashlib
import json
import logging
import os
import re
import threading
import uuid
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .batch import Manifest, connect_manifest, settings, report_id, prepare_document, put_if_changed, queue_ingestion, sync_status
from .documents import MODALITIES, normalize_modality, structure

MAX_BYTES = 10 * 1024 * 1024
LOG = logging.getLogger('radiology.library')


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def error_detail(exc, stage):
    response = getattr(exc, 'response', {})
    code = response.get('Error', {}).get('Code', type(exc).__name__)
    request_id = response.get('ResponseMetadata', {}).get('RequestId', '')
    if isinstance(exc, ValueError):
        reason = str(exc)[:400]
        action = 'Correct the file or selection and validate it again.'
    elif code in ('AccessDenied', 'AccessDeniedException', 'UnauthorizedOperation'):
        reason = 'AWS denied permission for this step.'
        action = 'Check the application AWS profile/role and the S3, DynamoDB, Bedrock and KMS permissions.'
    elif code in ('ResourceNotFoundException', 'NoSuchBucket'):
        reason = 'A configured AWS resource was not found.'
        action = 'Check the table, bucket, Knowledge Base and data-source IDs in .env.'
    elif code in ('NoSuchKey', '404'):
        reason = 'The uploaded file is missing from S3.'
        action = 'Select the file and upload it again.'
    elif code in ('ThrottlingException', 'TooManyRequestsException'):
        reason = 'AWS temporarily limited requests.'
        action = 'Wait briefly, then retry.'
    elif 'Another ingestion process' in str(exc):
        reason = 'Another import currently holds the tenant lock.'
        action = 'Wait for that import to finish; background processing will retry.'
    else:
        reason = 'The service could not complete this step.'
        action = 'Check network access and AWS credentials, then retry. Use the error code and request ID for investigation.'
    detail = {'time': timestamp(), 'stage': stage, 'code': code, 'reason': reason, 'action': action, 'requestId': request_id}
    # Do not log report content, credentials, presigned URLs or arbitrary SDK responses.
    LOG.error(json.dumps(detail))
    return detail


def validate(content, filename, modality):
    if not content or len(content) > MAX_BYTES:
        raise ValueError('Each file must contain data and be at most 10 MB.')
    if Path(filename).suffix.lower() not in ('.docx', '.pdf', '.txt'):
        raise ValueError('Choose DOCX, text-based PDF or TXT files. Convert legacy DOC files first.')
    if normalize_modality(modality) not in MODALITIES:
        raise ValueError('Choose a supported modality.')
    # Bound decompressed DOCX size before XML extraction.
    if filename.lower().endswith('.docx'):
        import io, zipfile
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 50 * 1024 * 1024:
                raise ValueError('DOCX uncompressed content exceeds 50 MB.')
    text, metadata = prepare_document(Path(filename), content, Path('.'), selected_modality=modality)
    return text, metadata


class Library:
    def __init__(self, tenant=None):
        import boto3
        from botocore.config import Config
        self.cfg = settings()
        if tenant is not None:
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',tenant): raise ValueError('Invalid hospital identity.')
            self.cfg['tenant']=tenant
        self.tenant = self.cfg['tenant']
        self.table = connect_manifest()
        config = Config(connect_timeout=10, read_timeout=30, retries={'max_attempts':3})
        # KMS-encrypted browser POST uploads require SigV4, including in us-east-1.
        self.s3 = boto3.client('s3', region_name=self.cfg['region'],
                               config=config.merge(Config(signature_version='s3v4')))
        self.agent = boto3.client('bedrock-agent', region_name=self.cfg['region'], config=config)

    def key(self, batch_id):
        if not re.fullmatch(r'[a-f0-9]{32}', batch_id): raise ValueError('Invalid batch ID.')
        return {'PK':'TENANT#'+self.tenant, 'SK':'LIBRARY#BATCH#'+batch_id}

    def save(self, batch):
        batch['updatedAt'] = timestamp()
        batch['events'] = batch.get('events', [])[-40:]
        self.table.put_item(Item={**self.key(batch['id']), 'payload':json.dumps(batch)})

    def get(self, batch_id):
        item = self.table.get_item(Key=self.key(batch_id), ConsistentRead=True).get('Item')
        if not item: raise ValueError('Batch was not found for this hospital.')
        return json.loads(item['payload'])

    def batches(self):
        from boto3.dynamodb.conditions import Key
        params = {'KeyConditionExpression':Key('PK').eq('TENANT#'+self.tenant) & Key('SK').begins_with('LIBRARY#BATCH#'), 'ConsistentRead':True}
        batches = []
        while True:
            result = self.table.query(**params)
            batches.extend(json.loads(x['payload']) for x in result.get('Items', []))
            if not result.get('LastEvaluatedKey'): break
            params['ExclusiveStartKey'] = result['LastEvaluatedKey']
        return batches

    def preview(self, payload):
        try:
            content = base64.b64decode(payload.get('content',''), validate=True)
        except Exception as exc:
            raise ValueError('File data is not valid base64.') from exc
        name = Path(str(payload.get('name','')).replace('\\','/')).name
        text, metadata = validate(content, name, payload.get('modality',''))
        identity = report_id(self.tenant, content)
        previous = self.table.get_item(Key={'PK':'TENANT#'+self.tenant, 'SK':'INGESTION#REPORT#'+identity},ConsistentRead=True).get('Item',{}).get('payload',{})
        if previous.get('modality') not in (None, 'UNKNOWN', metadata['modality']):
            raise ValueError('This file already belongs to a different modality. Review the existing import.')
        return {'name':name, 'size':len(content), 'sha256':hashlib.sha256(content).hexdigest(),
                'modality':metadata['modality'], 'words':len(text.split()),
                'duplicate':previous.get('status') in ('PROCESSING','COMPLETED') and bool(previous.get('s3_key'))}

    def create(self, payload):
        files = payload.get('files', [])
        modality = normalize_modality(payload.get('modality',''))
        if modality not in MODALITIES or not isinstance(files,list) or not 1 <= len(files) <= 100:
            raise ValueError('Select a modality and between 1 and 100 files per batch.')
        batch = {'id':uuid.uuid4().hex, 'modality':modality, 'status':'UPLOADING', 'createdAt':timestamp(), 'files':[], 'events':[]}
        uploads = []
        for file in files:
            name = Path(str(file.get('name','')).replace('\\','/')).name
            suffix = Path(name).suffix.lower()
            size = int(file.get('size',0)); digest = file.get('sha256','')
            if suffix not in ('.docx','.pdf','.txt') or not 0 < size <= MAX_BYTES or not re.fullmatch('[a-f0-9]{64}',digest):
                raise ValueError('Invalid file type, size or checksum.')
            file_id = uuid.uuid4().hex
            key = self.cfg['originalPrefix'].rstrip('/')+'/'+self.tenant+'/browser/'+batch['id']+'/'+file_id+suffix
            item = {'id':file_id, 'name':name, 'size':size, 'sha256':digest, 'originalKey':key, 'status':'UPLOADING'}
            batch['files'].append(item)
            fields = {'x-amz-server-side-encryption':'aws:kms'} if os.getenv('RAG_KMS_KEY_ID') else {'x-amz-server-side-encryption':'AES256'}
            if os.getenv('RAG_KMS_KEY_ID'): fields['x-amz-server-side-encryption-aws-kms-key-id'] = os.environ['RAG_KMS_KEY_ID']
            signed = self.s3.generate_presigned_post(self.cfg['bucket'], key, Fields=fields,
                Conditions=[['content-length-range',size,size], *[{k:v} for k,v in fields.items()]], ExpiresIn=3600)
            uploads.append({'id':file_id, **signed})
        self.save(batch)
        return {'batchId':batch['id'], 'uploads':uploads}

    def complete(self, batch_id, successful):
        manifest = Manifest(self.table); manifest.acquire(self.tenant)
        try:
            batch = self.get(batch_id)
            if batch['status'] != 'UPLOADING': return {'batchId':batch_id, 'status':batch['status']}
            for item in batch['files']:
                item['status'] = 'UPLOADED' if item['id'] in successful else 'FAILED'
                if item['status']=='FAILED':
                    item['error']={'stage':'upload','code':'BrowserUploadFailed','reason':'The browser could not upload this file to S3.', 'action':'Check S3 CORS/network access, then select the file and upload again.', 'time':timestamp()}
            batch['status']='READY'
            self.save(batch)
            return {'batchId':batch_id, 'status':'READY'}
        finally: manifest.release()

    def retry(self, batch_id):
        manifest = Manifest(self.table); manifest.acquire(self.tenant)
        try:
            batch=self.get(batch_id)
            for item in batch['files']:
                if item['status'] in ('FAILED','UPLOADING'):
                    item['status']='UPLOADED'; item.pop('error',None)
            batch['status']='READY'; batch.pop('error',None)
            self.save(batch)
            return {'status':'READY'}
        finally: manifest.release()

    def status(self):
        records = Manifest(self.table).records(self.tenant)
        counts = {}
        for modality in MODALITIES:
            values = [x for x in records.values() if x.get('modality')==modality]
            counts[modality] = dict(Counter(x.get('status','PENDING') for x in values))
        return {'counts':counts, 'batches':sorted(self.batches(), key=lambda x:x['createdAt'], reverse=True)[:20], 'workerError':WORKER_ERRORS.get(self.tenant)}

    def tick(self):
        if self.table.get_item(Key={'PK':'TENANT#'+self.tenant,'SK':'ACCOUNT#DELETING'},ConsistentRead=True).get('Item'):
            return
        batches = [b for b in self.batches() if b['status'] in ('READY','INDEXING')]
        if not batches: return
        manifest=Manifest(self.table); manifest.acquire(self.tenant)
        try:
            records=manifest.records(self.tenant)
            for old in batches:
                batch=self.get(old['id'])
                try:
                    for item in batch['files']:
                        if item['status'] != 'UPLOADED': continue
                        stage='read_upload'
                        try:
                            manifest.renew()
                            content=self.s3.get_object(Bucket=self.cfg['bucket'], Key=item['originalKey'])['Body'].read(MAX_BYTES+1)
                            if len(content)!=item['size'] or hashlib.sha256(content).hexdigest()!=item['sha256']:
                                raise ValueError('Uploaded file checksum/size differs from the validated file. Upload it again.')
                            stage='validate'
                            text, metadata=validate(content,item['name'],batch['modality'])
                            identity=report_id(self.tenant,content); item['reportId']=identity
                            previous=records.get(identity,{})
                            if previous.get('modality') not in (None,'UNKNOWN',batch['modality']):
                                raise ValueError('This file is already assigned to another modality.')
                            if previous.get('status')=='COMPLETED':
                                item['status']='DUPLICATE'; continue
                            if previous.get('status')=='PROCESSING' and previous.get('s3_key'):
                                item['status']='INDEXING'; batch['needsSync']=True; continue
                            stage='normalize'
                            key=self.cfg['prefix'].rstrip('/')+'/'+self.tenant+'/normalized/'+identity+'.txt'
                            metadata.update(structure(text),tenant_id=self.tenant,report_id=identity,report_type='old_report',s3_key=key,original_s3_key=item['originalKey'])
                            enc={'ServerSideEncryption':'AES256'}
                            if os.getenv('RAG_KMS_KEY_ID'): enc={'ServerSideEncryption':'aws:kms','SSEKMSKeyId':os.environ['RAG_KMS_KEY_ID']}
                            put_if_changed(self.s3,self.cfg['bucket'],key,text.encode(),ContentType='text/plain',**enc)
                            put_if_changed(self.s3,self.cfg['bucket'],key+'.metadata.json',json.dumps({'metadataAttributes':metadata},sort_keys=True).encode(),ContentType='application/json',**enc)
                            record={**metadata,'status':'PROCESSING','file_name':item['name'],'embedding_status':'QUEUED','vector_status':'QUEUED','error_message':''}
                            manifest.put(self.tenant,identity,record); records[identity]=record
                            item['status']='INDEXING'; batch['needsSync']=True
                        except Exception as exc:
                            item['status']='FAILED'; item['error']=error_detail(exc,stage)
                        finally:
                            self.save(batch)
                    batch['status']='INDEXING'
                    if batch.get('needsSync'):
                        manifest.renew()
                        job=queue_ingestion(self.agent,self.cfg)
                        batch['job']=job
                        if job['status'] != 'WAIT_FOR_ACTIVE_JOB':
                            batch['needsSync']=False
                            batch['syncStartedAt']=timestamp()
                        if not batch['events'] or batch['events'][-1].get('job')!=job:
                            batch['events'].append({'time':timestamp(),'stage':'ingestion','job':job})
                        self.save(batch)
                    sync_status(self.agent,self.cfg,manifest,records)
                    records=manifest.records(self.tenant)
                    if batch.get('job',{}).get('job_id'):
                        job=self.agent.get_ingestion_job(knowledgeBaseId=self.cfg['kb'],dataSourceId=self.cfg['ds'],ingestionJobId=batch['job']['job_id'])['ingestionJob']
                        batch['jobStatus']=job['status']
                        # KB jobs span hospitals. Never expose another hospital's
                        # document paths or global document counts in a batch response.
                        batch.pop('statistics',None)
                        if job.get('failureReasons'):
                            batch['failureReasons']=['The shared ingestion job reported errors. Review this batch’s per-document status.']
                    for item in batch['files']:
                        if item['status']!='INDEXING': continue
                        record=records.get(item.get('reportId'),{})
                        if record.get('status')=='COMPLETED': item['status']='INDEXED'
                        elif record.get('status')=='FAILED' or (batch.get('jobStatus') in ('FAILED','STOPPED') and not batch.get('needsSync')):
                            item['status']='FAILED'
                            item['error']={'time':timestamp(),'stage':'indexing','code':'DocumentIndexingFailed', 'reason':record.get('error_message') or 'Bedrock ingestion job failed or stopped.', 'action':'Review the ingestion job errors below, correct the cause, then retry.'}
                        elif batch.get('jobStatus')=='COMPLETE' and not batch.get('needsSync') and batch.get('syncStartedAt') and (datetime.now(timezone.utc)-datetime.fromisoformat(batch['syncStartedAt'])).total_seconds()>180:
                            item['status']='FAILED'
                            item['error']={'time':timestamp(),'stage':'indexing','code':'DocumentNotIndexed','reason':'The scan completed but this document has not reached INDEXED.', 'action':'Check the data-source prefix, metadata and ingestion statistics, then retry.'}
                    if all(x['status'] in ('INDEXED','DUPLICATE','FAILED') for x in batch['files']):
                        batch['status']='COMPLETED_WITH_ERRORS' if any(x['status']=='FAILED' for x in batch['files']) else 'COMPLETED'
                    batch.pop('error',None)
                except Exception as exc:
                    batch['error']=error_detail(exc,'ingestion'); batch['status']='ERROR'
                    batch['events'].append(batch['error'])
                self.save(batch)
        finally: manifest.release()


WORKER_ERROR = None
START_LOCK = threading.Lock()
STARTED = False
STARTED_TENANTS = set()
WORKER_ERRORS = {}


def start_worker(tenant=None):
    global STARTED
    with START_LOCK:
        identity=tenant or settings()['tenant']
        if identity in STARTED_TENANTS: return
        STARTED_TENANTS.add(identity)
    def run():
        global WORKER_ERROR
        while True:
            try:
                Library(tenant=identity).tick(); WORKER_ERRORS[identity]=None
            except Exception as exc:
                WORKER_ERRORS[identity]=error_detail(exc,'background_worker')
            threading.Event().wait(15)
    threading.Thread(target=run,daemon=True,name='historical-library').start()
