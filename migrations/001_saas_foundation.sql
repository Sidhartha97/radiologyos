-- Additive foundation only. The existing application tables and RAG are untouched.
CREATE SCHEMA saas;
REVOKE ALL ON SCHEMA saas FROM PUBLIC;

CREATE TABLE saas.tenants (
    tenant_id text PRIMARY KEY CHECK (tenant_id ~ '^[A-Za-z0-9_-]{1,80}$'),
    name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 160),
    kind text NOT NULL CHECK (kind IN ('HOSPITAL','INDIVIDUAL')),
    status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','ACTIVE','SUSPENDED')),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE saas.users (
    user_id uuid PRIMARY KEY,
    issuer text NOT NULL,
    subject text NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (issuer,subject)
);
CREATE TABLE saas.tenant_memberships (
    tenant_id text NOT NULL REFERENCES saas.tenants(tenant_id),
    user_id uuid NOT NULL REFERENCES saas.users(user_id),
    roles text[] NOT NULL CHECK (cardinality(roles)>0 AND roles <@ ARRAY['hospital_admin','radiologist','transcriptionist','individual_owner']::text[]),
    enabled boolean NOT NULL DEFAULT true,
    can_sign boolean NOT NULL DEFAULT false,
    can_use_ai boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id,user_id),
    CHECK (NOT can_sign OR 'radiologist'=ANY(roles))
);
CREATE INDEX memberships_by_user ON saas.tenant_memberships(user_id,enabled);
CREATE TABLE saas.tenant_settings (
    tenant_id text PRIMARY KEY REFERENCES saas.tenants(tenant_id),
    allow_hospital_admin_create_users boolean NOT NULL DEFAULT false,
    max_users integer NOT NULL DEFAULT 1 CHECK (max_users>=1),
    version bigint NOT NULL DEFAULT 1 CHECK (version>0)
);
CREATE TABLE saas.platform_roles (
    user_id uuid PRIMARY KEY REFERENCES saas.users(user_id),
    role text NOT NULL CHECK (role='platform_admin'),
    enabled boolean NOT NULL DEFAULT true
);
CREATE TABLE saas.report_controls (
    tenant_id text NOT NULL REFERENCES saas.tenants(tenant_id),
    report_id text NOT NULL CHECK (length(report_id) BETWEEN 1 AND 128),
    patient_id text NOT NULL CHECK (length(patient_id) BETWEEN 1 AND 128),
    version bigint NOT NULL DEFAULT 1 CHECK (version>0),
    fence bigint NOT NULL DEFAULT 0 CHECK (fence>=0),
    workflow_status text NOT NULL DEFAULT 'DRAFT' CHECK (workflow_status IN
      ('DRAFT','IN_TRANSCRIPTION','READY_FOR_REVIEW','UNDER_REVIEW','RETURNED_FOR_CORRECTION','FINALIZED','AMENDED','CANCELLED')),
    legacy_final boolean NOT NULL DEFAULT false,
    signed_by uuid,
    signed_at timestamptz,
    PRIMARY KEY (tenant_id,report_id),
    FOREIGN KEY (tenant_id,signed_by) REFERENCES saas.tenant_memberships(tenant_id,user_id),
    CHECK ((signed_by IS NULL)=(signed_at IS NULL))
);
CREATE INDEX report_worklist ON saas.report_controls(tenant_id,workflow_status);
CREATE TABLE saas.report_assignments (
    tenant_id text NOT NULL,
    report_id text NOT NULL,
    user_id uuid NOT NULL,
    PRIMARY KEY (tenant_id,report_id,user_id),
    FOREIGN KEY (tenant_id,report_id) REFERENCES saas.report_controls(tenant_id,report_id),
    FOREIGN KEY (tenant_id,user_id) REFERENCES saas.tenant_memberships(tenant_id,user_id)
);
CREATE TABLE saas.audit_logs (
    event_id uuid PRIMARY KEY,
    tenant_id text REFERENCES saas.tenants(tenant_id),
    actor_id uuid REFERENCES saas.users(user_id),
    action text NOT NULL,
    object_id text,
    request_id text,
    outcome text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_tenant_time ON saas.audit_logs(tenant_id,created_at DESC);

-- These policies apply only to new tables. Activating RLS on legacy clinical
-- tables is a later migration once every legacy connection sets tenant context.
ALTER TABLE saas.tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas.tenants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON saas.tenants
 USING (tenant_id=current_setting('radiology.tenant_id',true))
 WITH CHECK (tenant_id=current_setting('radiology.tenant_id',true));
ALTER TABLE saas.tenant_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas.tenant_settings FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON saas.tenant_settings
 USING (tenant_id=current_setting('radiology.tenant_id',true))
 WITH CHECK (tenant_id=current_setting('radiology.tenant_id',true));
ALTER TABLE saas.report_controls ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas.report_controls FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON saas.report_controls
 USING (tenant_id=current_setting('radiology.tenant_id',true))
 WITH CHECK (tenant_id=current_setting('radiology.tenant_id',true));
ALTER TABLE saas.report_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas.report_assignments FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON saas.report_assignments
 USING (tenant_id=current_setting('radiology.tenant_id',true))
 WITH CHECK (tenant_id=current_setting('radiology.tenant_id',true));
ALTER TABLE saas.tenant_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas.tenant_memberships FORCE ROW LEVEL SECURITY;
CREATE POLICY member_identity ON saas.tenant_memberships
 USING (user_id::text=current_setting('radiology.user_id',true))
 WITH CHECK (false);
ALTER TABLE saas.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas.users FORCE ROW LEVEL SECURITY;
CREATE POLICY user_identity ON saas.users
 USING (issuer=current_setting('radiology.issuer',true) AND subject=current_setting('radiology.subject',true))
 WITH CHECK (false);
ALTER TABLE saas.platform_roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas.platform_roles FORCE ROW LEVEL SECURITY;
CREATE POLICY platform_identity ON saas.platform_roles
 USING (user_id::text=current_setting('radiology.user_id',true))
 WITH CHECK (false);
ALTER TABLE saas.audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas.audit_logs FORCE ROW LEVEL SECURITY;
CREATE POLICY audit_scope ON saas.audit_logs
 USING (tenant_id=current_setting('radiology.tenant_id',true))
 WITH CHECK (tenant_id=current_setting('radiology.tenant_id',true)
             AND actor_id::text=current_setting('radiology.user_id',true));
