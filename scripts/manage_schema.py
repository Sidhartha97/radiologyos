"""Explicit SaaS schema tool. Never reads application .env, server.py or AWS secrets."""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from saas.schema import apply, backfill, grant_runtime, inspect_legacy, legacy_rows, migration_status


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('status','preflight','apply','backfill','grant-runtime'))
    parser.add_argument('--runtime-role',help='Existing dedicated non-owner database login')
    parser.add_argument('--confirm-database',help='Required for writes: exact database name')
    parser.add_argument('--allow-remote',action='store_true',help='Explicit operator approval for a non-loopback target')
    args=parser.parse_args(argv)
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    dsn=os.environ.get('SAAS_MIGRATION_DATABASE_URL','')
    if not dsn:
        parser.error('Set SAAS_MIGRATION_DATABASE_URL explicitly; application DATABASE_URL is deliberately not used.')
    target=conninfo_to_dict(dsn)
    if target.get('service') or target.get('hostaddr'):
        parser.error('Use an explicit host, database and user, without service or hostaddr overrides.')
    host=target.get('host','')
    if not host or ',' in host or not target.get('dbname') or not target.get('user'):
        parser.error('Connection must specify one host, database and user.')
    remote=host not in ('localhost','127.0.0.1','::1')
    if remote and not args.allow_remote:
        parser.error('Remote database refused. Obtain approval and explicitly pass --allow-remote.')
    writing=args.action in ('apply','backfill','grant-runtime')
    if writing and args.confirm_database!=target['dbname']:
        parser.error('Writes require --confirm-database matching the connection database.')
    try:
        with psycopg.connect(dsn,connect_timeout=5,sslmode='require' if remote else target.get('sslmode','prefer')) as conn:
            if not writing: conn.execute('SET TRANSACTION READ ONLY')
            actual=conn.execute('SELECT current_database()').fetchone()[0]
            if actual!=target['dbname']: raise ValueError('Connected database differs from requested database.')
            if args.action=='status': result=migration_status(conn)
            elif args.action=='preflight': result=inspect_legacy(*legacy_rows(conn))
            elif args.action=='apply': result=apply(conn)
            elif args.action=='backfill': result=backfill(conn)
            else:
                if not args.runtime_role: raise ValueError('Provide --runtime-role.')
                result=grant_runtime(conn,args.runtime_role)
        print(json.dumps({'action':args.action,'result':result},indent=2))
    except Exception as exc:
        # SQL errors can include row contents; do not print driver details or DSN.
        print(json.dumps({'error':str(exc) if isinstance(exc,ValueError) else type(exc).__name__,
                          'action':args.action,'committed':False}),file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':
    raise SystemExit(main())
