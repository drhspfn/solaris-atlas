"""Add the deterministic speaker-to-character source relation."""

from alembic import op

revision = "0002_speaker_character_crosswalk"
down_revision = "0001_initial_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO ontology.relation_type (id, key, label, category, directional, metadata)
        SELECT COALESCE(MAX(id), 0) + 1, 'references_character', 'References Character', 'source', true, '{}'
        FROM ontology.relation_type
        ON CONFLICT (key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM ontology.relation_type WHERE key = 'references_character'")
