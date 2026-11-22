"""Local synthetic UI preview. NOT an authentication server for deployment.

Binds only to loopback through the test fixture and creates its own disposable
database. No .env, RDS, Cognito or AWS calls. Stop with Ctrl+C for cleanup.
"""
import argparse
import os
import sys
import threading
import uuid
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role',choices=('platform','hospital_admin','radiologist','transcriptionist','individual'),default='platform')
    args=parser.parse_args()
    if not os.getenv('SAAS_TEST_DATABASE_URL'):
        parser.error('Set SAAS_TEST_DATABASE_URL explicitly to the isolated loopback test database.')
    from test_saas_postgres import PostgresFoundationTests
    from saas.accounts import provision_identity,grant_platform_admin
    from saas.tenants import VerifiedIdentity
    from unittest.mock import patch
    PostgresFoundationTests.setUpClass()
    case=PostgresFoundationTests()
    try:
        if args.role=='platform':
            identity=VerifiedIdentity('issuer','preview-platform')
            with case.owner() as conn:
                user=provision_identity(conn,identity,'preview@example.test',email_verified=True)
                grant_platform_admin(conn,user)
        elif args.role=='hospital_admin':
            tenant,identity,_=case._admin_fixture(seats=5)
        else:
            tenant,identity,held=case._workflow_fixture(role='transcriptionist' if args.role=='transcriptionist' else 'radiologist',signer=args.role!='transcriptionist')
            from saas import locks
            from saas.tenants import workspace_transaction
            with workspace_transaction(case.runtime,identity,tenant) as (conn,workspace): locks.release(conn,workspace,'r',held)
            if args.role=='individual':
                with case.owner() as conn:
                    conn.execute("UPDATE saas.tenants SET kind='INDIVIDUAL',name='Synthetic individual workspace' WHERE tenant_id=%s",(tenant,))
                    conn.execute('UPDATE saas.tenant_settings SET max_users=1 WHERE tenant_id=%s',(tenant,))
                    conn.execute("UPDATE saas.tenant_memberships SET roles=ARRAY['individual_owner','radiologist'] WHERE tenant_id=%s",(tenant,))
        request,_,provider=case._http(identity)
        provider.initiate_auth.side_effect=lambda **kw:{'AuthenticationResult':{'AccessToken':'preview-'+uuid.uuid4().hex}}
        with patch('saas.api.cognito_config',return_value={'issuer':'issuer','client':'client'}),patch.dict(os.environ,{'APP_COOKIE_SECURE':'false'}):
            print('SYNTHETIC LOCAL PREVIEW ONLY — authentication is mocked.',flush=True)
            print('URL: '+request.server_url,flush=True)
            print('Use preview@example.test and any nonempty test password. Never enter real credentials.',flush=True)
            print('Type stop and press Enter to close the preview and remove its test database.',flush=True)
            for line in sys.stdin:
                if line.strip().lower()=='stop': break
    except KeyboardInterrupt: pass
    finally:
        case.doCleanups(); PostgresFoundationTests.doClassCleanups()


if __name__=='__main__': main()
