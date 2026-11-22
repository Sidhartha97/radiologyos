-- Email identities for customers, hospital-scoped employee codes for staff.
ALTER TABLE saas.users ADD COLUMN email text;
ALTER TABLE saas.users ADD COLUMN email_verified boolean NOT NULL DEFAULT false;
ALTER TABLE saas.users ADD CONSTRAINT user_email_valid
 CHECK (email IS NULL OR (email=lower(btrim(email)) AND length(email)<=254 AND position('@' IN email)>1));
CREATE UNIQUE INDEX user_email_per_issuer ON saas.users(issuer,email) WHERE email IS NOT NULL;
ALTER TABLE saas.tenant_memberships ADD COLUMN employee_code text;
ALTER TABLE saas.tenant_memberships ADD CONSTRAINT employee_code_valid
 CHECK (employee_code IS NULL OR employee_code ~ '^[A-Z0-9][A-Z0-9_-]{0,39}$');
CREATE UNIQUE INDEX employee_code_per_hospital ON saas.tenant_memberships(tenant_id,employee_code)
 WHERE employee_code IS NOT NULL;

CREATE TABLE saas.sessions (
 token_hash text PRIMARY KEY CHECK (token_hash ~ '^[a-f0-9]{64}$'),
 user_id uuid NOT NULL REFERENCES saas.users(user_id),
 active_tenant_id text,
 expires_at timestamptz NOT NULL,
 revoked_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY (active_tenant_id,user_id) REFERENCES saas.tenant_memberships(tenant_id,user_id)
);
CREATE INDEX sessions_expiry ON saas.sessions(expires_at);
ALTER TABLE saas.sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas.sessions FORCE ROW LEVEL SECURITY;
CREATE POLICY own_session ON saas.sessions
 USING (user_id::text=current_setting('radiology.user_id',true))
 WITH CHECK (user_id::text=current_setting('radiology.user_id',true));

-- A logout is permanent for that token, even if a caller tries to create a
-- new session with it. Sessions must not extend the Cognito token expiry.
CREATE FUNCTION saas.protect_session() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='UPDATE' AND (NEW.token_hash<>OLD.token_hash OR NEW.user_id<>OLD.user_id
     OR NEW.expires_at<>OLD.expires_at OR NEW.created_at<>OLD.created_at
     OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at)) THEN
   RAISE EXCEPTION 'Session identity, expiry and revocation are immutable';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER session_immutable BEFORE UPDATE ON saas.sessions
 FOR EACH ROW EXECUTE FUNCTION saas.protect_session();
REVOKE ALL ON FUNCTION saas.protect_session() FROM PUBLIC;
