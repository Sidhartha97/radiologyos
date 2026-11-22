-- Transcriptionist AI never reads or clears the patient-wide conversation.
CREATE TABLE saas.report_conversations (
 tenant_id text NOT NULL,report_id text NOT NULL,payload jsonb NOT NULL,
 expires_at timestamptz NOT NULL,PRIMARY KEY(tenant_id,report_id),
 FOREIGN KEY(tenant_id,report_id) REFERENCES saas.report_controls(tenant_id,report_id)
);
CREATE INDEX report_chat_expiry ON saas.report_conversations(expires_at);
CREATE FUNCTION saas.report_chat_allowed(p_tenant text,p_report text)
 RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,saas AS $$
 SELECT p_tenant=current_setting('radiology.tenant_id',true) AND EXISTS(
  SELECT 1 FROM saas.tenant_memberships m JOIN saas.users u USING(user_id)
  JOIN saas.tenants t USING(tenant_id) WHERE m.tenant_id=p_tenant
   AND m.user_id::text=current_setting('radiology.user_id',true) AND m.enabled AND m.can_use_ai
   AND u.enabled AND t.status='ACTIVE' AND ('radiologist'=ANY(m.roles) OR
    ('transcriptionist'=ANY(m.roles) AND EXISTS(SELECT 1 FROM saas.report_assignments a
     WHERE a.tenant_id=p_tenant AND a.report_id=p_report AND a.user_id=m.user_id))));
$$;
REVOKE ALL ON FUNCTION saas.report_chat_allowed(text,text) FROM PUBLIC;
ALTER TABLE saas.report_conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas.report_conversations FORCE ROW LEVEL SECURITY;
CREATE POLICY assigned_report_ai ON saas.report_conversations
 USING(saas.report_chat_allowed(tenant_id,report_id))
 WITH CHECK(saas.report_chat_allowed(tenant_id,report_id));
