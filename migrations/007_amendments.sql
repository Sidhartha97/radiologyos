ALTER TABLE saas.report_controls ADD COLUMN amends_report_id text;
ALTER TABLE saas.report_controls ADD COLUMN amendment_reason text;
ALTER TABLE saas.report_controls ADD CONSTRAINT amendment_parent
 FOREIGN KEY(tenant_id,amends_report_id) REFERENCES saas.report_controls(tenant_id,report_id);
ALTER TABLE saas.report_controls ADD CONSTRAINT amendment_reason_required
 CHECK ((amends_report_id IS NULL AND amendment_reason IS NULL) OR
        (amends_report_id IS NOT NULL AND length(btrim(amendment_reason)) BETWEEN 1 AND 1000
         AND amends_report_id<>report_id));
