-- Telemetry only: no prices, balances, prompt text or clinical report content.
CREATE TABLE saas.model_token_events (
 event_id uuid PRIMARY KEY,tenant_id text NOT NULL,actor_id uuid NOT NULL,
 model_id text NOT NULL CHECK(length(model_id) BETWEEN 1 AND 512),
 state text NOT NULL DEFAULT 'STARTED' CHECK(state IN ('STARTED','RECORDED','USAGE_MISSING','OUTCOME_UNKNOWN')),
 input_tokens bigint CHECK(input_tokens>=0),output_tokens bigint CHECK(output_tokens>=0),
 total_tokens bigint CHECK(total_tokens>=0),cache_read_tokens bigint CHECK(cache_read_tokens>=0),
 cache_write_tokens bigint CHECK(cache_write_tokens>=0),provider_request_id text CHECK(length(provider_request_id)<=128),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),completed_at timestamptz,
 FOREIGN KEY(tenant_id,actor_id) REFERENCES saas.tenant_memberships(tenant_id,user_id)
);
CREATE INDEX model_tokens_actor ON saas.model_token_events(tenant_id,actor_id,created_at);
ALTER TABLE saas.model_token_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE saas.model_token_events FORCE ROW LEVEL SECURITY;
CREATE POLICY scoped_token_events ON saas.model_token_events
 USING(tenant_id=current_setting('radiology.tenant_id',true) AND actor_id::text=current_setting('radiology.user_id',true))
 WITH CHECK(tenant_id=current_setting('radiology.tenant_id',true) AND actor_id::text=current_setting('radiology.user_id',true));
CREATE POLICY platform_token_read ON saas.model_token_events FOR SELECT USING(EXISTS(
 SELECT 1 FROM saas.platform_roles WHERE user_id::text=current_setting('radiology.user_id',true) AND enabled));
CREATE POLICY platform_business_user_read ON saas.users FOR SELECT USING(EXISTS(
 SELECT 1 FROM saas.platform_roles WHERE user_id::text=current_setting('radiology.user_id',true) AND enabled));
CREATE FUNCTION saas.protect_token_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.state<>'STARTED' OR NEW.state='STARTED' OR
  (NEW.event_id,NEW.tenant_id,NEW.actor_id,NEW.model_id,NEW.created_at)
   IS DISTINCT FROM (OLD.event_id,OLD.tenant_id,OLD.actor_id,OLD.model_id,OLD.created_at) THEN
   RAISE EXCEPTION 'Model token event identity/settlement is immutable';
 END IF;
 RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION saas.protect_token_event() FROM PUBLIC;
CREATE TRIGGER token_event_protection BEFORE UPDATE ON saas.model_token_events
 FOR EACH ROW EXECUTE FUNCTION saas.protect_token_event();
