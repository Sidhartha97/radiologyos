"""Fail-closed production preflight. This is not a security/compliance certificate."""
import os
from urllib.parse import urlparse
from .schema import migration_status
from .tenants import check_runtime_role
from .platform import require_platform


def configuration(environ=None):
    env=os.environ if environ is None else environ
    checks=[]
    def add(name,ok): checks.append({'name':name,'ok':bool(ok)})
    add('SaaS authorization enabled',env.get('SAAS_ENABLED','false').lower()=='true')
    add('Secure session cookies',env.get('APP_COOKIE_SECURE','true').lower()=='true')
    add('Dedicated runtime database URL configured',bool(env.get('SAAS_DATABASE_URL') or env.get('SAAS_DATABASE_SECRET_ARN')))
    origin=urlparse(env.get('APP_PUBLIC_ORIGIN',''))
    add('HTTPS public origin configured',origin.scheme=='https' and bool(origin.hostname) and not origin.username and not origin.password and origin.path in ('','/') and not origin.query and not origin.fragment)
    allowed={value.strip().lower() for value in env.get('APP_ALLOWED_HOSTS','').split(',') if value.strip()}
    add('Explicit public host allowlist',bool(allowed) and origin.netloc.lower() in allowed and '*' not in allowed)
    add('Supported usage-tracked provider',env.get('LLM_PROVIDER','bedrock').lower()=='bedrock')
    return checks


def database(connect):
    with connect() as conn:
        check_runtime_role(conn)
        pending=[row for row in migration_status(conn) if row['status']!='APPLIED']
        return {'name':'All reviewed migrations applied','ok':not pending,'pendingMigrations':len(pending)}


def inspect(connect,identity,demo=False):
    with connect() as conn: require_platform(conn,identity)
    checks=configuration()
    try: checks.append(database(connect))
    except Exception: checks.append({'name':'Database runtime role/schema readiness','ok':False})
    checks.append({'name':'Real identity provider (not demo)','ok':not demo})
    return {'checks':checks,'configurationReady':all(item['ok'] for item in checks),
        'notice':'Configuration checks only. Live AWS, TLS, backup/restore, durable workers, security and clinical review must pass separately before production.'}


def enforce_startup(connect):
    if os.getenv('APP_ENV','development').lower()!='production': return
    checks=configuration(); checks.append(database(connect))
    if not all(item['ok'] for item in checks):
        failed=', '.join(item['name'] for item in checks if not item['ok'])
        raise RuntimeError('Production startup refused: '+failed)
