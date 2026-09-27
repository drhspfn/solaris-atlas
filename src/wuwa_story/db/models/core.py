from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from wuwa_story.db.base import Base, MetadataMixin


class Character(MetadataMixin, Base):
    __tablename__ = "character"
    __table_args__ = ({"schema": "core"},)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    canonical_name: Mapped[str | None] = mapped_column(String(256))
    name_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("i18n.localization_key.id", ondelete="SET NULL")
    )
    nickname_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("i18n.localization_key.id", ondelete="SET NULL")
    )
    description: Mapped[str | None] = mapped_column(Text)
    playable: Mapped[bool | None] = mapped_column(Boolean)


class Organization(MetadataMixin, Base):
    __tablename__ = "organization"
    __table_args__ = ({"schema": "core"},)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    canonical_name: Mapped[str | None] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)


class Location(MetadataMixin, Base):
    __tablename__ = "location"
    __table_args__ = ({"schema": "core"},)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    canonical_name: Mapped[str | None] = mapped_column(String(256))
    name_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("i18n.localization_key.id", ondelete="SET NULL")
    )
    game_location_id: Mapped[str | None] = mapped_column(String(256), index=True)
    parent_location_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("core.location.node_id", ondelete="SET NULL")
    )


class Item(MetadataMixin, Base):
    __tablename__ = "item"
    __table_args__ = (
        UniqueConstraint("game_item_id", name="uq_core_item_game_id"),
        {"schema": "core"},
    )
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    canonical_name: Mapped[str | None] = mapped_column(String(256))
    name_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("i18n.localization_key.id", ondelete="SET NULL")
    )
    description_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("i18n.localization_key.id", ondelete="SET NULL")
    )
    description: Mapped[str | None] = mapped_column(Text)
    game_item_id: Mapped[int | None] = mapped_column(BigInteger)


class Term(MetadataMixin, Base):
    __tablename__ = "term"
    __table_args__ = ({"schema": "core"},)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    canonical_name: Mapped[str | None] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)


class Concept(MetadataMixin, Base):
    __tablename__ = "concept"
    __table_args__ = ({"schema": "core"},)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    canonical_name: Mapped[str | None] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)


class Speaker(MetadataMixin, Base):
    __tablename__ = "speaker"
    __table_args__ = ({"schema": "core"},)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    game_speaker_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    name_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("i18n.localization_key.id", ondelete="SET NULL")
    )


class NPC(MetadataMixin, Base):
    __tablename__ = "npc"
    __table_args__ = ({"schema": "core"},)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    game_npc_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    source_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw.source_record.id", ondelete="SET NULL")
    )


class SpeakerEntityLink(MetadataMixin, Base):
    __tablename__ = "speaker_entity_link"
    __table_args__ = (
        CheckConstraint(
            "resolution_type IN ('explicit_crosswalk','unique_external_reference','manual_verified')",
            name="valid_resolution_type",
        ),
        {"schema": "core"},
    )
    speaker_node_id: Mapped[int] = mapped_column(
        ForeignKey("core.speaker.node_id", ondelete="CASCADE"), primary_key=True
    )
    entity_node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    resolution_type: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float | None] = mapped_column()
    source_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw.source_record.id", ondelete="SET NULL")
    )


class Quest(MetadataMixin, Base):
    __tablename__ = "quest"
    __table_args__ = (
        UniqueConstraint("game_quest_id", name="uq_core_quest_game_id"),
        {"schema": "core"},
    )
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    game_quest_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    name_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("i18n.localization_key.id", ondelete="SET NULL")
    )
    description_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("i18n.localization_key.id", ondelete="SET NULL")
    )
    quest_type: Mapped[str | None] = mapped_column(String(64))
    region_id: Mapped[str | None] = mapped_column(String(128))


class QuestNode(MetadataMixin, Base):
    __tablename__ = "quest_node"
    __table_args__ = ({"schema": "core"},)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    game_node_id: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    game_quest_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    node_type: Mapped[str | None] = mapped_column(String(64))
    source_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw.source_record.id", ondelete="SET NULL")
    )


class QuestState(MetadataMixin, Base):
    __tablename__ = "quest_state"
    __table_args__ = (Index("ix_quest_state_state_key", "state_key"), {"schema": "core"})
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    state_key: Mapped[str] = mapped_column(String(512), nullable=False)
    flow_list_name: Mapped[str | None] = mapped_column(String(256))
    flow_id: Mapped[int | None] = mapped_column(BigInteger)
    state_id: Mapped[int | None] = mapped_column(BigInteger)
    source_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw.source_record.id", ondelete="SET NULL")
    )


