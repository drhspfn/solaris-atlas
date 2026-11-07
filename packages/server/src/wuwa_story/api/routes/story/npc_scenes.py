"""Paged, source-backed interaction scenes for NPC families."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.api.public_addresses import public_addresses
from wuwa_story.api.routes.story.npc_profile import (
    interaction_dialogue,
    npc_flow_state_ids,
    npc_record,
)
from wuwa_story.db.models.core import DialogueLine, Quest, QuestState
from wuwa_story.db.models.graph import Node
from wuwa_story.db.models.i18n import Locale
from wuwa_story.db.repositories.quest_scope import quest_state_links
from wuwa_story.db.session import get_session
from wuwa_story.story.service import (
    _localized,
    _node_label,
    _quest_info,
    prefetch_localizations,
    prefetch_node_labels,
)

router = APIRouter(tags=["story browsing"])


def scene_preview_rows(release_id, state_ids):
    """Limit source dialogue per requested state with deterministic ordering."""
    lines = interaction_dialogue(release_id, state_ids).subquery()
    ranked = (
        select(
            lines.c.state_id,
            lines.c.line_id,
            lines.c.speaker_id,
            func.row_number()
            .over(partition_by=lines.c.state_id, order_by=(Node.canonical_key, lines.c.speaker_id))
            .label("position"),
        )
        .join(Node, Node.id == lines.c.line_id)
        .subquery()
    )
    return select(ranked).where(ranked.c.position <= 3).subquery()


@router.get("/npcs/{canonical_key:path}/scenes")
@public_addresses
async def npc_scenes(
    canonical_key: str,
    locale: str = "en",
    game_version: str | None = None,
    limit: int = Query(8, ge=1, le=50),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    node, _, npc, release_id = await npc_record(session, canonical_key, game_version)
    if await session.scalar(select(Locale.id).where(Locale.code == locale)) is None:
        raise HTTPException(400, "unknown locale")
    states = await npc_flow_state_ids(session, node.id, npc, release_id) if release_id else []
    total = len(states)
    page = states[offset : offset + limit]
    if not page:
        return {"results": [], "total": total, "limit": limit, "offset": offset}

    state_names = dict(
        (
            await session.execute(
                select(QuestState.node_id, QuestState.state_key).where(QuestState.node_id.in_(page))
            )
        ).all()
    )
    lines = interaction_dialogue(release_id, page).subquery()
    participants = list(
        await session.execute(
            select(lines.c.state_id, lines.c.speaker_id)
            .where(lines.c.speaker_id.is_not(None))
            .distinct()
            .order_by(lines.c.state_id, lines.c.speaker_id)
        )
    )
    ranked = scene_preview_rows(release_id, page)
    previews = list(
        await session.execute(
            select(DialogueLine, ranked.c.state_id, ranked.c.speaker_id)
            .join(ranked, ranked.c.line_id == DialogueLine.node_id)
            .order_by(ranked.c.state_id, ranked.c.position)
        )
    )
    speaker_ids = {speaker for _, speaker in participants}
    speaker_ids.update(speaker for _, _, speaker in previews if speaker is not None)
    await prefetch_node_labels(session, sorted(speaker_ids), locale)
    await prefetch_localizations(
        session, [line.localization_key_id for line, _, _ in previews], locale, release_id
    )
    labels = {
        speaker: await _node_label(session, speaker, locale) for speaker in sorted(speaker_ids)
    }
    speakers_by_state = {identity: [] for identity in page}
    for identity, speaker in participants:
        speakers_by_state[identity].append(labels[speaker])
    previews_by_state = {identity: [] for identity in page}
    for line, identity, speaker in previews:
        localized = await _localized(session, line.localization_key_id, locale, release_id)
        value = (localized or {}).get("content") or line.inline_text or ""
        previews_by_state[identity].append({"text": value, "speaker": labels.get(speaker)})

    links = quest_state_links(release_id, state_ids=page)
    associations = list(
        await session.execute(
            select(links.c.quest_id, links.c.state_id).order_by(links.c.state_id, links.c.quest_id)
        )
    )
    quest_ids = {quest_id for quest_id, _ in associations}
    quests = (
        list(await session.scalars(select(Quest).where(Quest.node_id.in_(quest_ids))))
        if quest_ids
        else []
    )
    quest_info = {
        quest.node_id: await _quest_info(session, quest, locale, game_version) for quest in quests
    }
    quests_by_state = {identity: [] for identity in page}
    for quest_id, identity in associations:
        if quest_id in quest_info:
            quests_by_state[identity].append(quest_info[quest_id])

    results = []
    for identity in page:
        scene_quests = quests_by_state[identity]
        title = next(
            (
                (quest.get("name") or {}).get("content")
                for quest in scene_quests
                if isinstance(quest.get("name"), dict)
                and (quest["name"].get("content") or "").strip()
            ),
            None,
        )
        if not title:
            title = next(
                (
                    line["text"].strip()[:120]
                    for line in previews_by_state[identity]
                    if line["text"].strip()
                ),
                "Conversation",
            )
        results.append(
            {
                "id": identity,
                "state_id": identity,
                "title": title,
                "speakers": speakers_by_state[identity],
                "preview": previews_by_state[identity],
                "quests": scene_quests,
                "flow_state": state_names.get(identity),
            }
        )
    return {"results": results, "total": total, "limit": limit, "offset": offset}
