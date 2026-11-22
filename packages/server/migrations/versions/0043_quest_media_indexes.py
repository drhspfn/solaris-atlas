"""Index quest media lookups without blocking imports."""

from alembic import op
from sqlalchemy import text

revision = "0043_quest_media_indexes"
down_revision = "0042_projection_perf_idx"
branch_labels = None
depends_on = None

INDEXES = {
    "ix_file_reference_source_path_hash": "USING hash (source_path)",
    "ix_file_reference_type_owner": "USING btree (reference_type, owner_node_id)",
}


def upgrade():
    with op.get_context().autocommit_block():
        for name, columns in INDEXES.items():
            valid = op.get_bind().scalar(text("""
                SELECT i.indisvalid FROM pg_index i
                JOIN pg_class c ON c.oid=i.indexrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='storage' AND c.relname=:name
            """), {"name": name})
            if valid is False:
                op.execute(f"DROP INDEX CONCURRENTLY storage.{name}")
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} "
                       f"ON storage.file_reference {columns}")


def downgrade():
    with op.get_context().autocommit_block():
        for name in INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS storage.{name}")
