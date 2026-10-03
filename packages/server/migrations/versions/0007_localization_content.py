"""Share exact localization text across release snapshots.

Run with API and import workers stopped. PostgreSQL retains dropped-column space
until a separately scheduled table rewrite; this migration never runs VACUUM FULL.
"""

from alembic import op
from sqlalchemy import inspect, text

from wuwa_story.db.models.i18n import LocalizationContent

revision = "0007_localization_content"
down_revision = "0006_tile_maps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Initial foundation uses current metadata, including the dictionary.
    if "content" not in {
        column["name"] for column in inspect(bind).get_columns("localization_value", schema="i18n")
    }:
        return
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute("LOCK TABLE i18n.localization_value IN ACCESS EXCLUSIVE MODE")
    mismatch = bind.scalar(
        text("""
        SELECT EXISTS (
            SELECT 1 FROM i18n.localization_value
            WHERE content IS NOT NULL AND content_hash IS NOT NULL
              AND content_hash <> sha256(convert_to(content, 'UTF8'))
        )
    """)
    )
    if mismatch:
        raise ValueError("Localization content hashes are inconsistent; migration aborted")
    LocalizationContent.__table__.create(bind, checkfirst=True)
    op.execute("""
        INSERT INTO i18n.localization_content (content_hash, content)
        SELECT sha256(convert_to(content, 'UTF8')), content
        FROM i18n.localization_value WHERE content IS NOT NULL
        GROUP BY content
    """)
    op.execute("""
        ALTER TABLE i18n.localization_value ADD COLUMN content_id bigint
        REFERENCES i18n.localization_content(id) ON DELETE RESTRICT
    """)
    op.execute("""
        UPDATE i18n.localization_value v SET content_id = c.id
        FROM i18n.localization_content c
        WHERE c.content_hash = sha256(convert_to(v.content, 'UTF8'))
          AND c.content = v.content
    """)
    missing = bind.scalar(
        text("""
        SELECT EXISTS (SELECT 1 FROM i18n.localization_value
                       WHERE content IS NOT NULL AND content_id IS NULL)
    """)
    )
    if missing:
        raise ValueError("Localization backfill is incomplete; migration aborted")
    op.drop_column("localization_value", "content", schema="i18n")
    op.drop_column("localization_value", "content_hash", schema="i18n")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute("LOCK TABLE i18n.localization_value IN ACCESS EXCLUSIVE MODE")
    op.execute("ALTER TABLE i18n.localization_value ADD COLUMN content text")
    op.execute("ALTER TABLE i18n.localization_value ADD COLUMN content_hash bytea")
    op.execute("""
        UPDATE i18n.localization_value v
        SET content = c.content, content_hash = c.content_hash
        FROM i18n.localization_content c WHERE c.id = v.content_id
    """)
    op.drop_column("localization_value", "content_id", schema="i18n")
    op.drop_table("localization_content", schema="i18n")
