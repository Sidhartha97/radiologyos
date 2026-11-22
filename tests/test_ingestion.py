import ast
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile
import uuid
import re
import html
from types import SimpleNamespace
from contextlib import redirect_stdout
from unittest.mock import Mock, patch
from ingestion.documents import extract, classify, structure, validate_single_report
from ingestion.retrieval import retrieve_reports
from ingestion.scope import resolve_modality, ModalityRequired, ModalityConflict
from ingestion import batch

TEXT='MRI KNEE\nTECHNIQUE\nTechnique example.\nFINDINGS\n• Historical effusion.\nIMPRESSION\n1. Historical impression.'
CFG={'tenantId':'t1','bucket':'reports','prefix':'old-reports/','finalPrefix':'final-reports/','knowledgeBaseId':'kb'}


class Missing(Exception):
    response={'Error':{'Code':'404'}}


class S3:
    def __init__(self): self.objects={}; self.metadata={}; self.writes=0
    def head_object(self,Bucket,Key):
        if Key not in self.objects: raise Missing()
        return {'Metadata':self.metadata[Key]}
    def put_object(self,Bucket,Key,Body,Metadata=None,**kwargs):
        self.objects[Key]=Body; self.metadata[Key]=Metadata or {}; self.writes+=1
    def get_object(self,Bucket,Key): return {'Body':io.BytesIO(self.objects[Key])}


class MemoryManifest:
    items={}
    def __init__(self,conn): pass
    def initialize(self): pass
    def acquire(self, tenant): pass
    def release(self): pass
    def records(self,tenant): return {k:dict(v) for k,v in self.items.items()}
    def put(self,tenant,identity,payload): self.items[identity]=dict(payload)


def hit(index=1,modality='MRI',tenant='t1',score=.9):
    return {'metadata':{'tenant_id':tenant,'modality':modality,'report_id':str(index)},'score':score,
        'location':{'s3Location':{'uri':f's3://reports/old-reports/{tenant}/{index}.txt'}},'content':{'text':'partial chunk only'}}


