"""Tenant-keyed PostgreSQL state. No implicit adoption of legacy/global data."""
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import MutableMapping

STATE = ContextVar('radiology_state', default=None)


def empty_state():
    return {'patients':[], 'reports':[], 'templates':[], 'uploads':[], 'aiAudit':[],
            'reportVersions':[], 'modalities':['CT','MRI','USG','XRAY','BMD','MAMMOGRAM']}


def initialize(connect):
    with connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS hospital_memberships (
            issuer text NOT NULL, subject text NOT NULL, tenant_id text NOT NULL,
            role text NOT NULL CHECK(role IN ('admin','radiologist')), enabled boolean NOT NULL DEFAULT true,
            PRIMARY KEY(issuer,subject))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS hospital_state (
            tenant_id text PRIMARY KEY, payload jsonb NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now())''')
        conn.execute('''CREATE TABLE IF NOT EXISTS hospital_report_versions (
            tenant_id text NOT NULL, version_id text NOT NULL, payload jsonb NOT NULL,
            PRIMARY KEY(tenant_id,version_id))''')


@contextmanager
def transaction(connect, tenant, writable=False):
    from psycopg.types.json import Jsonb
    with connect() as conn:
        if writable:
            conn.execute('INSERT INTO hospital_state(tenant_id,payload) VALUES(%s,%s) ON CONFLICT DO NOTHING',
                         (tenant,Jsonb(empty_state())))
        row = conn.execute('SELECT payload FROM hospital_state WHERE tenant_id=%s'+(' FOR UPDATE' if writable else ''), (tenant,)).fetchone()
        token = STATE.set(row[0] if row else empty_state())
        try:
            yield
            if writable:
                conn.execute('UPDATE hospital_state SET payload=%s,updated_at=now() WHERE tenant_id=%s',
                             (Jsonb(STATE.get()),tenant))
        finally:
            STATE.reset(token)


class ScopedState(MutableMapping):
    def value(self):
        value = STATE.get()
        if value is None:
            raise RuntimeError('No hospital data context.')
        return value
    def __getitem__(self,key): return self.value()[key]
    def __setitem__(self,key,value): self.value()[key]=value
    def __delitem__(self,key): del self.value()[key]
    def __iter__(self): return iter(self.value())
    def __len__(self): return len(self.value())
