"""Explicit operator rollout. No .env loading, master-password logging or Terraform apply.

Run only with reviewed AWS/database approval. This is not application startup.
"""
import argparse
import json
from pathlib import Path
import secrets
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['database','iam','invite-admin','verify'])
    parser.add_argument('--profile',required=True)
    parser.add_argument('--region',default='us-east-1')
    parser.add_argument('--confirm-account',required=True)
    parser.add_argument('--database',default='radiology')
    parser.add_argument('--host',required=True)
    parser.add_argument('--master-secret',required=True)
    parser.add_argument('--runtime-secret',default='radiologyos-dev/application-postgres')
    parser.add_argument('--runtime-role',default='radiologyos_runtime')
    parser.add_argument('--application-role',default='radiologyos-dev-application')
    parser.add_argument('--kms-key',required=True)
    parser.add_argument('--pool',required=True)
    parser.add_argument('--email')
    args=parser.parse_args()
    import boto3
    import psycopg
    from psycopg import sql
    from botocore.config import Config
    from saas.schema import apply,grant_runtime,inspect_legacy,legacy_rows,migration_status
    from saas.tenants import check_runtime_role
    from saas.accounts import email_address
    stage='identity'
    try:
        session=boto3.Session(profile_name=args.profile,region_name=args.region)
        def client(name): return session.client(name,config=Config(connect_timeout=5,read_timeout=15,retries={'max_attempts':2}))
        if client('sts').get_caller_identity()['Account']!=args.confirm_account:
            raise ValueError('AWS account mismatch')
        sm=client('secretsmanager')
        def owner():
            value=json.loads(sm.get_secret_value(SecretId=args.master_secret)['SecretString'])
            return psycopg.connect(host=args.host,port=5432,dbname=args.database,
                user=value['username'],password=value['password'],connect_timeout=8,sslmode='require')
        if args.action=='database':
            import re
            if not re.fullmatch(r'[a-z][a-z0-9_]{1,62}',args.runtime_role): raise ValueError('Invalid runtime role')
            stage='database-migrations'
            with owner() as conn:
                preflight=inspect_legacy(*legacy_rows(conn))
                if not preflight['ready'] or preflight['tenants'] or preflight['reports']:
                    raise ValueError('Nonempty legacy database requires reviewed backfill')
                if conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(args.runtime_role,)).fetchone():
                    raise ValueError('Runtime role already exists; use verify, never silently rotate credentials')
                try:
                    sm.describe_secret(SecretId=args.runtime_secret)
                except sm.exceptions.ResourceNotFoundException: pass
                else: raise ValueError('Runtime secret already exists; review partial rollout before retry')
                rows=apply(conn)
                password=secrets.token_urlsafe(48)
                conn.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE').format(sql.Identifier(args.runtime_role),sql.Literal(password)))
                grant_runtime(conn,args.runtime_role)
            stage='runtime-secret-create'
            result=sm.create_secret(Name=args.runtime_secret,KmsKeyId=args.kms_key,
                Description='Radiology OS dedicated non-owner application PostgreSQL credentials',
                SecretString=json.dumps({'host':args.host,'port':5432,'dbname':args.database,
                    'username':args.runtime_role,'password':password}),
                Tags=[{'Key':'Project','Value':'radiologyos'},{'Key':'Environment','Value':'dev'}])
            print(json.dumps({'action':args.action,'migrationsApplied':len(rows),'runtimeSecretArn':result['ARN'],'runtimeRole':args.runtime_role}))
        elif args.action=='iam':
            stage='scoped-iam-policy'
            arn=sm.describe_secret(SecretId=args.runtime_secret)['ARN']
            pool=f'arn:aws:cognito-idp:{args.region}:{args.confirm_account}:userpool/{args.pool}'
            policy={'Version':'2012-10-17','Statement':[
                {'Effect':'Allow','Action':['secretsmanager:GetSecretValue'],'Resource':arn},
                {'Effect':'Allow','Action':['cognito-idp:AdminCreateUser','cognito-idp:AdminGetUser'],'Resource':pool}]}
            # Separate policy: never overwrite Terraform's application policy.
            client('iam').put_role_policy(RoleName=args.application_role,PolicyName='radiologyos-approved-runtime-access',PolicyDocument=json.dumps(policy))
            print(json.dumps({'action':args.action,'role':args.application_role,'policy':'radiologyos-approved-runtime-access','terraformChanged':False}))
        elif args.action=='invite-admin':
            email=email_address(args.email)
            stage='cognito-platform-user'
            cognito=client('cognito-idp')
            try: user=cognito.admin_get_user(UserPoolId=args.pool,Username=email)
            except cognito.exceptions.UserNotFoundException:
                user=cognito.admin_create_user(UserPoolId=args.pool,Username=email,MessageAction='SUPPRESS',UserAttributes=[{'Name':'email','Value':email}])['User']
            values={item['Name']:item['Value'] for item in user.get('Attributes',user.get('UserAttributes',[]))}
            if values.get('email')!=email or not values.get('sub') or not user.get('Enabled',True): raise ValueError('Cognito identity mismatch')
            if user.get('UserStatus')!='FORCE_CHANGE_PASSWORD': raise ValueError('Existing account is not a new invitation; no password reset performed')
            issuer=f'https://cognito-idp.{args.region}.amazonaws.com/{args.pool}'
            stage='platform-bound-invitation'
            with owner() as conn:
                conn.execute('INSERT INTO saas.platform_invitations(issuer,subject,email) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',(issuer,values['sub'],email))
            stage='cognito-invitation-delivery'
            cognito.admin_create_user(UserPoolId=args.pool,Username=email,MessageAction='RESEND',DesiredDeliveryMediums=['EMAIL'])
            print(json.dumps({'action':args.action,'status':'INVITATION_REQUESTED','email':email,'emailVerificationRequired':True,'role':'platform_admin','clinicalPermissions':False}))
        else:
            stage='runtime-verification'
            value=json.loads(sm.get_secret_value(SecretId=args.runtime_secret)['SecretString'])
            if value['username']!=args.runtime_role or value['host']!=args.host or value['dbname']!=args.database:
                raise ValueError('Runtime secret target mismatch')
            with psycopg.connect(host=value['host'],port=value['port'],dbname=value['dbname'],user=value['username'],password=value['password'],connect_timeout=8,sslmode='require') as conn:
                conn.execute('SET TRANSACTION READ ONLY')
                check_runtime_role(conn)
                rows=migration_status(conn)
                if any(row['status']!='APPLIED' for row in rows): raise ValueError('Pending migrations')
                if conn.execute('SELECT count(*) FROM saas.report_controls').fetchone()[0]: raise ValueError('Unscoped report access')
                print(json.dumps({'action':args.action,'migrations':len(rows),'nonOwnerRuntime':True,'unscopedReportsVisible':0}))
        return 0
    except Exception as exc:
        # No SQL/provider details: those can contain secrets or patient records.
        code=getattr(exc,'response',{}).get('Error',{}).get('Code',type(exc).__name__)
        print(json.dumps({'action':args.action,'failedStage':stage,'errorCode':code,'notice':'Check the completed stages before retrying; external operations are not one transaction.'}),file=sys.stderr)
        return 1


if __name__=='__main__': raise SystemExit(main())