class Documents(unittest.TestCase):
    def test_explicit_modality_for_single_modality_folder(self):
        root=Path('historical/CT')
        _, metadata=batch.prepare_document(root/'USG_CECT.txt', b'CECT ABDOMEN\nFINDINGS\nExample\nIMPRESSION\nExample', root, selected_modality='CT')
        self.assertEqual(metadata['modality'],'CT')
        self.assertEqual(metadata['classification_method'],'selected')
        with self.assertRaisesRegex(ValueError,'Multiple reports'):
            batch.prepare_document(root/'combined.txt',(TEXT+'\n'+TEXT).encode(),root,selected_modality='CT')

    def test_folder_modality_overrides_conflicting_filename(self):
        root = Path('historical')
        _, metadata = batch.prepare_document(root/'CT'/'USG_CECT_ABDOMEN.txt',
            b'CECT ABDOMEN\nFINDINGS\nExample finding.\nIMPRESSION\nExample impression.', root, True)
        self.assertEqual(metadata['modality'], 'CT')
        self.assertEqual(metadata['classification_method'], 'folder')

    def test_folder_mr_alias_and_combined_rejection(self):
        root = Path('historical')
        _, metadata = batch.prepare_document(root/'MR'/'study.txt', TEXT.encode(), root, True)
        self.assertEqual(metadata['modality'], 'MRI')
        with self.assertRaisesRegex(ValueError, 'Multiple reports'):
            batch.prepare_document(root/'MR'/'study.txt', (TEXT+'\n'+TEXT).encode(), root, True)

    def test_docx_complete_sections(self):
        content=io.BytesIO()
        with zipfile.ZipFile(content,'w') as z:
            z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>FINDINGS</w:t></w:r></w:p><w:p><w:r><w:t>IMPRESSION</w:t></w:r></w:p></w:document>')
        self.assertEqual(extract(content.getvalue(),'.docx'),'FINDINGS\nIMPRESSION')

    def test_pdf(self):
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
        writer=PdfWriter(); page=writer.add_blank_page(600,800)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream=DecodedStreamObject(); stream.set_data(b'BT /F1 12 Tf 10 700 Td (FINDINGS) Tj 0 -20 Td (IMPRESSION) Tj ET')
        page[NameObject('/Contents')]=writer._add_object(stream)
        output=io.BytesIO(); writer.write(output)
        text=extract(output.getvalue(),'.pdf')
        self.assertIn('FINDINGS',text); self.assertIn('IMPRESSION',text)

    def test_docx_word_list_preserved(self):
        content=io.BytesIO()
        with zipfile.ZipFile(content,'w') as z:
            z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>Effusion.</w:t></w:r></w:p></w:document>')
            z.writestr('word/numbering.xml','<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/></w:lvl></w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>')
        self.assertEqual(extract(content.getvalue(),'.docx'),'1. Effusion.')

    def test_empty_and_broken(self):
        for content,suffix in ((b'', '.txt'),(b'broken','.docx'),(b'broken','.pdf')):
            with self.subTest(suffix=suffix),self.assertRaises(Exception): extract(content,suffix)

    def test_modalities_regions(self):
        for text,modality,region in [('MRI BRAIN','MRI','BRAIN'),('HRCT CHEST','CT','CHEST'),('USG WHOLE ABDOMEN','USG','ABDOMEN'),('X-ray knee','XRAY','KNEE'),('MRCP','MRI','ABDOMEN')]:
            with self.subTest(text=text):
                actual=classify(text); self.assertEqual(actual['modality'],modality); self.assertEqual(actual['body_region'],region)
                self.assertEqual(actual['study_key'],text.upper())

    def test_style(self):
        result=structure(TEXT)
        self.assertEqual(result['sections'],'TECHNIQUE,FINDINGS,IMPRESSION')
        self.assertEqual(result['findings_style'],'bullet'); self.assertEqual(result['impression_style'],'numbered')

    def test_rules_skip_model(self):
        fallback=Mock()
        classify(TEXT,fallback=fallback); fallback.assert_not_called()

    def test_model_fallback(self):
        result=classify('unclassified',fallback=lambda _:dict(modality='USG',body_region='ABDOMEN',study_type='USG ABDOMEN'))
        self.assertEqual(result['classification_method'],'bedrock')

    def test_combined_reports(self):
        with self.assertRaisesRegex(ValueError,'Multiple reports'): validate_single_report(TEXT+'\n'+TEXT)


class Retrieval(unittest.TestCase):
    def setUp(self):
        self.s3=S3()
        for index in range(1,10): self.s3.objects[f'old-reports/t1/{index}.txt']=TEXT.encode()
        self.client=Mock(); self.client.retrieve.return_value={'retrievalResults':[hit(),hit(),hit(2)]}

    def test_complete_deduplicated(self):
        reports,_=retrieve_reports(self.client,self.s3,CFG,{'prompt':'MRI knee effusion','modality':'CT','confirmedModality':'MRI'})
        self.assertEqual(len(reports),2); self.assertIn('Historical impression',reports[0]['text'])
        self.assertNotIn('partial chunk',reports[0]['text'])
        filt=str(self.client.retrieve.call_args.kwargs)
        self.assertIn('MRI',filt); self.assertIn('tenant_id',filt)

    def test_cross_tenant_and_modality_rejected(self):
        self.client.retrieve.return_value={'retrievalResults':[hit(1,tenant='other'),hit(2,modality='CT')]}
        reports,_=retrieve_reports(self.client,self.s3,CFG,{'prompt':'MRI knee','tenantId':'other'})
        self.assertEqual(reports,[])

    def test_ct_and_usg(self):
        for modality,query in [('CT','CT brain infarct'),('USG','USG fatty liver')]:
            self.client.retrieve.return_value={'retrievalResults':[hit(modality=modality)]}
            reports,_=retrieve_reports(self.client,self.s3,CFG,{'prompt':query})
            self.assertEqual(len(reports),1)

    def test_top_k(self):
        self.client.retrieve.return_value={'retrievalResults':[hit(i) for i in range(1,10)]}
        with patch.dict(os.environ,{'RAG_TOP_K':'3'}):
            reports,_=retrieve_reports(self.client,self.s3,CFG,{'prompt':'MRI knee'})
        self.assertEqual(len(reports),3)

    def test_context_limit_skips_not_truncates(self):
        with patch.dict(os.environ,{'RAG_CONTEXT_MAX_CHARS':'10'}):
            reports,_=retrieve_reports(self.client,self.s3,CFG,{'prompt':'MRI knee'})
        self.assertEqual(reports,[])

    def test_no_results(self):
        self.client.retrieve.return_value={}
        self.assertEqual(retrieve_reports(self.client,self.s3,CFG,{'prompt':'MRI brain'})[0],[])
        for call in self.client.retrieve.call_args_list:
            filt=str(call.kwargs['retrievalConfiguration'])
            self.assertIn('MRI',filt); self.assertIn('tenant_id',filt)


