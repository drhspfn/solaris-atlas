"""Source-verified cutscene transcript ownership."""

from alembic import op

revision = "0011_cutscene_transcript"
down_revision = "0010_agent_revisit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute(
        "INSERT INTO ontology.relation_type (id, key, label, category, directional) "
        "SELECT COALESCE(MAX(id), 0) + 1, 'has_transcript_state', "
        "'Has transcript state', 'source', true FROM ontology.relation_type "
        "ON CONFLICT (key) DO NOTHING"
    )


def downgrade() -> None:
    # Keep imported evidence; older application versions ignore this relation.
    pass
