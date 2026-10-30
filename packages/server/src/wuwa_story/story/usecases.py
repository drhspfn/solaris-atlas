"""Application use cases for quest profiles and dialogue browsing."""

from typing import Any

from wuwa_story.db.repositories import story_browsing as repository
from wuwa_story.db.repositories.nodes import get_node, get_quest
from wuwa_story.story.domain import StoryBrowsingError
from wuwa_story.story.illustrations import illustration_payloads
from wuwa_story.story.media import dialogue_media
from wuwa_story.story.runtime_service import present_blocks
from wuwa_story.story.service import (
    _quest_info,
    _quest_page_state_ids,
    _quest_state_ids,
    _release_id,
    dialogue_lines,
    prefetch_dialogue,
    quest_profile_payload,
    scene_payloads,
)
from wuwa_story.story.tree_order import authored_order


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
    states = await _quest_page_state_ids(session, quest.node_id, release_id)
    tree_order = await authored_order(session, quest.game_quest_id, release_id)
    if tree_order:
        states = list(set(states) | set(tree_order['state_ids']))
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
        ordered_states=tree_order['state_ids'] if tree_order else None,
    )
    if rows is None:
        raise StoryBrowsingError(status_code=404, detail="Cited passage not found in this quest")
    scene_rows = await repository.quest_scenes(session, quest.node_id)
    await prefetch_dialogue(session, rows, locale, release_id, include_raw=include_raw)
    media_by_line = await dialogue_media(session, [row[0].node_id for row in rows], release_id)
    lines = await dialogue_lines(session, rows, locale, release_id, media_by_line, include_raw=include_raw)
    illustrations = await illustration_payloads(session, rows)
    for row, line in zip(rows, lines, strict=True):
        if row[0].node_id in illustrations:
            line['illustration'] = illustrations[row[0].node_id]
    if tree_order:
        ordered_ids = set(tree_order['state_ids'])
        for row, line in zip(rows, lines, strict=True):
            line['story_order_mapped'] = row[2].node_id in ordered_ids
            if position := tree_order.get('positions', {}).get(row[2].state_key):
                line['story_position'] = [*position, row[1].action_index, row[0].source_index]
    return {
        "quest": await _quest_info(session, quest, locale, game_version),
        "ordering_semantics": (
            f"Authored tree order from game client {tree_order['asset_version']}; "
            "parallel and conditional branches do not establish a guaranteed playthrough."
            if tree_order else
            "Authored state/action/talk ordering; branches and runtime conditions are not flattened into a guaranteed playthrough."
        ),
        "tree_order_asset_version": tree_order['asset_version'] if tree_order else None,
        "scenes": scene_payloads(scene_rows),
        "story_blocks": await present_blocks(session, tree_order, locale, release_id),
        "filters": {"character": character, "text": q, "locale": locale},
        "offset": offset,
        "limit": limit,
        "lines_returned": len(rows),
        "lines": lines,
    }
