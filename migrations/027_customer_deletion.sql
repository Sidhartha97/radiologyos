-- Private durable cleanup receipts. No patient/report text or runtime table grants.
CREATE TABLE saas.customer_deletions (
 job_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id text NOT NULL UNIQUE,
 actor_id uuid NOT NULL,tenant_name text,reason text NOT NULL,
 state text NOT NULL CHECK(state IN ('PREVIEW','CONFIRMED','RUNNING','FAILED','COMPLETE')),
 confirmation_token uuid NOT NULL DEFAULT gen_random_uuid(),
 expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '10 minutes',
 quarantine_until timestamptz,available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 worker_token uuid,lease_until timestamptz,manifest jsonb NOT NULL DEFAULT '{}',
 stage text NOT NULL DEFAULT 'PREVIEW',error_code text,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 completed_at timestamptz
);
CREATE TABLE saas.identity_deletion_claims (
 issuer text NOT NULL,subject text NOT NULL,job_id uuid NOT NULL,
 PRIMARY KEY(issuer,subject)
);
REVOKE ALL ON saas.customer_deletions,saas.identity_deletion_claims FROM PUBLIC;

CREATE FUNCTION saas.guard_deleting_identity() RETURNS trigger LANGUAGE plpgsql
 SECURITY DEFINER SET search_path=pg_catalog,saas AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended(NEW.user_id::text,770));
 IF EXISTS(SELECT 1 FROM saas.identity_deletion_claims c JOIN saas.users u
   ON u.issuer=c.issuer AND u.subject=c.subject WHERE u.user_id=NEW.user_id) THEN
  RAISE EXCEPTION 'Identity deletion is in progress';
 END IF;
 RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION saas.guard_deleting_identity() FROM PUBLIC;
CREATE TRIGGER deleting_identity_guard BEFORE INSERT OR UPDATE OF user_id ON saas.tenant_memberships
 FOR EACH ROW EXECUTE FUNCTION saas.guard_deleting_identity();

-- Existing immutable history remains immutable to runtime users. Only the actual
-- table owner, inside the confirmed/fenced purge function below, may erase it.
CREATE OR REPLACE FUNCTION saas.reject_history_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' AND current_user::regrole=(SELECT relowner FROM pg_catalog.pg_class WHERE oid=TG_RELID)
  AND current_setting('radiology.purge_tenant',true)=OLD.tenant_id THEN
  IF EXISTS(SELECT 1 FROM saas.customer_deletions WHERE tenant_id=OLD.tenant_id
    AND state='RUNNING' AND stage='PURGING_SQL' AND lease_until>clock_timestamp()) THEN RETURN OLD; END IF;
 END IF;
 RAISE EXCEPTION 'Signed reports and workflow history are immutable';
END;
$$;
CREATE OR REPLACE FUNCTION saas.protect_final_control() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' AND current_user::regrole=(SELECT relowner FROM pg_catalog.pg_class WHERE oid=TG_RELID)
  AND current_setting('radiology.purge_tenant',true)=OLD.tenant_id THEN
  IF EXISTS(SELECT 1 FROM saas.customer_deletions WHERE tenant_id=OLD.tenant_id
    AND state='RUNNING' AND stage='PURGING_SQL' AND lease_until>clock_timestamp()) THEN RETURN OLD; END IF;
 END IF;
 IF OLD.workflow_status IN ('FINALIZED','AMENDED') THEN
  RAISE EXCEPTION 'Signed report controls are immutable; create an amendment';
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END;
$$;

CREATE FUNCTION saas.guard_deleting_tenant() RETURNS trigger LANGUAGE plpgsql
 SECURITY DEFINER SET search_path=pg_catalog,saas AS $$
BEGIN
 IF NEW.status<>'SUSPENDED' AND EXISTS(SELECT 1 FROM saas.customer_deletions
  WHERE tenant_id=NEW.tenant_id AND state IN ('CONFIRMED','RUNNING','FAILED','COMPLETE')) THEN
  RAISE EXCEPTION 'Customer deletion cannot be reactivated';
 END IF;
 RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION saas.guard_deleting_tenant() FROM PUBLIC;
CREATE TRIGGER deleting_tenant_guard BEFORE UPDATE OF status ON saas.tenants
 FOR EACH ROW EXECUTE FUNCTION saas.guard_deleting_tenant();

