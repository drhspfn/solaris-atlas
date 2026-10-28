"""Application use cases for quest profiles and dialogue browsing."""

from typing import Any

from wuwa_story.db.repositories import story_browsing as repository
from wuwa_story.db.repositories.nodes import get_node, get_quest
from wuwa_story.story.domain import StoryBrowsingError
from wuwa_story.story.media import dialogue_media
from wuwa_story.story.service import (
    _quest_info,
    _quest_state_ids,
    _release_id,
    dialogue_lines,
    prefetch_dialogue,
    quest_profile_payload,
    scene_payloads,
)


async def quest_profile(session, game_quest_id, locale="en", game_version=None) -> dict[str, Any]:
    quest = await get_quest(session, game_quest_id)
    if quest is None:
        raise StoryBrowsingError(status_code=404, detail="quest not found")
    return await quest_profile_payload(session, quest, locale, game_version)


async def search_dialogue(
    session,
    q,
    character=None,
    quest_id=None,
    locale="en",
    game_version=None,
    limit=50,
    offset=0,
    *,
    node_lookup=get_node,
) -> dict[str, Any]:
    release_id = await _release_id(session, game_version)
    locale_row = await repository.locale(session, locale)
    if locale_row is None:
        raise StoryBrowsingError(status_code=400, detail=f"unknown locale: {locale}")
    character_node = None
    if character:
        character_node = await node_lookup(session, character, ("character",))
        if character_node is None:
            raise StoryBrowsingError(status_code=404, detail="character not found")
    states = None
    if quest_id is not None:
        quest = await repository.quest_by_game_id(session, quest_id)
        if quest is None:
            raise StoryBrowsingError(status_code=404, detail="quest not found")
        states = await _quest_state_ids(session, quest.node_id, release_id)
    rows = await repository.search_dialogue_rows(
        session,
        q,
        locale_row,
        release_id,
        character_node.id if character_node else None,
        states,
        limit,
        offset,
    )
    await prefetch_dialogue(session, rows, locale, release_id, include_raw=True)
    media_by_line = await dialogue_media(session, [row[0].node_id for row in rows], release_id)
    return {
        "query": q,
        "locale": locale,
        "game_version": game_version,
        "total_returned": len(rows),
        "offset": offset,
        "limit": limit,
        "results": await dialogue_lines(session, rows, locale, release_id, media_by_line),
    }


async def quest_transcript(
    session,
    game_quest_id,
    character=None,
    q=None,
    locale="en",
    game_version=None,
    limit=500,
    offset=0,
    focus_line=None,
    include_raw=True,
    *,
    node_lookup=get_node,
) -> dict[str, Any]:
    quest = await get_quest(session, game_quest_id)
    if quest is None:
        raise StoryBrowsingError(status_code=404, detail="quest not found")
    game_quest_id = quest.game_quest_id
    release_id = await _release_id(session, game_version)
    states = await _quest_state_ids(session, quest.node_id, release_id)
    character_node = None
    if character:
        character_node = await node_lookup(session, character, ("character",))
        if character_node is None:
            raise StoryBrowsingError(status_code=404, detail="character not found")
    locale_row = await repository.locale(session, locale) if q else None
    rows, offset = await repository.quest_transcript_rows(
        session,
        states,
        release_id,
        character_node.id if character_node else None,
        q,
        locale_row,
        focus_line,
        limit,
        offset,
    )
    if rows is None:
        raise StoryBrowsingError(status_code=404, detail="Cited passage not found in this quest")
    scene_rows = await repository.quest_scenes(session, quest.node_id)
    await prefetch_dialogue(session, rows, locale, release_id, include_raw=include_raw)
    media_by_line = await dialogue_media(session, [row[0].node_id for row in rows], release_id)
    return {
        "quest": await _quest_info(session, quest, locale, game_version),
        "ordering_semantics": "Authored state/action/talk ordering; branches and runtime conditions are not flattened into a guaranteed playthrough.",
        "scenes": scene_payloads(scene_rows),
        "filters": {"character": character, "text": q, "locale": locale},
        "offset": offset,
        "limit": limit,
        "lines_returned": len(rows),
        "lines": await dialogue_lines(
            session, rows, locale, release_id, media_by_line, include_raw=include_raw
        ),
    }
