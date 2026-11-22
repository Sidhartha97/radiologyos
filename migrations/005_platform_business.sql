-- A platform administrator may read business configuration, but this policy
-- deliberately grants no access to clinical controls, reports, chat or files.
CREATE POLICY platform_tenant_read ON saas.tenants FOR SELECT
 USING (EXISTS (SELECT 1 FROM saas.platform_roles p
   WHERE p.user_id::text=current_setting('radiology.user_id',true) AND p.enabled));
CREATE POLICY platform_settings_read ON saas.tenant_settings FOR SELECT
 USING (EXISTS (SELECT 1 FROM saas.platform_roles p
   WHERE p.user_id::text=current_setting('radiology.user_id',true) AND p.enabled));
-- One initial registration per verified identity and account type. Further
-- hospital memberships use explicit invitations or operator admission.
CREATE TABLE saas.registrations (
 user_id uuid NOT NULL REFERENCES saas.users(user_id),
 kind text NOT NULL CHECK(kind IN ('HOSPITAL','INDIVIDUAL')),
 tenant_id text NOT NULL UNIQUE REFERENCES saas.tenants(tenant_id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(user_id,kind)
);
ALTER TABLE saas.registrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas.registrations FORCE ROW LEVEL SECURITY;
CREATE POLICY own_registration ON saas.registrations FOR SELECT
 USING (user_id::text=current_setting('radiology.user_id',true));
