"""Retain entity display keys for characters, items, and areas."""

import sqlalchemy as sa
from alembic import op

revision = "0003_entity_search_keys"
down_revision = "0002_speaker_character_crosswalk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Revision 0001 creates the current SQLAlchemy metadata, which may already
    # include these later-added columns and foreign keys on a fresh install.
    inspector = sa.inspect(op.get_bind())
    for table, column, constraint in (
        ("character", "nickname_key_id", "fk_character_nickname_key_id_localization_key"),
        ("item", "description_key_id", "fk_item_description_key_id_localization_key"),
        ("location", "name_key_id", "fk_location_name_key_id_localization_key"),
    ):
        columns = {entry["name"] for entry in inspector.get_columns(table, schema="core")}
        if column not in columns:
            op.add_column(table, sa.Column(column, sa.BigInteger(), nullable=True), schema="core")
        foreign_keys = inspector.get_foreign_keys(table, schema="core")
        has_reference = any(
            entry.get("constrained_columns") == [column]
            and entry.get("referred_schema") == "i18n"
            and entry.get("referred_table") == "localization_key"
            and entry.get("referred_columns") == ["id"]
            for entry in foreign_keys
        )
        if not has_reference:
            op.create_foreign_key(
                constraint,
                table,
                "localization_key",
                [column],
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
