-- Run as the database owner with psql. No password is embedded in this file.
-- Set a password separately using: \password solaris_agent_reader
-- Then configure AGENT_READ_DATABASE_URL with this dedicated login.
\set ON_ERROR_STOP on
DO $$
DECLARE source_schema text;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='solaris_agent_reader') THEN
        CREATE ROLE solaris_agent_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
    END IF;
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO solaris_agent_reader', current_database());
    FOR source_schema IN SELECT nspname FROM pg_namespace
        WHERE nspname IN ('core','raw','graph','i18n','ontology','content','story','search','storage')
    LOOP
        EXECUTE format('GRANT USAGE ON SCHEMA %I TO solaris_agent_reader', source_schema);
        EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO solaris_agent_reader', source_schema);
        EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT SELECT ON TABLES TO solaris_agent_reader', source_schema);
    END LOOP;
END $$;
ALTER ROLE solaris_agent_reader SET default_transaction_read_only=on;
ALTER ROLE solaris_agent_reader SET statement_timeout='5s';
ALTER ROLE solaris_agent_reader SET lock_timeout='1s';
