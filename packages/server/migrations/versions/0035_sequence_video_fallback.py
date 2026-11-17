"""Store administrator-selected recordings independently of imported sources."""

import sqlalchemy as sa
from alembic import op

revision = "0035_sequence_video_fallback"
down_revision = "0034_import_performance_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sequence_video_fallback",
        sa.Column("occurrence_id", sa.String(64), primary_key=True),
        sa.Column("video_id", sa.String(11), nullable=False),
        sa.Column("start_seconds", sa.Integer, nullable=False),
        sa.Column("end_seconds", sa.Integer),
        sa.CheckConstraint("start_seconds >= 0", name="ck_sequence_video_start"),
        sa.CheckConstraint("end_seconds IS NULL OR end_seconds > start_seconds",
                           name="ck_sequence_video_end"),
        schema="ops",
    )
    op.execute("INSERT INTO ops.public_cache_revision VALUES ('ops.sequence_video_fallback', txid_current())")
    op.execute("CREATE TRIGGER public_cache_changed AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE "
               "ON ops.sequence_video_fallback FOR EACH STATEMENT "
               "EXECUTE FUNCTION ops.bump_public_cache_revision()")


def downgrade() -> None:
    op.drop_table("sequence_video_fallback", schema="ops")
    op.execute("DELETE FROM ops.public_cache_revision WHERE table_name='ops.sequence_video_fallback'")
