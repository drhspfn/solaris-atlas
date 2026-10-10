"""Versioned event archive and schedule occurrences."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012_game_event_archive"
down_revision = "0011_cutscene_transcript"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_event",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("source_id", sa.String(256), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("event_kind", sa.String(32), nullable=False),
        sa.Column("group_label_key", sa.String(256)),
        sa.Column("game_path", sa.String(512)),
        sa.Column("banner_path", sa.String(1024)),
        sa.Column("rewards", postgresql.JSONB()),
        sa.Column("source_url", sa.String(1024), nullable=False),
        sa.Column("source_revision", sa.String(64), nullable=False),
        sa.Column("source_data", postgresql.JSONB(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("source", "source_id", name="uq_game_event_source_identity"),
        schema="core",
    )
    op.create_table(
        "game_event_occurrence",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column("source_occurrence_id", sa.String(256), nullable=False),
        sa.Column("game_version", sa.String(32)),
        sa.Column("server", sa.String(32), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True)),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("banner_path", sa.String(1024)),
        sa.Column("rewards", postgresql.JSONB()),
        sa.Column("season", postgresql.JSONB()),
        sa.Column("source_data", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["core.game_event.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "event_id", "source_occurrence_id", "server", name="uq_game_event_occurrence"
        ),
        schema="core",
    )
    op.create_index(
        "ix_game_event_occurrence_event_id",
        "game_event_occurrence",
        ["event_id"],
        schema="core",
    )
    op.create_index(
        "ix_game_event_occurrence_starts_at",
        "game_event_occurrence",
        ["starts_at"],
        schema="core",
    )
    op.create_index(
        "ix_game_event_occurrence_dates",
        "game_event_occurrence",
        ["starts_at", "ends_at"],
        schema="core",
    )


def downgrade() -> None:
    op.drop_table("game_event_occurrence", schema="core")
    op.drop_table("game_event", schema="core")
