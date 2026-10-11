import os
import json
import logging
from urllib.parse import urlparse
from .documents import classify, normalize_modality, extract
from .scope import resolve_modality, tenant_prefixes

LOG = logging.getLogger('radiology.audit')


def retrieve_reports(client, s3, cfg, request):
    query = request.get('prompt','').strip() or request.get('currentReport','').strip()
    analysis = classify(query)
    modality = resolve_modality(request)
    analysis['modality'] = modality
    prefixes = tenant_prefixes(cfg)
    region = classify(request['bodyPart'])['body_region'] if request.get('bodyPart') else analysis['body_region']
    # cfg is constructed from authenticated server context, never browser tenant fields.
    tenant = cfg['tenantId']
    terms = [{'equals': {'key':'tenant_id','value':tenant}}]
    terms.append({'equals':{'key':'modality','value':modality}})
    filters = []
    study = (request.get('studyName') or (analysis['study_key'] if analysis['modality'] != 'UNKNOWN' and len(analysis['study_key'].split()) <= 8 else '')).strip().upper()
    if study and (analysis.get('study_key','').startswith(modality) or request.get('studyName')):
        filters.append(terms + [{'equals':{'key':'study_key','value':study}}])
    if region and region != 'UNKNOWN':
        filters.append(terms + [{'equals':{'key':'body_region','value':region}}])
    filters.append(terms)
    top_k = max(1, min(5, int(os.getenv('RAG_TOP_K','5'))))
    threshold = float(os.getenv('RAG_MIN_SCORE','0'))
    max_context = int(os.getenv('RAG_CONTEXT_MAX_CHARS','30000'))
    max_report = int(os.getenv('RAG_REPORT_MAX_CHARS','15000'))
    chosen = {}
    for terms in filters:
        filt = {'andAll':terms} if len(terms)>1 else terms[0]
        response = client.retrieve(knowledgeBaseId=cfg['knowledgeBaseId'], retrievalQuery={'text':query},
            retrievalConfiguration={'vectorSearchConfiguration':{'numberOfResults':min(100,top_k*4),'filter':filt}})
        for item in sorted(response.get('retrievalResults',[]), key=lambda x:x.get('score',0), reverse=True):
            md = item.get('metadata',{})
            if md.get('tenant_id') != tenant or (modality and modality != 'UNKNOWN' and normalize_modality(md.get('modality')) != modality): continue
            if item.get('score',0) < threshold: continue
            uri = item.get('location',{}).get('s3Location',{}).get('uri','')
            identity = md.get('report_id') or uri
            if identity and identity not in chosen: chosen[identity]=item
        LOG.info(json.dumps({'event':'rag_search','requestId':request.get('_requestId'),
            'tenant':tenant,'modality':modality,'filters':{'andAll':[
                term if term['equals']['key'] in ('tenant_id','modality') else
                {'equals':{'key':term['equals']['key'],'value':'[scope value redacted]'}} for term in terms]},
            'reportIds':[x.get('metadata',{}).get('report_id') for x in chosen.values()]}))
        if chosen: break
    result = []
    used = 0
    for item in list(chosen.values())[:top_k]:
        uri = item.get('location',{}).get('s3Location',{}).get('uri','')
        parsed = urlparse(uri)
        key = parsed.path.lstrip('/')
        if parsed.scheme != 's3' or parsed.netloc != cfg['bucket']: continue
        if not any(key.startswith(prefix) for prefix in prefixes): continue
        data = s3.get_object(Bucket=cfg['bucket'],Key=key)['Body'].read()
        suffix = '.' + key.rsplit('.',1)[-1].lower()
        text = extract(data,suffix)
        # Skip oversized reports rather than silently truncate findings/impression.
        if len(text)>max_report or used+len(text)>max_context: continue
        used += len(text)
        result.append({'uri':uri,'text':text,'score':item.get('score',0),'metadata':item.get('metadata',{})})
    return result, analysis
