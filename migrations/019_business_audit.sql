-- Platform audit review deliberately excludes clinical report/chat events.
CREATE POLICY platform_business_audit ON saas.audit_logs FOR SELECT USING(
 action IN ('account.created','tenant.configuration_updated','member.access_changed',
   'member.invitation_created','member.invitation_revoked','member.deactivated','member.reactivated')
 AND EXISTS(SELECT 1 FROM saas.platform_roles WHERE user_id::text=current_setting('radiology.user_id',true) AND enabled));
