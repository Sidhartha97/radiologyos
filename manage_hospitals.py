"""Operator-only provisioning. Never exposed as a public registration endpoint."""
import argparse
import json
import re


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--subject',help='Verified Cognito user sub (not email)')
    parser.add_argument('--tenant',required=True)
    parser.add_argument('--role',choices=('admin','radiologist'),default='radiologist')
    parser.add_argument('--disable',action='store_true')
    parser.add_argument('--import-legacy',action='store_true',help='Explicitly assign old global app data to this hospital')
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',args.tenant): parser.error('Invalid hospital ID')
    if not args.subject and not args.import_legacy: parser.error('Provide --subject or --import-legacy')
    import server
    from security import cognito_config
    from tenant_store import initialize, empty_state
    from psycopg.types.json import Jsonb
    initialize(server.pg_connect)
    with server.pg_connect() as conn:
        if args.import_legacy:
            if conn.execute('SELECT 1 FROM hospital_state WHERE tenant_id=%s',(args.tenant,)).fetchone():
                parser.error('Hospital state already exists. Import refused to prevent overwriting data.')
            value=server.load_postgres() if server.POSTGRES_ENABLED else json.loads(server.DATA.read_text())
            for key in ('patients','reports','templates','uploads','aiAudit','reportVersions'):
                for item in value.get(key,[]):
                    if item.get('tenantId') not in (None,args.tenant):
                        parser.error('Legacy data contains another hospital. Resolve ownership before importing.')
                    item['tenantId']=args.tenant
            conn.execute('INSERT INTO hospital_state(tenant_id,payload) VALUES(%s,%s)',(args.tenant,Jsonb(value)))
        if args.subject:
            conn.execute('''INSERT INTO hospital_memberships(issuer,subject,tenant_id,role,enabled)
                VALUES(%s,%s,%s,%s,%s) ON CONFLICT(issuer,subject) DO UPDATE
                SET tenant_id=EXCLUDED.tenant_id,role=EXCLUDED.role,enabled=EXCLUDED.enabled''',
                (cognito_config()['issuer'],args.subject,args.tenant,args.role,not args.disable))
    print('Provisioning complete. No passwords or tokens were printed.')


if __name__=='__main__': main()
