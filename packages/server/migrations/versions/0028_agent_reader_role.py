"""Prepare the passwordless reader during database deployment."""

from alembic import op

revision = "0028_agent_reader_role"
down_revision = "0027_agent_workspace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SELECT pg_advisory_xact_lock(705,81)")
    op.execute("""
        DO $$ DECLARE source_schema text; BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='solaris_agent_readonly') THEN
                BEGIN
                    CREATE ROLE solaris_agent_readonly NOLOGIN NOSUPERUSER NOCREATEDB
                        NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
                EXCEPTION WHEN duplicate_object OR unique_violation THEN NULL; END;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='solaris_agent_readonly'
                AND (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls))
                OR EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member
                    WHERE r.rolname='solaris_agent_readonly')
                OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname NOT IN ('pg_catalog','information_schema')
                    AND c.relkind IN ('r','p','v','m','f')
                    AND has_table_privilege('solaris_agent_readonly',c.oid,'INSERT,UPDATE,DELETE,TRUNCATE')) THEN
                RAISE EXCEPTION 'Managed reader has unsafe attributes or grants';
            END IF;
            EXECUTE format('GRANT solaris_agent_readonly TO %I WITH ADMIN FALSE, INHERIT FALSE, SET TRUE',
                           current_user);
            FOR source_schema IN SELECT nspname FROM pg_namespace
                WHERE nspname IN ('core','raw','graph','i18n','ontology','content','story','search','storage')
            LOOP
                EXECUTE format('GRANT USAGE ON SCHEMA %I TO solaris_agent_readonly', source_schema);
                EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO solaris_agent_readonly', source_schema);
                EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT SELECT ON TABLES TO solaris_agent_readonly',
                               source_schema);
            END LOOP;
        END $$
    """)


def downgrade() -> None:
    # Cluster-shared role/memberships may serve other databases and live workers.
    # Retain read-only grants; downgrading must not remove shared authorization.
    pass