class Batch(unittest.TestCase):
    def test_dynamodb_manifest_tenant_query_pagination(self):
        from decimal import Decimal
        table=Mock()
        table.query.side_effect=[
            {'Items':[{'report_id':'a','payload':{'status':'COMPLETED','score':Decimal('0.95')}}],
             'LastEvaluatedKey':{'PK':'TENANT#t1','SK':'INGESTION#REPORT#a'}},
            {'Items':[{'report_id':'b','payload':{'status':'PROCESSING'}}]}]
        result=batch.Manifest(table).records('t1')
        self.assertEqual(set(result),{'a','b'})
        self.assertEqual(result['a']['score'],0.95)
        self.assertTrue(table.query.call_args.kwargs['ConsistentRead'])
        condition=table.query.call_args.kwargs['KeyConditionExpression'].get_expression()['values']
        self.assertEqual(condition[0].get_expression()['values'][1],'TENANT#t1')
        self.assertEqual(condition[1].get_expression()['values'][1],'INGESTION#REPORT#')
        self.assertIn('ExclusiveStartKey', table.query.call_args.kwargs)

    def test_dynamodb_manifest_payload_serialization_and_lock(self):
        from boto3.dynamodb.types import TypeSerializer
        table=Mock(); table.get_item.return_value={}; manifest=batch.Manifest(table)
        manifest.acquire('t1')
        manifest.put('t1','r1',{'status':'PROCESSING','classification_confidence':0.95,'processed_at':None})
        item=table.put_item.call_args.kwargs['Item']
        TypeSerializer().serialize(item)
        self.assertEqual(item['PK'],'TENANT#t1')
        self.assertEqual(item['SK'],'INGESTION#REPORT#r1')
        with self.assertRaises(ValueError): manifest.put('other','r1',{})
        manifest.release()
        self.assertEqual(table.delete_item.call_args.kwargs['ExpressionAttributeValues'][':owner'],manifest.owner)

    def test_dynamodb_manifest_rejects_second_importer(self):
        from botocore.exceptions import ClientError
        table=Mock()
        table.update_item.side_effect=ClientError({'Error':{'Code':'ConditionalCheckFailedException'}},'UpdateItem')
        table.get_item.return_value={}
        manifest=batch.Manifest(table)
        with self.assertRaisesRegex(RuntimeError,'Another ingestion'):
            manifest.acquire('t1')
        manifest.release()
        table.delete_item.assert_not_called()

    def test_manifest_connection_uses_dynamodb_not_postgres(self):
        import boto3
        with patch.dict(os.environ,{'DYNAMODB_TABLE':'radiologyos-dev','DATABASE_URL':'unreachable'}),patch.object(boto3,'resource') as resource:
            batch.connect_manifest()
        self.assertEqual(resource.call_args.args[0],'dynamodb')
        resource.return_value.Table.assert_called_once_with('radiologyos-dev')

    def test_upload_unchanged(self):
        s3=S3()
        self.assertTrue(batch.put_if_changed(s3,'b','k',b'text'))
        self.assertFalse(batch.put_if_changed(s3,'b','k',b'text'))
        self.assertEqual(s3.writes,1)

    def test_discover_recursive_dedup_tenant(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'sub').mkdir()
            (root/'a.txt').write_text(TEXT,encoding='utf-8'); (root/'sub'/'b.txt').write_text(TEXT,encoding='utf-8')
            files,errors=batch.discover(root,'t1'); self.assertEqual(len(files),1); self.assertFalse(errors)
            self.assertNotEqual(batch.report_id('t1',b'a'),batch.report_id('t2',b'a'))

    def test_per_document_index_state_not_job_state(self):
        manifest=Mock(); agent=Mock()
        agent.list_knowledge_base_documents.return_value={'documentDetails':[{'identifier':{'s3':{'uri':'s3://b/a.txt'}},'status':'FAILED','statusReason':'metadata rejected'}]}
        records={'r':{'status':'PROCESSING','s3_key':'a.txt'}}
        batch.sync_status(agent,{'kb':'kb','ds':'ds','bucket':'b','tenant':'t1'},manifest,records)
        self.assertEqual(records['r']['status'],'FAILED')
        agent.list_knowledge_base_documents.return_value['documentDetails'][0]['status']='INDEXED'
        records['r']['status']='PROCESSING'
        batch.sync_status(agent,{'kb':'kb','ds':'ds','bucket':'b','tenant':'t1'},manifest,records)
        self.assertEqual(records['r']['status'],'COMPLETED')

    def test_existing_job_reused(self):
        agent=Mock(); agent.list_ingestion_jobs.return_value={'ingestionJobSummaries':[{'status':'IN_PROGRESS','ingestionJobId':'job'}]}
        self.assertEqual(batch.queue_ingestion(agent,{'kb':'k','ds':'d'})['job_id'],'job')
        agent.start_ingestion_job.assert_not_called()

    def test_dry_run_no_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/'a.txt').write_text(TEXT,encoding='utf-8')
            with patch.object(batch,'load_env'),patch.object(batch,'connect_manifest',side_effect=RuntimeError('offline')),patch.object(batch,'put_if_changed') as upload,redirect_stdout(io.StringIO()) as out:
                batch.main(['--folder',folder,'--dry-run'])
            upload.assert_not_called(); self.assertIn('unique_files',out.getvalue())

    def test_limit_resume_retry(self):
        import boto3
        conn=Mock(); conn.execute.return_value.fetchone.return_value=(True,)
        agent=Mock(); agent.list_knowledge_base_documents.return_value={}; agent.list_ingestion_jobs.return_value={}
        agent.start_ingestion_job.return_value={'ingestionJob':{'status':'STARTING','ingestionJobId':'j'}}
        s3=S3(); MemoryManifest.items={}
        cfg={'region':'us-east-1','tenant':'t1','bucket':'b','prefix':'old-reports/','originalPrefix':'historical-originals/','kb':'k','ds':'d'}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'a.txt').write_text(TEXT,encoding='utf-8'); (root/'b.txt').write_text('MRI BRAIN\nFINDINGS\ncurrent text\nIMPRESSION\nexample',encoding='utf-8')
            with patch.dict(os.environ,{'DEFAULT_TENANT_ID':'t1'}),patch.object(batch,'load_env'),patch.object(batch,'settings',return_value=cfg),patch.object(batch,'connect_manifest',return_value=conn),patch.object(batch,'Manifest',MemoryManifest),patch.object(boto3,'client',side_effect=lambda name,**kw: s3 if name=='s3' else agent),redirect_stdout(io.StringIO()):
                batch.main(['--folder',folder,'--limit','1','--batch-size','1'])
                self.assertEqual(sum(x['status']=='PROCESSING' for x in MemoryManifest.items.values()),1,MemoryManifest.items)
                batch.main(['--folder',folder,'--resume'])
                self.assertEqual(sum(x['status']=='PROCESSING' for x in MemoryManifest.items.values()),2)
                writes=s3.writes
                batch.main(['--folder',folder,'--resume']); self.assertEqual(s3.writes,writes)
                first=next(iter(MemoryManifest.items.values())); first['status']='FAILED'
                batch.main(['--folder',folder,'--retry-failed']); self.assertEqual(first['status'],'FAILED')
                self.assertEqual(s3.writes,writes)
                self.assertEqual(sum(x['status']=='PROCESSING' for x in MemoryManifest.items.values()),2)


