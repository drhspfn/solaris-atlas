"""Drop duplicate B-tree on localization_value and add missing import/search indexes."""

from alembic import op

revision = "0034_import_performance_indexes"
down_revision = "0033_visual_video_sources"
branch_labels = None
depends_on = None

_CORE_KEY_INDEXES = (
    ("ix_core_character_name_key_id", "character", "name_key_id"),
    ("ix_core_character_nickname_key_id", "character", "nickname_key_id"),
    ("ix_core_location_name_key_id", "location", "name_key_id"),
    ("ix_core_item_name_key_id", "item", "name_key_id"),
    ("ix_core_item_description_key_id", "item", "description_key_id"),
    ("ix_core_speaker_name_key_id", "speaker", "name_key_id"),
    ("ix_core_quest_name_key_id", "quest", "name_key_id"),
    ("ix_core_quest_description_key_id", "quest", "description_key_id"),
    ("ix_core_player_choice_localization_key_id",
     "player_choice", "localization_key_id"),
    ("ix_core_phone_message_localization_key_id",
     "phone_message", "localization_key_id"),
    ("ix_core_narration_localization_key_id", "narration", "localization_key_id"),
)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout='5s'")
    op.drop_constraint(
        "uq_localization_release_key_locale",
        "localization_value",
        schema="i18n",
        type_="unique",
    )
    op.create_index(
        "ix_node_revision_release_node",
        "node_revision",
        ["release_id", "node_id"],
        schema="graph",
        if_not_exists=True,
    )
    for index_name, table_name, column_name in _CORE_KEY_INDEXES:
        op.create_index(
            index_name,
            table_name,
            [column_name],
            schema="core",
            if_not_exists=True,
        )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout='5s'")
    for index_name, table_name, _ in reversed(_CORE_KEY_INDEXES):
        op.drop_index(index_name, table_name=table_name,
                      schema="core", if_exists=True)
    op.drop_index(
        "ix_node_revision_release_node",
        table_name="node_revision",
        schema="graph",
        if_exists=True,
    )
    op.create_unique_constraint(
        "uq_localization_release_key_locale",
        "localization_value",
        ["release_id", "key_id", "locale_id"],
        schema="i18n",
    )
