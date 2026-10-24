"""Index localization lookups across imported snapshots without blocking writers."""

from alembic import op
from sqlalchemy import text

revision = "0016_story_read_indexes"
down_revision = "0015_public_slugs"
branch_labels = None
depends_on = None

INDEX = "ix_localization_value_key_locale_release"


def upgrade():
    with op.get_context().autocommit_block():
        valid = op.get_bind().scalar(
            text("""
            SELECT i.indisvalid FROM pg_index i
            JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='i18n' AND c.relname=:name
        """),
            {"name": INDEX},
        )
        if valid is False:
            op.execute(f"DROP INDEX CONCURRENTLY i18n.{INDEX}")
        op.create_index(
            INDEX,
            "localization_value",
            ["key_id", "locale_id", "release_id"],
            schema="i18n",
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index(
            INDEX,
            table_name="localization_value",
            schema="i18n",
            postgresql_concurrently=True,
            if_exists=True,
        )
