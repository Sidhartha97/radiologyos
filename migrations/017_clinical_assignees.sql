-- Minimal clinical directory, not an email/user-profile or cross-hospital API.
CREATE FUNCTION saas.clinical_assignees(p_tenant text)
 RETURNS TABLE(user_id uuid,employee_code text,roles text[]) LANGUAGE sql STABLE
 SECURITY DEFINER SET search_path=pg_catalog,saas AS $$
 SELECT m.user_id,m.employee_code,m.roles FROM saas.tenant_memberships m
 WHERE m.tenant_id=p_tenant AND m.enabled AND m.roles && ARRAY['radiologist','transcriptionist']::text[]
 AND p_tenant=current_setting('radiology.tenant_id',true)
 AND EXISTS(SELECT 1 FROM saas.tenant_memberships a JOIN saas.users u USING(user_id)
   WHERE a.tenant_id=p_tenant AND a.user_id::text=current_setting('radiology.user_id',true)
   AND a.enabled AND u.enabled AND 'radiologist'=ANY(a.roles))
 ORDER BY m.employee_code NULLS FIRST,m.user_id;
$$;
REVOKE ALL ON FUNCTION saas.clinical_assignees(text) FROM PUBLIC;
