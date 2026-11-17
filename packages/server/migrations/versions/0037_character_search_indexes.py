"""Index character title and exact alias search without blocking writers."""

from alembic import op
from sqlalchemy import text

revision = "0037_character_search_indexes"
down_revision = "0036_event_snapshots"
branch_labels = None
depends_on = None

INDEXES = {
    "ix_character_read_title_fts": "to_tsvector('simple'::regconfig, title)",
    "ix_character_read_alias_keys": "(card['alias_keys'])",
}


def upgrade():
    # A cancelled concurrent build leaves an invalid index that IF NOT EXISTS
    # alone would silently retain. Each index is independently retryable.
    with op.get_context().autocommit_block():
        for name, expression in INDEXES.items():
            valid = op.get_bind().scalar(text("""
                SELECT i.indisvalid FROM pg_index i
                JOIN pg_class c ON c.oid=i.indexrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='core' AND c.relname=:name
            """), {"name": name})
            if valid is False:
                op.execute(f"DROP INDEX CONCURRENTLY core.{name}")
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} "
                       f"ON core.character_read USING gin ({expression})")


def downgrade():
    with op.get_context().autocommit_block():
        for name in INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS core.{name}")
