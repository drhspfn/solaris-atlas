"""Store complete client event catalog membership separately from schedules."""

import sqlalchemy as sa
from alembic import op

revision = "0035_event_snapshots"
down_revision = "0034_import_performance_indexes"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "game_event_snapshot",
        sa.Column("version", sa.String(32), primary_key=True),
        sa.Column("download_id", sa.String(64), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        schema="core",
    )
    op.create_table(
        "game_event_snapshot_member",
        sa.Column("version", sa.String(32), sa.ForeignKey("core.game_event_snapshot.version", ondelete="CASCADE"), primary_key=True),
        sa.Column("event_id", sa.BigInteger(), sa.ForeignKey("core.game_event.id", ondelete="CASCADE"), primary_key=True),
        schema="core",
    )
    for table in ("game_event_snapshot", "game_event_snapshot_member"):
        op.execute(f"CREATE TRIGGER public_cache_changed AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON core.{table} FOR EACH STATEMENT EXECUTE FUNCTION ops.bump_public_cache_revision()")


def downgrade():
    op.drop_table("game_event_snapshot_member", schema="core")
    op.drop_table("game_event_snapshot", schema="core")
