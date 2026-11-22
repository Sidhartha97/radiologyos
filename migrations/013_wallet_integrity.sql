ALTER TABLE saas.credit_lots ADD CONSTRAINT lot_remaining_bound CHECK(remaining_units<=grant_units);
CREATE FUNCTION saas.protect_credit_lot() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.lot_id<>OLD.lot_id OR NEW.tenant_id<>OLD.tenant_id OR NEW.grant_key<>OLD.grant_key
    OR NEW.grant_units<>OLD.grant_units OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
    OR NEW.created_at<>OLD.created_at OR NEW.remaining_units>OLD.remaining_units THEN
   RAISE EXCEPTION 'Credit grants are immutable; use a new compensating grant';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER credit_grant_immutable BEFORE UPDATE ON saas.credit_lots
 FOR EACH ROW EXECUTE FUNCTION saas.protect_credit_lot();
REVOKE ALL ON FUNCTION saas.protect_credit_lot() FROM PUBLIC;
DROP POLICY tenant_scope ON saas.credit_lots;
CREATE POLICY tenant_read ON saas.credit_lots FOR SELECT USING(tenant_id=current_setting('radiology.tenant_id',true));
CREATE POLICY tenant_spend ON saas.credit_lots FOR UPDATE
 USING(tenant_id=current_setting('radiology.tenant_id',true)) WITH CHECK(tenant_id=current_setting('radiology.tenant_id',true));
CREATE POLICY platform_grant ON saas.credit_lots FOR INSERT
 WITH CHECK(tenant_id=current_setting('radiology.tenant_id',true) AND EXISTS(
   SELECT 1 FROM saas.platform_roles WHERE user_id::text=current_setting('radiology.user_id',true) AND enabled));
