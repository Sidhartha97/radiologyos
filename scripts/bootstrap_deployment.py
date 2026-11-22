"""Explicit reviewed deployment bootstrap, never imported/run at app startup."""
import argparse
import json
from pathlib import Path
import secrets
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def bootstrap(config,master_secret,admin_email=None):
    import boto3,psycopg
    from psycopg import sql
    from saas.schema import apply,grant_runtime
    from saas.accounts import email_address,provision_identity,grant_platform_admin
    from saas.tenants import VerifiedIdentity,check_runtime_role
    sm=boto3.client('secretsmanager',region_name=config['AWS_REGION'])
    owner=json.loads(sm.get_secret_value(SecretId=master_secret)['SecretString'])
    arn=config['SAAS_DATABASE_SECRET_ARN']; runtime=None
    try: runtime=json.loads(sm.get_secret_value(SecretId=arn)['SecretString'])
    except sm.exceptions.ResourceNotFoundException: pass
    target={'host':config['POSTGRES_ENDPOINT'],'port':5432,'dbname':config['POSTGRES_DATABASE']}
    role='radiologyos_runtime'
    if runtime and any(runtime.get(key)!=value for key,value in {**target,'username':role}.items()):
        raise ValueError('Runtime secret target differs from current infrastructure; operator review required')
    created=False
    with psycopg.connect(**target,user=owner['username'],password=owner['password'],sslmode='require',connect_timeout=8) as conn:
        if conn.execute('SELECT current_database()').fetchone()[0]!=target['dbname']: raise ValueError('Wrong database')
        applied=apply(conn)
        exists=conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(role,)).fetchone()
        if not exists:
            if runtime: raise ValueError('Existing credential has no matching runtime role; operator review required')
            runtime={**target,'username':role,'password':secrets.token_urlsafe(48)}
            conn.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE').format(sql.Identifier(role),sql.Literal(runtime['password'])))
            created=True
        elif not runtime: raise ValueError('Runtime role exists without credentials; explicit recovery required')
        grant_runtime(conn,role)
    if created: sm.put_secret_value(SecretId=arn,SecretString=json.dumps(runtime))
    with psycopg.connect(**target,user=role,password=runtime['password'],sslmode='require',connect_timeout=8) as conn:
        check_runtime_role(conn)
    invitation='NOT_REQUESTED'
    if admin_email:
        email=email_address(admin_email); client=boto3.client('cognito-idp',region_name=config['AWS_REGION'])
        pool=config['COGNITO_USER_POOL_ID']; new=False
        try: user=client.admin_get_user(UserPoolId=pool,Username=email)
        except client.exceptions.UserNotFoundException:
            user=client.admin_create_user(UserPoolId=pool,Username=email,MessageAction='SUPPRESS',UserAttributes=[{'Name':'email','Value':email}])['User']; new=True
        values={a['Name']:a['Value'] for a in user.get('Attributes',user.get('UserAttributes',[]))}
        if values.get('email','').lower()!=email or not values.get('sub') or not user.get('Enabled',True): raise ValueError('Admin identity mismatch')
        identity=VerifiedIdentity(f'https://cognito-idp.{config["AWS_REGION"]}.amazonaws.com/{pool}',values['sub'])
        with psycopg.connect(**target,user=owner['username'],password=owner['password'],sslmode='require',connect_timeout=8) as conn:
            if values.get('email_verified')=='true':
                user_id=provision_identity(conn,identity,email,email_verified=True); grant_platform_admin(conn,user_id)
                invitation='EXISTING_VERIFIED_ADMIN'
            else:
                conn.execute('INSERT INTO saas.platform_invitations(issuer,subject,email) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',(identity.issuer,identity.subject,email))
                invitation='PENDING_EMAIL_VERIFICATION'
        if new:
            client.admin_create_user(UserPoolId=pool,Username=email,MessageAction='RESEND',DesiredDeliveryMediums=['EMAIL'])
            invitation='INVITATION_REQUESTED'
    return {'migrations':len(applied),'runtimeRole':role,'createdRuntime':created,'platformAdmin':invitation}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config-secret',required=True)
    parser.add_argument('--master-secret',required=True)
    parser.add_argument('--region',required=True)
    parser.add_argument('--admin-email')
    parser.add_argument('--approve-schema',action='store_true')
    args=parser.parse_args()
    if not args.approve_schema: parser.error('Reviewed migrations require --approve-schema')
    try:
        import boto3
        config=json.loads(boto3.client('secretsmanager',region_name=args.region).get_secret_value(SecretId=args.config_secret)['SecretString'])
        if config['AWS_REGION']!=args.region: raise ValueError('Region mismatch')
        print(json.dumps({'bootstrap':bootstrap(config,args.master_secret,args.admin_email)}))
        return 0
    except Exception as exc:
        print(json.dumps({'errorCode':getattr(exc,'response',{}).get('Error',{}).get('Code',type(exc).__name__),'stage':'bootstrap','notice':'No credential details logged. Review partial completion before retrying.'}),file=sys.stderr)
        return 1


if __name__=='__main__': raise SystemExit(main())
