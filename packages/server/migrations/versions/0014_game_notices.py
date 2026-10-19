"""Archive the public launcher/game notice feed and immutable revisions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0014_game_notices"
down_revision = "0013_lore_chunks"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_table(
        "game_notice",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("feed", sa.String(256), nullable=False),
        sa.Column("locale", sa.String(16), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("revision", sa.String(64), nullable=False),
        sa.Column("content", JSONB(), nullable=False),
        sa.Column("related_event_ids", JSONB(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("feed", "locale", "source_id", name="uq_game_notice_identity"),
        schema="core",
    )
    for column in ("category", "published_at"):
        op.create_index(f"ix_game_notice_{column}", "game_notice", [column], schema="core")
    op.create_table(
        "game_notice_revision",
        sa.Column(
            "notice_id",
            sa.BigInteger(),
            sa.ForeignKey("core.game_notice.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("revision", sa.String(64), primary_key=True),
        sa.Column("content", JSONB(), nullable=False),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema="core",
    )
    for table in ("game_notice", "game_notice_revision", "game_event", "game_event_occurrence"):
        op.execute(
            f"INSERT INTO ops.public_cache_revision VALUES ('core.{table}', txid_current()) ON CONFLICT DO NOTHING"
        )
        op.execute(
            f"CREATE TRIGGER public_cache_changed AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON core.{table} FOR EACH STATEMENT EXECUTE FUNCTION ops.bump_public_cache_revision()"
        )


def downgrade():
    for table in ("game_notice", "game_notice_revision", "game_event", "game_event_occurrence"):
        op.execute(f"DROP TRIGGER IF EXISTS public_cache_changed ON core.{table}")
        op.execute(f"DELETE FROM ops.public_cache_revision WHERE table_name='core.{table}'")
    op.drop_table("game_notice_revision", schema="core")
    op.drop_table("game_notice", schema="core")
