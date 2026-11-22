ALTER TABLE saas.audit_logs ADD COLUMN reason text CHECK(reason IS NULL OR length(reason)<=1000);
-- Runtime business writes require a real global platform grant.
DROP POLICY tenant_scope ON saas.tenants;
CREATE POLICY tenant_read ON saas.tenants FOR SELECT
 USING(tenant_id=current_setting('radiology.tenant_id',true));
DROP POLICY tenant_scope ON saas.tenant_settings;
CREATE POLICY tenant_read ON saas.tenant_settings FOR SELECT
 USING(tenant_id=current_setting('radiology.tenant_id',true));
CREATE POLICY platform_tenant_update ON saas.tenants FOR UPDATE
 USING(EXISTS(SELECT 1 FROM saas.platform_roles p WHERE p.user_id::text=current_setting('radiology.user_id',true) AND p.enabled))
 WITH CHECK(EXISTS(SELECT 1 FROM saas.platform_roles p WHERE p.user_id::text=current_setting('radiology.user_id',true) AND p.enabled));
CREATE POLICY platform_settings_update ON saas.tenant_settings FOR UPDATE
 USING(EXISTS(SELECT 1 FROM saas.platform_roles p WHERE p.user_id::text=current_setting('radiology.user_id',true) AND p.enabled))
 WITH CHECK(EXISTS(SELECT 1 FROM saas.platform_roles p WHERE p.user_id::text=current_setting('radiology.user_id',true) AND p.enabled));
CREATE POLICY platform_members_read ON saas.tenant_memberships FOR SELECT
 USING(EXISTS(SELECT 1 FROM saas.platform_roles p WHERE p.user_id::text=current_setting('radiology.user_id',true) AND p.enabled));
