-- Hospital admins can serialize admissions without permission to change seats.
CREATE FUNCTION saas.lock_seats(p_tenant text)
 RETURNS TABLE(max_users integer,allow_hospital_admin_create_users boolean,version bigint)
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,saas AS $$
BEGIN
 IF p_tenant IS DISTINCT FROM current_setting('radiology.tenant_id',true) OR
    NOT saas.hospital_admin(p_tenant,current_setting('radiology.user_id',true)) THEN
   RAISE EXCEPTION 'Hospital administrator required' USING ERRCODE='42501';
 END IF;
 RETURN QUERY SELECT s.max_users,s.allow_hospital_admin_create_users,s.version
   FROM saas.tenant_settings s WHERE s.tenant_id=p_tenant FOR UPDATE;
END;
$$;
REVOKE ALL ON FUNCTION saas.lock_seats(text) FROM PUBLIC;
