"""Register inferred scenario context without changing existing evidence."""
from alembic import op

revision = "0018_scenario_story_context"
down_revision = "0017_dungeon_story_context"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute(
        "INSERT INTO ontology.relation_type (id,key,label,category,directional) "
        "SELECT COALESCE(MAX(id),0)+1,'references_scenario_flow_state',"
        "'Inferred scenario context','source',true FROM ontology.relation_type "
        "ON CONFLICT (key) DO NOTHING"
    )


def downgrade():
    # Older readers ignore this relation; preserve its evidence.
    pass
