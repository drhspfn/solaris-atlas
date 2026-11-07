from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

import pytest

from wuwa_story.api.routes.story import npc_profile, profiles
from wuwa_story.db.models.core import Character
from wuwa_story.db.models.graph import Node
from wuwa_story.db.repositories.character_identity import character_family
from wuwa_story.db.repositories.npc_family import combine_npc_records
from wuwa_story.read_models.readers.entities import ProjectedPayload


def member(node_id, *, instances=(), flows=(), shops=()):
    return (Row(node_id=node_id, metadata_json={"npc": {"blueprint_id": node_id, "instances": list(instances),
                                      "flow_references": list(flows), "shops": list(shops)}}),
            Row(id=node_id, canonical_key=f"character:npc:{node_id}"))


def test_group_preserves_all_interactions_and_deduplicates_placed_instances():
    first = member(1, instances=[{"map_id": 8, "entity_id": 42}],
                   flows=[{"flow": ["one", 1, 1], "basis": "explicit_flow_triple"}],
                   shops=[{"id": 9, "shopfixed": [{"Id": 8}]}])
    second = member(2, instances=[{"map_id": 8, "entity_id": 42}, {"map_id": 9, "entity_id": 43}],
                    flows=[{"flow": ["two", 1, 1], "basis": "explicit_flow_triple"}],
                    shops=[{"id": 9, "shopfixed": [{"Id": 8}]}])
    group = combine_npc_records([first, second])
    assert len(group["instances"]) == 2
    assert group["instances"][1]["character_key"] == "character:npc:2"
    assert [ref["flow"][0] for ref in group["flow_references"]] == ["one", "two"]
    assert group["owner_ids"] == [1, 2]
    assert len(group["shops"]) == 1
    assert "character_key" not in second[0].metadata_json["npc"]["instances"][0]


@pytest.mark.asyncio
async def test_non_rover_family_does_not_require_gender_or_rename_person():
    identity = {"family": "person:123", "canonical_key": "character:123"}
    first, node = member(1)
    first.metadata_json["public_identity"] = identity
    session = Row(execute=AsyncMock(return_value=[(first, node), member(2)]))
    ids, family = await character_family(session, first, node, AsyncMock())
    assert ids == [1, 2]
    assert family == {"canonical_key": "character:123", "variants": [],
                      "members": ["character:npc:1", "character:npc:2"]}


@pytest.mark.asyncio
async def test_group_instance_page_is_bounded_and_localized(monkeypatch):
    group = combine_npc_records([member(1, instances=[
        {"map_id": 8, "entity_id": i, "name": {"key": "Name", "en": "Old"}}
        for i in range(60)])])
    monkeypatch.setattr(npc_profile, "npc_record", AsyncMock(return_value=(None, None, group, 21)))
    monkeypatch.setattr(npc_profile, "prefetch_localizations", AsyncMock())
    monkeypatch.setattr(npc_profile, "_localized", AsyncMock(return_value={"content": "Cristoforo"}))
    session = Row(execute=AsyncMock(return_value=Row(all=lambda: [("Name", 7)])))
    result = await npc_profile.npc_instances("character:npc:1", locale="en", limit=20,
                                             offset=20, session=session)
    assert result["total"] == 60 and len(result["results"]) == 20
    assert result["results"][0]["entity_id"] == 20
    assert result["results"][0]["name"] == {"content": "Cristoforo"}


@pytest.mark.asyncio
async def test_numeric_npc_profile_resolves_source_family_before_cached_profile(monkeypatch):
    character, node = member(1)
    character.metadata_json["role_type"] = 6
    monkeypatch.setattr(profiles, "get_node", AsyncMock(return_value=None))
    async def get(model, identity):
        assert identity == 1
        return node if model is Node else character if model is Character else None
    session = Row(get=get)
    monkeypatch.setattr(npc_profile, "npc_record", AsyncMock(return_value=(node, character, {}, 21)))
    render = AsyncMock(return_value=ProjectedPayload({"character": {"canonical_key": node.canonical_key}}))
    monkeypatch.setattr(npc_profile, "npc_profile", render)
    result = await profiles.character_profile("1", session=session, connections_limit=12)
    assert result["character"]["canonical_key"] == "character:npc:1"
    render.assert_awaited_once()


@pytest.mark.asyncio
async def test_shared_template_is_not_titled_as_one_of_its_different_people(monkeypatch):
    character, node = member(1)
    character.name_key_id = 7
    character.canonical_name = "Abby"
    character.description = None
    character.metadata_json["public_identity"] = {"classification": "mixed_instances"}
    monkeypatch.setattr(npc_profile, "_localized", AsyncMock(return_value={"content": "Abby"}))
    result = await npc_profile.npc_profile(Row(), node, character, {}, 21, "en")
    assert result["character"]["name"]["content"] == "Shared NPC template"
    assert "differently named" in result["character"]["description"]
