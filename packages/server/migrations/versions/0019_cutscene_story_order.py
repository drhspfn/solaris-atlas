"""Store snapshot-specific cutscene browsing order."""
import sqlalchemy as sa
from alembic import op

revision = "0019_cutscene_story_order"
down_revision = "0018_scenario_story_context"
branch_labels = None
depends_on = None


def upgrade():
    # Fresh databases may already have the table from the current ORM metadata.
    if not sa.inspect(op.get_bind()).has_table("cutscene_story_order", schema="core"):
        op.create_table(
            "cutscene_story_order",
            sa.Column("release_id", sa.BigInteger(), sa.ForeignKey(
                "ops.game_release.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("cutscene_node_id", sa.BigInteger(), sa.ForeignKey(
                "core.cutscene.node_id", ondelete="CASCADE"), primary_key=True),
            sa.Column("quest_rank", sa.Integer(), nullable=True),
            sa.Column("sort_index", sa.Integer(), nullable=False),
            sa.Column("basis", sa.String(32), nullable=False),
            schema="core",
        )
    if not sa.inspect(op.get_bind()).has_table("cutscene_order_revision", schema="core"):
        op.create_table("cutscene_order_revision",
                        sa.Column("id", sa.SmallInteger(), primary_key=True),
                        sa.Column("revision", sa.BigInteger(), nullable=False), schema="core")
    op.execute("INSERT INTO core.cutscene_order_revision (id, revision) VALUES (1, 1) ON CONFLICT (id) DO NOTHING")
    op.execute("""
        INSERT INTO core.cutscene_story_order (release_id, cutscene_node_id, quest_rank, sort_index, basis)
        SELECT release_id, node_id, NULL,
               row_number() OVER (PARTITION BY release_id ORDER BY cg_name, node_id) - 1, 'fallback'
        FROM (
            SELECT DISTINCT revision.release_id, scene.node_id, scene.cg_name
            FROM core.cutscene scene
            JOIN graph.node_revision revision ON revision.node_id = scene.node_id
            WHERE revision.release_id IS NOT NULL
        ) scenes
        ON CONFLICT (release_id, cutscene_node_id) DO NOTHING
    """)


def downgrade():
    op.drop_table("cutscene_story_order", schema="core")
    op.drop_table("cutscene_order_revision", schema="core")
