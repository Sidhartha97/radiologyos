CREATE TABLE saas.invitations (
 invitation_id uuid PRIMARY KEY,
 tenant_id text NOT NULL REFERENCES saas.tenants(tenant_id),
 employee_code text NOT NULL CHECK(employee_code ~ '^[A-Z0-9][A-Z0-9_-]{0,39}$'),
 email text NOT NULL,
 role text NOT NULL CHECK(role IN ('radiologist','transcriptionist')),
 invited_by uuid NOT NULL REFERENCES saas.users(user_id),
 status text NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','PROVISIONING','INVITED','ACCEPTED','FAILED','EXPIRED','REVOKED')),
 cognito_subject text,
 cognito_issuer text,
 error_code text,
 expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '7 days',
 lease_until timestamptz,
 delivery_token uuid,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,employee_code)
);
CREATE INDEX invitation_email ON saas.invitations(email,status,expires_at);
ALTER TABLE saas.invitations ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas.invitations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON saas.invitations
 USING(tenant_id=current_setting('radiology.tenant_id',true))
 WITH CHECK(tenant_id=current_setting('radiology.tenant_id',true));

CREATE FUNCTION saas.hospital_admin(p_tenant text,p_user text)
 RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,saas AS $$
 SELECT EXISTS(SELECT 1 FROM saas.tenant_memberships WHERE tenant_id=p_tenant
   AND user_id::text=p_user AND enabled AND 'hospital_admin'=ANY(roles));
$$;
REVOKE ALL ON FUNCTION saas.hospital_admin(text,text) FROM PUBLIC;
CREATE POLICY hospital_roster ON saas.tenant_memberships FOR SELECT
 USING(tenant_id=current_setting('radiology.tenant_id',true)
       AND saas.hospital_admin(tenant_id,current_setting('radiology.user_id',true)));
CREATE POLICY hospital_deactivation ON saas.tenant_memberships FOR UPDATE
 USING(tenant_id=current_setting('radiology.tenant_id',true)
       AND saas.hospital_admin(tenant_id,current_setting('radiology.user_id',true)))
 WITH CHECK(tenant_id=current_setting('radiology.tenant_id',true));

-- This is a narrow backend provisioning operation. The backend must verify the
-- Cognito access token AND GetUser's email_verified attribute before invocation.
-- No caller-supplied role/tenant/signing grant is accepted by this function.
CREATE FUNCTION saas.accept_verified_invitations(p_issuer text,p_subject text,p_email text)
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,saas AS $$
DECLARE member_id uuid; invitation record; admitted integer := 0; maximum integer;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM saas.invitations WHERE email=p_email AND cognito_subject=p_subject AND cognito_issuer=p_issuer
      AND status='INVITED' AND expires_at>clock_timestamp()) THEN RETURN 0; END IF;
 INSERT INTO saas.users(user_id,issuer,subject,email,email_verified)
  VALUES(gen_random_uuid(),p_issuer,p_subject,p_email,true) ON CONFLICT(issuer,subject) DO NOTHING;
 SELECT user_id INTO member_id FROM saas.users WHERE issuer=p_issuer AND subject=p_subject
  AND enabled AND (email IS NULL OR email=p_email) FOR UPDATE;
 IF member_id IS NULL THEN RAISE EXCEPTION 'Identity unavailable'; END IF;
 UPDATE saas.users SET email=p_email,email_verified=true WHERE user_id=member_id;
 FOR invitation IN SELECT i.* FROM saas.invitations i JOIN saas.tenants t USING(tenant_id)
     WHERE i.email=p_email AND i.cognito_subject=p_subject AND i.cognito_issuer=p_issuer AND i.status='INVITED'
       AND i.expires_at>clock_timestamp() AND t.status='ACTIVE' ORDER BY i.tenant_id LOOP
   SELECT max_users INTO maximum FROM saas.tenant_settings WHERE tenant_id=invitation.tenant_id FOR UPDATE;
   PERFORM 1 FROM saas.invitations WHERE invitation_id=invitation.invitation_id AND status='INVITED' FOR UPDATE;
   IF NOT FOUND THEN CONTINUE; END IF;
   IF EXISTS(SELECT 1 FROM saas.tenant_memberships WHERE tenant_id=invitation.tenant_id AND user_id=member_id) THEN
     RAISE EXCEPTION 'Membership already exists';
   END IF;
   IF (SELECT count(*) FROM saas.tenant_memberships WHERE tenant_id=invitation.tenant_id AND enabled)>=maximum THEN
     RAISE EXCEPTION 'No seats available';
   END IF;
   INSERT INTO saas.tenant_memberships(tenant_id,user_id,roles,employee_code)
    VALUES(invitation.tenant_id,member_id,ARRAY[invitation.role],invitation.employee_code);
   INSERT INTO saas.employee_directory(tenant_id,employee_code,user_id,issuer,cognito_login)
    VALUES(invitation.tenant_id,invitation.employee_code,member_id,p_issuer,p_email);
   UPDATE saas.invitations SET status='ACCEPTED',lease_until=NULL WHERE invitation_id=invitation.invitation_id;
   admitted := admitted+1;
 END LOOP;
 RETURN admitted;
END;
$$;
REVOKE ALL ON FUNCTION saas.accept_verified_invitations(text,text,text) FROM PUBLIC;
