-- Provider usage and the exact credit-rate snapshot, with no clinical payload.
CREATE TABLE saas.usage_receipts (
 tenant_id text NOT NULL,
 reservation_id uuid NOT NULL,
 model_id text NOT NULL CHECK(length(model_id) BETWEEN 1 AND 512),
 rate_version text NOT NULL CHECK(length(rate_version) BETWEEN 1 AND 80),
 input_per_thousand bigint NOT NULL CHECK(input_per_thousand>0),
 output_per_thousand bigint NOT NULL CHECK(output_per_thousand>0),
 input_tokens bigint NOT NULL CHECK(input_tokens>=0),
 output_tokens bigint NOT NULL CHECK(output_tokens>=0),
 credit_units bigint NOT NULL CHECK(credit_units>=0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(tenant_id,reservation_id),
 FOREIGN KEY(tenant_id,reservation_id) REFERENCES saas.reservations(tenant_id,reservation_id)
);
ALTER TABLE saas.usage_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas.usage_receipts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON saas.usage_receipts
 USING(tenant_id=current_setting('radiology.tenant_id',true))
 WITH CHECK(tenant_id=current_setting('radiology.tenant_id',true));
CREATE TRIGGER usage_receipt_immutable BEFORE UPDATE OR DELETE ON saas.usage_receipts
 FOR EACH ROW EXECUTE FUNCTION saas.reject_history_change();
