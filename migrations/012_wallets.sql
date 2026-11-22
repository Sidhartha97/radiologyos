CREATE TABLE saas.wallets (
 tenant_id text PRIMARY KEY REFERENCES saas.tenants(tenant_id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE saas.credit_lots (
 lot_id uuid PRIMARY KEY,
 tenant_id text NOT NULL REFERENCES saas.wallets(tenant_id),
 grant_key text NOT NULL CHECK(length(grant_key) BETWEEN 1 AND 160),
 grant_units bigint NOT NULL CHECK(grant_units>0),
 remaining_units bigint NOT NULL CHECK(remaining_units>=0),
 reserved_units bigint NOT NULL DEFAULT 0 CHECK(reserved_units>=0 AND reserved_units<=remaining_units),
 expires_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,grant_key),UNIQUE(tenant_id,lot_id)
);
CREATE TABLE saas.reservations (
 reservation_id uuid PRIMARY KEY,
 tenant_id text NOT NULL REFERENCES saas.wallets(tenant_id),
 request_id uuid NOT NULL,
 actor_id uuid NOT NULL,
 model_id text NOT NULL,
 hold_units bigint NOT NULL CHECK(hold_units>0),
 state text NOT NULL DEFAULT 'RESERVED' CHECK(state IN ('RESERVED','INVOKING','SETTLED','RELEASED','OUTCOME_UNKNOWN')),
 actual_units bigint CHECK(actual_units>=0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 started_at timestamptz,
 completed_at timestamptz,
 UNIQUE(tenant_id,request_id),UNIQUE(tenant_id,reservation_id),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES saas.tenant_memberships(tenant_id,user_id)
);
CREATE TABLE saas.reservation_lots (
 tenant_id text NOT NULL,reservation_id uuid NOT NULL,lot_id uuid NOT NULL,
 units bigint NOT NULL CHECK(units>0),
 PRIMARY KEY(tenant_id,reservation_id,lot_id),
 FOREIGN KEY(tenant_id,reservation_id) REFERENCES saas.reservations(tenant_id,reservation_id),
 FOREIGN KEY(tenant_id,lot_id) REFERENCES saas.credit_lots(tenant_id,lot_id)
);
CREATE TABLE saas.wallet_ledger (
 event_id uuid PRIMARY KEY,tenant_id text NOT NULL,
 lot_id uuid NOT NULL,reservation_id uuid,
 kind text NOT NULL CHECK(kind IN ('GRANT','DEBIT','EXPIRE')),
 delta_units bigint NOT NULL CHECK(delta_units<>0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(tenant_id,lot_id) REFERENCES saas.credit_lots(tenant_id,lot_id),
 FOREIGN KEY(tenant_id,reservation_id) REFERENCES saas.reservations(tenant_id,reservation_id)
);
CREATE INDEX lots_by_tenant_expiry ON saas.credit_lots(tenant_id,expires_at);
CREATE INDEX reservations_by_state ON saas.reservations(tenant_id,state,created_at);
CREATE INDEX ledger_by_tenant_time ON saas.wallet_ledger(tenant_id,created_at);
ALTER TABLE saas.wallets ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas.wallets FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON saas.wallets
 USING(tenant_id=current_setting('radiology.tenant_id',true)) WITH CHECK(tenant_id=current_setting('radiology.tenant_id',true));
ALTER TABLE saas.credit_lots ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas.credit_lots FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON saas.credit_lots
 USING(tenant_id=current_setting('radiology.tenant_id',true)) WITH CHECK(tenant_id=current_setting('radiology.tenant_id',true));
ALTER TABLE saas.reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas.reservations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON saas.reservations
 USING(tenant_id=current_setting('radiology.tenant_id',true)) WITH CHECK(tenant_id=current_setting('radiology.tenant_id',true));
ALTER TABLE saas.reservation_lots ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas.reservation_lots FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON saas.reservation_lots
 USING(tenant_id=current_setting('radiology.tenant_id',true)) WITH CHECK(tenant_id=current_setting('radiology.tenant_id',true));
ALTER TABLE saas.wallet_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas.wallet_ledger FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON saas.wallet_ledger
 USING(tenant_id=current_setting('radiology.tenant_id',true)) WITH CHECK(tenant_id=current_setting('radiology.tenant_id',true));
CREATE TRIGGER wallet_history_immutable BEFORE UPDATE OR DELETE ON saas.wallet_ledger
 FOR EACH ROW EXECUTE FUNCTION saas.reject_history_change();
