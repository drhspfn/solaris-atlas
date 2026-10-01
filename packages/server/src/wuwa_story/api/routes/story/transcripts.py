"""Dialogue search and quest transcript endpoints."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.api.routes.story.shared import (
    _dialogue_payload,
    _quest_info,
    _quest_state_ids,
    _release_id,
)
from wuwa_story.api.routes.story.media import dialogue_media
from wuwa_story.db.models.core import (
    DialogueLine,
    Quest,
    QuestAction,
    QuestState,
    SpeakerEntityLink,
)
from wuwa_story.db.models.graph import Edge, Node
from wuwa_story.db.models.i18n import Locale, LocalizationKey, LocalizationValue
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.models.story import Scene
from wuwa_story.db.session import get_session

router = APIRouter(tags=["story browsing"])

@router.get("/dialogue/search")
async def search_dialogue(
    q: str = Query(min_length=1, max_length=512),
    character: str | None = None,
    quest_id: int | None = None,
    locale: str = "en",
    game_version: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    release_id = await _release_id(session, game_version)
    locale_row = await session.scalar(select(Locale).where(Locale.code == locale))
    if locale_row is None:
        raise HTTPException(status_code=400, detail=f"unknown locale: {locale}")
    statement = (
        select(DialogueLine, QuestAction, QuestState, Node, SourceRecord, SourceFile)
        .join(Node, Node.id == DialogueLine.node_id)
        .outerjoin(QuestAction, QuestAction.node_id == DialogueLine.action_node_id)
        .outerjoin(QuestState, QuestState.node_id == QuestAction.quest_state_node_id)
        .outerjoin(SourceRecord, SourceRecord.id == DialogueLine.source_record_id)
        .outerjoin(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .outerjoin(LocalizationKey, LocalizationKey.id == DialogueLine.localization_key_id)
        .outerjoin(
            LocalizationValue,
            and_(
                LocalizationValue.key_id == DialogueLine.localization_key_id,
                LocalizationValue.locale_id == locale_row.id,
                LocalizationValue.release_id == release_id,
            ),
        )
        .where(
            or_(LocalizationValue.content.ilike(f"%{q}%"), DialogueLine.inline_text.ilike(f"%{q}%"))
        )
    )
    if character:
        character_node = await session.scalar(select(Node).where(Node.canonical_key == character))
        if character_node is None:
            raise HTTPException(status_code=404, detail="character not found")
        speaker_ids = select(SpeakerEntityLink.speaker_node_id).where(
            SpeakerEntityLink.entity_node_id == character_node.id
        )
        statement = statement.where(DialogueLine.speaker_node_id.in_(speaker_ids))
    if quest_id is not None:
        quest = await session.scalar(select(Quest).where(Quest.game_quest_id == quest_id))
        if quest is None:
            raise HTTPException(status_code=404, detail="quest not found")
        states = await _quest_state_ids(session, quest.node_id)
        statement = statement.where(QuestAction.quest_state_node_id.in_(states))
    statement = (
        statement.order_by(
            QuestState.state_key,
            QuestAction.action_index,
            DialogueLine.source_index,
            DialogueLine.node_id,
        )
        .offset(offset)
        .limit(limit)
    )
    rows = list((await session.execute(statement)).all())
    media_by_line = await dialogue_media(session, [row[0].node_id for row in rows], release_id)
    return {
        "query": q,
        "locale": locale,
        "game_version": game_version,
        "total_returned": len(rows),
        "offset": offset,
        "limit": limit,
        "results": [
            {**await _dialogue_payload(session, row, locale_code=locale, release_id=release_id),
             "media": media_by_line.get(row[0].node_id, {"voice_references": [], "audio_event_paths": []})}
            for row in rows
        ],
    }

@router.get("/quests/{game_quest_id}/transcript")
async def quest_transcript(
    game_quest_id: int,
    character: str | None = None,
    q: str | None = Query(default=None, max_length=512),
    locale: str = "en",
    game_version: str | None = None,
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    quest = await session.scalar(select(Quest).where(Quest.game_quest_id == game_quest_id))
    if quest is None:
        raise HTTPException(status_code=404, detail="quest not found")
    states = await _quest_state_ids(session, quest.node_id)
    release_id = await _release_id(session, game_version)
    statement = (
        select(DialogueLine, QuestAction, QuestState, Node, SourceRecord, SourceFile)
        .join(QuestAction, QuestAction.node_id == DialogueLine.action_node_id)
        .join(QuestState, QuestState.node_id == QuestAction.quest_state_node_id)
        .join(Node, Node.id == DialogueLine.node_id)
        .outerjoin(SourceRecord, SourceRecord.id == DialogueLine.source_record_id)
        .outerjoin(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .where(QuestAction.quest_state_node_id.in_(states))
    )
    if character:
        character_node = await session.scalar(select(Node).where(Node.canonical_key == character))
        if character_node is None:
            raise HTTPException(status_code=404, detail="character not found")
        speaker_ids = select(SpeakerEntityLink.speaker_node_id).where(
            SpeakerEntityLink.entity_node_id == character_node.id
        )
        statement = statement.where(DialogueLine.speaker_node_id.in_(speaker_ids))
    if q:
        locale_row = await session.scalar(select(Locale).where(Locale.code == locale))
        value = (
            select(LocalizationValue.key_id)
            .join(LocalizationKey, LocalizationKey.id == LocalizationValue.key_id)
            .where(
                LocalizationValue.release_id == release_id,
                LocalizationValue.locale_id == locale_row.id if locale_row else False,
                LocalizationValue.content.ilike(f"%{q}%"),
            )
        )
        statement = statement.where(
            or_(
                DialogueLine.localization_key_id.in_(value),
                DialogueLine.inline_text.ilike(f"%{q}%"),
            )
        )
    statement = (
        statement.order_by(
            QuestState.state_key,
            QuestAction.action_index,
            DialogueLine.source_index,
            DialogueLine.node_id,
        )
        .offset(offset)
        .limit(limit)
    )
    rows = list((await session.execute(statement)).all())
    plot_steps = (
        select(Edge.to_node_id)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .where(Edge.from_node_id == quest.node_id, RelationType.key == "has_plot_step")
    )
    scene_ids = (
        select(Edge.to_node_id)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .where(Edge.from_node_id.in_(plot_steps), RelationType.key == "presents_scene")
    )
    scene_rows = await session.scalars(
        select(Scene)
        .where(Scene.node_id.in_(scene_ids))
        .order_by(Scene.authored_order, Scene.node_id)
    )
    media_by_line = await dialogue_media(session, [row[0].node_id for row in rows], release_id)
    return {
        "quest": await _quest_info(session, quest, locale, game_version),
        "ordering_semantics": "Authored state/action/talk ordering; branches and runtime conditions are not flattened into a guaranteed playthrough.",
        "scenes": [
            {
                "canonical_key": (await session.get(Node, scene.node_id)).canonical_key,
                "title": scene.title,
                "authored_order": scene.authored_order,
                "ordering_basis": scene.source_basis,
            }
            for scene in scene_rows
        ],
        "filters": {"character": character, "text": q, "locale": locale},
        "offset": offset,
        "limit": limit,
        "lines_returned": len(rows),
        "lines": [
            {**await _dialogue_payload(session, row, locale_code=locale, release_id=release_id),
             "media": media_by_line.get(row[0].node_id, {"voice_references": [], "audio_event_paths": []})}
            for row in rows
        ],
    }
