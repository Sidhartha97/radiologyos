CREATE TABLE saas.report_events (
 event_id uuid PRIMARY KEY,
 tenant_id text NOT NULL,
 report_id text NOT NULL,
 version bigint NOT NULL CHECK(version>0),
 actor_id uuid NOT NULL,
 from_status text NOT NULL,
 to_status text NOT NULL,
 reason text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,report_id,version),
 FOREIGN KEY(tenant_id,report_id) REFERENCES saas.report_controls(tenant_id,report_id),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES saas.tenant_memberships(tenant_id,user_id)
);
CREATE TABLE saas.signed_reports (
 tenant_id text NOT NULL,
 report_id text NOT NULL,
 version bigint NOT NULL,
 snapshot jsonb NOT NULL,
 signed_by uuid NOT NULL,
 signed_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,report_id,version),
 FOREIGN KEY(tenant_id,report_id) REFERENCES saas.report_controls(tenant_id,report_id),
 FOREIGN KEY(tenant_id,signed_by) REFERENCES saas.tenant_memberships(tenant_id,user_id)
);
CREATE TABLE saas.publication_jobs (
 job_id uuid PRIMARY KEY,
 tenant_id text NOT NULL,
 report_id text NOT NULL,
 version bigint NOT NULL,
 state text NOT NULL DEFAULT 'PENDING' CHECK(state IN ('PENDING','RUNNING','COMPLETE','FAILED')),
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0),
 available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 lease_until timestamptz,
 worker_token uuid,
 error_code text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,report_id,version),
 FOREIGN KEY(tenant_id,report_id,version) REFERENCES saas.signed_reports(tenant_id,report_id,version)
);
CREATE INDEX publication_ready ON saas.publication_jobs(state,available_at);
ALTER TABLE saas.report_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas.report_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON saas.report_events
 USING (tenant_id=current_setting('radiology.tenant_id',true))
 WITH CHECK (tenant_id=current_setting('radiology.tenant_id',true) AND actor_id::text=current_setting('radiology.user_id',true));
ALTER TABLE saas.signed_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas.signed_reports FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON saas.signed_reports
 USING (tenant_id=current_setting('radiology.tenant_id',true))
 WITH CHECK (tenant_id=current_setting('radiology.tenant_id',true) AND signed_by::text=current_setting('radiology.user_id',true));
ALTER TABLE saas.publication_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas.publication_jobs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON saas.publication_jobs
 USING (tenant_id=current_setting('radiology.tenant_id',true))
 WITH CHECK (tenant_id=current_setting('radiology.tenant_id',true));

CREATE FUNCTION saas.reject_history_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Signed reports and workflow history are immutable'; END;
$$;
CREATE TRIGGER signed_immutable BEFORE UPDATE OR DELETE ON saas.signed_reports
 FOR EACH ROW EXECUTE FUNCTION saas.reject_history_change();
CREATE TRIGGER event_immutable BEFORE UPDATE OR DELETE ON saas.report_events
 FOR EACH ROW EXECUTE FUNCTION saas.reject_history_change();
REVOKE ALL ON FUNCTION saas.reject_history_change() FROM PUBLIC;

CREATE FUNCTION saas.protect_final_control() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.workflow_status IN ('FINALIZED','AMENDED') THEN
   RAISE EXCEPTION 'Signed report controls are immutable; create an amendment';
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER final_control_immutable BEFORE UPDATE OR DELETE ON saas.report_controls
 FOR EACH ROW EXECUTE FUNCTION saas.protect_final_control();
REVOKE ALL ON FUNCTION saas.protect_final_control() FROM PUBLIC;
