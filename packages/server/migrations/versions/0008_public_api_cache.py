"""Transactional cache revisions for public content, including worker writes."""

from alembic import op

revision = "0008_public_api_cache"
down_revision = "0007_localization_content"
branch_labels = None
depends_on = None

# Never watch auth or job bookkeeping. Each content table has its own row so
# independent writers do not contend on one global revision lock.
TABLES_SQL = """
SELECT schemaname, tablename FROM pg_tables
WHERE schemaname IN ('storage', 'raw', 'i18n', 'graph', 'core',
                     'ontology', 'story', 'content', 'search')
   OR (schemaname = 'ops' AND tablename IN ('game_release', 'import_run', 'status_type'))
ORDER BY schemaname, tablename
"""


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute("""
        CREATE TABLE ops.public_cache_revision (
            table_name text PRIMARY KEY,
            transaction_id bigint NOT NULL
        )
    """)
    op.execute("""
        CREATE FUNCTION ops.bump_public_cache_revision() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            INSERT INTO ops.public_cache_revision AS revision VALUES (
                TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME, txid_current())
            ON CONFLICT (table_name) DO UPDATE
                SET transaction_id = EXCLUDED.transaction_id
                WHERE revision.transaction_id <> EXCLUDED.transaction_id;
            RETURN NULL;
        END;
        $$
    """)
    # Statement triggers have constant cost for a batch of millions of rows.
    # Both the data and its revision become visible at the same commit.
    op.execute(f"""
        DO $$ DECLARE t record; BEGIN
            FOR t IN {TABLES_SQL} LOOP
                INSERT INTO ops.public_cache_revision VALUES (
                    t.schemaname || '.' || t.tablename, txid_current());
                EXECUTE format('CREATE TRIGGER public_cache_changed '
                    'AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON %I.%I '
                    'FOR EACH STATEMENT EXECUTE FUNCTION ops.bump_public_cache_revision()',
                    t.schemaname, t.tablename);
            END LOOP;
        END $$
    """)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute(f"""
        DO $$ DECLARE t record; BEGIN
            FOR t IN {TABLES_SQL} LOOP
                EXECUTE format('DROP TRIGGER IF EXISTS public_cache_changed ON %I.%I',
                               t.schemaname, t.tablename);
            END LOOP;
        END $$
    """)
    op.execute("DROP FUNCTION ops.bump_public_cache_revision()")
    op.execute("DROP TABLE ops.public_cache_revision")
