-- Explicit opt-in runtime migration. The SaaS application never runs this at startup.
CREATE TABLE IF NOT EXISTS public.hospital_state (
 tenant_id text PRIMARY KEY, payload jsonb NOT NULL,
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.hospital_report_versions (
 tenant_id text NOT NULL,version_id text NOT NULL,payload jsonb NOT NULL,
 PRIMARY KEY(tenant_id,version_id)
);
CREATE TABLE IF NOT EXISTS public.patient_conversations (
 tenant_id text NOT NULL,patient_id text NOT NULL,payload jsonb NOT NULL,
 expires_at timestamptz NOT NULL,PRIMARY KEY(tenant_id,patient_id)
);
CREATE INDEX IF NOT EXISTS patient_conversations_expiry ON public.patient_conversations(expires_at);
ALTER TABLE public.hospital_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.hospital_state FORCE ROW LEVEL SECURITY;
CREATE POLICY saas_state_scope ON public.hospital_state
 USING(tenant_id=current_setting('radiology.tenant_id',true))
 WITH CHECK(tenant_id=current_setting('radiology.tenant_id',true));
ALTER TABLE public.hospital_report_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.hospital_report_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY saas_versions_scope ON public.hospital_report_versions
 USING(tenant_id=current_setting('radiology.tenant_id',true))
 WITH CHECK(tenant_id=current_setting('radiology.tenant_id',true));
ALTER TABLE public.patient_conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.patient_conversations FORCE ROW LEVEL SECURITY;
CREATE POLICY saas_chat_scope ON public.patient_conversations
 USING(tenant_id=current_setting('radiology.tenant_id',true))
 WITH CHECK(tenant_id=current_setting('radiology.tenant_id',true));

-- Authentication directory is private to the backend. Hospital staff still
-- authenticate against Cognito; employee codes never become passwords.
CREATE TABLE saas.employee_directory (
 tenant_id text NOT NULL,employee_code text NOT NULL,user_id uuid NOT NULL,
 issuer text NOT NULL,cognito_login text NOT NULL,
 PRIMARY KEY(tenant_id,employee_code),
 FOREIGN KEY(tenant_id,user_id) REFERENCES saas.tenant_memberships(tenant_id,user_id)
);
REVOKE ALL ON saas.employee_directory FROM PUBLIC;
INSERT INTO saas.employee_directory(tenant_id,employee_code,user_id,issuer,cognito_login)
 SELECT m.tenant_id,m.employee_code,m.user_id,u.issuer,u.email
 FROM saas.tenant_memberships m JOIN saas.users u USING(user_id)
 WHERE m.employee_code IS NOT NULL AND u.email IS NOT NULL AND u.email_verified;
CREATE FUNCTION saas.employee_login(p_issuer text,p_tenant text,p_code text)
 RETURNS text LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,saas AS $$
 SELECT cognito_login FROM saas.employee_directory
 WHERE issuer=p_issuer AND tenant_id=p_tenant AND employee_code=p_code;
$$;
REVOKE ALL ON FUNCTION saas.employee_login(text,text,text) FROM PUBLIC;