class QuestAction(MetadataMixin, Base):
    __tablename__ = "quest_action"
    __table_args__ = (
        UniqueConstraint("quest_state_node_id", "action_index", name="uq_quest_action_state_index"),
        {"schema": "core"},
    )
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    quest_state_node_id: Mapped[int] = mapped_column(
        ForeignKey("core.quest_state.node_id", ondelete="CASCADE"), nullable=False, index=True
    )
    action_id: Mapped[str | None] = mapped_column(String(256))
    action_guid: Mapped[str | None] = mapped_column(String(256))
    action_name: Mapped[str] = mapped_column(String(128), nullable=False)
    action_index: Mapped[int] = mapped_column(Integer, nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    source_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw.source_record.id", ondelete="SET NULL")
    )


class DialogueLine(MetadataMixin, Base):
    __tablename__ = "dialogue_line"
    __table_args__ = (
        Index("ix_dialogue_speaker", "speaker_node_id"),
        Index("ix_dialogue_localization_key", "localization_key_id"),
        {"schema": "core"},
    )
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    action_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("core.quest_action.node_id", ondelete="SET NULL")
    )
    speaker_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("core.speaker.node_id", ondelete="SET NULL")
    )
    localization_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("i18n.localization_key.id", ondelete="SET NULL")
    )
    inline_text: Mapped[str | None] = mapped_column(Text)
    game_talk_item_id: Mapped[str | None] = mapped_column(String(512))
    game_text_id: Mapped[str | None] = mapped_column(String(512))
    source_type: Mapped[str | None] = mapped_column(String(64))
    source_index: Mapped[int | None] = mapped_column(Integer)
    source_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw.source_record.id", ondelete="SET NULL")
    )


class PlayerChoice(MetadataMixin, Base):
    __tablename__ = "player_choice"
    __table_args__ = ({"schema": "core"},)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    localization_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("i18n.localization_key.id", ondelete="SET NULL")
    )
    source_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw.source_record.id", ondelete="SET NULL")
    )


class PhoneMessage(MetadataMixin, Base):
    __tablename__ = "phone_message"
    __table_args__ = ({"schema": "core"},)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    conversation_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("core.conversation.node_id", ondelete="SET NULL")
    )
    speaker_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("core.speaker.node_id", ondelete="SET NULL")
    )
    localization_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("i18n.localization_key.id", ondelete="SET NULL")
    )
    source_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw.source_record.id", ondelete="SET NULL")
    )


class Narration(MetadataMixin, Base):
    __tablename__ = "narration"
    __table_args__ = ({"schema": "core"},)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    localization_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("i18n.localization_key.id", ondelete="SET NULL")
    )
    narration_type: Mapped[str | None] = mapped_column(String(64))
    source_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw.source_record.id", ondelete="SET NULL")
    )


class Conversation(MetadataMixin, Base):
    __tablename__ = "conversation"
    __table_args__ = ({"schema": "core"},)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    quest_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("core.quest.node_id", ondelete="SET NULL")
    )
    scene_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("story.scene.node_id", ondelete="SET NULL")
    )


class ConversationMember(MetadataMixin, Base):
    __tablename__ = "conversation_member"
    __table_args__ = ({"schema": "core"},)
    conversation_node_id: Mapped[int] = mapped_column(
        ForeignKey("core.conversation.node_id", ondelete="CASCADE"), primary_key=True
    )
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    order_index: Mapped[int | None] = mapped_column(Integer)
    ordering_basis: Mapped[str] = mapped_column(String(32), nullable=False)


class MediaAsset(MetadataMixin, Base):
    __tablename__ = "media_asset"
    __table_args__ = ({"schema": "core"},)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    media_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    original_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("storage.file_object.id", ondelete="SET NULL")
    )
    preferred_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("storage.file_object.id", ondelete="SET NULL")
    )
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    bitrate: Mapped[int | None] = mapped_column(Integer)


class VoiceReference(MetadataMixin, Base):
    __tablename__ = "voice_reference"
    __table_args__ = ({"schema": "core"},)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    plot_audio_id: Mapped[str | None] = mapped_column(String(512))
    file_name: Mapped[str | None] = mapped_column(String(1024))
    media_asset_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("core.media_asset.node_id", ondelete="SET NULL")
    )


class AudioEvent(MetadataMixin, Base):
    __tablename__ = "audio_event"
    __table_args__ = ({"schema": "core"},)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    event_path: Mapped[str | None] = mapped_column(String(1024))
    source_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw.source_record.id", ondelete="SET NULL")
    )


class Cutscene(MetadataMixin, Base):
    __tablename__ = "cutscene"
    __table_args__ = ({"schema": "core"},)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    cg_name: Mapped[str | None] = mapped_column(String(256))
    cg_file: Mapped[str | None] = mapped_column(String(1024))
    media_asset_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("core.media_asset.node_id", ondelete="SET NULL")
    )
