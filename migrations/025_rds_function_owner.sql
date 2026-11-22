-- RDS migration owners are not PostgreSQL superusers. Explicitly permit the
-- actual table owner to run narrowly scoped SECURITY DEFINER functions and
-- operator bootstrap. Runtime logins may never own/inherit these tables (see
-- check_runtime_role/grant_runtime); tenant policies still apply to runtime SQL.
DO $$
DECLARE target record;
BEGIN
 FOR target IN SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE c.relkind='r' AND c.relrowsecurity AND
   (n.nspname='saas' OR (n.nspname='public' AND c.relname IN
    ('hospital_state','hospital_report_versions','patient_conversations')))
 LOOP
  EXECUTE format('CREATE POLICY trusted_migration_owner ON %I.%I FOR ALL
   USING (current_user::regrole=(SELECT relowner FROM pg_catalog.pg_class WHERE oid=%L::regclass))
   WITH CHECK (current_user::regrole=(SELECT relowner FROM pg_catalog.pg_class WHERE oid=%L::regclass))',
   target.nspname,target.relname,target.nspname||'.'||target.relname,target.nspname||'.'||target.relname);
 END LOOP;
END;
$$;