-- Runtime is a trusted service, not an SQL login exposed to customers. UI actions
-- require a resolved platform identity; worker actions can only process already
-- confirmed jobs and require a private expiring worker token. No arbitrary SQL.
CREATE FUNCTION saas.customer_deletion(p_action text,p_tenant text,p_job uuid,p_token uuid,p_payload jsonb)
 RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,saas AS $$
DECLARE actor uuid; job saas.customer_deletions; tenant saas.tenants; candidate record;
 identity_list jsonb:='[]'; table_name text; counts jsonb;
BEGIN
 IF p_action IN ('PREVIEW','CONFIRM','STATUS','RETRY') THEN
  SELECT u.user_id INTO actor FROM saas.users u JOIN saas.platform_roles p USING(user_id)
   WHERE u.issuer=current_setting('radiology.issuer',true) AND u.subject=current_setting('radiology.subject',true)
   AND u.enabled AND p.enabled;
  IF actor IS NULL THEN RAISE EXCEPTION 'Platform administrator required'; END IF;
  IF p_action='PREVIEW' THEN
   SELECT * INTO tenant FROM saas.tenants WHERE tenant_id=p_tenant FOR UPDATE;
   IF NOT FOUND THEN RAISE EXCEPTION 'Customer not found'; END IF;
   SELECT * INTO job FROM saas.customer_deletions WHERE tenant_id=p_tenant;
   IF FOUND AND job.state<>'PREVIEW' THEN RAISE EXCEPTION 'Deletion already requested'; END IF;
   INSERT INTO saas.customer_deletions(tenant_id,actor_id,tenant_name,reason,state)
    VALUES(p_tenant,actor,tenant.name,'Awaiting explicit confirmation','PREVIEW')
    ON CONFLICT(tenant_id) DO UPDATE SET actor_id=EXCLUDED.actor_id,tenant_name=EXCLUDED.tenant_name,
    confirmation_token=gen_random_uuid(),expires_at=clock_timestamp()+interval '10 minutes'
    RETURNING * INTO job;
   SELECT jsonb_build_object('reports',(SELECT count(*) FROM saas.report_controls WHERE tenant_id=p_tenant),
    'members',(SELECT count(*) FROM saas.tenant_memberships WHERE tenant_id=p_tenant),
    'patients',coalesce((SELECT jsonb_array_length(payload->'patients') FROM public.hospital_state WHERE tenant_id=p_tenant),0)) INTO counts;
   RETURN jsonb_build_object('jobId',job.job_id,'confirmationToken',job.confirmation_token,
    'tenantId',p_tenant,'name',tenant.name,'counts',counts,'uploadExpiryGraceSeconds',7200);
  END IF;
  SELECT * INTO job FROM saas.customer_deletions WHERE tenant_id=p_tenant AND (p_job IS NULL OR job_id=p_job) FOR UPDATE;
  IF NOT FOUND THEN
   IF p_action='STATUS' THEN RETURN jsonb_build_object('state','NONE'); END IF;
   RAISE EXCEPTION 'Deletion not found';
  END IF;
  IF p_action='CONFIRM' THEN
   IF job.state<>'PREVIEW' OR job.actor_id<>actor OR job.confirmation_token IS DISTINCT FROM p_token
    OR job.expires_at<=clock_timestamp() OR p_payload->'confirm' IS DISTINCT FROM 'true'::jsonb
    OR length(btrim(coalesce(p_payload->>'reason',''))) NOT BETWEEN 1 AND 1000 THEN
    RAISE EXCEPTION 'Explicit fresh deletion confirmation required';
   END IF;
   UPDATE saas.tenants SET status='SUSPENDED' WHERE tenant_id=p_tenant;
   DELETE FROM saas.report_locks WHERE tenant_id=p_tenant;
   UPDATE saas.customer_deletions SET state='CONFIRMED',stage='QUARANTINE',
    reason=btrim(p_payload->>'reason'),quarantine_until=clock_timestamp()+interval '2 hours',
    available_at=clock_timestamp(),error_code=NULL WHERE job_id=p_job RETURNING * INTO job;
  ELSIF p_action='RETRY' THEN
   IF job.state<>'FAILED' THEN RAISE EXCEPTION 'Only failed cleanup may be retried'; END IF;
   UPDATE saas.customer_deletions SET state='CONFIRMED',available_at=clock_timestamp(),error_code=NULL
    ,manifest=manifest-'indexRequested'-'indexStartedAt'
    WHERE job_id=p_job RETURNING * INTO job;
  END IF;
  RETURN jsonb_build_object('jobId',job.job_id,'tenantId',job.tenant_id,'state',job.state,
   'stage',job.stage,'errorCode',job.error_code,'quarantineUntil',job.quarantine_until,'completedAt',job.completed_at);
 END IF;
 IF p_action='NEXT' THEN
  SELECT * INTO job FROM saas.customer_deletions WHERE available_at<=clock_timestamp() AND
   (state='CONFIRMED' OR (state='RUNNING' AND lease_until<clock_timestamp()))
   ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1;
  IF NOT FOUND THEN RETURN NULL; END IF;
  UPDATE saas.customer_deletions SET state='RUNNING',worker_token=gen_random_uuid(),
   lease_until=clock_timestamp()+interval '10 minutes' WHERE job_id=job.job_id RETURNING * INTO job;
  RETURN jsonb_build_object('jobId',job.job_id,'tenantId',job.tenant_id,'workerToken',job.worker_token,
   'quarantineUntil',job.quarantine_until,'manifest',job.manifest,'stage',job.stage);
 END IF;
 SELECT * INTO job FROM saas.customer_deletions WHERE job_id=p_job AND worker_token=p_token
  AND state='RUNNING' AND lease_until>clock_timestamp() FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Deletion worker lease expired'; END IF;
 IF p_action='SAVE' THEN
  IF p_payload->>'state' NOT IN ('CONFIRMED','RUNNING','FAILED') THEN RAISE EXCEPTION 'Invalid progress'; END IF;
  UPDATE saas.customer_deletions SET state=p_payload->>'state',stage=p_payload->>'stage',
   manifest=coalesce(p_payload->'manifest',manifest),error_code=p_payload->>'errorCode',
   available_at=clock_timestamp()+make_interval(secs=>coalesce((p_payload->>'delaySeconds')::integer,0)),
   lease_until=clock_timestamp()+interval '10 minutes' WHERE job_id=p_job;
  RETURN jsonb_build_object('ok',true);
 ELSIF p_action='IDENTITIES' THEN
  FOR candidate IN SELECT DISTINCT u.user_id,u.issuer,u.subject,u.email FROM saas.users u
   WHERE EXISTS(SELECT 1 FROM saas.tenant_memberships m WHERE m.tenant_id=job.tenant_id AND m.user_id=u.user_id)
   OR EXISTS(SELECT 1 FROM saas.account_invitations i WHERE i.tenant_id=job.tenant_id AND i.cognito_issuer=u.issuer AND i.cognito_subject=u.subject)
   OR EXISTS(SELECT 1 FROM saas.invitations i WHERE i.tenant_id=job.tenant_id AND i.cognito_issuer=u.issuer AND i.cognito_subject=u.subject)
   ORDER BY u.user_id LOOP
   PERFORM pg_advisory_xact_lock(hashtextextended(candidate.user_id::text,770));
   IF EXISTS(SELECT 1 FROM saas.tenant_memberships WHERE user_id=candidate.user_id AND tenant_id<>job.tenant_id)
    OR EXISTS(SELECT 1 FROM saas.platform_roles WHERE user_id=candidate.user_id)
    OR EXISTS(SELECT 1 FROM saas.account_invitations WHERE cognito_issuer=candidate.issuer AND cognito_subject=candidate.subject AND tenant_id<>job.tenant_id AND status<>'REVOKED')
    OR EXISTS(SELECT 1 FROM saas.invitations WHERE cognito_issuer=candidate.issuer AND cognito_subject=candidate.subject AND tenant_id<>job.tenant_id AND status<>'REVOKED')
    OR EXISTS(SELECT 1 FROM saas.platform_invitations WHERE issuer=candidate.issuer AND subject=candidate.subject) THEN CONTINUE; END IF;
   INSERT INTO saas.identity_deletion_claims(issuer,subject,job_id) VALUES(candidate.issuer,candidate.subject,p_job) ON CONFLICT DO NOTHING;
   UPDATE saas.users SET enabled=false WHERE user_id=candidate.user_id;
  END LOOP;
  -- Also cover invited Cognito users who never completed their first login.
  FOR candidate IN SELECT cognito_issuer issuer,cognito_subject subject,email FROM saas.account_invitations WHERE tenant_id=job.tenant_id
   UNION SELECT cognito_issuer,cognito_subject,email FROM saas.invitations WHERE tenant_id=job.tenant_id LOOP
   IF candidate.subject IS NULL OR candidate.issuer IS NULL OR
    EXISTS(SELECT 1 FROM saas.users WHERE issuer=candidate.issuer AND subject=candidate.subject) OR
    EXISTS(SELECT 1 FROM saas.platform_invitations WHERE issuer=candidate.issuer AND subject=candidate.subject) OR
    EXISTS(SELECT 1 FROM saas.account_invitations WHERE cognito_issuer=candidate.issuer AND cognito_subject=candidate.subject AND tenant_id<>job.tenant_id AND status<>'REVOKED') OR
    EXISTS(SELECT 1 FROM saas.invitations WHERE cognito_issuer=candidate.issuer AND cognito_subject=candidate.subject AND tenant_id<>job.tenant_id AND status<>'REVOKED') THEN CONTINUE; END IF;
   INSERT INTO saas.identity_deletion_claims(issuer,subject,job_id) VALUES(candidate.issuer,candidate.subject,p_job) ON CONFLICT DO NOTHING;
  END LOOP;
  SELECT coalesce(jsonb_agg(jsonb_build_object('issuer',c.issuer,'subject',c.subject,'email',coalesce(
   (SELECT email FROM saas.users WHERE issuer=c.issuer AND subject=c.subject),
   (SELECT email FROM saas.account_invitations WHERE cognito_issuer=c.issuer AND cognito_subject=c.subject AND tenant_id=job.tenant_id LIMIT 1),
   (SELECT email FROM saas.invitations WHERE cognito_issuer=c.issuer AND cognito_subject=c.subject AND tenant_id=job.tenant_id LIMIT 1)))),'[]') INTO identity_list
   FROM saas.identity_deletion_claims c WHERE c.job_id=p_job;
  RETURN identity_list;
 ELSIF p_action='COMPLETE' THEN
  IF job.quarantine_until>clock_timestamp() OR NOT coalesce((job.manifest->>'storageDeleted')::boolean,false)
   OR NOT coalesce((job.manifest->>'indexDeleted')::boolean,false)
   OR NOT coalesce((job.manifest->>'dynamoDeleted')::boolean,false)
   OR NOT coalesce((job.manifest->>'identitiesDeleted')::boolean,false) THEN RAISE EXCEPTION 'External deletion not verified'; END IF;
  UPDATE saas.customer_deletions SET stage='PURGING_SQL' WHERE job_id=p_job;
  PERFORM set_config('radiology.purge_tenant',job.tenant_id,true);
  UPDATE saas.sessions SET active_tenant_id=NULL WHERE active_tenant_id=job.tenant_id;
  FOREACH table_name IN ARRAY ARRAY[
   'public.patient_conversations','public.hospital_report_versions','public.hospital_state',
   'saas.publication_jobs','saas.report_conversations','saas.report_assignments','saas.report_locks',
   'saas.signed_reports','saas.report_events','saas.report_controls','saas.usage_receipts',
   'saas.reservation_lots','saas.wallet_ledger','saas.reservations','saas.credit_lots','saas.wallets',
   'saas.model_token_events','saas.manual_payments','saas.audit_logs','saas.employee_directory',
   'saas.invitations','saas.account_invitations','saas.registrations','saas.tenant_memberships',
   'saas.tenant_settings','saas.tenants'] LOOP
    EXECUTE format('DELETE FROM %s WHERE tenant_id=$1',table_name) USING job.tenant_id;
  END LOOP;
  IF to_regclass('public.hospital_memberships') IS NOT NULL THEN
   EXECUTE 'DELETE FROM public.hospital_memberships WHERE tenant_id=$1' USING job.tenant_id;
  END IF;
  FOR candidate IN SELECT u.user_id FROM saas.users u JOIN saas.identity_deletion_claims c USING(issuer,subject)
   WHERE c.job_id=p_job LOOP
   DELETE FROM saas.sessions WHERE user_id=candidate.user_id;
   DELETE FROM saas.session_security WHERE user_id=candidate.user_id;
   DELETE FROM saas.users WHERE user_id=candidate.user_id;
  END LOOP;
  DELETE FROM saas.identity_deletion_claims WHERE job_id=p_job;
  UPDATE saas.customer_deletions SET state='COMPLETE',stage='COMPLETE',manifest='{}',
   tenant_name=NULL,reason='Confirmed cleanup completed',worker_token=NULL,lease_until=NULL,
   completed_at=clock_timestamp() WHERE job_id=p_job;
  RETURN jsonb_build_object('state','COMPLETE');
 END IF;
 RAISE EXCEPTION 'Unknown deletion operation';
END;
$$;
REVOKE ALL ON FUNCTION saas.customer_deletion(text,text,uuid,uuid,jsonb) FROM PUBLIC;
