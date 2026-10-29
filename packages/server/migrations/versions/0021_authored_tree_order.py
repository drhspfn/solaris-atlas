"""Store source-versioned quest and LevelPlay tree positions."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0021_authored_tree_order"
down_revision = "0020_packaged_cutscene_context"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '10s'")
    if not sa.inspect(op.get_bind()).has_table("authored_tree_order", schema="core"):
        op.create_table(
            "authored_tree_order",
            sa.Column("asset_version", sa.String(32), primary_key=True),
            sa.Column("tree_kind", sa.String(16), primary_key=True),
            sa.Column("tree_id", sa.BigInteger(), primary_key=True),
            sa.Column("source_path", sa.String(1024), nullable=False),
            sa.Column("source_sha256", sa.LargeBinary(), nullable=False),
            sa.Column("nodes", JSONB(), nullable=False),
            schema="core",
        )


def downgrade():
    op.drop_table("authored_tree_order", schema="core")
