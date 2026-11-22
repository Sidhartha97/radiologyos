-- Invited employees need their code mapping for first sign-in, before acceptance.
CREATE OR REPLACE FUNCTION saas.employee_login(p_issuer text,p_tenant text,p_code text)
 RETURNS text LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,saas AS $$
 SELECT cognito_login FROM saas.employee_directory
 WHERE issuer=p_issuer AND tenant_id=p_tenant AND employee_code=p_code
 UNION ALL
 SELECT email FROM saas.invitations
 WHERE cognito_issuer=p_issuer AND tenant_id=p_tenant AND employee_code=p_code AND status='INVITED'
   AND cognito_subject IS NOT NULL AND expires_at>clock_timestamp()
 LIMIT 1;
$$;
REVOKE ALL ON FUNCTION saas.employee_login(text,text,text) FROM PUBLIC;
