"""Opt-in SaaS runtime connections and immutable request/worker workspace scope."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import os
import atexit
import hashlib
import json
import threading

from .permissions import SaaSError,Workspace
from .tenants import check_runtime_role,resolve_user,VerifiedIdentity


@dataclass(frozen=True)
class Scope:
    identity: VerifiedIdentity
    tenant_id: str


SCOPE=ContextVar('saas_scope',default=None)


@contextmanager
def scoped(identity,tenant_id):
    token=SCOPE.set(Scope(identity,tenant_id))
    try: yield
    finally: SCOPE.reset(token)


def authorize_connection(conn,scope):
    check_runtime_role(conn)
    user=resolve_user(conn,scope.identity)
    member=conn.execute('''SELECT roles,can_sign,can_use_ai FROM saas.tenant_memberships
        WHERE tenant_id=%s AND user_id=%s AND enabled''',(scope.tenant_id,user)).fetchone()
    if not member: raise SaaSError('Workspace membership is unavailable.')
    conn.execute("SELECT set_config('radiology.tenant_id',%s,true)",(scope.tenant_id,))
    tenant=conn.execute("SELECT kind FROM saas.tenants WHERE tenant_id=%s AND status='ACTIVE'",(scope.tenant_id,)).fetchone()
    if not tenant: raise SaaSError('Workspace is not active.')
    return Workspace(scope.tenant_id,user,tenant[0],frozenset(member[0]),member[1],member[2])


def connection_config():
    from psycopg.conninfo import conninfo_to_dict
    dsn=os.getenv('SAAS_DATABASE_URL','')
    if dsn: config=conninfo_to_dict(dsn)
    else:
        from .database_secret import credentials
        config=credentials()
    local=config.get('host') in ('127.0.0.1','localhost','::1') and not config.get('hostaddr') and not config.get('service')
    if not local and os.getenv('SAAS_DATABASE_SSLROOTCERT'):
        config.update(sslmode='verify-full',sslrootcert=os.environ['SAAS_DATABASE_SSLROOTCERT'])
    else: config['sslmode']=config.get('sslmode','prefer') if local else 'require'
    config['connect_timeout']=5
    return config


_pool_lock=threading.Lock()
_pool=None
_pool_key=None


def _configure(conn):
    # DISCARD ALL also removes server prepared statements. Keep driver and
    # server state consistent while prioritizing isolation over auto-prepare.
    conn.prepare_threshold=None


def _reset(conn):
    # Runs before reuse, including after explicit commit, rollback or SET
    # SESSION in a borrower. Clears role, tenant/identity GUCs, temp objects,
    # advisory locks and other session state. A reset failure discards the socket.
    conn.autocommit=True
    try: conn.execute('DISCARD ALL')
    finally: conn.autocommit=False


def close_pool():
    global _pool,_pool_key
    with _pool_lock:
        previous=_pool; _pool=None; _pool_key=None
    if previous is not None: previous.close()


atexit.register(close_pool)


def get_pool():
    from psycopg_pool import ConnectionPool
    global _pool,_pool_key
    config=connection_config()
    # Passwords/DSNs are never used as pool names or emitted in logs. A rotated
    # secret replaces the pool; borrowed old connections finish then close.
    try:
        maximum=int(os.getenv('SAAS_DB_POOL_MAX_SIZE','4'))
        timeout=float(os.getenv('SAAS_DB_POOL_TIMEOUT','10'))
    except ValueError:
        raise SaaSError('Invalid database pool limits.','DatabasePoolConfiguration',503) from None
    if not 1<=maximum<=16 or not 1<=timeout<=30:
        raise SaaSError('Invalid database pool limits.','DatabasePoolConfiguration',503)
    key=hashlib.sha256(json.dumps([config,maximum,timeout],sort_keys=True).encode()).digest()
    previous=None
    with _pool_lock:
        if _pool is None or _pool_key!=key:
            previous=_pool
            _pool=ConnectionPool(kwargs=config,min_size=1,max_size=maximum,
                max_waiting=32,timeout=timeout,max_lifetime=900,max_idle=60,
                configure=_configure,reset=_reset,check=ConnectionPool.check_connection,
                name='saas-runtime',open=False)
            _pool.open(); _pool_key=key
        current=_pool
    if previous is not None: previous.close()
    return current


@contextmanager
def connect():
    from psycopg_pool import PoolTimeout,TooManyRequests
    try:
        with get_pool().connection() as conn:
            # This guard is lease-local only, never a cached identity/membership.
            # The next borrower always rechecks the role and resolves identity.
            conn._saas_role_checked=False
            try:
                check_runtime_role(conn)
                conn._saas_role_checked=True
                if SCOPE.get(): authorize_connection(conn,SCOPE.get())
                yield conn
            finally: conn._saas_role_checked=False
    except (PoolTimeout,TooManyRequests):
        raise SaaSError('Database connections are busy or unavailable. Retry shortly.','DatabaseBusy',503) from None


def ready(connect_fn=connect):
    from .schema import files,digest
    with connect_fn() as conn:
        rows=dict(conn.execute('SELECT version,checksum FROM public.saas_schema_migrations').fetchall())
        if rows!={path.name:digest(path) for path in files()}:
            raise SaaSError('Apply the reviewed SaaS migrations before enabling this mode.','MigrationRequired',503)
