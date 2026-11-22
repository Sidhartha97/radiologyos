-- Operator-only, subject-bound bootstrap. No public registration/admin grants.
CREATE TABLE saas.platform_invitations (
 issuer text NOT NULL, subject text NOT NULL, email text NOT NULL,
 expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '7 days',
 accepted_at timestamptz, PRIMARY KEY(issuer,subject)
);
REVOKE ALL ON saas.platform_invitations FROM PUBLIC;
-- Authentication adapter calls this only after JWT verification AND Cognito
-- GetUser confirms the same subject and verified email. Runtime cannot issue
-- or read invitations. An accepted invitation never re-enables revoked access.
CREATE FUNCTION saas.accept_platform_invitation(p_issuer text,p_subject text,p_email text)
 RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,saas AS $$
DECLARE invitation record; member_id uuid;
BEGIN
 SELECT * INTO invitation FROM saas.platform_invitations
 WHERE issuer=p_issuer AND subject=p_subject AND email=p_email
 AND accepted_at IS NULL AND expires_at>clock_timestamp() FOR UPDATE;
 IF NOT FOUND THEN RETURN 0; END IF;
 INSERT INTO saas.users(user_id,issuer,subject,email,email_verified)
 VALUES(gen_random_uuid(),p_issuer,p_subject,p_email,true)
 ON CONFLICT(issuer,subject) DO NOTHING;
 SELECT user_id INTO member_id FROM saas.users WHERE issuer=p_issuer
 AND subject=p_subject AND enabled AND (email IS NULL OR email=p_email) FOR UPDATE;
 IF member_id IS NULL THEN RAISE EXCEPTION 'Identity unavailable'; END IF;
 UPDATE saas.users SET email=p_email,email_verified=true WHERE user_id=member_id;
 INSERT INTO saas.platform_roles(user_id,role) VALUES(member_id,'platform_admin')
 ON CONFLICT(user_id) DO NOTHING;
 UPDATE saas.platform_invitations SET accepted_at=clock_timestamp()
 WHERE issuer=p_issuer AND subject=p_subject;
 RETURN 1;
END;
$$;
REVOKE ALL ON FUNCTION saas.accept_platform_invitation(text,text,text) FROM PUBLIC;
