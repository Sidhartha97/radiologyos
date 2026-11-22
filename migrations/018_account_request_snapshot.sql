ALTER TABLE saas.account_invitations ADD COLUMN creation_snapshot jsonb;
UPDATE saas.account_invitations i SET creation_snapshot=jsonb_build_object(
 'email',i.email,'name',t.name,'kind',t.kind,'maxUsers',s.max_users,
 'allowStaffCreation',s.allow_hospital_admin_create_users,'status',t.status,'reason',i.reason)
 FROM saas.tenants t JOIN saas.tenant_settings s USING(tenant_id) WHERE i.tenant_id=t.tenant_id;
ALTER TABLE saas.account_invitations ALTER COLUMN creation_snapshot SET NOT NULL;
CREATE FUNCTION saas.protect_account_request() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (NEW.invitation_id,NEW.tenant_id,NEW.request_id,NEW.created_by,NEW.email,NEW.reason,NEW.creation_snapshot)
   IS DISTINCT FROM (OLD.invitation_id,OLD.tenant_id,OLD.request_id,OLD.created_by,OLD.email,OLD.reason,OLD.creation_snapshot) THEN
   RAISE EXCEPTION 'Account creation request is immutable';
 END IF;
 RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION saas.protect_account_request() FROM PUBLIC;
CREATE TRIGGER account_request_immutable BEFORE UPDATE ON saas.account_invitations
 FOR EACH ROW EXECUTE FUNCTION saas.protect_account_request();
