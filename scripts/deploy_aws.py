"""Deploy current source to the approved single-node EC2 testing host.

Supports /32-limited HTTP testing ingress or SSM tunnel access. No Terraform
apply/destroy, local Docker access or embedded AWS keys required.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import time

ROOT=Path(__file__).resolve().parents[1]
TOP_FILES=('Dockerfile','requirements.txt','server.py','security.py','tenant_store.py','patient_chat.py','manage_hospitals.py','migrate_json_to_postgres.py')
DIRS=('saas','ingestion','migrations','scripts','frontend/src')
FRONT_FILES=('index.html','package.json','package-lock.json','tsconfig.json','tsconfig.node.json','vite.config.ts')


def source_bundle(root=ROOT):
    paths=[root/name for name in TOP_FILES]+[root/'frontend'/name for name in FRONT_FILES]
    for directory in DIRS:
        paths.extend(path for path in (root/directory).rglob('*') if path.is_file() and '__pycache__' not in path.parts and path.suffix not in ('.pyc','.pyo'))
    digest=hashlib.sha256(); entries=[]
    for path in sorted(set(paths)):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()): raise ValueError('Unsafe source path')
        relative=path.relative_to(root).as_posix(); data=path.read_bytes()
        digest.update(relative.encode()+b'\0'+data); entries.append((relative,data))
    buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w:gz') as archive:
        for relative,data in entries:
            item=tarfile.TarInfo(relative); item.size=len(data); item.mode=0o644
            archive.addfile(item,io.BytesIO(data))
    return digest.hexdigest()[:32],buffer.getvalue()


def terraform_outputs():
    result=subprocess.run(['terraform','output','-json'],cwd=ROOT/'terraform',check=True,capture_output=True,text=True)
    return {key:value['value'] for key,value in json.loads(result.stdout).items()}


def tunnel_environment(environ=None,*,windows=None):
    """Find an installed plugin for the AWS child process; no system PATH edits."""
    env=dict(os.environ if environ is None else environ)
    path_key=next((key for key in env if key.upper()=='PATH'),'PATH')
    path=env.get(path_key,'')
    if shutil.which('session-manager-plugin',path=path): return env
    is_windows=sys.platform=='win32' if windows is None else windows
    if is_windows:
        for name,default in (('ProgramFiles',r'C:\Program Files'),('ProgramFiles(x86)',r'C:\Program Files (x86)')):
            root=next((value for key,value in env.items() if key.lower()==name.lower()),default)
            executable=Path(root)/'Amazon'/'SessionManagerPlugin'/'bin'/'session-manager-plugin.exe'
            if executable.is_file():
                env[path_key]=str(executable.parent)+os.pathsep+path
                return env
    raise ValueError('AWS Session Manager plugin not found. Complete the official Windows installation: '
        'https://docs.aws.amazon.com/systems-manager/latest/userguide/install-plugin-windows.html')


def start_tunnel(instance,profile,region,local_port):
    env=tunnel_environment()
    return subprocess.call(['aws','ssm','start-session','--target',instance,'--document-name','AWS-StartPortForwardingSession',
        '--parameters',json.dumps({'portNumber':['8000'],'localPortNumber':[str(local_port)]}),
        '--profile',profile,'--region',region],env=env)


def environment(outputs,region):
    mapping={'RAG_S3_BUCKET':'documents_bucket','BEDROCK_KNOWLEDGE_BASE_ID':'knowledge_base_id',
        'BEDROCK_DATA_SOURCE_ID':'data_source_id','BEDROCK_FINAL_DATA_SOURCE_ID':'final_data_source_id',
        'BEDROCK_MODEL_ARN':'generation_model_arn','RAG_KMS_KEY_ID':'kms_key_arn','DYNAMODB_TABLE':'dynamodb_table',
        'COGNITO_USER_POOL_ID':'cognito_user_pool_id','COGNITO_CLIENT_ID':'cognito_client_id',
        'POSTGRES_ENDPOINT':'postgres_endpoint','POSTGRES_DATABASE':'postgres_database',
        'SAAS_DATABASE_SECRET_ARN':'runtime_database_secret_arn'}
    config={key:str(outputs[value]) for key,value in mapping.items()}
    if any(value in ('','None') for value in config.values()): raise ValueError('Terraform outputs incomplete')
    return {**config,'AWS_REGION':region,'AWS_DEFAULT_REGION':region,'APP_HOST':'0.0.0.0','APP_PORT':'8000',
        'APP_ENV':'development','APP_COOKIE_SECURE':'false','SAAS_ENABLED':'true','SAAS_AI_ENABLED':'true',
        'SAAS_USER_PROVISIONING_ENABLED':'true','AWS_RAG_ENABLED':'true','LLM_PROVIDER':'bedrock',
        'SAAS_CUSTOMER_DELETION_ENABLED':'true',
        'RAG_S3_PREFIX':'old-reports/','RAG_FINAL_REPORTS_PREFIX':'final-reports/','RAG_AUTO_INGEST_FINAL':'true',
        'SAAS_DB_POOL_MAX_SIZE':'4'}


def run_command(client,instance,commands,label,timeout=1800):
    response=client.send_command(InstanceIds=[instance],DocumentName='AWS-RunShellScript',TimeoutSeconds=60,
        Parameters={'commands':['set -eu',*commands],'executionTimeout':[str(timeout)]})
    command=response['Command']['CommandId']; deadline=time.monotonic()+timeout+90; next_notice=0
    while time.monotonic()<deadline:
        try: result=client.get_command_invocation(CommandId=command,InstanceId=instance)
        except client.exceptions.InvocationDoesNotExist: time.sleep(2); continue
        if result['Status']=='Success':
            print(json.dumps({'stage':label,'status':'COMPLETE','commandId':command}),flush=True)
            return result
        if result['Status'] in ('Failed','TimedOut','Cancelled','Cancelling'):
            raise RuntimeError(f'{label} failed; inspect SSM command {command} (no credentials are included in commands)')
        if time.monotonic()>=next_notice:
            print(json.dumps({'stage':label,'status':result['Status'],'commandId':command}),flush=True); next_notice=time.monotonic()+30
        time.sleep(5)
    raise RuntimeError(label+' wait limit reached; inspect SSM status before retrying')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['deploy','tunnel','status'])
    parser.add_argument('--profile',default='radiology-dev')
    parser.add_argument('--region',default='us-east-1')
    parser.add_argument('--admin-email')
    parser.add_argument('--approve-schema',action='store_true')
    parser.add_argument('--local-port',type=int,default=18000)
    args=parser.parse_args()
    try:
        outputs=terraform_outputs(); instance=outputs.get('testing_instance_id')
        if not instance: raise ValueError('Enable/apply the reviewed testing-host Terraform configuration first')
        if args.action=='tunnel':
            return start_tunnel(instance,args.profile,args.region,args.local_port)
        import boto3
        from botocore.config import Config
        session=boto3.Session(profile_name=args.profile,region_name=args.region)
        client=lambda name:session.client(name,config=Config(connect_timeout=5,read_timeout=15,retries={'max_attempts':2}))
        account=client('sts').get_caller_identity()['Account']
        if outputs['application_role_arn'].split(':')[4]!=account: raise ValueError('AWS account differs from Terraform state')
        ssm=client('ssm')
        if args.action=='status':
            result=run_command(ssm,instance,['systemctl is-active radiologyos.service','curl --fail --silent http://127.0.0.1:8000/api/health'],'health',60)
            print(result.get('StandardOutputContent','')); return 0
        if not args.approve_schema: raise ValueError('Deploy requires explicit approval of reviewed migrations via --approve-schema')
        config=environment(outputs,args.region)
        client('secretsmanager').put_secret_value(SecretId=outputs['application_config_secret_arn'],SecretString=json.dumps(config))
        digest,bundle=source_bundle(); key='deployment-bundles/'+digest+'.tar.gz'
        client('s3').put_object(Bucket=outputs['documents_bucket'],Key=key,Body=bundle,ContentType='application/gzip',
            ServerSideEncryption='aws:kms',SSEKMSKeyId=outputs['kms_key_arn'])
        repository=outputs['application_repository_url']; image=repository+':'+digest
        ecr=client('ecr'); exists=False
        try: ecr.describe_images(repositoryName=repository.split('/',1)[1],imageIds=[{'imageTag':digest}]); exists=True
        except ecr.exceptions.ImageNotFoundException: pass
        q=shlex.quote; directory='/opt/radiologyos/builds/'+digest
        commands=['cloud-init status --wait',f'mkdir -p {q(directory)}',
            f'aws s3 cp {q("s3://"+outputs["documents_bucket"]+"/"+key)} {q(directory+"/source.tar.gz")} --region {q(args.region)}',
            f'tar -xzf {q(directory+"/source.tar.gz")} -C {q(directory)}',
            f'aws ecr get-login-password --region {q(args.region)} | docker login --username AWS --password-stdin {q(repository.split("/")[0])}']
        if exists: commands.append(f'docker pull {q(image)}')
        else: commands.extend([f'docker build --label radiologyos=true -t {q(image)} {q(directory)}',f'docker push {q(image)}'])
        run_command(ssm,instance,commands,'image-build',1800)
        # Only this explicit operator bootstrap temporarily gets master-secret
        # read access. The normal host policy cannot read that secret.
        iam=client('iam'); role=outputs['testing_host_role_name']; policy='radiologyos-bootstrap-temporary'
        bootstrap_permission_added=False
        try:
            iam.put_role_policy(RoleName=role,PolicyName=policy,PolicyDocument=json.dumps({'Version':'2012-10-17','Statement':[
                {'Effect':'Allow','Action':['secretsmanager:GetSecretValue'],'Resource':outputs['postgres_secret_arn']}]}))
            bootstrap_permission_added=True
            time.sleep(10)
            command=f'docker run --rm --network host -e AWS_DEFAULT_REGION={q(args.region)} {q(image)} python scripts/bootstrap_deployment.py --approve-schema --region {q(args.region)} --config-secret {q(outputs["application_config_secret_arn"])} --master-secret {q(outputs["postgres_secret_arn"])}'
            if args.admin_email: command+=' --admin-email '+q(args.admin_email)
            result=run_command(ssm,instance,[command],'reviewed-bootstrap',600)
            print(result.get('StandardOutputContent',''))
        finally:
            if bootstrap_permission_added: iam.delete_role_policy(RoleName=role,PolicyName=policy)
        run_command(ssm,instance,[f'printf "%s\\n" {q(image)} > /opt/radiologyos/image.txt',
            'systemctl restart radiologyos.service',
            'for attempt in $(seq 1 30); do if curl --fail --silent http://127.0.0.1:8000/api/health; then exit 0; fi; sleep 2; done; exit 1'],'start',90)
        web_ip=outputs.get('testing_instance_public_ip')
        access=f'http://{web_ip}:8000/' if web_ip else 'No public web ingress is configured; set testing_app_client_cidr to one IPv4 /32 or use the SSM tunnel.'
        print(json.dumps({'deployment':'RUNNING','instance':instance,'image':image,'access':access}))
        return 0
    except Exception as exc:
        # No SQL/provider details or secret data; only our controlled operational errors.
        message=str(exc) if type(exc) in (ValueError,RuntimeError) else type(exc).__name__
        print(json.dumps({'deployment':'FAILED','reason':message}),file=sys.stderr); return 1


if __name__=='__main__': raise SystemExit(main())
