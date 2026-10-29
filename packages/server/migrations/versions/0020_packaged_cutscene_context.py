"""Register package-backed quest-to-cutscene context."""
from alembic import op

revision = "0020_packaged_cutscene_context"
down_revision = "0019_cutscene_story_order"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute(
        "INSERT INTO ontology.relation_type (id,key,label,category,directional) "
        "SELECT COALESCE(MAX(id),0)+1,'references_packaged_cutscene',"
        "'Related video package','source',true FROM ontology.relation_type "
        "ON CONFLICT (key) DO NOTHING"
    )


def downgrade():
    # Preserve existing evidence; older readers ignore this relation.
    pass
