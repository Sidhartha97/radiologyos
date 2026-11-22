-- Self-service application session revocation. No Cognito passwords/tokens stored.
CREATE TABLE saas.session_security (
 user_id uuid PRIMARY KEY REFERENCES saas.users(user_id),
 valid_after timestamptz NOT NULL
);
ALTER TABLE saas.session_security ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas.session_security FORCE ROW LEVEL SECURITY;
CREATE POLICY own_session_security ON saas.session_security
 USING(user_id::text=current_setting('radiology.user_id',true))
 WITH CHECK(user_id::text=current_setting('radiology.user_id',true));
ALTER TABLE saas.sessions ADD COLUMN authenticated_at timestamptz;
CREATE FUNCTION saas.protect_session_authentication() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.authenticated_at IS DISTINCT FROM OLD.authenticated_at THEN
  RAISE EXCEPTION 'Session authentication time is immutable';
 END IF;
 RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION saas.protect_session_authentication() FROM PUBLIC;
CREATE TRIGGER immutable_authentication_time BEFORE UPDATE ON saas.sessions
 FOR EACH ROW EXECUTE FUNCTION saas.protect_session_authentication();
CREATE FUNCTION saas.protect_revocation_cutoff() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.user_id<>OLD.user_id OR NEW.valid_after<OLD.valid_after THEN
  RAISE EXCEPTION 'Session revocation cutoff cannot move backwards';
 END IF;
 RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION saas.protect_revocation_cutoff() FROM PUBLIC;
CREATE TRIGGER monotonic_revocation_cutoff BEFORE UPDATE ON saas.session_security
 FOR EACH ROW EXECUTE FUNCTION saas.protect_revocation_cutoff();
