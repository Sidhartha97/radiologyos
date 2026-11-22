"""Real RLS/transaction tests. Opt in with SAAS_TEST_DATABASE_URL (loopback only).

Creates and removes its own uniquely named database/role. Never reads .env or AWS.
The named test connection must allow CREATE DATABASE and CREATE ROLE.
"""
import os
import secrets
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor

from saas.schema import apply,backfill,grant_runtime,migration_status
from saas.tenants import VerifiedIdentity,workspace_transaction,list_workspaces
from saas.permissions import SaaSError
from saas import locks
from saas import accounts,sessions,workflow,platform,onboarding,publication,memberships,wallets
import time
import ast
import json
import threading
import http.client
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock,patch

DSN=os.environ.get('SAAS_TEST_DATABASE_URL','')


@unittest.skipUnless(DSN,'Set SAAS_TEST_DATABASE_URL to run real local PostgreSQL isolation tests.')
class PostgresFoundationTests(unittest.TestCase):
    def setUp(self):
        from saas.runtime import close_pool
        close_pool()
        self.addCleanup(close_pool)

    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict,make_conninfo
        cls.psycopg,cls.sql=psycopg,sql
        config=conninfo_to_dict(DSN)
        if config.get('host') not in ('localhost','127.0.0.1','::1') or config.get('hostaddr') or config.get('service'):
            raise RuntimeError('Integration tests require an explicit loopback-only database URL.')
        if not config.get('dbname','').startswith('radiology_saas_test'):
            raise RuntimeError('Test connection database name must start with radiology_saas_test.')
        suffix=uuid.uuid4().hex[:12]
        cls.name='radiology_saas_it_'+suffix
        cls.role='saas_test_'+suffix
        cls.database_created=cls.role_created=False
        cls.admin_dsn=DSN
        cls.addClassCleanup(cls.cleanup)
        password=secrets.token_urlsafe(30)
        with psycopg.connect(DSN,autocommit=True,connect_timeout=5) as conn:
            conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.name)))
            cls.database_created=True
            conn.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS').format(sql.Identifier(cls.role),sql.Literal(password)))
            cls.role_created=True
        cls.owner_dsn=make_conninfo(DSN,dbname=cls.name)
        cls.runtime_dsn=make_conninfo(DSN,dbname=cls.name,user=cls.role,password=password)
        with cls.owner() as conn:
            apply(conn); grant_runtime(conn,cls.role)
            conn.execute("CREATE TABLE public.hospital_memberships(issuer text,subject text,tenant_id text,role text,enabled boolean,PRIMARY KEY(issuer,subject))")
            conn.execute('CREATE TABLE IF NOT EXISTS public.hospital_state(tenant_id text PRIMARY KEY,payload jsonb)')
            conn.execute(sql.SQL('GRANT SELECT,UPDATE ON public.hospital_state TO {}').format(sql.Identifier(cls.role)))
            for tenant,subject,kind in [('hospital-a','a','HOSPITAL'),('hospital-b','b','HOSPITAL'),('personal-a','p','INDIVIDUAL')]:
                user=uuid.uuid5(uuid.NAMESPACE_DNS,subject)
                conn.execute("INSERT INTO saas.tenants(tenant_id,name,kind,status) VALUES(%s,%s,%s,'ACTIVE')",(tenant,tenant,kind))
                conn.execute("INSERT INTO saas.users(user_id,issuer,subject) VALUES(%s,'issuer',%s)",(user,subject))
                conn.execute("INSERT INTO saas.tenant_memberships(tenant_id,user_id,roles) VALUES(%s,%s,ARRAY['radiologist'])",(tenant,user))
                conn.execute("INSERT INTO saas.report_controls(tenant_id,report_id,patient_id) VALUES(%s,'r1','p1')",(tenant,))

    @classmethod
    def cleanup(cls):
        # Only targets generated above, never the user's supplied database.
        if not cls.name.startswith('radiology_saas_it_'): raise RuntimeError('Unsafe cleanup target')
        if not cls.database_created and not cls.role_created: return
        with cls.psycopg.connect(cls.admin_dsn,autocommit=True,connect_timeout=5) as conn:
            if cls.database_created:
                conn.execute(cls.sql.SQL('DROP DATABASE {} WITH (FORCE)').format(cls.sql.Identifier(cls.name)))
            if cls.role_created:
                conn.execute(cls.sql.SQL('DROP ROLE {}').format(cls.sql.Identifier(cls.role)))

    @classmethod
    def owner(cls): return cls.psycopg.connect(cls.owner_dsn,connect_timeout=5)

    @classmethod
    def runtime(cls): return cls.psycopg.connect(cls.runtime_dsn,connect_timeout=5)

    def test_migrations_are_idempotent_and_checksum_verified(self):
        with self.owner() as conn:
            self.assertTrue(all(x['status']=='APPLIED' for x in apply(conn)))
            conn.execute("UPDATE public.saas_schema_migrations SET checksum='wrong'")
            with self.assertRaises(ValueError): migration_status(conn)
            conn.rollback()

    def test_no_tenant_context_returns_no_reports(self):
        with self.runtime() as conn:
            self.assertEqual(conn.execute('SELECT * FROM saas.report_controls').fetchall(),[])

    def test_pool_reuses_connection_and_discards_committed_session_context(self):
        from saas.runtime import connect,scoped
        with patch.dict(os.environ,{'SAAS_DATABASE_URL':self.runtime_dsn,'SAAS_DB_POOL_MAX_SIZE':'1'}):
            with scoped(VerifiedIdentity('issuer','a'),'hospital-a'),connect() as conn:
                first_pid=conn.info.backend_pid
                self.assertEqual(conn.execute('SELECT tenant_id FROM saas.report_controls').fetchall(),[('hospital-a',)])
                # Simulate a mistakenly persistent setting plus explicit commit.
                conn.execute("SELECT set_config('radiology.tenant_id','hospital-b',false)")
                conn.commit()
            with connect() as conn:
                self.assertEqual(conn.info.backend_pid,first_pid)
                self.assertEqual(conn.execute('SELECT * FROM saas.report_controls').fetchall(),[])
                self.assertIn(conn.execute("SELECT current_setting('radiology.user_id',true)").fetchone()[0],(None,''))
            with scoped(VerifiedIdentity('issuer','b'),'hospital-b'),connect() as conn:
                self.assertEqual(conn.info.backend_pid,first_pid)
                self.assertEqual(conn.execute('SELECT tenant_id FROM saas.report_controls').fetchall(),[('hospital-b',)])

    def test_pool_rollback_clears_failed_borrower_and_leaks_no_writes(self):
        from saas.runtime import connect,scoped
        with patch.dict(os.environ,{'SAAS_DATABASE_URL':self.runtime_dsn,'SAAS_DB_POOL_MAX_SIZE':'1'}):
            with self.assertRaises(RuntimeError),scoped(VerifiedIdentity('issuer','a'),'hospital-a'),connect() as conn:
                conn.execute("INSERT INTO saas.report_controls(tenant_id,report_id,patient_id) VALUES('hospital-a','pool-rollback','p1')")
                raise RuntimeError('Synthetic cancellation')
            with connect() as conn:
                self.assertEqual(conn.execute('SELECT * FROM saas.report_controls').fetchall(),[])
            with scoped(VerifiedIdentity('issuer','a'),'hospital-a'),connect() as conn:
                self.assertIsNone(conn.execute("SELECT 1 FROM saas.report_controls WHERE report_id='pool-rollback'").fetchone())

    def test_pool_parallel_borrowers_cannot_see_other_hospitals(self):
        from saas.runtime import connect,scoped,get_pool
        def read(subject):
            tenant='hospital-'+subject
            with scoped(VerifiedIdentity('issuer',subject),tenant),connect() as conn:
                rows=conn.execute('SELECT tenant_id FROM saas.report_controls').fetchall()
                return rows==[(tenant,)]
        with patch.dict(os.environ,{'SAAS_DATABASE_URL':self.runtime_dsn,'SAAS_DB_POOL_MAX_SIZE':'2'}):
            with ThreadPoolExecutor(max_workers=6) as workers:
                self.assertTrue(all(workers.map(read,['a','b']*6)))
            self.assertLessEqual(get_pool().get_stats()['pool_size'],2)

    def test_pooled_connections_do_not_cache_membership_permissions(self):
        from saas.runtime import connect,scoped
        with patch.dict(os.environ,{'SAAS_DATABASE_URL':self.runtime_dsn,'SAAS_DB_POOL_MAX_SIZE':'1'}):
            with scoped(VerifiedIdentity('issuer','a'),'hospital-a'),connect(): pass
            with self.owner() as conn: conn.execute("UPDATE saas.tenant_memberships SET enabled=false WHERE tenant_id='hospital-a'")
            try:
                with self.assertRaises(SaaSError),scoped(VerifiedIdentity('issuer','a'),'hospital-a'),connect(): pass
            finally:
                with self.owner() as conn: conn.execute("UPDATE saas.tenant_memberships SET enabled=true WHERE tenant_id='hospital-a'")

    def test_pool_replaces_closed_connections_and_rolls_over_configuration(self):
        from saas.runtime import connect,get_pool
        from psycopg.conninfo import make_conninfo
        with patch.dict(os.environ,{'SAAS_DATABASE_URL':self.runtime_dsn,'SAAS_DB_POOL_MAX_SIZE':'1'}):
            with connect() as conn:
                pid=conn.info.backend_pid
                conn.close()
            with connect() as conn: self.assertNotEqual(conn.info.backend_pid,pid)
            previous=get_pool()
            with patch.dict(os.environ,{'SAAS_DATABASE_URL':make_conninfo(self.runtime_dsn,application_name='new-runtime-config')}):
                with connect() as conn:
                    self.assertEqual(conn.execute('SHOW application_name').fetchone()[0],'new-runtime-config')
                self.assertTrue(previous.closed)

    def test_pool_rejects_owner_and_preserves_transaction_guard(self):
        from saas.runtime import connect
        with patch.dict(os.environ,{'SAAS_DATABASE_URL':self.owner_dsn,'SAAS_DB_POOL_MAX_SIZE':'1'}):
            with self.assertRaises(SaaSError) as caught:
                with connect(): pass
            self.assertEqual(caught.exception.code,'UnsafeDatabaseRole')

    def test_session_description_does_not_wait_on_another_session_row_lock(self):
        tenant,identity,_=self._workflow_fixture()
        token='lock-free-read-'+uuid.uuid4().hex
        sessions.create(self.runtime,identity,token,int(time.time())+300,int(time.time()))
        with self.owner() as conn:
            conn.execute('SELECT token_hash FROM saas.sessions WHERE token_hash=%s FOR UPDATE',(sessions.token_digest(token),))
            def bounded_connect():
                return self.psycopg.connect(self.runtime_dsn,options='-c statement_timeout=500',connect_timeout=5)
            self.assertIsNone(sessions.describe(bounded_connect,identity,token)['workspace'])

    def test_rds_non_superuser_function_owner_and_bound_platform_invitation(self):
        # RDS owners cannot BYPASSRLS. Simulate that instead of relying on the
        # local fixture's superuser; every ownership change rolls back.
        migration_role='migration_'+uuid.uuid4().hex[:12]
        subject='bootstrap-'+uuid.uuid4().hex
        with self.owner() as conn, conn.transaction(force_rollback=True):
            conn.execute(self.sql.SQL('CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS').format(self.sql.Identifier(migration_role)))
            conn.execute(self.sql.SQL('GRANT USAGE,CREATE ON SCHEMA saas,public TO {}').format(self.sql.Identifier(migration_role)))
            for schema,name in conn.execute("SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind='r' AND (n.nspname='saas' OR (n.nspname='public' AND c.relname IN ('hospital_state','hospital_report_versions','patient_conversations','hospital_memberships')))").fetchall():
                conn.execute(self.sql.SQL('ALTER TABLE {}.{} OWNER TO {}').format(self.sql.Identifier(schema),self.sql.Identifier(name),self.sql.Identifier(migration_role)))
            for name,args in conn.execute("SELECT proname,pg_get_function_identity_arguments(oid) FROM pg_proc WHERE pronamespace='saas'::regnamespace").fetchall():
                conn.execute(self.sql.SQL('ALTER FUNCTION saas.{}({}) OWNER TO {}').format(self.sql.Identifier(name),self.sql.SQL(args),self.sql.Identifier(migration_role)))
            conn.execute(self.sql.SQL('SET LOCAL ROLE {}').format(self.sql.Identifier(migration_role)))
            conn.execute('INSERT INTO saas.platform_invitations(issuer,subject,email) VALUES(%s,%s,%s)',('issuer',subject,'owner@example.test'))
            conn.execute(self.sql.SQL('SET LOCAL ROLE {}').format(self.sql.Identifier(self.role)))
            with self.assertRaises(self.psycopg.errors.InsufficientPrivilege), conn.transaction():
                conn.execute('SELECT * FROM saas.platform_invitations')
            self.assertEqual(conn.execute('SELECT saas.accept_platform_invitation(%s,%s,%s)',('issuer','wrong','owner@example.test')).fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT saas.accept_platform_invitation(%s,%s,%s)',('issuer',subject,'wrong@example.test')).fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT saas.accept_platform_invitation(%s,%s,%s)',('issuer',subject,'owner@example.test')).fetchone()[0],1)
            user_id=platform.require_platform(conn,VerifiedIdentity('issuer',subject))
            self.assertEqual(conn.execute('SELECT * FROM saas.tenant_memberships WHERE user_id=%s',(user_id,)).fetchall(),[])
            self.assertEqual(conn.execute('SELECT * FROM saas.report_controls').fetchall(),[])
            self.assertEqual(conn.execute("SELECT saas.accept_account_invitation('issuer','nobody','none@example.test')").fetchone()[0],0)
            conn.execute(self.sql.SQL('SET LOCAL ROLE {}').format(self.sql.Identifier(migration_role)))
            conn.execute('UPDATE saas.platform_roles SET enabled=false WHERE user_id=(SELECT user_id FROM saas.users WHERE subject=%s)',(subject,))
            conn.execute(self.sql.SQL('SET LOCAL ROLE {}').format(self.sql.Identifier(self.role)))
            self.assertEqual(conn.execute('SELECT saas.accept_platform_invitation(%s,%s,%s)',('issuer',subject,'owner@example.test')).fetchone()[0],0)
            with self.assertRaises(SaaSError): platform.require_platform(conn,VerifiedIdentity('issuer',subject))

    def test_authorized_context_sees_only_its_tenant(self):
        for tenant,subject in [('hospital-a','a'),('hospital-b','b'),('personal-a','p')]:
            with workspace_transaction(self.runtime,VerifiedIdentity('issuer',subject),tenant) as (conn,workspace):
                self.assertEqual(conn.execute('SELECT tenant_id FROM saas.report_controls').fetchall(),[(tenant,)])
                self.assertEqual(workspace.tenant_id,tenant)

    def test_forged_workspace_selection_rejected(self):
        with self.assertRaises(SaaSError):
            with workspace_transaction(self.runtime,VerifiedIdentity('issuer','a'),'hospital-b'):
                self.fail('Foreign workspace admitted')

    def test_rls_blocks_foreign_insert(self):
        with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
            with workspace_transaction(self.runtime,VerifiedIdentity('issuer','a'),'hospital-a') as (conn,_):
                conn.execute("INSERT INTO saas.report_controls(tenant_id,report_id,patient_id) VALUES('hospital-b','attack','p1')")

    def test_disabled_membership_rejected(self):
        with self.owner() as conn:
            conn.execute("UPDATE saas.tenant_memberships SET enabled=false WHERE tenant_id='hospital-a'")
        try:
            with self.assertRaises(SaaSError):
                with workspace_transaction(self.runtime,VerifiedIdentity('issuer','a'),'hospital-a'): pass
        finally:
            with self.owner() as conn: conn.execute("UPDATE saas.tenant_memberships SET enabled=true WHERE tenant_id='hospital-a'")

    def test_owner_credentials_rejected_by_runtime_boundary(self):
        with self.assertRaises(SaaSError) as caught:
            with workspace_transaction(self.owner,VerifiedIdentity('issuer','a'),'hospital-a'): pass
        self.assertEqual(caught.exception.code,'UnsafeDatabaseRole')

    def test_multiple_memberships_list_without_cross_tenant_data(self):
        with self.owner() as conn:
            conn.execute("INSERT INTO saas.tenant_memberships(tenant_id,user_id,roles) SELECT 'personal-a',user_id,ARRAY['radiologist'] FROM saas.users WHERE subject='a'")
        try:
            choices=list_workspaces(self.runtime,VerifiedIdentity('issuer','a'))
            self.assertEqual([x['tenantId'] for x in choices],['hospital-a','personal-a'])
            self.assertNotIn('body',str(choices))
        finally:
            with self.owner() as conn:
                conn.execute("DELETE FROM saas.tenant_memberships WHERE tenant_id='personal-a' AND user_id=(SELECT user_id FROM saas.users WHERE subject='a')")

    def test_transaction_rollback_discards_control_write(self):
        with self.assertRaises(RuntimeError):
            with workspace_transaction(self.runtime,VerifiedIdentity('issuer','a'),'hospital-a') as (conn,_):
                conn.execute("INSERT INTO saas.report_controls(tenant_id,report_id,patient_id) VALUES('hospital-a','rollback','p1')")
                raise RuntimeError('Cancel transaction')
        with self.owner() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM saas.report_controls WHERE report_id='rollback'").fetchone())

    def test_legacy_backfill_is_idempotent_and_does_not_grant_signing(self):
        from psycopg.types.json import Jsonb
        payload={'patients':[{'id':'p'}],'reports':[{'id':'r','patientId':'p','status':'Final','body':'synthetic'}]}
        with self.owner() as conn:
            conn.execute("INSERT INTO public.hospital_memberships VALUES('issuer','legacy','legacy','admin',true)")
            conn.execute('INSERT INTO public.hospital_state(tenant_id,payload) VALUES(%s,%s)',('legacy',Jsonb(payload)))
            backfill(conn); backfill(conn)
            self.assertEqual(conn.execute("SELECT roles,can_sign,can_use_ai FROM saas.tenant_memberships WHERE tenant_id='legacy'").fetchone(),(['hospital_admin'],False,False))
            self.assertEqual(conn.execute("SELECT legacy_final,signed_by FROM saas.report_controls WHERE tenant_id='legacy'").fetchone(),(True,None))
            self.assertEqual(conn.execute("SELECT payload FROM public.hospital_state WHERE tenant_id='legacy'").fetchone()[0],payload)
            conn.rollback()

    def test_lease_concurrency_and_expired_fencing(self):
        def attempt():
            try:
                with workspace_transaction(self.runtime,VerifiedIdentity('issuer','a'),'hospital-a') as (conn,workspace):
                    return locks.acquire(conn,workspace,'r1',uuid.uuid4())
            except SaaSError as exc: return exc.code
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda _:attempt(),range(2)))
            grants=[result for result in results if isinstance(result,dict)]
            self.assertEqual(len(grants),1); self.assertIn('ReportLocked',results)
            old=grants[0]
            with self.owner() as conn:
                conn.execute("UPDATE saas.report_locks SET expires_at=clock_timestamp()-interval '1 second' WHERE tenant_id='hospital-a'")
            new=attempt()
            self.assertIsInstance(new,dict); self.assertGreater(new['fence'],old['fence'])
            with self.assertRaises(SaaSError) as caught:
                with workspace_transaction(self.runtime,VerifiedIdentity('issuer','a'),'hospital-a') as (conn,workspace):
                    locks.check(conn,workspace,'r1',old,1)
            self.assertEqual(caught.exception.code,'LeaseLost')
            with workspace_transaction(self.runtime,VerifiedIdentity('issuer','a'),'hospital-a') as (conn,workspace):
                locks.renew(conn,workspace,'r1',new)
                locks.release(conn,workspace,'r1',new)
        finally:
            with self.owner() as conn: conn.execute("DELETE FROM saas.report_locks WHERE tenant_id='hospital-a'")

    def test_individual_version_check_without_lease(self):
        with workspace_transaction(self.runtime,VerifiedIdentity('issuer','p'),'personal-a') as (conn,workspace):
            self.assertEqual(locks.check(conn,workspace,'r1',None,1)['version'],1)
            with self.assertRaises(SaaSError) as caught: locks.check(conn,workspace,'r1',None,99)
            self.assertEqual(caught.exception.code,'StaleReport')

    def test_hospital_write_requires_credential(self):
        with workspace_transaction(self.runtime,VerifiedIdentity('issuer','a'),'hospital-a') as (conn,workspace):
            with self.assertRaises(SaaSError): locks.check(conn,workspace,'r1',None,1)

    def test_session_workspace_and_logout_cannot_be_replayed(self):
        identity=VerifiedIdentity('issuer','a'); token='synthetic-access-'+uuid.uuid4().hex
        first=sessions.create(self.runtime,identity,token,int(time.time())+600)
        self.assertIsNone(first['workspace'])
        sessions.select_workspace(self.runtime,identity,token,'hospital-a')
        self.assertEqual(sessions.describe(self.runtime,identity,token,'hospital-a')['workspace']['tenantId'],'hospital-a')
        with self.assertRaises(SaaSError): sessions.select_workspace(self.runtime,identity,token,'hospital-b')
        with self.assertRaises(SaaSError) as caught: sessions.describe(self.runtime,identity,token,'hospital-b')
        self.assertEqual(caught.exception.code,'WorkspaceChanged')
        with self.assertRaises(SaaSError): sessions.describe(self.runtime,VerifiedIdentity('issuer','b'),token)
        sessions.revoke(self.runtime,identity,token)
        with self.assertRaises(SaaSError): sessions.describe(self.runtime,identity,token)
        with self.assertRaises(SaaSError): sessions.create(self.runtime,identity,token,int(time.time())+600)

    def test_session_expiry_and_revocation_are_immutable_in_database(self):
        identity=VerifiedIdentity('issuer','b'); token='synthetic-access-'+uuid.uuid4().hex
        sessions.create(self.runtime,identity,token,int(time.time())+600)
        with self.assertRaises(self.psycopg.errors.RaiseException):
            with workspace_transaction(self.runtime,identity,'hospital-b') as (conn,_):
                conn.execute("UPDATE saas.sessions SET expires_at=expires_at+interval '1 hour' WHERE token_hash=%s",(sessions.token_digest(token),))
        sessions.revoke(self.runtime,identity,token)
        with self.assertRaises(self.psycopg.errors.RaiseException):
            with workspace_transaction(self.runtime,identity,'hospital-b') as (conn,_):
                conn.execute('UPDATE saas.sessions SET revoked_at=NULL WHERE token_hash=%s',(sessions.token_digest(token),))

    def test_platform_only_account_has_no_clinical_workspace(self):
        identity=VerifiedIdentity('issuer','platform-'+uuid.uuid4().hex)
        with self.owner() as conn:
            user=accounts.provision_identity(conn,identity,'platform-'+uuid.uuid4().hex+'@example.test',email_verified=True)
            accounts.grant_platform_admin(conn,user)
        token='synthetic-platform-'+uuid.uuid4().hex
        session=sessions.create(self.runtime,identity,token,int(time.time())+600)
        self.assertTrue(session['platformAdmin']); self.assertIsNone(session['workspace'])
        self.assertEqual(list_workspaces(self.runtime,identity),[])
        with self.assertRaises(SaaSError): sessions.select_workspace(self.runtime,identity,token,'hospital-a')
        with self.runtime() as conn:
            self.assertEqual(conn.execute('SELECT * FROM saas.report_controls').fetchall(),[])

    def test_employee_codes_are_hospital_scoped_and_reserved_after_deactivation(self):
        with self.owner() as conn:
            for tenant in ('employees-a','employees-b'):
                conn.execute("INSERT INTO saas.tenants(tenant_id,name,kind,status) VALUES(%s,%s,'HOSPITAL','ACTIVE')",(tenant,tenant))
                conn.execute('INSERT INTO saas.tenant_settings(tenant_id,max_users) VALUES(%s,3)',(tenant,))
            users=[]
            for n in range(3):
                users.append(accounts.provision_identity(conn,VerifiedIdentity('issuer','employee-'+uuid.uuid4().hex),
                    uuid.uuid4().hex+'@example.test',email_verified=True))
            accounts.add_employee(conn,'employees-a',users[0],'emp-001',['radiologist'])
            accounts.add_employee(conn,'employees-b',users[1],'EMP-001',['transcriptionist'])
            self.assertFalse(accounts.add_employee(conn,'employees-a',users[0],'EMP-001',['radiologist'])['created'])
            self.assertEqual(conn.execute("SELECT can_sign FROM saas.tenant_memberships WHERE tenant_id='employees-a'").fetchone()[0],False)
            self.assertIn('@example.test',accounts.employee_login_identity(conn,'employees-a','emp-001'))
            conn.execute("UPDATE saas.tenant_memberships SET enabled=false WHERE tenant_id='employees-a'")
            with self.assertRaises(SaaSError): accounts.employee_login_identity(conn,'employees-a','EMP-001')
            with self.assertRaises(SaaSError) as caught: accounts.add_employee(conn,'employees-a',users[2],'EMP-001',['radiologist'])
            self.assertEqual(caught.exception.code,'EmployeeCodeExists')
            conn.rollback()

    def test_employee_admission_seat_race(self):
        tenant='seat-'+uuid.uuid4().hex[:8]
        with self.owner() as conn:
            conn.execute("INSERT INTO saas.tenants(tenant_id,name,kind,status) VALUES(%s,%s,'HOSPITAL','ACTIVE')",(tenant,tenant))
            conn.execute('INSERT INTO saas.tenant_settings(tenant_id,max_users) VALUES(%s,1)',(tenant,))
            users=[accounts.provision_identity(conn,VerifiedIdentity('issuer',uuid.uuid4().hex),uuid.uuid4().hex+'@example.test',email_verified=True) for _ in range(2)]
        def admit(n):
            try:
                with self.owner() as conn: return accounts.add_employee(conn,tenant,users[n],'EMP-'+str(n),['radiologist'])
            except SaaSError as exc: return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(admit,range(2)))
        self.assertEqual(sum(isinstance(x,dict) for x in results),1)
        self.assertIn('SeatLimit',results)

    def _workflow_fixture(self,role='radiologist',signer=False):
        from psycopg.types.json import Jsonb
        tenant='workflow-'+uuid.uuid4().hex[:10]; subject=uuid.uuid4().hex
        user=uuid.uuid4()
        with self.owner() as conn:
            conn.execute("INSERT INTO saas.tenants(tenant_id,name,kind,status) VALUES(%s,%s,'HOSPITAL','ACTIVE')",(tenant,tenant))
            conn.execute('INSERT INTO saas.tenant_settings(tenant_id,max_users) VALUES(%s,3)',(tenant,))
            conn.execute("INSERT INTO saas.users(user_id,issuer,subject) VALUES(%s,'issuer',%s)",(user,subject))
            conn.execute('INSERT INTO saas.tenant_memberships(tenant_id,user_id,roles,can_sign) VALUES(%s,%s,%s,%s)',(tenant,user,[role],signer))
            conn.execute("INSERT INTO saas.report_controls(tenant_id,report_id,patient_id) VALUES(%s,'r','p')",(tenant,))
            conn.execute("INSERT INTO saas.report_assignments(tenant_id,report_id,user_id) VALUES(%s,'r',%s)",(tenant,user))
            conn.execute('INSERT INTO public.hospital_state(tenant_id,payload) VALUES(%s,%s)',(tenant,Jsonb({'patients':[{'id':'p','name':'Synthetic patient'}],
                'modalities':['CT','MRI','USG'],'templates':[],
                'reports':[{'id':'r','patientId':'p','modality':'CT','status':'Draft','body':'original synthetic'}]})))
        identity=VerifiedIdentity('issuer',subject)
        with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
            lease=locks.acquire(conn,workspace,'r',uuid.uuid4())
        return tenant,identity,lease

    def test_signing_commits_snapshot_outbox_and_releases_lease(self):
        tenant,identity,lease=self._workflow_fixture(signer=True)
        with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
            result=workflow.save(conn,workspace,'r','synthetic signed body',1,lease,target='FINALIZED',request_id='test-request')
            self.assertEqual(result['status'],'Final'); self.assertEqual(result['version'],2)
            self.assertEqual(result['signedBy'],workspace.user_id)
            self.assertEqual(conn.execute('SELECT count(*) FROM saas.signed_reports').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT state FROM saas.publication_jobs').fetchone()[0],'PENDING')
            self.assertIsNone(conn.execute('SELECT 1 FROM saas.report_locks').fetchone())
            audit=conn.execute('SELECT action,object_id,outcome FROM saas.audit_logs').fetchone()
            self.assertEqual(audit,('report.finalized','r','SUCCESS'))
        with self.assertRaises(SaaSError) as caught:
            with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
                workflow.save(conn,workspace,'r','overwrite',2,lease)
        self.assertEqual(caught.exception.code,'ReportReadOnly')
        with self.assertRaises(self.psycopg.errors.RaiseException):
            with self.owner() as conn: conn.execute('UPDATE saas.signed_reports SET snapshot=%s WHERE tenant_id=%s',('{}',tenant))

    def test_transcriptionist_cannot_sign_via_service(self):
        tenant,identity,lease=self._workflow_fixture(role='transcriptionist')
        with self.assertRaises(SaaSError):
            with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
                workflow.save(conn,workspace,'r','synthetic',1,lease,target='FINALIZED')
        with self.owner() as conn:
            self.assertEqual(conn.execute('SELECT version FROM saas.report_controls WHERE tenant_id=%s',(tenant,)).fetchone()[0],1)
            self.assertIsNone(conn.execute('SELECT 1 FROM saas.publication_jobs WHERE tenant_id=%s',(tenant,)).fetchone())

    def test_workflow_rollback_is_atomic(self):
        tenant,identity,lease=self._workflow_fixture(signer=True)
        with self.assertRaises(RuntimeError):
            with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
                workflow.save(conn,workspace,'r','signed then rolled back',1,lease,target='FINALIZED')
                raise RuntimeError('synthetic failure before commit')
        with self.owner() as conn:
            self.assertEqual(conn.execute('SELECT payload FROM public.hospital_state WHERE tenant_id=%s',(tenant,)).fetchone()[0]['reports'][0]['body'],'original synthetic')
            self.assertEqual(conn.execute('SELECT version FROM saas.report_controls WHERE tenant_id=%s',(tenant,)).fetchone()[0],1)
            for table in ('signed_reports','publication_jobs','report_events','audit_logs'):
                self.assertIsNone(conn.execute('SELECT 1 FROM saas.'+table+' WHERE tenant_id=%s',(tenant,)).fetchone())

    def test_workflow_requires_version_and_return_reason(self):
        tenant,identity,lease=self._workflow_fixture(signer=True)
        with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
            with self.assertRaises(SaaSError) as caught: workflow.save(conn,workspace,'r','synthetic',None,lease)
            self.assertEqual(caught.exception.code,'VersionRequired')
            with self.assertRaises(SaaSError) as caught: workflow.save(conn,workspace,'r','synthetic',1,lease,target='CANCELLED')
            self.assertEqual(caught.exception.code,'ReasonRequired')

    def test_email_registration_is_pending_idempotent_and_has_no_paid_or_signing_grant(self):
        identity=VerifiedIdentity('issuer','registration-'+uuid.uuid4().hex)
        email=uuid.uuid4().hex+'@example.test'
        for kind in ('HOSPITAL','INDIVIDUAL'):
            with self.owner() as conn:
                first=onboarding.register(conn,identity,email,kind,'Synthetic registration',email_verified=True)
                second=onboarding.register(conn,identity,email,kind,'Ignored duplicate name',email_verified=True)
                self.assertEqual(first['tenantId'],second['tenantId']); self.assertFalse(second['created'])
                self.assertEqual(conn.execute('SELECT status FROM saas.tenants WHERE tenant_id=%s',(first['tenantId'],)).fetchone()[0],'PENDING')
                grants=conn.execute('SELECT can_sign,can_use_ai,employee_code FROM saas.tenant_memberships WHERE tenant_id=%s',(first['tenantId'],)).fetchone()
                self.assertEqual(grants,(False,False,None))
            with self.assertRaises(SaaSError):
                with workspace_transaction(self.runtime,identity,first['tenantId']): pass
        self.assertEqual(list_workspaces(self.runtime,identity),[])

    def test_platform_business_listing_does_not_enable_clinical_reads(self):
        identity=VerifiedIdentity('issuer','business-'+uuid.uuid4().hex)
        with self.owner() as conn:
            user=accounts.provision_identity(conn,identity,uuid.uuid4().hex+'@example.test',email_verified=True)
            accounts.grant_platform_admin(conn,user)
        page=platform.list_tenants(self.runtime,identity,limit=1)
        self.assertEqual(len(page['tenants']),1); self.assertIsNotNone(page['nextCursor'])
        self.assertEqual(set(page['tenants'][0]),{'tenantId','name','kind','status','createdAt','maxUsers','allowStaffCreation','version'})
        with self.runtime() as conn:
            platform.require_platform(conn,identity)
            self.assertEqual(conn.execute('SELECT * FROM saas.signed_reports').fetchall(),[])
            self.assertEqual(conn.execute('SELECT * FROM saas.report_controls').fetchall(),[])
        with self.assertRaises(SaaSError): platform.list_tenants(self.runtime,VerifiedIdentity('issuer','a'))

    def test_publication_claim_recovery_rejects_stale_worker(self):
        tenant,identity,lease=self._workflow_fixture(signer=True)
        with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
            workflow.save(conn,workspace,'r','synthetic',1,lease,target='FINALIZED')
        with workspace_transaction(self.runtime,identity,tenant) as (conn,_): old=publication.claim(conn,tenant)
        with workspace_transaction(self.runtime,identity,tenant) as (conn,_):
            self.assertEqual(publication.snapshot(conn,tenant,old)['body'],'synthetic')
        with workspace_transaction(self.runtime,identity,tenant) as (conn,_): self.assertIsNone(publication.claim(conn,tenant))
        with self.owner() as conn:
            conn.execute("UPDATE saas.publication_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE tenant_id=%s",(tenant,))
        with workspace_transaction(self.runtime,identity,tenant) as (conn,_): new=publication.claim(conn,tenant)
        self.assertEqual(new['attempts'],2); self.assertNotEqual(old['workerToken'],new['workerToken'])
        with self.assertRaises(SaaSError):
            with workspace_transaction(self.runtime,identity,tenant) as (conn,_): publication.finish(conn,tenant,old)
        with self.assertRaises(SaaSError):
            with workspace_transaction(self.runtime,identity,tenant) as (conn,_): publication.snapshot(conn,tenant,old)
        with workspace_transaction(self.runtime,identity,tenant) as (conn,_):
            self.assertEqual(publication.finish(conn,tenant,new)['state'],'COMPLETE')

    def test_transcriptionist_cannot_edit_after_submission_until_returned(self):
        tenant,identity,lease=self._workflow_fixture(role='transcriptionist')
        with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
            workflow.save(conn,workspace,'r','synthetic submission',1,lease,target='READY_FOR_REVIEW')
        with self.assertRaises(SaaSError) as caught:
            with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
                locks.acquire(conn,workspace,'r',uuid.uuid4())
        self.assertEqual(caught.exception.code,'ReportUnderReview')

    def test_publication_retry_is_bounded_and_logs_only_error_code(self):
        tenant,identity,lease=self._workflow_fixture(signer=True)
        with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
            workflow.save(conn,workspace,'r','synthetic retry report',1,lease,target='FINALIZED')
        for attempt in (1,2):
            with workspace_transaction(self.runtime,identity,tenant) as (conn,_):
                job=publication.claim(conn,tenant)
                with self.assertRaises(SaaSError):
                    publication.finish(conn,tenant,job,error_code='raw error containing report text or credentials')
                outcome=publication.finish(conn,tenant,job,error_code='AccessDenied',max_attempts=2)
                self.assertEqual(outcome['state'],'PENDING' if attempt==1 else 'FAILED')
            with self.owner() as conn:
                conn.execute("UPDATE saas.publication_jobs SET available_at=clock_timestamp()-interval '1 second' WHERE tenant_id=%s",(tenant,))
        with workspace_transaction(self.runtime,identity,tenant) as (conn,_):
            self.assertIsNone(publication.claim(conn,tenant))
            self.assertEqual(conn.execute('SELECT error_code FROM saas.publication_jobs').fetchone()[0],'AccessDenied')

    def _http(self,identity,prelogin=True,*,demo_library=None):
        """Exercise the real server handler, real SaaS gateway and real runtime DB.

        JWT verification is injected; signature tests use real signed JWTs in
        test_security. No server import, environment secret lookup or AWS calls.
        """
        import datetime,re,mimetypes
        from urllib.parse import urlparse,parse_qs
        from saas.api import API
        from security import IDENTITY,AccessError,audit,login_failure,require_admin
        from tenant_store import ScopedState
        from ingestion.scope import ModalityRequired,ModalityConflict
        token='synthetic-http-'+uuid.uuid4().hex
        verifier=Mock()
        verifier.verify_claims.return_value={'iss':identity.issuer,'sub':identity.subject,'exp':int(time.time())+600,'auth_time':int(time.time())}
        provider=Mock(); provider.initiate_auth.return_value={'AuthenticationResult':{'AccessToken':token}}
        with self.owner() as conn:
            stored=conn.execute('SELECT email FROM saas.users WHERE issuer=%s AND subject=%s',(identity.issuer,identity.subject)).fetchone()
        provider.get_user.return_value={'UserAttributes':[{'Name':'sub','Value':identity.subject},
            {'Name':'email','Value':(stored[0] if stored else None) or 'preview@example.test'},{'Name':'email_verified','Value':'true'}]}
        gateway=API(self.runtime,lambda name:provider,verifier,demo_library=demo_library)
        if prelogin: gateway.auth.login(token)
        source=Path(__file__).resolve().parents[1]/'server.py'
        tree=ast.parse(source.read_text())
        selected=ast.Module(body=[node for node in tree.body if isinstance(node,(ast.FunctionDef,ast.ClassDef))
            and node.name in ('App','send','body','save','now','validate_chat_patient','RequestRollback')],type_ignores=[])
        namespace={'json':json,'os':os,'uuid':uuid,'datetime':datetime,'re':re,'urlparse':urlparse,'parse_qs':parse_qs,
            'BaseHTTPRequestHandler':BaseHTTPRequestHandler,'ScopedState':ScopedState,'db':ScopedState(),
            'IDENTITY':IDENTITY,'AccessError':AccessError,'audit':audit,'login_failure':login_failure,'require_admin':require_admin,
            'ModalityRequired':ModalityRequired,'ModalityConflict':ModalityConflict,'SAAS_ENABLED':True,'SAAS_API':gateway,
            'ensure_security':lambda:None,'pg_connect':self.runtime,'aws_client':Mock(),
            'aws_settings':lambda:{'tenantId':IDENTITY.get()['tenantId']},'MAX_BYTES':10_000_000,
            'STATIC':source.parent/'frontend'/'dist','mimetypes':mimetypes}
        exec(compile(selected,str(source),'exec'),namespace)
        server=ThreadingHTTPServer(('127.0.0.1',0),namespace['App'])
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        def request(method,path,payload=None,workspace=None,headers=None,anonymous=False):
            client=http.client.HTTPConnection(*server.server_address,timeout=5)
            request_headers={'Content-Type':'application/json'}
            if not anonymous: request_headers['Authorization']='Bearer '+token
            if workspace: request_headers['X-Workspace-ID']=workspace
            request_headers.update(headers or {})
            client.request(method,path,json.dumps(payload) if payload is not None else None,request_headers)
            response=client.getresponse(); result=json.loads(response.read()); status=response.status
            cookies=response.getheader('Set-Cookie'); client.close()
            return status,result,cookies
        request.server_url=f'http://127.0.0.1:{server.server_address[1]}'
        return request,gateway,provider

    def test_http_workspace_scope_and_report_write_preconditions(self):
        tenant,identity,lease=self._workflow_fixture(signer=True)
        request,_,_=self._http(identity)
        self.assertEqual(request('GET','/api/data')[0],409)
        self.assertEqual(request('POST','/api/session/workspace',{'tenantId':tenant})[0],200)
        self.assertEqual(request('GET','/api/data',workspace='hospital-b')[0],409)
        status,result,_=request('GET','/api/data',workspace=tenant)
        self.assertEqual(status,200); self.assertEqual(result['reports'][0]['version'],1)
        self.assertEqual(request('PUT','/api/reports/r',{'body':'test'},workspace=tenant)[0],428)
        self.assertEqual(request('PUT','/api/reports/r',{'body':'test','version':1},workspace=tenant)[0],409)
        self.assertEqual(request('PUT','/api/reports/foreign',{'version':1,'lease':lease},workspace=tenant)[0],404)
        status,result,_=request('PUT','/api/reports/r',{'body':'HTTP updated','version':1,'lease':lease,'modality':'MRI'},workspace=tenant)
        self.assertEqual(status,200); self.assertEqual(result['version'],2); self.assertEqual(result['modality'],'MRI')
        self.assertEqual(request('PUT','/api/reports/r',{'body':'stale','version':1,'lease':lease},workspace=tenant)[0],412)

    def test_clinical_http_authenticates_once_and_keeps_workspace_header_guard(self):
        tenant,identity,_=self._workflow_fixture()
        request,gateway,_=self._http(identity)
        self.assertEqual(request('POST','/api/session/workspace',{'tenantId':tenant})[0],200)
        with patch.object(gateway.auth,'authenticate',wraps=gateway.auth.authenticate) as authenticate:
            self.assertEqual(request('GET','/api/data',workspace=tenant)[0],200)
            self.assertEqual(authenticate.call_count,1)
        self.assertEqual(request('GET','/api/data')[0],409)
        self.assertEqual(request('GET','/api/data',workspace='hospital-b')[0],409)

    def test_combined_dashboard_is_platform_only_and_business_only(self):
        identity=self._platform_identity()
        request,_,_=self._http(identity)
        status,value,_=request('GET','/api/platform/dashboard')
        self.assertEqual(status,200)
        self.assertEqual(value['overview'],platform.overview(self.runtime,identity))
        self.assertEqual(value['events'],platform.audit_events(self.runtime,identity)['events'])
        self.assertNotIn('patients',value); self.assertNotIn('reports',value)
        request,_,_=self._http(VerifiedIdentity('issuer','a'))
        self.assertEqual(request('GET','/api/platform/dashboard')[0],403)

    def test_http_platform_user_is_business_only(self):
        identity=VerifiedIdentity('issuer',uuid.uuid4().hex)
        with self.owner() as conn:
            user=accounts.provision_identity(conn,identity,uuid.uuid4().hex+'@example.test',email_verified=True)
            accounts.grant_platform_admin(conn,user)
        request,_,_=self._http(identity)
        status,result,_=request('GET','/api/platform/tenants')
        self.assertEqual(status,200); self.assertNotIn('body',str(result))
        for path in ('/api/data','/api/patient-chat/p','/api/library','/api/rag/debug?modality=CT'):
            self.assertEqual(request('GET',path)[0],409)
        self.assertEqual(request('POST','/api/session/workspace',{'tenantId':'hospital-a'})[0],403)

    def test_http_transcriptionist_cannot_use_legacy_save_to_sign(self):
        tenant,identity,lease=self._workflow_fixture(role='transcriptionist')
        request,_,_=self._http(identity)
        request('POST','/api/session/workspace',{'tenantId':tenant})
        status,_,_=request('PUT','/api/reports/r',{'body':'try sign','status':'Final','version':1,'lease':lease},workspace=tenant)
        self.assertEqual(status,403)
        self.assertEqual(request('GET','/api/patient-chat/p',workspace=tenant)[0],403)
        self.assertEqual(request('POST','/api/uploads',{},workspace=tenant)[0],409)

    def test_http_finalization_and_cookie_logout(self):
        tenant,identity,lease=self._workflow_fixture(signer=True)
        request,_,_=self._http(identity)
        request('POST','/api/session/workspace',{'tenantId':tenant})
        status,result,_=request('POST','/api/reports/r/finalize',{'body':'signed over HTTP','version':1,'lease':lease},workspace=tenant)
        self.assertEqual(status,200); self.assertEqual(result['workflowStatus'],'FINALIZED')
        self.assertEqual(request('PUT','/api/reports/r',{'body':'overwrite','version':2,'lease':lease},workspace=tenant)[0],409)
        status,_,cookie=request('POST','/api/auth/logout',{})
        self.assertEqual(status,200); self.assertIn('Max-Age=0',cookie)
        self.assertEqual(request('GET','/api/auth/me')[0],401)

    def test_http_origin_rejection_and_private_challenges(self):
        tenant,identity,_=self._workflow_fixture(signer=True)
        request,_,provider=self._http(identity)
        self.assertEqual(request('POST','/api/session/workspace',{'tenantId':tenant},headers={'Origin':'https://foreign.example'})[0],403)
        provider.initiate_auth.return_value={'ChallengeName':'NEW_PASSWORD_REQUIRED','Session':'private-cognito-session',
            'ChallengeParameters':{'USER_ID_FOR_SRP':'private-login@example.test'}}
        with patch('saas.api.cognito_config',return_value={'issuer':'issuer','client':'client'}):
            status,result,cookie=request('POST','/api/auth/login',{'username':'customer@example.test','password':'synthetic-password'},anonymous=True)
        self.assertEqual(status,200); self.assertIsNone(cookie)
        self.assertIn('challengeId',result); self.assertNotIn('private-',str(result))

    def test_employee_directory_is_not_readable_by_runtime(self):
        with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
            with self.runtime() as conn: conn.execute('SELECT * FROM saas.employee_directory')

    def test_http_amendment_preserves_original_signed_report(self):
        tenant,identity,lease=self._workflow_fixture(signer=True)
        request,_,_=self._http(identity)
        request('POST','/api/session/workspace',{'tenantId':tenant})
        self.assertEqual(request('POST','/api/reports/r/finalize',{'body':'original signed','version':1,'lease':lease},workspace=tenant)[0],200)
        status,draft,_=request('POST','/api/reports/r/amend',{'version':2,'reason':'Correct a synthetic typo'},workspace=tenant)
        self.assertEqual(status,200); self.assertNotEqual(draft['id'],'r'); self.assertEqual(draft['amendsReportId'],'r')
        status,held,_=request('POST',f"/api/reports/{draft['id']}/lock",{'editingSession':str(uuid.uuid4())},workspace=tenant)
        self.assertEqual(status,200)
        status,amended,_=request('POST',f"/api/reports/{draft['id']}/finalize",{'body':'corrected signed','version':1,'lease':held},workspace=tenant)
        self.assertEqual(status,200); self.assertEqual(amended['workflowStatus'],'AMENDED')
        with self.owner() as conn:
            originals=conn.execute('SELECT snapshot FROM saas.signed_reports WHERE tenant_id=%s AND report_id=%s',(tenant,'r')).fetchall()
            self.assertEqual(len(originals),1); self.assertEqual(originals[0][0]['body'],'original signed')
            self.assertEqual(conn.execute('SELECT version FROM saas.report_controls WHERE tenant_id=%s AND report_id=%s',(tenant,'r')).fetchone()[0],2)
            self.assertEqual(conn.execute('SELECT count(*) FROM saas.publication_jobs WHERE tenant_id=%s',(tenant,)).fetchone()[0],2)

    def test_runtime_connection_scopes_legacy_state_and_resets_between_requests(self):
        from saas.runtime import scoped,connect
        tenant,identity,_=self._workflow_fixture(signer=True)
        with patch.dict(os.environ,{'SAAS_DATABASE_URL':self.runtime_dsn}):
            with connect() as conn: self.assertEqual(conn.execute('SELECT * FROM public.hospital_state').fetchall(),[])
            with scoped(identity,tenant):
                with connect() as conn: self.assertEqual(conn.execute('SELECT tenant_id FROM public.hospital_state').fetchall(),[(tenant,)])
            with connect() as conn: self.assertEqual(conn.execute('SELECT * FROM public.hospital_state').fetchall(),[])

    def test_http_employee_login_uses_private_mapping_and_checks_hospital(self):
        tenant='staff-'+uuid.uuid4().hex[:8]; identity=VerifiedIdentity('issuer',uuid.uuid4().hex)
        email=uuid.uuid4().hex+'@example.test'
        with self.owner() as conn:
            conn.execute("INSERT INTO saas.tenants(tenant_id,name,kind,status) VALUES(%s,%s,'HOSPITAL','ACTIVE')",(tenant,tenant))
            conn.execute('INSERT INTO saas.tenant_settings(tenant_id,max_users) VALUES(%s,2)',(tenant,))
            user=accounts.provision_identity(conn,identity,email,email_verified=True)
            accounts.add_employee(conn,tenant,user,'EMP-007',['radiologist'])
        request,_,provider=self._http(identity)
        with patch('saas.api.cognito_config',return_value={'issuer':'issuer','client':'client'}):
            status,result,cookie=request('POST','/api/auth/login',{'loginType':'employee','hospitalId':tenant,'employeeCode':'emp-007','password':'synthetic-password'},anonymous=True)
            self.assertEqual(status,200); self.assertIn('HttpOnly',cookie)
            self.assertEqual(result['workspace']['tenantId'],tenant); self.assertNotIn(email,str(result))
            self.assertEqual(provider.initiate_auth.call_args.kwargs['AuthParameters']['USERNAME'],email)
            self.assertEqual(request('POST','/api/auth/login',{'loginType':'employee','hospitalId':'foreign-hospital','employeeCode':'EMP-007','password':'synthetic-password'},anonymous=True)[0],401)

    def test_platform_configuration_requires_platform_grant_and_current_version(self):
        identity=VerifiedIdentity('issuer',uuid.uuid4().hex)
        tenant='managed-'+uuid.uuid4().hex[:8]
        with self.owner() as conn:
            user=accounts.provision_identity(conn,identity,uuid.uuid4().hex+'@example.test',email_verified=True)
            accounts.grant_platform_admin(conn,user)
            conn.execute("INSERT INTO saas.tenants(tenant_id,name,kind) VALUES(%s,'Synthetic hospital','HOSPITAL')",(tenant,))
            conn.execute('INSERT INTO saas.tenant_settings(tenant_id) VALUES(%s)',(tenant,))
        request,_,_=self._http(identity)
        payload={'name':'Activated synthetic hospital','status':'ACTIVE','maxUsers':3,'allowStaffCreation':True,'version':1,'reason':'Local setup test'}
        status,result,_=request('PUT','/api/platform/tenants/'+tenant,payload)
        self.assertEqual(status,200); self.assertEqual(result['version'],2)
        self.assertEqual(request('PUT','/api/platform/tenants/'+tenant,payload)[0],412)
        with self.owner() as conn:
            self.assertEqual(conn.execute('SELECT reason FROM saas.audit_logs WHERE tenant_id=%s',(tenant,)).fetchone()[0],'Local setup test')
        ordinary,_,_=self._http(VerifiedIdentity('issuer','a'))
        self.assertEqual(ordinary('PUT','/api/platform/tenants/'+tenant,{**payload,'version':2})[0],403)

    def test_platform_cannot_reduce_seats_below_active_members(self):
        identity=VerifiedIdentity('issuer',uuid.uuid4().hex)
        with self.owner() as conn:
            user=accounts.provision_identity(conn,identity,uuid.uuid4().hex+'@example.test',email_verified=True)
            accounts.grant_platform_admin(conn,user)
            conn.execute("INSERT INTO saas.tenant_settings(tenant_id,max_users) VALUES('hospital-a',3) ON CONFLICT DO NOTHING")
            conn.execute("INSERT INTO saas.tenant_memberships(tenant_id,user_id,roles) SELECT 'hospital-a',user_id,ARRAY['radiologist'] FROM saas.users WHERE subject='b'")
        try:
            with self.assertRaises(SaaSError) as caught:
                platform.update_tenant(self.runtime,identity,'hospital-a',{'name':'Hospital A','status':'ACTIVE','maxUsers':1,'allowStaffCreation':False,'version':1,'reason':'Test downgrade'},'test')
            self.assertEqual(caught.exception.code,'SeatLimit')
        finally:
            with self.owner() as conn:
                conn.execute("DELETE FROM saas.tenant_memberships WHERE tenant_id='hospital-a' AND user_id=(SELECT user_id FROM saas.users WHERE subject='b')")

    def _admin_fixture(self,seats=3,allowed=True):
        tenant='admin-'+uuid.uuid4().hex[:8]; identity=VerifiedIdentity('issuer',uuid.uuid4().hex)
        with self.owner() as conn:
            user=accounts.provision_identity(conn,identity,uuid.uuid4().hex+'@example.test',email_verified=True)
            conn.execute("INSERT INTO saas.tenants(tenant_id,name,kind,status) VALUES(%s,'Synthetic hospital','HOSPITAL','ACTIVE')",(tenant,))
            conn.execute('INSERT INTO saas.tenant_settings(tenant_id,max_users,allow_hospital_admin_create_users) VALUES(%s,%s,%s)',(tenant,seats,allowed))
            conn.execute("INSERT INTO saas.tenant_memberships(tenant_id,user_id,roles) VALUES(%s,%s,ARRAY['hospital_admin'])",(tenant,user))
        return tenant,identity,user

    def test_hospital_creation_flag_blocks_direct_invitation_api(self):
        tenant,identity,_=self._admin_fixture(allowed=False)
        request,_,_=self._http(identity)
        request('POST','/api/session/workspace',{'tenantId':tenant})
        self.assertEqual(request('GET','/api/tenant/members',workspace=tenant)[0],200)
        self.assertEqual(request('GET','/api/data',workspace=tenant)[0],403)
        self.assertEqual(request('POST','/api/tenant/invitations',{'email':'staff@example.test','employeeCode':'EMP-1','role':'radiologist'},workspace=tenant)[0],403)

    def test_parallel_invitations_reserve_only_available_seats(self):
        tenant,identity,_=self._admin_fixture(seats=2)
        def invite(n):
            try:
                with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
                    return memberships.invite(conn,workspace,{'email':f'staff{n}@example.test','employeeCode':f'EMP-{n}','role':'radiologist'},'local-test')
            except SaaSError as exc: return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(invite,range(2)))
        self.assertEqual(sum(isinstance(r,dict) for r in results),1); self.assertIn('SeatLimit',results)

    def test_invited_employee_verifies_email_before_membership_and_signin(self):
        tenant,admin,_=self._admin_fixture()
        email='new-'+uuid.uuid4().hex+'@example.test'; subject=uuid.uuid4().hex
        with workspace_transaction(self.runtime,admin,tenant) as (conn,workspace):
            invitation=memberships.invite(conn,workspace,{'email':email,'employeeCode':'EMP-NEW','role':'radiologist'},'local-test')
        cognito=Mock(); cognito.admin_create_user.return_value={'User':{'Attributes':[{'Name':'email','Value':email},{'Name':'sub','Value':subject}]}}
        self.assertEqual(memberships.deliver(self.runtime,admin,tenant,invitation['id'],cognito,'pool','issuer')['status'],'INVITED')
        identity=VerifiedIdentity('issuer',subject)
        request,_,provider=self._http(identity,prelogin=False)
        attributes=[{'Name':'sub','Value':subject},{'Name':'email','Value':email}]
        provider.get_user.return_value={'UserAttributes':attributes+[{'Name':'email_verified','Value':'false'}]}
        with patch('saas.api.cognito_config',return_value={'issuer':'issuer','client':'client'}):
            status,challenge,cookie=request('POST','/api/auth/login',{'loginType':'employee','hospitalId':tenant,'employeeCode':'EMP-NEW','password':'synthetic-password'},anonymous=True)
            self.assertEqual(status,200); self.assertEqual(challenge['challenge'],'VERIFY_EMAIL'); self.assertIsNone(cookie)
            with self.owner() as conn: self.assertIsNone(conn.execute('SELECT 1 FROM saas.users WHERE subject=%s',(subject,)).fetchone())
            provider.get_user.return_value={'UserAttributes':attributes+[{'Name':'email_verified','Value':'true'}]}
            status,session,cookie=request('POST','/api/auth/login',{**challenge,'answer':'123456'},anonymous=True)
        self.assertEqual(status,200); self.assertEqual(session['workspace']['tenantId'],tenant)
        self.assertFalse(session['workspace']['canSign']); self.assertFalse(session['workspace']['canUseAi'])
        self.assertIn('HttpOnly',cookie)
        with self.runtime() as conn:
            self.assertEqual(conn.execute('SELECT saas.accept_verified_invitations(%s,%s,%s)',('issuer',subject,email)).fetchone()[0],0)

    def test_hospital_admin_cannot_deactivate_last_administrator(self):
        tenant,identity,user=self._admin_fixture()
        with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
            with self.assertRaises(SaaSError) as caught: memberships.deactivate(conn,workspace,user,'test')
            self.assertEqual(caught.exception.code,'LastAdministrator')

    def test_invitation_delivery_failure_preserves_safe_code_and_reserved_seat(self):
        tenant,identity,_=self._admin_fixture(seats=2)
        with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
            invitation=memberships.invite(conn,workspace,{'email':'failed@example.test','employeeCode':'EMP-FAIL','role':'transcriptionist'},'test')
        class FailedDelivery(Exception):
            response={'Error':{'Code':'AccessDeniedException','Message':'private-credential-and-email'}}
        cognito=Mock(); cognito.admin_create_user.side_effect=FailedDelivery()
        result=memberships.deliver(self.runtime,identity,tenant,invitation['id'],cognito,'pool','issuer')
        self.assertEqual(result['errorCode'],'AccessDeniedException'); self.assertNotIn('private-',str(result))
        with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
            with self.assertRaises(SaaSError) as caught: memberships.invite(conn,workspace,{'email':'extra@example.test','employeeCode':'EMP-EXTRA','role':'radiologist'},'test')
            self.assertEqual(caught.exception.code,'SeatLimit')

    def _platform_identity(self):
        identity=VerifiedIdentity('issuer','platform-'+uuid.uuid4().hex)
        with self.owner() as conn:
            user=accounts.provision_identity(conn,identity,uuid.uuid4().hex+'@example.test',email_verified=True)
            accounts.grant_platform_admin(conn,user)
        return identity

    def test_platform_access_change_is_versioned_audited_and_revokes_lease(self):
        from saas import access
        tenant,identity,lease=self._workflow_fixture(signer=True)
        admin=self._platform_identity()
        row=access.roster(self.runtime,admin,tenant)['members'][0]
        payload={'roles':['radiologist'],'enabled':True,'canSign':False,'canUseAi':False,'version':row['version'],'reason':'Revoke signing after verification review'}
        result=access.update(self.runtime,admin,tenant,row['userId'],payload,'test-access')
        self.assertEqual(result['version'],row['version']+1)
        with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
            self.assertFalse(workspace.can_sign)
            self.assertEqual(conn.execute('SELECT count(*) FROM saas.report_locks').fetchone()[0],0)
            self.assertEqual(conn.execute("SELECT reason FROM saas.audit_logs WHERE action='member.access_changed'").fetchone()[0],payload['reason'])
        with self.assertRaises(SaaSError) as caught: access.update(self.runtime,admin,tenant,row['userId'],payload,'again')
        self.assertEqual(caught.exception.code,'StaleMembership')
        with self.assertRaises(SaaSError): access.roster(self.runtime,identity,tenant)
        with self.runtime() as conn:
            platform.require_platform(conn,admin)
            self.assertEqual(conn.execute('SELECT count(*) FROM public.hospital_state').fetchone()[0],0)

    def test_http_platform_member_controls_and_recent_authentication(self):
        tenant,_,_=self._admin_fixture(); admin=self._platform_identity()
        request,gateway,_=self._http(admin)
        status,result,_=request('GET',f'/api/platform/tenants/{tenant}/members')
        self.assertEqual(status,200)
        member=result['members'][0]
        payload={'roles':['hospital_admin'],'enabled':True,'canSign':False,'canUseAi':False,'version':member['version'],'reason':'Confirm development administrator access'}
        self.assertEqual(request('PUT',f"/api/platform/tenants/{tenant}/members/{member['userId']}",payload)[0],200)
        gateway.auth.verifier.verify_claims.return_value['auth_time']=int(time.time())-301
        payload['version']+=1
        status,result,_=request('PUT',f"/api/platform/tenants/{tenant}/members/{member['userId']}",payload)
        self.assertEqual(status,409); self.assertEqual(result['code'],'ReauthenticationRequired')

    def test_individual_member_cannot_be_changed_to_hospital_admin(self):
        from saas import access
        tenant,identity,_=self._workflow_fixture(signer=True)
        with self.owner() as conn:
            conn.execute("UPDATE saas.tenants SET kind='INDIVIDUAL' WHERE tenant_id=%s",(tenant,))
            conn.execute("UPDATE saas.tenant_memberships SET roles=ARRAY['individual_owner','radiologist'] WHERE tenant_id=%s",(tenant,))
        admin=self._platform_identity(); member=access.roster(self.runtime,admin,tenant)['members'][0]
        payload={'roles':['hospital_admin'],'enabled':True,'canSign':False,'canUseAi':False,'version':member['version'],'reason':'Invalid cross-workspace role test'}
        with self.assertRaises(SaaSError) as caught: access.update(self.runtime,admin,tenant,member['userId'],payload,'test')
        self.assertEqual(caught.exception.code,'InvalidRoles')
        with workspace_transaction(self.runtime,identity,tenant) as (_,workspace):
            self.assertEqual(workspace.kind,'INDIVIDUAL'); self.assertTrue(workspace.allows('reports.sign'))
            self.assertFalse(workspace.allows('users.invite'))

    def test_hospital_admin_cannot_grant_self_signing_in_sql_or_api(self):
        tenant,identity,user=self._admin_fixture()
        with self.assertRaises(self.psycopg.errors.RaiseException):
            with workspace_transaction(self.runtime,identity,tenant) as (conn,_):
                conn.execute("UPDATE saas.tenant_memberships SET roles=ARRAY['hospital_admin','radiologist'],can_sign=true WHERE tenant_id=%s AND user_id=%s",(tenant,user))
        request,_,_=self._http(identity)
        self.assertEqual(request('GET',f'/api/platform/tenants/{tenant}/members')[0],403)

    def test_last_admin_and_foreign_member_access_changes_rejected(self):
        from saas import access
        tenant,_,user=self._admin_fixture(); admin=self._platform_identity()
        payload={'roles':['radiologist'],'enabled':True,'canSign':False,'canUseAi':False,'version':1,'reason':'Synthetic test'}
        with self.assertRaises(SaaSError) as caught: access.update(self.runtime,admin,tenant,str(user),payload,'test')
        self.assertEqual(caught.exception.code,'LastAdministrator')
        with self.assertRaises(SaaSError) as caught: access.update(self.runtime,admin,'hospital-b',str(user),payload,'test')
        self.assertEqual(caught.exception.code,'NotFound')

    def test_hospital_reactivation_obeys_reserved_seats_and_invitation_revocation(self):
        tenant,identity,_=self._admin_fixture(seats=2)
        with self.owner() as conn:
            user=accounts.provision_identity(conn,VerifiedIdentity('issuer',uuid.uuid4().hex),uuid.uuid4().hex+'@example.test',email_verified=True)
            accounts.add_employee(conn,tenant,user,'EMP-INACTIVE',['radiologist'])
        with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
            memberships.deactivate(conn,workspace,str(user),'test')
            version=conn.execute('SELECT version FROM saas.tenant_memberships WHERE tenant_id=%s AND user_id=%s',(tenant,user)).fetchone()[0]
            invitation=memberships.invite(conn,workspace,{'email':'reserved@example.test','employeeCode':'EMP-RESERVED','role':'radiologist'},'test')
            with self.assertRaises(SaaSError) as caught: memberships.reactivate(conn,workspace,str(user),version,'test')
            self.assertEqual(caught.exception.code,'SeatLimit')
            memberships.revoke_invitation(conn,workspace,invitation['id'],'test')
            memberships.reactivate(conn,workspace,str(user),version,'test')
            self.assertTrue(conn.execute('SELECT enabled FROM saas.tenant_memberships WHERE tenant_id=%s AND user_id=%s',(tenant,user)).fetchone()[0])
        cognito=Mock()
        with self.assertRaises(SaaSError): memberships.deliver(self.runtime,identity,tenant,invitation['id'],cognito,'pool','issuer')
        cognito.admin_create_user.assert_not_called()

    def test_only_platform_creates_accounts_with_idempotent_pending_owner(self):
        from saas import provisioning
        admin=self._platform_identity()
        payload={'kind':'HOSPITAL','name':'New synthetic hospital','email':'owner@example.test','requestId':str(uuid.uuid4()),'reason':'Local provisioning test','maxUsers':3,'allowStaffCreation':True,'status':'ACTIVE'}
        first=provisioning.create(self.runtime,admin,payload,'test')
        again=provisioning.create(self.runtime,admin,payload,'test')
        self.assertEqual(first['tenantId'],again['tenantId']); self.assertFalse(again['created'])
        with self.owner() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM saas.tenant_memberships WHERE tenant_id=%s',(first['tenantId'],)).fetchone()[0],0)
        with self.assertRaises(SaaSError): provisioning.create(self.runtime,VerifiedIdentity('issuer','a'),{**payload,'requestId':str(uuid.uuid4())},'test')
        with self.assertRaises(SaaSError) as caught: provisioning.create(self.runtime,admin,{**payload,'email':'different@example.test'},'test')
        self.assertEqual(caught.exception.code,'IdempotencyConflict')

    def test_account_owner_acceptance_requires_bound_verified_subject_and_no_clinical_grant(self):
        from saas import provisioning
        admin=self._platform_identity(); email=uuid.uuid4().hex+'@example.test'; subject=uuid.uuid4().hex
        created=provisioning.create(self.runtime,admin,{'kind':'INDIVIDUAL','name':'Synthetic individual','email':email,'requestId':str(uuid.uuid4()),'reason':'Local test','status':'ACTIVE'},'test')
        cognito=Mock(); cognito.admin_create_user.return_value={'User':{'Attributes':[{'Name':'email','Value':email},{'Name':'sub','Value':subject}]}}
        self.assertEqual(provisioning.deliver(self.runtime,admin,created['invitationId'],cognito,'pool','issuer')['status'],'INVITED')
        with self.runtime() as conn:
            self.assertEqual(conn.execute('SELECT saas.accept_account_invitation(%s,%s,%s)',('issuer','foreign-subject',email)).fetchone()[0],0)
        identity=VerifiedIdentity('issuer',subject); request,_,provider=self._http(identity,prelogin=False)
        provider.get_user.return_value={'UserAttributes':[{'Name':'sub','Value':subject},{'Name':'email','Value':email},{'Name':'email_verified','Value':'true'}]}
        with patch('saas.api.cognito_config',return_value={'issuer':'issuer','client':'client'}):
            self.assertEqual(request('POST','/api/auth/login',{'username':email,'password':'dummy'},anonymous=True)[0],200)
        with workspace_transaction(self.runtime,identity,created['tenantId']) as (_,workspace):
            self.assertEqual(workspace.roles,frozenset(['individual_owner','radiologist']))
            self.assertFalse(workspace.can_sign); self.assertFalse(workspace.can_use_ai)
        with self.runtime() as conn:
            self.assertEqual(conn.execute('SELECT saas.accept_account_invitation(%s,%s,%s)',('issuer',subject,email)).fetchone()[0],0)

    def test_report_assignment_controls_transcriptionist_visibility(self):
        from saas import assignments
        tenant,radiologist,lease=self._workflow_fixture(signer=True)
        staff=VerifiedIdentity('issuer',uuid.uuid4().hex)
        with self.owner() as conn:
            user=accounts.provision_identity(conn,staff,uuid.uuid4().hex+'@example.test',email_verified=True)
            accounts.add_employee(conn,tenant,user,'TX-DEMO',['transcriptionist'])
        rad_request,_,_=self._http(radiologist); staff_request,_,_=self._http(staff)
        rad_request('POST','/api/session/workspace',{'tenantId':tenant}); staff_request('POST','/api/session/workspace',{'tenantId':tenant})
        self.assertEqual(staff_request('GET','/api/data',workspace=tenant)[1]['reports'],[])
        self.assertEqual(staff_request('GET','/api/clinical/members',workspace=tenant)[0],403)
        payload={'userId':user,'assigned':True,'version':1,'lease':lease}
        self.assertEqual(rad_request('POST','/api/reports/r/assign',payload,workspace=tenant)[0],200)
        self.assertEqual(len(staff_request('GET','/api/data',workspace=tenant)[1]['reports']),1)
        self.assertEqual(staff_request('POST','/api/reports/r/assign',{**payload,'version':2},workspace=tenant)[0],403)
        self.assertEqual(rad_request('POST','/api/reports/r/assign',{**payload,'assigned':False,'version':2},workspace=tenant)[0],200)
        self.assertEqual(staff_request('GET','/api/data',workspace=tenant)[1]['reports'],[])
        with workspace_transaction(self.runtime,radiologist,tenant) as (conn,workspace):
            self.assertEqual(conn.execute('SELECT saas.clinical_assignees(%s)',('hospital-b',)).fetchall(),[])

    def test_platform_overview_and_audit_exclude_clinical_events(self):
        from saas import provisioning
        admin=self._platform_identity()
        created=provisioning.create(self.runtime,admin,{'kind':'HOSPITAL','name':'Audit test','email':'audit-owner@example.test','requestId':str(uuid.uuid4()),'reason':'Local test'},'safe-request')
        with self.owner() as conn:
            conn.execute("INSERT INTO saas.audit_logs(event_id,tenant_id,action,outcome,reason) VALUES(%s,%s,'report.signed','SUCCESS','clinical-private-content')",(uuid.uuid4(),created['tenantId']))
        events=platform.audit_events(self.runtime,admin)['events']
        self.assertTrue(any(e['action']=='account.created' for e in events))
        self.assertNotIn('report.signed',str(events)); self.assertNotIn('clinical-private-content',str(events))
        self.assertGreater(platform.overview(self.runtime,admin)['activeMemberships'],0)
        with self.assertRaises(SaaSError): platform.audit_events(self.runtime,VerifiedIdentity('issuer','a'))

    def test_hospital_admin_cannot_insert_other_workspaces_through_shared_runtime_grants(self):
        tenant,identity,_=self._admin_fixture()
        with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
            with workspace_transaction(self.runtime,identity,tenant) as (conn,_):
                conn.execute("INSERT INTO saas.tenants(tenant_id,name,kind) VALUES('forged-hospital','forged','HOSPITAL')")

    def test_platform_can_grant_transcriptionist_ai_but_not_signing(self):
        from saas import access
        tenant,identity,_=self._workflow_fixture(role='transcriptionist')
        admin=self._platform_identity(); member=access.roster(self.runtime,admin,tenant)['members'][0]
        payload={'roles':['transcriptionist'],'enabled':True,'canSign':False,'canUseAi':True,'version':member['version'],'reason':'Allow drafting only'}
        access.update(self.runtime,admin,tenant,member['userId'],payload,'test')
        with workspace_transaction(self.runtime,identity,tenant) as (_,workspace):
            self.assertTrue(workspace.allows('ai.generate')); self.assertFalse(workspace.allows('reports.sign'))
        with self.assertRaises(SaaSError): access.update(self.runtime,admin,tenant,member['userId'],{**payload,'version':member['version']+1,'canSign':True},'test')

    def _report_chat_fixture(self):
        from patient_chat import fresh,utcnow
        from psycopg.types.json import Jsonb
        tenant,identity,_=self._workflow_fixture(role='transcriptionist')
        with self.owner() as conn:
            conn.execute('UPDATE saas.tenant_memberships SET can_use_ai=true WHERE tenant_id=%s',(tenant,))
            conn.execute("INSERT INTO saas.report_controls(tenant_id,report_id,patient_id) VALUES(%s,'private-r','p')",(tenant,))
            private=fresh(utcnow()); private['messages']=[{'id':'private','role':'user','text':'PRIVATE OTHER REPORT','createdAt':utcnow().isoformat()}]
            conn.execute('INSERT INTO public.patient_conversations(tenant_id,patient_id,payload,expires_at) VALUES(%s,%s,%s,%s)',(tenant,'p',Jsonb(private),private['expiresAt']))
            conn.execute('INSERT INTO saas.report_conversations(tenant_id,report_id,payload,expires_at) VALUES(%s,%s,%s,%s)',(tenant,'private-r',Jsonb(private),private['expiresAt']))
        return tenant,identity

    def test_assigned_report_chat_does_not_read_patient_or_other_report_history(self):
        from saas import report_chat
        tenant,identity=self._report_chat_fixture(); jobs=[]
        generate=Mock(return_value={'mode':'aws','text':'CT draft for clinician review'})
        payload={'prompt':'Generate CT brain report','currentReport':'current allowed report','reportId':'r','patientId':'p','modality':'CT','requestId':str(uuid.uuid4())}
        with patch.dict(os.environ,{'SAAS_AI_ENABLED':'true'}):
            report_chat.handle(self.runtime,identity,tenant,'r','POST',payload,generate,'test',dispatch=jobs.append)
            jobs.pop()()
        generation=generate.call_args.args[0]
        self.assertEqual(generation['_chatHistory'],[])
        self.assertNotIn('PRIVATE OTHER REPORT',str(generation))
        conversation=report_chat.handle(self.runtime,identity,tenant,'r','GET',{},generate,'test')
        self.assertEqual(len(conversation['messages']),2)
        self.assertIn('CT draft',conversation['messages'][1]['draft'])
        with self.assertRaises(SaaSError): report_chat.handle(self.runtime,identity,tenant,'private-r','GET',{},generate,'test')
        with workspace_transaction(self.runtime,identity,tenant) as (conn,_):
            self.assertEqual(conn.execute('SELECT report_id FROM saas.report_conversations').fetchall(),[('r',)])
        request,_,_=self._http(identity); request('POST','/api/session/workspace',{'tenantId':tenant})
        self.assertEqual(request('GET','/api/report-chat/r',workspace=tenant)[0],200)
        self.assertEqual(request('GET','/api/report-chat/private-r',workspace=tenant)[0],404)
        self.assertEqual(request('GET','/api/patient-chat/p',workspace=tenant)[0],403)
        self.assertEqual(request('POST','/api/rag/chat',payload,workspace=tenant)[0],403)

    def test_transcriptionist_ai_revocation_blocks_worker_and_chat_history(self):
        from saas import report_chat
        tenant,identity=self._report_chat_fixture(); jobs=[]; generate=Mock()
        payload={'prompt':'Generate CT report','modality':'CT','reportId':'r','patientId':'p','requestId':str(uuid.uuid4())}
        with patch.dict(os.environ,{'SAAS_AI_ENABLED':'true'}):
            report_chat.handle(self.runtime,identity,tenant,'r','POST',payload,generate,'test',dispatch=jobs.append)
            with self.owner() as conn: conn.execute('UPDATE saas.tenant_memberships SET can_use_ai=false WHERE tenant_id=%s',(tenant,))
            jobs.pop()()
        generate.assert_not_called()
        with self.assertRaises(SaaSError): report_chat.handle(self.runtime,identity,tenant,'r','GET',{},generate,'test')

    def test_assigned_chat_rejects_forged_patient_and_disabled_generation(self):
        from saas import report_chat
        tenant,identity=self._report_chat_fixture(); generate=Mock()
        with patch.dict(os.environ,{'SAAS_AI_ENABLED':'true'}):
            with self.assertRaises(SaaSError) as caught:
                report_chat.handle(self.runtime,identity,tenant,'r','POST',{'prompt':'CT report','modality':'CT','patientId':'other-patient'},generate,'test')
            self.assertEqual(caught.exception.code,'NotFound')
        with patch.dict(os.environ,{'SAAS_AI_ENABLED':'false'}):
            with self.assertRaises(SaaSError) as caught:
                report_chat.handle(self.runtime,identity,tenant,'r','POST',{'prompt':'CT report','modality':'CT'},generate,'test')
            self.assertEqual(caught.exception.code,'AIUnavailable')
        generate.assert_not_called()

    def test_same_tab_lease_retry_fences_old_credential_and_other_tab_stays_blocked(self):
        tenant,identity,lease=self._workflow_fixture()
        with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
            renewed=locks.acquire(conn,workspace,'r',lease['editingSession'])
            self.assertGreater(renewed['fence'],lease['fence'])
            with self.assertRaises(SaaSError): locks.check(conn,workspace,'r',lease,1)
            locks.check(conn,workspace,'r',renewed,1)
            with self.assertRaises(SaaSError): locks.acquire(conn,workspace,'r',uuid.uuid4())

    def test_transcriptionist_can_save_and_handoff_but_not_sign(self):
        from saas import assignments
        tenant,identity,lease=self._workflow_fixture(role='transcriptionist')
        with self.owner() as conn:
            reviewer=accounts.provision_identity(conn,VerifiedIdentity('issuer',uuid.uuid4().hex),'review-'+uuid.uuid4().hex+'@example.test',email_verified=True)
            accounts.add_employee(conn,tenant,reviewer,'REVIEWER',['radiologist'])
        request,_,_=self._http(identity); request('POST','/api/session/workspace',{'tenantId':tenant})
        self.assertEqual(request('GET','/api/clinical/reviewers',workspace=tenant)[0],200)
        status,value,_=request('POST','/api/reports/r/handoff',{'reviewerId':reviewer,'version':1,'lease':lease,'body':'saved transcription for review'},workspace=tenant)
        self.assertEqual(status,200); self.assertEqual(value['workflowStatus'],'READY_FOR_REVIEW')
        with self.owner() as conn:
            self.assertTrue(conn.execute('SELECT 1 FROM saas.report_assignments WHERE tenant_id=%s AND report_id=%s AND user_id=%s',(tenant,'r',reviewer)).fetchone())
            self.assertEqual(conn.execute('SELECT count(*) FROM saas.report_locks WHERE tenant_id=%s',(tenant,)).fetchone()[0],0)
            self.assertIsNone(conn.execute('SELECT signed_by FROM saas.report_controls WHERE tenant_id=%s',(tenant,)).fetchone()[0])
        self.assertEqual(request('GET','/api/reports/r',workspace=tenant)[1]['body'],'saved transcription for review')
        self.assertEqual(request('GET','/api/reports/foreign',workspace=tenant)[0],404)

    def test_token_tracking_is_actor_scoped_and_platform_totals_use_actual_counters(self):
        from saas.token_tracking import Tracker,dashboard,counters
        from saas.runtime import scoped
        tenant,identity=self._wallet_fixture(); tracker=Tracker(self.runtime)
        with scoped(identity,tenant):
            event=tracker.begin('synthetic-model')
            tracker.finish(event,counters({'usage':{'inputTokens':120,'outputTokens':30,'totalTokens':150}}))
            incomplete=tracker.begin('synthetic-model'); tracker.finish(incomplete,counters({}))
        admin=self._platform_identity(); result=dashboard(self.runtime,admin)
        self.assertEqual(result['totals']['inputTokens'],120); self.assertEqual(result['totals']['outputTokens'],30)
        self.assertEqual(result['totals']['totalTokens'],150); self.assertEqual(result['totals']['unresolvedCalls'],1)
        with self.assertRaises(SaaSError): dashboard(self.runtime,identity)
        with workspace_transaction(self.runtime,VerifiedIdentity('issuer','b'),'hospital-b') as (conn,_):
            self.assertEqual(conn.execute('SELECT count(*) FROM saas.model_token_events').fetchone()[0],0)

    def test_manual_payment_verification_never_grants_credits_and_duplicate_reference_rejected(self):
        from saas import manual_payments
        admin=self._platform_identity(); tenant,_,_=self._admin_fixture()
        reference='synthetic-'+uuid.uuid4().hex
        record=manual_payments.create(self.runtime,admin,{'tenantId':tenant,'amount':'1500.25','method':'UPI','reference':reference,'reason':'Recorded after customer communication'},'test')
        with self.assertRaises(SaaSError) as caught:
            manual_payments.create(self.runtime,admin,{'tenantId':tenant,'amount':'1500.25','method':'UPI','reference':reference,'reason':'Retry'},'test')
        self.assertEqual(caught.exception.code,'DuplicatePayment')
        payload={'state':'VERIFIED','version':1,'reason':'Independent bank receipt checked','receivedAmount':'1500.25'}
        with self.assertRaises(SaaSError): manual_payments.decide(self.runtime,admin,record['paymentId'],{**payload,'receivedAmount':'1.00'},'test')
        self.assertEqual(manual_payments.decide(self.runtime,admin,record['paymentId'],payload,'test')['state'],'VERIFIED')
        with self.assertRaises(SaaSError): manual_payments.decide(self.runtime,admin,record['paymentId'],payload,'test')
        with self.owner() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM saas.credit_lots WHERE tenant_id=%s',(tenant,)).fetchone()[0],0)
        with self.assertRaises(SaaSError): manual_payments.listing(self.runtime,VerifiedIdentity('issuer','a'))

    def test_application_signout_all_rejects_old_sessions_and_old_authentication(self):
        tenant,identity,_=self._admin_fixture()
        old='old-session-'+uuid.uuid4().hex; second='other-session-'+uuid.uuid4().hex; expiry=int(time.time())+600
        sessions.create(self.runtime,identity,old,expiry,int(time.time())-10)
        sessions.create(self.runtime,identity,second,expiry,int(time.time())-10)
        sessions.revoke_all(self.runtime,identity)
        with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
            with self.assertRaises(self.psycopg.errors.RaiseException),conn.transaction():
                conn.execute('UPDATE saas.sessions SET authenticated_at=clock_timestamp() WHERE user_id=%s',(workspace.user_id,))
            with self.assertRaises(self.psycopg.errors.RaiseException),conn.transaction():
                conn.execute("UPDATE saas.session_security SET valid_after=valid_after-interval '1 day'")
        for token in (old,second):
            with self.assertRaises(SaaSError): sessions.describe(self.runtime,identity,token)
        with self.assertRaises(SaaSError): sessions.create(self.runtime,identity,'replayed-'+uuid.uuid4().hex,expiry,int(time.time())-10)
        with self.assertRaises(SaaSError): sessions.create(self.runtime,identity,'missing-auth-'+uuid.uuid4().hex,expiry)
        # Synthetic timestamp after the cutoff, without sleeping or using Cognito.
        sessions.create(self.runtime,identity,'fresh-session-'+uuid.uuid4().hex,expiry,int(time.time())+2)

    def test_required_mfa_login_keeps_provider_session_private_and_is_single_use(self):
        tenant,identity,_=self._admin_fixture(); request,_,provider=self._http(identity,prelogin=False)
        authenticated=provider.initiate_auth.return_value
        provider.initiate_auth.return_value={'ChallengeName':'MFA_SETUP','Session':'private-first','ChallengeParameters':{'MFAS_CAN_SETUP':'["SOFTWARE_TOKEN_MFA"]'}}
        provider.associate_software_token.return_value={'Session':'private-associated','SecretCode':'ABCDEFGHIJKLMNOP'}
        provider.verify_software_token.return_value={'Status':'SUCCESS','Session':'private-verified'}
        provider.respond_to_auth_challenge.return_value=authenticated
        with patch('saas.api.cognito_config',return_value={'issuer':'issuer','client':'client'}):
            status,challenge,cookie=request('POST','/api/auth/login',{'username':'synthetic@example.test','password':'synthetic-password'},anonymous=True)
            self.assertEqual(status,200); self.assertIsNone(cookie); self.assertEqual(challenge['challenge'],'MFA_SETUP')
            self.assertNotIn('private-',str(challenge)); self.assertEqual(challenge['setupSecret'],'ABCDEFGHIJKLMNOP')
            status,result,cookie=request('POST','/api/auth/login',{'challengeId':challenge['challengeId'],'answer':'123456'},anonymous=True)
            self.assertEqual(status,200); self.assertIsNotNone(cookie); self.assertTrue(result['ok'])
            self.assertEqual(request('POST','/api/auth/login',{'challengeId':challenge['challengeId'],'answer':'123456'},anonymous=True)[0],401)

    def test_production_checks_are_platform_only_and_do_not_expose_secrets(self):
        request,_,_=self._http(self._platform_identity())
        status,value,_=request('GET','/api/platform/readiness')
        self.assertEqual(status,200); self.assertIn('configurationReady',value)
        self.assertNotIn('password',str(value).lower())
        request,_,_=self._http(VerifiedIdentity('issuer','a'))
        self.assertEqual(request('GET','/api/platform/readiness')[0],403)

    def _wallet_fixture(self,amount=100,expires_at=None):
        tenant,identity,_=self._workflow_fixture()
        with self.owner() as conn:
            conn.execute('UPDATE saas.tenant_memberships SET can_use_ai=true WHERE tenant_id=%s',(tenant,))
            wallets.grant(conn,tenant,'synthetic-grant',amount,expires_at)
        return tenant,identity

    def test_usage_receipt_and_wallet_settle_together_idempotently(self):
        from saas.usage import Rate,record
        tenant,identity=self._wallet_fixture()
        rate=Rate('synthetic-v1','synthetic-model',3,7)
        usage={'inputTokens':1000,'outputTokens':1000,'totalTokens':2000}
        with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
            reservation=wallets.reserve(conn,workspace,uuid.uuid4(),'synthetic-model',60)
            wallets.begin(conn,workspace,reservation['reservationId'])
            for _ in range(2):
                self.assertEqual(record(conn,workspace,reservation['reservationId'],rate,'synthetic-model',usage)['creditUnits'],10)
            self.assertEqual(wallets.balance(conn,tenant),{'availableUnits':90,'reservedUnits':0})
            self.assertEqual(conn.execute('SELECT count(*) FROM saas.usage_receipts').fetchone()[0],1)
            with self.assertRaises(SaaSError):
                record(conn,workspace,reservation['reservationId'],Rate('other-version','synthetic-model',3,7),'synthetic-model',usage)

    def test_usage_receipt_failure_rolls_back_wallet_debit(self):
        from saas.usage import Rate,record
        tenant,identity=self._wallet_fixture()
        with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
            reservation=wallets.reserve(conn,workspace,uuid.uuid4(),'synthetic-model',60)
            wallets.begin(conn,workspace,reservation['reservationId'])
        with self.assertRaises(RuntimeError):
            with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
                record(conn,workspace,reservation['reservationId'],Rate('v1','synthetic-model',3,7),
                       'synthetic-model',{'inputTokens':1000,'outputTokens':1000,'totalTokens':2000})
                raise RuntimeError('Simulate worker transaction failure')
        with workspace_transaction(self.runtime,identity,tenant) as (conn,_):
            self.assertEqual(wallets.balance(conn,tenant),{'availableUnits':40,'reservedUnits':60})
            self.assertEqual(conn.execute('SELECT count(*) FROM saas.usage_receipts').fetchone()[0],0)

    def test_unknown_usage_keeps_hold_and_receipts_are_tenant_scoped(self):
        from saas.usage import Rate,record
        tenant,identity=self._wallet_fixture()
        with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
            reservation=wallets.reserve(conn,workspace,uuid.uuid4(),'synthetic-model',60)
            wallets.begin(conn,workspace,reservation['reservationId'])
            with self.assertRaises(SaaSError):
                record(conn,workspace,reservation['reservationId'],Rate('v1','synthetic-model',3,7),'synthetic-model',{})
            wallets.unknown(conn,workspace,reservation['reservationId'])
            self.assertEqual(wallets.balance(conn,tenant),{'availableUnits':40,'reservedUnits':60})
            record(conn,workspace,reservation['reservationId'],Rate('v1','synthetic-model',3,7),
                   'synthetic-model',{'inputTokens':1000,'outputTokens':1000,'totalTokens':2000})
        with workspace_transaction(self.runtime,VerifiedIdentity('issuer','b'),'hospital-b') as (conn,_):
            self.assertEqual(conn.execute('SELECT count(*) FROM saas.usage_receipts').fetchone()[0],0)
        with self.assertRaises(self.psycopg.errors.RaiseException):
            with self.owner() as conn:
                conn.execute('UPDATE saas.usage_receipts SET credit_units=0 WHERE tenant_id=%s',(tenant,))

    def test_wallet_parallel_calls_cannot_overspend(self):
        tenant,identity=self._wallet_fixture()
        def hold(_):
            try:
                with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
                    return wallets.reserve(conn,workspace,uuid.uuid4(),'synthetic-model',15)
            except SaaSError as exc: return exc.code
        with ThreadPoolExecutor(max_workers=10) as pool: results=list(pool.map(hold,range(10)))
        self.assertEqual(sum(isinstance(result,dict) for result in results),6)
        self.assertEqual(results.count('InsufficientCredits'),4)
        with workspace_transaction(self.runtime,identity,tenant) as (conn,_):
            self.assertEqual(wallets.balance(conn,tenant),{'availableUnits':10,'reservedUnits':90})

    def test_wallet_settlement_and_invocation_are_idempotent(self):
        tenant,identity=self._wallet_fixture(); request_id=uuid.uuid4()
        with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
            reservation=wallets.reserve(conn,workspace,request_id,'synthetic-model',60)
            again=wallets.reserve(conn,workspace,request_id,'synthetic-model',60)
            self.assertEqual(again['reservationId'],reservation['reservationId']); self.assertFalse(again['created'])
            wallets.begin(conn,workspace,reservation['reservationId'])
            with self.assertRaises(SaaSError): wallets.begin(conn,workspace,reservation['reservationId'])
            wallets.complete(conn,workspace,reservation['reservationId'],40)
            wallets.complete(conn,workspace,reservation['reservationId'],40)
            self.assertEqual(wallets.balance(conn,tenant),{'availableUnits':60,'reservedUnits':0})
            self.assertEqual(conn.execute('SELECT sum(delta_units) FROM saas.wallet_ledger').fetchone()[0],60)
            with self.assertRaises(SaaSError): wallets.complete(conn,workspace,reservation['reservationId'],41)

    def test_wallet_unknown_outcome_keeps_hold_and_prevents_reinvocation(self):
        tenant,identity=self._wallet_fixture()
        with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
            reservation=wallets.reserve(conn,workspace,uuid.uuid4(),'synthetic-model',60)
            wallets.begin(conn,workspace,reservation['reservationId']); wallets.unknown(conn,workspace,reservation['reservationId'])
            with self.assertRaises(SaaSError): wallets.complete(conn,workspace,reservation['reservationId'])
            with self.assertRaises(SaaSError): wallets.begin(conn,workspace,reservation['reservationId'])
            self.assertEqual(wallets.balance(conn,tenant),{'availableUnits':40,'reservedUnits':60})

    def test_wallet_expiry_keeps_reserved_credit_until_settlement(self):
        from datetime import datetime,timezone,timedelta
        tenant,identity=self._wallet_fixture(expires_at=datetime.now(timezone.utc)+timedelta(seconds=2))
        with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
            reservation=wallets.reserve(conn,workspace,uuid.uuid4(),'synthetic-model',60)
            wallets.begin(conn,workspace,reservation['reservationId'])
        time.sleep(2.1)
        with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
            self.assertEqual(wallets.balance(conn,tenant),{'availableUnits':0,'reservedUnits':60})
            wallets.complete(conn,workspace,reservation['reservationId'],40)
            self.assertEqual(wallets.balance(conn,tenant),{'availableUnits':0,'reservedUnits':0})
            self.assertEqual(conn.execute('SELECT sum(delta_units) FROM saas.wallet_ledger').fetchone()[0],0)

    def test_customer_deletion_confirmation_fencing_and_signed_history_purge(self):
        from saas import deletion
        tenant,identity,lease=self._workflow_fixture(signer=True)
        other,_,_=self._workflow_fixture()
        admin=self._platform_identity()
        with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
            exclusive=workspace.user_id
            workflow.save(conn,workspace,'r','synthetic signed body',1,lease,target='FINALIZED',request_id='purge-test')
        # A member shared with another hospital, and a platform administrator,
        # must retain their identity and their other workspace records.
        with self.owner() as conn:
            shared=conn.execute('SELECT user_id FROM saas.tenant_memberships WHERE tenant_id=%s',(other,)).fetchone()[0]
            platform_id=conn.execute('SELECT user_id FROM saas.users WHERE issuer=%s AND subject=%s',(admin.issuer,admin.subject)).fetchone()[0]
            for user in (shared,platform_id):
                conn.execute("INSERT INTO saas.tenant_memberships(tenant_id,user_id,roles) VALUES(%s,%s,ARRAY['radiologist'])",(tenant,user))
        with self.assertRaises(SaaSError): deletion.call(self.runtime,'PREVIEW',tenant,identity=identity)
        preview=deletion.call(self.runtime,'PREVIEW',tenant,identity=admin)
        self.assertEqual(preview['counts']['reports'],1)
        self.assertEqual(preview['counts']['members'],3)
        for token,payload in [(str(uuid.uuid4()),{'confirm':True,'reason':'test'}),(preview['confirmationToken'],{'confirm':'true','reason':'test'})]:
            with self.assertRaises(SaaSError):
                deletion.call(self.runtime,'CONFIRM',tenant,preview['jobId'],token,payload,admin)
        result=deletion.call(self.runtime,'CONFIRM',tenant,preview['jobId'],preview['confirmationToken'],{'confirm':True,'reason':'Synthetic fixture cleanup'},admin)
        self.assertEqual(result['state'],'CONFIRMED')
        with self.assertRaises(SaaSError):
            with workspace_transaction(self.runtime,identity,tenant): self.fail('Suspended workspace admitted')
        with self.assertRaises(self.psycopg.errors.RaiseException):
            with self.owner() as conn: conn.execute("UPDATE saas.tenants SET status='ACTIVE' WHERE tenant_id=%s",(tenant,))
        job=deletion.call(self.runtime,'NEXT')
        self.assertEqual(job['jobId'],preview['jobId'])
        with self.assertRaises(self.psycopg.errors.RaiseException):
            deletion.call(self.runtime,'COMPLETE',job=job['jobId'],token=job['workerToken'])
        claims=deletion.call(self.runtime,'IDENTITIES',job=job['jobId'],token=job['workerToken'])
        self.assertEqual({x['subject'] for x in claims},{identity.subject})
        with self.assertRaises(self.psycopg.errors.RaiseException):
            with self.owner() as conn:
                conn.execute("INSERT INTO saas.tenant_memberships(tenant_id,user_id,roles) VALUES(%s,%s,ARRAY['radiologist'])",(other,exclusive))
        with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
            with self.runtime() as conn: conn.execute('SELECT * FROM saas.customer_deletions')
        with self.assertRaises(self.psycopg.errors.RaiseException):
            with self.owner() as conn:
                conn.execute("SELECT set_config('radiology.purge_tenant',%s,true)",(tenant,))
                conn.execute('DELETE FROM saas.signed_reports WHERE tenant_id=%s',(tenant,))
        # Only disposable local fixture time is advanced. No live account API.
        with self.owner() as conn:
            conn.execute("UPDATE saas.customer_deletions SET quarantine_until=clock_timestamp()-interval '1 second' WHERE job_id=%s",(job['jobId'],))
        deletion.call(self.runtime,'SAVE',job=job['jobId'],token=job['workerToken'],payload={'state':'RUNNING','stage':'DATABASE',
            'manifest':{key:True for key in ('storageDeleted','indexDeleted','dynamoDeleted','identitiesDeleted')}})
        self.assertEqual(deletion.call(self.runtime,'COMPLETE',job=job['jobId'],token=job['workerToken']),{'state':'COMPLETE'})
        with self.owner() as conn:
            for table in ('tenants','tenant_memberships','signed_reports','report_events','report_controls','publication_jobs'):
                self.assertEqual(conn.execute('SELECT count(*) FROM saas.'+table+' WHERE tenant_id=%s',(tenant,)).fetchone()[0],0)
            self.assertIsNone(conn.execute('SELECT 1 FROM saas.users WHERE user_id=%s',(exclusive,)).fetchone())
            self.assertEqual(conn.execute('SELECT count(*) FROM saas.users WHERE user_id=ANY(%s)',([shared,platform_id],)).fetchone()[0],2)
            self.assertEqual(conn.execute('SELECT count(*) FROM saas.report_controls WHERE tenant_id=%s',(other,)).fetchone()[0],1)

    def test_customer_purge_works_with_rds_non_superuser_owner(self):
        from psycopg.types.json import Jsonb
        tenant,identity,lease=self._workflow_fixture(signer=True)
        admin=self._platform_identity()
        with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
            workflow.save(conn,workspace,'r','synthetic signed body',1,lease,target='FINALIZED',request_id='non-super-purge')
        owner='purge_owner_'+uuid.uuid4().hex[:12]
        with self.owner() as conn,conn.transaction(force_rollback=True):
            conn.execute(self.sql.SQL('CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS').format(self.sql.Identifier(owner)))
            conn.execute(self.sql.SQL('GRANT USAGE,CREATE ON SCHEMA saas,public TO {}').format(self.sql.Identifier(owner)))
            for schema,name in conn.execute("SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind='r' AND (n.nspname='saas' OR (n.nspname='public' AND c.relname IN ('hospital_state','hospital_report_versions','patient_conversations','hospital_memberships')))").fetchall():
                conn.execute(self.sql.SQL('ALTER TABLE {}.{} OWNER TO {}').format(self.sql.Identifier(schema),self.sql.Identifier(name),self.sql.Identifier(owner)))
            for name,args in conn.execute("SELECT proname,pg_get_function_identity_arguments(oid) FROM pg_proc WHERE pronamespace='saas'::regnamespace").fetchall():
                conn.execute(self.sql.SQL('ALTER FUNCTION saas.{}({}) OWNER TO {}').format(self.sql.Identifier(name),self.sql.SQL(args),self.sql.Identifier(owner)))
            def role(value): conn.execute(self.sql.SQL('SET LOCAL ROLE {}').format(self.sql.Identifier(value)))
            def call(action,job=None,token=None,payload=None):
                return conn.execute('SELECT saas.customer_deletion(%s,%s,%s,%s,%s)',(action,tenant,job,token,Jsonb(payload or {}))).fetchone()[0]
            role(self.role); platform.require_platform(conn,admin)
            preview=call('PREVIEW')
            self.assertEqual(preview['counts']['reports'],1)
            call('CONFIRM',preview['jobId'],preview['confirmationToken'],{'confirm':True,'reason':'Local fixture only'})
            job=call('NEXT'); call('IDENTITIES',job['jobId'],job['workerToken'])
            role(owner)
            conn.execute("UPDATE saas.customer_deletions SET quarantine_until=clock_timestamp()-interval '1 second' WHERE job_id=%s",(job['jobId'],))
            role(self.role)
            call('SAVE',job['jobId'],job['workerToken'],{'state':'RUNNING','stage':'DATABASE',
                'manifest':{key:True for key in ('storageDeleted','indexDeleted','dynamoDeleted','identitiesDeleted')}})
            self.assertEqual(call('COMPLETE',job['jobId'],job['workerToken']),{'state':'COMPLETE'})
            role(owner)
            self.assertIsNone(conn.execute('SELECT 1 FROM saas.signed_reports WHERE tenant_id=%s',(tenant,)).fetchone())

    def test_wallet_foreign_reservation_and_forged_grant_rejected(self):
        tenant,identity=self._wallet_fixture()
        with workspace_transaction(self.runtime,identity,tenant) as (conn,workspace):
            reservation=wallets.reserve(conn,workspace,uuid.uuid4(),'synthetic-model',50)
        with workspace_transaction(self.runtime,VerifiedIdentity('issuer','b'),'hospital-b') as (conn,workspace):
            with self.assertRaises(SaaSError): wallets.begin(conn,workspace,reservation['reservationId'])
        with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
            with workspace_transaction(self.runtime,identity,tenant) as (conn,_): wallets.grant(conn,tenant,'forged-grant',1000)


if __name__=='__main__': unittest.main()
