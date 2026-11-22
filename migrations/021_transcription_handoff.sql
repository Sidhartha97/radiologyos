CREATE FUNCTION saas.review_radiologists(p_tenant text)
 RETURNS TABLE(user_id uuid,employee_code text) LANGUAGE sql STABLE
 SECURITY DEFINER SET search_path=pg_catalog,saas AS $$
 SELECT m.user_id,m.employee_code FROM saas.tenant_memberships m JOIN saas.users u USING(user_id)
 WHERE m.tenant_id=p_tenant AND m.enabled AND u.enabled AND 'radiologist'=ANY(m.roles)
 AND p_tenant=current_setting('radiology.tenant_id',true)
 AND EXISTS(SELECT 1 FROM saas.tenant_memberships a JOIN saas.users x USING(user_id)
  WHERE a.tenant_id=p_tenant AND a.user_id::text=current_setting('radiology.user_id',true)
  AND a.enabled AND x.enabled AND a.roles && ARRAY['radiologist','transcriptionist']::text[])
 ORDER BY m.employee_code NULLS FIRST,m.user_id;
$$;
REVOKE ALL ON FUNCTION saas.review_radiologists(text) FROM PUBLIC;
