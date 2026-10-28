"""Register explicit dungeon scenario context, preserving imported provenance."""

from alembic import op

revision = "0017_dungeon_story_context"
down_revision = "0016_story_read_indexes"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute(
        "INSERT INTO ontology.relation_type (id,key,label,category,directional) "
        "SELECT COALESCE(MAX(id),0)+1,'references_dungeon_flow_state',"
        "'Dungeon scenario context','source',true FROM ontology.relation_type "
        "ON CONFLICT (key) DO NOTHING"
    )


def downgrade():
    # Older readers ignore the relation; do not erase imported evidence.
    pass
