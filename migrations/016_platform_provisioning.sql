-- Only a platform administrator may create a hospital/personal workspace.
CREATE POLICY platform_tenant_create ON saas.tenants FOR INSERT WITH CHECK(EXISTS(
 SELECT 1 FROM saas.platform_roles WHERE user_id::text=current_setting('radiology.user_id',true) AND enabled));
CREATE POLICY platform_settings_create ON saas.tenant_settings FOR INSERT WITH CHECK(EXISTS(
 SELECT 1 FROM saas.platform_roles WHERE user_id::text=current_setting('radiology.user_id',true) AND enabled));
CREATE TABLE saas.account_invitations (
 invitation_id uuid PRIMARY KEY,tenant_id text NOT NULL UNIQUE REFERENCES saas.tenants(tenant_id),
 request_id uuid NOT NULL,created_by uuid NOT NULL REFERENCES saas.users(user_id),
 email text NOT NULL,reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 1000),
 status text NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','PROVISIONING','INVITED','ACCEPTED','FAILED','REVOKED')),
 cognito_issuer text,cognito_subject text,error_code text,delivery_token uuid,lease_until timestamptz,
 expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '7 days',
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(created_by,request_id)
);
ALTER TABLE saas.account_invitations ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas.account_invitations FORCE ROW LEVEL SECURITY;
CREATE POLICY platform_account_invites ON saas.account_invitations FOR ALL
 USING(EXISTS(SELECT 1 FROM saas.platform_roles WHERE user_id::text=current_setting('radiology.user_id',true) AND enabled))
 WITH CHECK(EXISTS(SELECT 1 FROM saas.platform_roles WHERE user_id::text=current_setting('radiology.user_id',true) AND enabled));
-- Trusted authentication adapter only: after JWT + Cognito GetUser verification.
-- No browser-supplied roles, tenant choice or signing grants are accepted.
CREATE FUNCTION saas.accept_account_invitation(p_issuer text,p_subject text,p_email text)
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,saas AS $$
DECLARE invitation record; member_id uuid; admitted integer:=0;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM saas.account_invitations WHERE cognito_issuer=p_issuer AND cognito_subject=p_subject
   AND email=p_email AND status='INVITED' AND expires_at>clock_timestamp()) THEN RETURN 0; END IF;
 INSERT INTO saas.users(user_id,issuer,subject,email,email_verified)
  VALUES(gen_random_uuid(),p_issuer,p_subject,p_email,true) ON CONFLICT(issuer,subject) DO NOTHING;
 SELECT user_id INTO member_id FROM saas.users WHERE issuer=p_issuer AND subject=p_subject AND enabled
  AND (email IS NULL OR email=p_email) FOR UPDATE;
 IF member_id IS NULL THEN RAISE EXCEPTION 'Identity unavailable'; END IF;
 UPDATE saas.users SET email=p_email,email_verified=true WHERE user_id=member_id;
 FOR invitation IN SELECT i.invitation_id,i.tenant_id,t.kind FROM saas.account_invitations i
  JOIN saas.tenants t USING(tenant_id) WHERE i.cognito_issuer=p_issuer AND i.cognito_subject=p_subject
  AND i.email=p_email AND i.status='INVITED' AND i.expires_at>clock_timestamp() AND t.status='ACTIVE'
  ORDER BY i.tenant_id LOOP
   PERFORM 1 FROM saas.tenant_settings WHERE tenant_id=invitation.tenant_id FOR UPDATE;
   PERFORM 1 FROM saas.account_invitations WHERE invitation_id=invitation.invitation_id
     AND status='INVITED' AND expires_at>clock_timestamp() FOR UPDATE;
   IF NOT FOUND THEN CONTINUE; END IF;
   IF EXISTS(SELECT 1 FROM saas.tenant_memberships WHERE tenant_id=invitation.tenant_id) THEN
     RAISE EXCEPTION 'Account owner already assigned';
   END IF;
   INSERT INTO saas.tenant_memberships(tenant_id,user_id,roles) VALUES(invitation.tenant_id,member_id,
     CASE WHEN invitation.kind='HOSPITAL' THEN ARRAY['hospital_admin'] ELSE ARRAY['individual_owner','radiologist'] END);
   UPDATE saas.account_invitations SET status='ACCEPTED',delivery_token=NULL,lease_until=NULL
     WHERE invitation_id=invitation.invitation_id;
   admitted:=admitted+1;
 END LOOP;
 RETURN admitted;
END;
$$;
REVOKE ALL ON FUNCTION saas.accept_account_invitation(text,text,text) FROM PUBLIC;
