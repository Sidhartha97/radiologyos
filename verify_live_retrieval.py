"""Read-only scope verification, no generation calls and no report text printed."""
import argparse
import json
import uuid
from ingestion.batch import load_env
from ingestion.retrieval import retrieve_reports


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tenant',required=True,help='Operator-supplied hospital to verify (not an application endpoint)')
    parser.add_argument('--profile',default='radiology-dev')
    args=parser.parse_args()
    load_env()
    import boto3, os
    session=boto3.Session(profile_name=args.profile,region_name=os.getenv('AWS_REGION','us-east-1'))
    cfg={'tenantId':args.tenant,'bucket':os.getenv('RAG_S3_BUCKET') or os.getenv('documents_bucket'),
         'knowledgeBaseId':os.getenv('BEDROCK_KNOWLEDGE_BASE_ID') or os.getenv('knowledge_base_id'),
         'prefix':os.getenv('RAG_S3_PREFIX','old-reports/'),'finalPrefix':os.getenv('RAG_FINAL_REPORTS_PREFIX','final-reports/')}
    for modality, query in [('USG','USG abdomen and pelvis'),('CT','CT brain'),('MRI','MRI spine')]:
        try:
            refs,_=retrieve_reports(session.client('bedrock-agent-runtime'),session.client('s3'),cfg,
                                   {'modality':modality,'prompt':query,'_requestId':uuid.uuid4().hex})
            matched=all(r['metadata'].get('tenant_id')==args.tenant and r['metadata'].get('modality')==modality for r in refs)
            print(json.dumps({'modality':modality,'status':'PASS' if refs and matched else 'NO_MATCH' if matched else 'FAIL',
                'count':len(refs),'reportIds':[r['metadata'].get('report_id') for r in refs]}))
        except Exception as exc:
            code=getattr(exc,'response',{}).get('Error',{}).get('Code',type(exc).__name__)
            print(json.dumps({'modality':modality,'status':'ERROR','code':code}))


if __name__=='__main__': main()
