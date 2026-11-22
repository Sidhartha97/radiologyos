CREATE TABLE saas.report_locks (
    tenant_id text NOT NULL,
    report_id text NOT NULL,
    user_id uuid NOT NULL,
    editing_session uuid NOT NULL,
    token_hash text NOT NULL,
    fence bigint NOT NULL CHECK (fence>0),
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id,report_id),
    FOREIGN KEY (tenant_id,report_id) REFERENCES saas.report_controls(tenant_id,report_id),
    FOREIGN KEY (tenant_id,user_id) REFERENCES saas.tenant_memberships(tenant_id,user_id)
);
CREATE INDEX lease_expiry ON saas.report_locks(expires_at);
ALTER TABLE saas.report_locks ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas.report_locks FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON saas.report_locks
 USING (tenant_id=current_setting('radiology.tenant_id',true))
 WITH CHECK (tenant_id=current_setting('radiology.tenant_id',true));
