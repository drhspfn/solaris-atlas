"""Treat frames from one video as one independent retrieval source."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0033_visual_video_sources"
down_revision = "0032_visual_model_search"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.add_column("visual_reference", sa.Column("source_group", sa.String(128)), schema="core")
    op.create_index(
        "ix_visual_reference_source_group", "visual_reference", ["source_group"], schema="core"
    )
    op.create_table(
        "visual_video_frame",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "scan_id",
            sa.BigInteger(),
            sa.ForeignKey("ops.processing_run.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("frame_index", sa.Integer(), nullable=False),
        sa.Column("time", sa.Float(), nullable=False),
        sa.Column(
            "file_id",
            sa.BigInteger(),
            sa.ForeignKey("storage.file_object.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("detections", JSONB(), nullable=False),
        sa.UniqueConstraint("scan_id", "frame_index"),
        schema="ops",
    )


def downgrade():
    op.drop_table("visual_video_frame", schema="ops")
    op.drop_index("ix_visual_reference_source_group", table_name="visual_reference", schema="core")
    op.drop_column("visual_reference", "source_group", schema="core")
