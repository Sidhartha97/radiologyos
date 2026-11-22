"""Loopback demo adapter ONLY: validates/stages in memory, never calls AWS.

No document is reported as indexed. Data disappears with the demo process.
Uses the real file validator, not a substitute extraction/RAG pipeline.
"""
import base64
import copy
import hashlib
import re
import threading
import uuid
from pathlib import Path
from ingestion.library import validate,timestamp,MAX_BYTES
from ingestion.documents import MODALITIES,normalize_modality
from saas.permissions import SaaSError


class DemoLibrary:
    def __init__(self):
        self.batches={}; self.lock=threading.RLock(); self.bytes={}

    def content(self,payload):
        try: content=base64.b64decode(payload.get('content',''),validate=True)
        except (ValueError,TypeError): raise SaaSError('File data is not valid base64.','InvalidFile',400) from None
        if not content or len(content)>MAX_BYTES: raise SaaSError('Choose a nonempty file up to 10 MB.','InvalidFile',400)
        return content

    def handle(self,tenant,method,path,payload):
        with self.lock:
            return self._handle(tenant,method,path,payload)

    def _handle(self,tenant,method,path,payload):
        batches=self.batches.setdefault(tenant,{})
        if method=='GET' and path=='/api/library':
            return {'mode':'local-demo','notice':'Local demo: validation and temporary file staging only. No S3 upload, Bedrock indexing or AI retrieval. Use synthetic files; staged data is removed when this demo stops.',
                'counts':{m:{'LOCAL_STAGED':sum(f['status']=='LOCAL_STAGED' for b in batches.values() if b['modality']==m for f in b['files'])} for m in MODALITIES},
                'batches':copy.deepcopy(list(batches.values())[::-1][-50:])}
        if method!='POST': raise SaaSError('Endpoint not found.','NotFound',404)
        if path=='/api/library/validate':
            content=self.content(payload); name=Path(str(payload.get('name','')).replace('\\','/')).name
            try: text,metadata=validate(content,name,payload.get('modality',''))
            except Exception: raise SaaSError('File validation failed. Use one readable DOCX, text-based PDF or TXT report per file and select its modality.','InvalidFile',400) from None
            digest=hashlib.sha256(content).hexdigest()
            return {'name':name,'size':len(content),'sha256':digest,'modality':metadata['modality'],'words':len(text.split()),
                'duplicate':any(f['sha256']==digest and f['status']=='LOCAL_STAGED' for b in batches.values() for f in b['files'])}
        if path=='/api/library/batches':
            modality=normalize_modality(payload.get('modality','')); files=payload.get('files')
            if modality not in MODALITIES or not isinstance(files,list) or not 1<=len(files)<=100:
                raise SaaSError('Choose a modality and 1–100 validated files.','InvalidBatch',400)
            if len(batches)>=50: raise SaaSError('Demo batch limit reached. Restart the disposable demo.','DemoLimit',409)
            batch={'id':uuid.uuid4().hex,'modality':modality,'status':'UPLOADING','createdAt':timestamp(),'files':[],'events':[]}
            for file in files:
                if not isinstance(file,dict): raise SaaSError('Validate the file first.','InvalidFile',400)
                name=Path(str(file.get('name','')).replace('\\','/')).name; size=file.get('size'); digest=file.get('sha256','')
                if Path(name).suffix.lower() not in ('.txt','.pdf','.docx') or type(size) is not int or not 0<size<=MAX_BYTES or not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):
                    raise SaaSError('Validate the file first.','InvalidFile',400)
                batch['files'].append({'id':uuid.uuid4().hex,'name':name,'size':size,'sha256':digest,'status':'UPLOADING'})
            batches[batch['id']]=batch
            return {'batchId':batch['id'],'uploads':[{'id':f['id'],'transport':'local-demo','fields':{'batchId':batch['id'],'fileId':f['id']}} for f in batch['files']]}
        if path=='/api/library/demo-upload':
            batch=batches.get(payload.get('batchId')); file=next((f for f in batch['files'] if f['id']==payload.get('fileId')),None) if batch else None
            if not file: raise SaaSError('File not found in this workspace.','NotFound',404)
            if batch['status']!='UPLOADING': raise SaaSError('Batch is already closed.','BatchClosed',409)
            content=self.content(payload)
            if len(content)!=file['size'] or hashlib.sha256(content).hexdigest()!=file['sha256']:
                raise SaaSError('File changed after validation. Validate it again.','ChecksumMismatch',400)
            try: validate(content,file['name'],batch['modality'])
            except Exception: raise SaaSError('This file could not be validated.','InvalidFile',400) from None
            key=(tenant,batch['id'],file['id'])
            if sum(len(v) for k,v in self.bytes.items() if k[0]==tenant)-len(self.bytes.get(key,b''))+len(content)>40*1024*1024:
                raise SaaSError('Demo staging limit is 40 MB per workspace. Restart the disposable demo.','DemoLimit',409)
            self.bytes[key]=content; file['status']='LOCAL_STAGED'
            return {'status':'LOCAL_STAGED'}
        match=re.fullmatch(r'/api/library/batches/([a-f0-9]{32})/(complete|retry)',path)
        if match:
            batch=batches.get(match[1])
            if not batch: raise SaaSError('Batch not found in this workspace.','NotFound',404)
            if batch['status']!='UPLOADING': return {'batchId':batch['id'],'status':batch['status']}
            for f in batch['files']:
                if f['status']!='LOCAL_STAGED':
                    f['status']='FAILED'; f['error']={'stage':'local-staging','code':'FileNotStaged','reason':'This file was not received by the local demo.','action':'Select, validate and stage the file again.'}
            batch['status']='LOCAL_STAGED' if all(f['status']=='LOCAL_STAGED' for f in batch['files']) else 'COMPLETED_WITH_ERRORS'
            batch['events'].append({'time':timestamp(),'message':'Local staging finished. No S3 upload or indexing was attempted.'})
            return {'batchId':batch['id'],'status':batch['status']}
        raise SaaSError('Endpoint not found.','NotFound',404)
