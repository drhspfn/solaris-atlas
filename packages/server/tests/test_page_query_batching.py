from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

import pytest

from wuwa_story.api.routes import nodes, story
from wuwa_story.api.routes.story import profiles
from wuwa_story.db.repositories import story_browsing as repository
from wuwa_story.story import service


@pytest.mark.asyncio
async def test_node_context_prefetches_target_and_neighbors_before_rendering(monkeypatch):
    target = (Row(node_id=1), Row(node_id=8), Row(node_id=9, state_key="flow", source_record_id=None), None, None, None)
    neighbors = [target, (Row(node_id=2), target[1], target[2], None, None, None)]
    session = Row(
        get=AsyncMock(side_effect=[Row(key="dialogue_line"), Row(action_node_id=8, source_index=0)]),
        execute=AsyncMock(side_effect=[Row(first=lambda: target), Row(all=lambda: neighbors), Row(first=lambda: None)]),
        scalars=AsyncMock(side_effect=[[], []]),
    )
    monkeypatch.setattr(nodes, "get_node", AsyncMock(return_value=Row(id=1, canonical_key="line:1", type_id=1)))
    monkeypatch.setattr(story, "_release_id", AsyncMock(return_value=None))
    prefetched = AsyncMock()
    monkeypatch.setattr(service, "prefetch_dialogue", prefetched)

    async def payload(_session, row, **_kwargs):
        prefetched.assert_awaited_once_with(session, [target, *neighbors], "en", None)
        return {"id": row[0].node_id, "action": {"params": {}}, "provenance": {"raw_record": {}}}

    monkeypatch.setattr(story, "_dialogue_payload", payload)
    result = await nodes.node_narrative_context("line:1", session=session)
    assert result["dialogue"]["id"] == 1
    assert [line["id"] for line in result["context"]] == [1, 2]
    assert "params" not in result["dialogue"]["action"]
    assert "raw_record" not in result["context"][1]["provenance"]


@pytest.mark.asyncio
async def test_character_reuses_shared_scene_and_batches_transcript(monkeypatch):
    state = Row(node_id=9, state_key="flow", source_record_id=None)
    transcript = [(Row(node_id=1),), (Row(node_id=2),)]
    speaker = Row(node_id=4, game_speaker_id=40)
    link = Row(resolution_type="exact", source_record_id=3)
    character = Row(canonical_name="Name", name_key_id=None, description=None, playable=True)
    session = Row(
        get=AsyncMock(return_value=character),
        execute=AsyncMock(side_effect=[
            [(link, speaker, Row(canonical_key="speaker:40"))],
            [], [(2, 9), (3, 9)], Row(all=lambda: transcript), Row(first=lambda: None), [],
        ]),
        scalars=AsyncMock(side_effect=[[9], [state], [], [state], [], [], []]),
        scalar=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(profiles, "get_node", AsyncMock(return_value=Row(id=1, canonical_key="character:1", slug=None)))
    monkeypatch.setattr(profiles, "_release_id", AsyncMock(return_value=5))
    prefetch = AsyncMock()
    monkeypatch.setattr(profiles, "prefetch_dialogue", prefetch)
    monkeypatch.setattr(profiles, "prefetch_node_labels", AsyncMock())
    monkeypatch.setattr(profiles, "_dialogue_payload", AsyncMock(side_effect=lambda _s, row, **_k: {"id": row[0].node_id}))
    monkeypatch.setattr(profiles, "_node_label", AsyncMock(side_effect=lambda _s, identity, _l: {"id": identity}))
    monkeypatch.setattr(profiles, "_localized", AsyncMock(return_value=None))
    monkeypatch.setattr(profiles, "_latest_source_record", AsyncMock(return_value=None))
    monkeypatch.setattr(profiles, "_character_progression_materials", AsyncMock(return_value=[]))

    result = await profiles.character_profile.__wrapped__("character:1", session=session)
    scenes = [entry["shared_scenes"][0] for entry in result["co_present_characters"]]
    assert scenes[0] == scenes[1]
    assert scenes[0]["dialogue"] == [{"id": 1}, {"id": 2}]
    assert not scenes[0]["dialogue_truncated"]
    prefetch.assert_awaited_once_with(session, transcript, "en", 5)
    assert session.execute.await_count == 6


@pytest.mark.asyncio
async def test_quest_requirement_items_batch_and_prefer_canonical_keys():
    canonical = Row(id=1, canonical_key="item:1", slug="other")
    slug_match = Row(id=2, canonical_key="item:2", slug="item:1")
    session = Row(scalars=AsyncMock(side_effect=[[canonical, slug_match], []]))
    keys = ["item:1", *[f"item:{i}" for i in range(3, 503)]]
    result = await repository.quest_requirement_items(session, keys)
    assert result["item:1"] is canonical
    assert len(result) == 1
    assert session.scalars.await_count == 2


@pytest.mark.asyncio
async def test_quest_requirements_batch_items_and_keep_order_and_missing_entries(monkeypatch):
    quest = Row(node_id=7, game_quest_id=70)
    records = [Row(data={"Data": {"items": [1, 2]}}, row_index=4),
               Row(data={"Data": {"items": [1]}}, row_index=5)]
    monkeypatch.setattr(service, "quest_item_requirements", lambda data: [{"item_id": i} for i in data["items"]])
    monkeypatch.setattr(service, "_release_id", AsyncMock(return_value=5))
    monkeypatch.setattr(service, "_quest_page_state_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_explicit_entity_links", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_quest_info", AsyncMock(return_value={}))
    monkeypatch.setattr(service, "_latest_source_record", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "prefetch_node_labels", AsyncMock())
    monkeypatch.setattr(service, "_node_label", AsyncMock(return_value={"id": 11}))
    for name in ("quest_node_rows", "quest_hierarchy_rows", "quest_states", "quest_actions", "quest_scenes"):
        monkeypatch.setattr(repository, name, AsyncMock(return_value=[]))
    monkeypatch.setattr(repository, "quest_requirement_records", AsyncMock(return_value=records))
    items = AsyncMock(return_value={"item:1": Row(id=11)})
    monkeypatch.setattr(repository, "quest_requirement_items", items)
    session = Row(info={})
    result = await service.quest_profile_payload(session, quest)
    items.assert_awaited_once_with(session, ["item:1", "item:2"])
    assert [(entry["item_id"], entry["item"], entry["source"]["row"]) for entry in result["item_requirements"]] == [
        (1, {"id": 11}, 4), (2, None, 4), (1, {"id": 11}, 5),
    ]
