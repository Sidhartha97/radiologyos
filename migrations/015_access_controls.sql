ALTER TABLE saas.tenant_memberships ADD COLUMN version bigint NOT NULL DEFAULT 1 CHECK(version>0);
CREATE POLICY platform_member_update ON saas.tenant_memberships FOR UPDATE
 USING(EXISTS(SELECT 1 FROM saas.platform_roles WHERE user_id::text=current_setting('radiology.user_id',true) AND enabled))
 WITH CHECK(EXISTS(SELECT 1 FROM saas.platform_roles WHERE user_id::text=current_setting('radiology.user_id',true) AND enabled));
-- Column grants are shared by the runtime. Hospital admins must NOT use their
-- enabled-update policy to change clinical grants or roles.
CREATE FUNCTION saas.protect_membership_access() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE trusted_operator boolean;
BEGIN
 SELECT rolsuper OR rolbypassrls INTO trusted_operator FROM pg_roles WHERE rolname=current_user;
 IF NEW.tenant_id<>OLD.tenant_id OR NEW.user_id<>OLD.user_id THEN
   RAISE EXCEPTION 'Membership identity cannot change';
 END IF;
 IF (NEW.roles,NEW.can_sign,NEW.can_use_ai) IS DISTINCT FROM (OLD.roles,OLD.can_sign,OLD.can_use_ai)
   AND NOT trusted_operator AND NOT EXISTS(SELECT 1 FROM saas.platform_roles
    WHERE user_id::text=current_setting('radiology.user_id',true) AND enabled) THEN
   RAISE EXCEPTION 'Platform approval is required for role and clinical grant changes';
 END IF;
 NEW.version:=OLD.version+1;
 RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION saas.protect_membership_access() FROM PUBLIC;
CREATE TRIGGER membership_access_protection BEFORE UPDATE ON saas.tenant_memberships
 FOR EACH ROW EXECUTE FUNCTION saas.protect_membership_access();