class Generation(unittest.TestCase):
    def functions(self):
        from ingestion.documents import normalize_modality
        # Load just pure functions, never import server.py (which loads real DB).
        source=Path(__file__).resolve().parents[1]/'server.py'
        tree=ast.parse(source.read_text())
        selected=ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('rag_prompt','rag_generate')],type_ignores=[])
        cfg={**CFG,'enabled':True,'llmProvider':'bedrock','modelArn':'model'}
        runtime=Mock(); runtime.converse.return_value={'output':{'message':{'content':[{'text':'STUDY\n**MRI KNEE**\nFINDINGS\nEffusion.\nIMPRESSION\nEffusion.'}]}}}
        namespace={'os':os,'json':json,'normalize_modality':normalize_modality,'aws_settings':lambda:cfg,'aws_client':lambda _:runtime,'retrieve_reports':Mock(return_value=([{'uri':'s3://reports/old-reports/1.txt','text':TEXT,'score':.9,'metadata':{}}],{}))}
        exec(compile(selected,str(source),'exec'),namespace)
        return namespace,runtime

    def test_generation_separates_current_and_reference(self):
        ns,runtime=self.functions()
        result=ns['rag_generate']({'prompt':'MRI knee with effusion','modality':'MRI'})
        self.assertEqual(result['mode'],'aws')
        call=runtime.converse.call_args.kwargs
        self.assertIn('Never transfer reference measurements',call['system'][0]['text'])
        self.assertIn('provisional defaults requiring clinician verification',call['system'][0]['text'])
        self.assertIn('never output [NOT PROVIDED]',call['system'][0]['text'])
        self.assertIn('Bold positive findings',call['system'][0]['text'])
        self.assertIn('review ALL related template statements',call['system'][0]['text'])
        self.assertIn('Do not invent associated findings',call['system'][0]['text'])
        self.assertIn('CLINICAL CONSISTENCY CHECK',call['messages'][0]['content'][0]['text'])
        self.assertIn('not current patient evidence',call['messages'][0]['content'][0]['text'])

    def test_no_result_never_fabricates_report(self):
        ns,runtime=self.functions(); ns['retrieve_reports'].return_value=([],{})
        result=ns['rag_generate']({'prompt':'MRI knee'})
        self.assertEqual(result['text'],''); runtime.converse.assert_not_called()

    def test_all_modalities_receive_paragraph_and_measurement_rules(self):
        for modality in ('CT','MRI','USG','XRAY','BMD','MAMMOGRAM'):
            with self.subTest(modality=modality):
                ns,runtime=self.functions()
                ns['rag_generate']({'modality':modality,'prompt':'Liver measures 157 mm'})
                call=runtime.converse.call_args.kwargs
                system=call['system'][0]['text']
                prompt=call['messages'][0]['content'][0]['text']
                self.assertIn('paragraphs without bullets or numbering',system)
                self.assertIn('bullet points ONLY in IMPRESSION',system)
                self.assertIn('exactly ..........',system)
                self.assertIn('Preserve every measurement explicitly supplied',prompt)
                self.assertIn('Liver measures 157 mm',prompt)

    def test_generation_error(self):
        ns,runtime=self.functions(); runtime.converse.side_effect=RuntimeError('offline')
        self.assertEqual(ns['rag_generate']({'prompt':'MRI knee'})['mode'],'error')

    def test_edit_uses_current_editor_and_history_without_retrieval(self):
        ns,runtime=self.functions()
        result=ns['rag_generate']({'prompt':'Change size to 165 mm','modality':'USG','intent':'edit',
            'currentReport':'MY MANUAL EDIT: liver 150 mm', '_chatHistory':[{'role':'user','content':'Previous request'}]})
        self.assertEqual(result['retrievalMode'],'editor-revision')
        ns['retrieve_reports'].assert_not_called()
        call=runtime.converse.call_args.kwargs
        self.assertIn('MY MANUAL EDIT: liver 150 mm',call['messages'][0]['content'][0]['text'])
        self.assertIn('Previous request',call['messages'][0]['content'][0]['text'])
        self.assertIn('EDIT MODE takes precedence',call['system'][0]['text'])

    def test_historical_style_not_selected_template_baseline(self):
        ns,runtime=self.functions()
        ns['db']={'templates':[{'id':'t1','modality':'MRI','body':'MRI KNEE\nFINDINGS\nNo joint effusion. Cartilage is preserved.'}]}
        result=ns['rag_generate']({'prompt':'Moderate joint effusion','modality':'MRI','templateId':'t1'})
        self.assertEqual(result['mode'],'aws')
        call=runtime.converse.call_args.kwargs
        self.assertNotIn('Cartilage is preserved.',call['messages'][0]['content'][0]['text'])
        self.assertIn('reference section order',call['system'][0]['text'])
        self.assertIn('terminology',call['system'][0]['text'])

    def test_selected_template_cannot_replace_missing_references(self):
        ns,runtime=self.functions()
        ns['db']={'templates':[{'id':'t1','modality':'CT','body':'CT brain'}]}
        ns['retrieve_reports'].return_value=([],{})
        self.assertEqual(ns['rag_generate']({'modality':'MRI','templateId':'t1'})['mode'],'error')
        runtime.converse.assert_not_called()

    def test_client_cannot_supply_unvalidated_baseline(self):
        ns,runtime=self.functions()
        ns['retrieve_reports'].return_value=([],{})
        self.assertEqual(ns['rag_generate']({'modality':'MRI','_studyTemplate':'Unvalidated normals'})['mode'],'error')
        runtime.converse.assert_not_called()


