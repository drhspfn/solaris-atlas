"""Retain entity display keys for characters, items, and areas."""

import sqlalchemy as sa
from alembic import op

revision = "0003_entity_search_keys"
down_revision = "0002_speaker_character_crosswalk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "character",
        sa.Column("nickname_key_id", sa.BigInteger(), nullable=True),
        schema="core",
    )
    op.create_foreign_key(
        "fk_character_nickname_key_id_localization_key",
        "character",
        "localization_key",
        ["nickname_key_id"],
        ["id"],
        source_schema="core",
        referent_schema="i18n",
        ondelete="SET NULL",
    )
    op.add_column(
        "item",
        sa.Column("description_key_id", sa.BigInteger(), nullable=True),
        schema="core",
    )
    op.create_foreign_key(
        "fk_item_description_key_id_localization_key",
        "item",
        "localization_key",
        ["description_key_id"],
        ["id"],
        source_schema="core",
        referent_schema="i18n",
        ondelete="SET NULL",
    )
    op.add_column(
        "location",
        sa.Column("name_key_id", sa.BigInteger(), nullable=True),
        schema="core",
    )
    op.create_foreign_key(
        "fk_location_name_key_id_localization_key",
        "location",
        "localization_key",
        ["name_key_id"],
        ["id"],
        source_schema="core",
        referent_schema="i18n",
        ondelete="SET NULL",
    )
    op.execute(
        """
        INSERT INTO ontology.relation_type (id, key, label, category, directional, metadata)
        SELECT COALESCE(MAX(id), 0) + 1, 'references_npc', 'References NPC', 'source', true, '{}'
        FROM ontology.relation_type
        ON CONFLICT (key) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO ontology.relation_type (id, key, label, category, directional, metadata)
        SELECT COALESCE(MAX(id), 0) + 1, 'decomposes_into', 'Decomposes Into', 'source', true, '{}'
        FROM ontology.relation_type
        ON CONFLICT (key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM ontology.relation_type WHERE key = 'decomposes_into'")
    op.execute("DELETE FROM ontology.relation_type WHERE key = 'references_npc'")
    op.drop_constraint("fk_location_name_key_id_localization_key", "location", schema="core")
    op.drop_column("location", "name_key_id", schema="core")
    op.drop_constraint("fk_item_description_key_id_localization_key", "item", schema="core")
    op.drop_column("item", "description_key_id", schema="core")
    op.drop_constraint("fk_character_nickname_key_id_localization_key", "character", schema="core")
    op.drop_column("character", "nickname_key_id", schema="core")
