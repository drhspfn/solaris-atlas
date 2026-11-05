from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from wuwa_story.api.pagination import cursor_scope, encode_cursor
from wuwa_story.read_models.readers import character_connections as connections


def profile():
    return {
        "character": {"canonical_key": "character:1501", "name": "Rover"},
        "family": {"variants": [{"canonical_key": "character:1502"}]},
        "speakers": [{"canonical_key": "speaker:1"}],
        "quests_with_dialogue": [
            {"canonical_key": f"quest:{n}", "game_quest_id": n, "href": f"/quests/{n}",
             "name": {"content": f"Chapter {n}", "source_record_id": 99},
             "classification": {"kind": "main", "label": "Main quest", "raw": "internal"}}
            for n in range(1, 31)
        ],
        "co_present_characters": [{
            "character": {"canonical_key": "character:2", "label": "Jingran", "href": "/characters/2"},
            "flow_states": ["one", "two", "three"],
            "shared_scenes": [{"flow_state": str(n), "source": {"raw": "private"},
                               "quest_refs": [{"canonical_key": f"quest:{n}", "name": f"Scene quest {n}"}],
                               "dialogue": [{"id": str(i), "text": {"content": "A line", "source": 3},
                                             "speaker": {"canonical_key": "speaker:2", "label": "Jingran"}}
                                            for i in range(9)]} for n in range(3)],
        }, {"character": {"canonical_key": "character:1502", "label": "Rover"}}],
        "co_present_speakers": [
            {"speaker": {"canonical_key": "speaker:2"}, "character_entities": [{"canonical_key": "character:2"}]},
            {"speaker": {"canonical_key": "speaker:3"}, "character_entities": []},
        ],
        "explicit_links": [
            {"target": {"canonical_key": "character:1501"}, "relation": "references_character"},
            {"target": {"canonical_key": "speaker:1"}, "relation": "references_character"},
            {"target": {"canonical_key": "character:3", "label": "Denia"}, "relation": "references_character"},
        ],
    }


def page(data, section="quests", **kwargs):
    return connections.page_connections(data, section, identity="character:1501", generation=7, **kwargs)


def test_cursor_pages_are_complete_deduplicated_and_bounded():
    data = profile()
    data["quests_with_dialogue"].append(data["quests_with_dialogue"][0])
    found, cursor = [], None
    while True:
        result = page(data, limit=12, cursor=cursor)
        assert result["total"] == 30
        assert len(result["results"]) <= 12
        found.extend(r["game_quest_id"] for r in result["results"])
        cursor = result["next_cursor"]
        assert result["has_more"] == bool(cursor)
        if not cursor:
            break
    assert found == list(range(1, 31))
    assert result["results"][0]["classification"] == {"kind": "main", "label": "Main quest"}
    assert "source_record_id" not in result["results"][0]["name"]


def test_search_covers_connections_and_quests_outside_the_initial_preview():
    data = profile()
    assert page(data, q="CHAPTER 29")["results"][0]["game_quest_id"] == 29
    result = page(data, "characters", q="  SCENE  QUEST  2 ")
    assert result["total"] == 1
    assert result["results"][0]["target"]["label"] == "Jingran"
    assert len(result["results"][0]["shared_scenes"]) == 2
    assert "_search" not in result["results"][0]


def test_shared_scene_previews_preserve_quest_links_without_shipping_transcripts_or_self_links():
    result = page(profile(), "characters")
    assert result["total"] == 2
    shared = result["results"][0]
    assert shared["relation"] == "Shared authored scene"
    assert shared["shared_scenes_truncated"]
    assert len(shared["flow_states"]) == 2
    scene = shared["shared_scenes"][0]
    assert len(scene["dialogue"]) == 3
    assert scene["dialogue_truncated"]
    assert scene["quest_refs"][0]["canonical_key"] == "quest:0"
    assert scene["dialogue"][0]["text"] == "A line"
    assert "source" not in scene
    assert result["results"][1]["relation"] == "references_character"


@pytest.mark.parametrize("change", [{"q": "Chapter"}, {"locale": "ja"}, {"section": "characters"}])
def test_cursor_cannot_be_reused_for_a_different_search_or_section(change):
    token = page(profile())["next_cursor"]
    section = change.get("section", "quests")
    change = {key: value for key, value in change.items() if key != "section"}
    with pytest.raises(HTTPException) as error:
        page(profile(), section, cursor=token, **change)
    assert error.value.status_code == 400


def test_stale_generation_is_a_restartable_conflict():
    token = page(profile())["next_cursor"]
    with pytest.raises(HTTPException) as error:
        connections.page_connections(profile(), "quests", identity="character:1501", generation=8, cursor=token)
    assert error.value.status_code == 409


@pytest.mark.parametrize("position", [-1, 31])
def test_invalid_positions_are_rejected(position):
    scope = cursor_scope("character_connections", "character:1501", "quests", "en", "current", "")
    with pytest.raises(HTTPException) as error:
        page(profile(), cursor=encode_cursor([7, position], scope))
    assert error.value.status_code == 400


@pytest.mark.asyncio
async def test_compact_profile_reuses_one_published_row_and_retains_full_source_contract(monkeypatch):
    original = profile()
    result = Mock()
    result.mappings.return_value.all.return_value = [
        {"entity_id": 1, "canonical_key": "character:1501", "generation": 7, "payload": original}
    ]
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    monkeypatch.setattr(connections, "should_route_to_projection", lambda *args, **kwargs: True)
    monkeypatch.setattr(connections.entities, "_require_scope", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(connections.entities, "_locale_id", AsyncMock(return_value=1))
    compact = await connections.compact_character_profile(session, "character:1501", limit=12)
    session.execute.assert_awaited_once()
    assert len(compact["quests_with_dialogue"]) == 12
    assert compact["connections_summary"]["quests"]["total"] == 30
    assert compact["unresolved_speakers_total"] == 1
    assert not {"co_present_characters", "co_present_speakers", "explicit_links"} & compact.keys()
    assert len(original["quests_with_dialogue"]) == 30
    assert len(original["co_present_characters"][0]["shared_scenes"][0]["dialogue"]) == 9