class ExistingEndpoints(unittest.TestCase):
    def namespace(self):
        source=Path(__file__).resolve().parents[1]/'server.py'
        tree=ast.parse(source.read_text())
        wanted={'plain_report_text','clean_metadata','publish_final_report'}
        selected=ast.Module(body=[n for n in tree.body if (isinstance(n,ast.FunctionDef) and n.name in wanted) or (isinstance(n,ast.ClassDef) and n.name=='App')],type_ignores=[])
        s3=S3(); cfg={**CFG,'enabled':True,'kmsKeyId':'','autoIngestFinal':False}
        sent=[]
        ns={'os':os,'json':json,'uuid':uuid,'re':re,'html':html,'Path':Path,'BaseHTTPRequestHandler':object,
            'aws_settings':lambda:cfg,'aws_client':lambda _:s3,'extract':extract,'classify':classify,'structure':structure,
            'normalize_modality':lambda value:value,'validate_single_report':validate_single_report,'resolve_modality':resolve_modality,
            'historical_id':batch.report_id,'put_if_changed':batch.put_if_changed,'now':lambda:'test',
            'start_ingestion':lambda:{'status':'STARTING'},'save':Mock(),'db':{'uploads':[]},
            'send':lambda handler,status,payload:sent.append((status,payload))}
        exec(compile(selected,str(source),'exec'),ns)
        return ns,s3,sent

    def test_finalized_report_metadata_and_full_text(self):
        ns,s3,_=self.namespace()
        result=ns['publish_final_report']({'id':'r1','patientId':'p','modality':'MRI','body':TEXT,'tenantId':'other'})
        self.assertEqual(result['ragStatus'],'UPLOADED')
        key=result['ragS3Key']; self.assertIn('/t1/',key)
        md=json.loads(s3.objects[key+'.metadata.json'])['metadataAttributes']
        self.assertEqual(md['body_region'],'KNEE'); self.assertEqual(md['tenant_id'],'t1')
        self.assertIn(b'IMPRESSION',s3.objects[key])

    def test_existing_upload_endpoint_original_normalized_and_tenant(self):
        ns,s3,sent=self.namespace()
        class Form(dict):
            def getfirst(self,key,default=''): return self.get(key,default)
        form=Form(file=SimpleNamespace(filename='MRI_knee.txt',file=io.BytesIO(TEXT.encode())),tenantId='other',modality='CT')
        ns['cgi']=SimpleNamespace(FieldStorage=lambda **kw:form)
        with tempfile.TemporaryDirectory() as folder:
            ns['UPLOADS']=Path(folder)
            handler=ns['App'](); handler.rfile=io.BytesIO(); handler.headers={}
            handler.upload()
        self.assertEqual(sent[0][0],201,sent)
        result=sent[0][1]; self.assertEqual(result['tenantId'],'t1'); self.assertEqual(result['modality'],'MRI')
        self.assertEqual(s3.objects[result['originalStorageKey']],TEXT.encode())
        self.assertIn(b'IMPRESSION',s3.objects[result['storageKey']])


if __name__=='__main__': unittest.main()
