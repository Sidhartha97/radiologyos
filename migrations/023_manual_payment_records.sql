-- Manual bookkeeping only: no subscription, credits, automatic payment or tax invoice.
CREATE TABLE saas.manual_payments (
 payment_id uuid PRIMARY KEY,tenant_id text NOT NULL REFERENCES saas.tenants(tenant_id),
 currency text NOT NULL DEFAULT 'INR' CHECK(currency='INR'),amount_paise bigint NOT NULL CHECK(amount_paise BETWEEN 1 AND 1000000000000),
 method text NOT NULL CHECK(method IN ('BANK','UPI','CASH','OTHER')),reference text NOT NULL CHECK(length(reference) BETWEEN 1 AND 160),
 reference_hash text NOT NULL UNIQUE,reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 1000),
 state text NOT NULL DEFAULT 'RECORDED' CHECK(state IN ('RECORDED','VERIFIED','REJECTED')),
 version bigint NOT NULL DEFAULT 1,created_by uuid NOT NULL REFERENCES saas.users(user_id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),verified_by uuid REFERENCES saas.users(user_id),
 decided_at timestamptz,decision_reason text
);
ALTER TABLE saas.manual_payments ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas.manual_payments FORCE ROW LEVEL SECURITY;
CREATE POLICY platform_manual_payments ON saas.manual_payments FOR ALL
 USING(EXISTS(SELECT 1 FROM saas.platform_roles WHERE user_id::text=current_setting('radiology.user_id',true) AND enabled))
 WITH CHECK(EXISTS(SELECT 1 FROM saas.platform_roles WHERE user_id::text=current_setting('radiology.user_id',true) AND enabled));
CREATE POLICY tenant_payment_read ON saas.manual_payments FOR SELECT USING(tenant_id=current_setting('radiology.tenant_id',true));
CREATE FUNCTION saas.protect_manual_payment() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.state<>'RECORDED' OR NEW.state NOT IN ('VERIFIED','REJECTED') OR
  (NEW.payment_id,NEW.tenant_id,NEW.amount_paise,NEW.currency,NEW.method,NEW.reference,NEW.reference_hash,NEW.created_by,NEW.created_at,NEW.reason)
   IS DISTINCT FROM (OLD.payment_id,OLD.tenant_id,OLD.amount_paise,OLD.currency,OLD.method,OLD.reference,OLD.reference_hash,OLD.created_by,OLD.created_at,OLD.reason) THEN
   RAISE EXCEPTION 'Manual payment identity and completed decisions cannot change';
 END IF;
 RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION saas.protect_manual_payment() FROM PUBLIC;
CREATE TRIGGER manual_payment_protection BEFORE UPDATE ON saas.manual_payments FOR EACH ROW EXECUTE FUNCTION saas.protect_manual_payment();
