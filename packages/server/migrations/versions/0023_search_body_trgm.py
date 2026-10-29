"""Index document bodies for trigram search without blocking writers."""

from alembic import op
from sqlalchemy import text

revision = "0023_search_body_trgm"
down_revision = "0022_tree_index_schema_version"
branch_labels = None
depends_on = None

INDEX = "ix_search_document_body_trgm"


def upgrade():
    with op.get_context().autocommit_block():
        valid = op.get_bind().scalar(
            text("""
            SELECT i.indisvalid FROM pg_index i
            JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='search' AND c.relname=:name
        """),
            {"name": INDEX},
        )
        if valid is False:
            op.execute(f"DROP INDEX CONCURRENTLY search.{INDEX}")
        op.create_index(
            INDEX,
            "document",
            ["body"],
            schema="search",
            postgresql_using="gin",
            postgresql_ops={"body": "gin_trgm_ops"},
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index(
            INDEX,
            table_name="document",
            schema="search",
            postgresql_concurrently=True,
            if_exists=True,
        )
