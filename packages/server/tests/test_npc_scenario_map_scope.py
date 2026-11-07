"""A source-backed map may disambiguate actor context, never global identity."""

import pytest

from wuwa_story.ingestion.npc_scenario import scenario_actor_edges

ENTITY = 119850013
STATE = "Example_1_36"


def npc(key, blueprint, map_id, name):
    return (key, {"blueprint_type": blueprint, "instances": [{
        "map_id": map_id, "entity_id": ENTITY, "name": {"en": name},
    }]})


def flow(actors=None, state=STATE):
    return (12, {"StateKey": state, "Actions": [
        {"Name": "BeginFlowTemplate", "Params": {"Actors": actors if actors is not None else [
            {"EntityId": ENTITY, "TalkerId": 1316}]}},
        {"Name": "ShowTalk", "Params": {"TalkItems": [{"Type": "Talk", "WhoId": 1316}]}},
    ]})


@pytest.fixture
def candidates():
    return [npc("character:npc:2595", "player", 909, "Abby"),
            npc("character:npc:17331", "nana", 906, "Nana")]


def edges(candidates, flow_maps, placements=None, actors=None, state=STATE):
    return list(scenario_actor_edges(
        candidates, [flow(actors, state)], "3.7", {ENTITY: {"player", "nana"}},
        placement_blueprints=placements if placements is not None else {
            (909, ENTITY): {"player"}, (906, ENTITY): {"nana"}},
        flow_maps=flow_maps,
    ))


def test_exact_authored_map_selects_placement_without_name_inference(candidates):
    # Even a misleading or missing label cannot influence the map-based association.
    candidates[0][1]["instances"][0]["name"] = {"en": "Nana"}
    candidates[1][1]["instances"][0]["name"] = None
    result = edges(candidates, {STATE: {909}})
    assert [(edge["from"], edge["type"], edge["to"]) for edge in result] == [
        ("character:npc:2595", "references_flow_state", f"flow_state:{STATE}"),
        ("character:npc:2595", "presents_talk_item", f"talk_item:{STATE}:1:0"),
    ]
    assert all("speaker:" not in edge["to"] and edge["type"] != "references_character"
               for edge in result)


@pytest.mark.parametrize("maps", [{}, {STATE: set()}, {STATE: {909, 906}}, {"other": {909}}])
def test_missing_or_ambiguous_map_rejects_duplicate_entity(candidates, maps):
    assert edges(candidates, maps) == []


def test_conflicting_raw_blueprints_at_same_placement_rejects_edge(candidates):
    assert edges(candidates, {STATE: {909}}, {
        (909, ENTITY): {"player", "other"}, (906, ENTITY): {"nana"},
    }) == []


def test_duplicate_talker_never_creates_direct_speech_edge(candidates):
    actors = [{"EntityId": ENTITY, "TalkerId": 1316},
              {"EntityId": 42, "TalkerId": 1316}]
    result = edges(candidates, {STATE: {909}}, actors=actors)
    assert [(edge["from"], edge["type"]) for edge in result] == [
        ("character:npc:2595", "references_flow_state")]


def test_player_actor_never_creates_npc_edges(candidates):
    assert edges(candidates, {STATE: {909}}, actors=[
        {"EntityId": ENTITY, "TalkerId": 1316, "IsPlayer": True}]) == []


def test_no_source_placement_for_selected_map_rejects_edge(candidates):
    assert edges(candidates, {STATE: {909}}, {(906, ENTITY): {"nana"}}) == []


def test_same_placement_with_multiple_npc_owners_is_ambiguous(candidates):
    candidates.append(npc("character:npc:other", "player", 909, "Abby"))
    assert edges(candidates, {STATE: {909}}) == []


def test_unnamed_mixed_template_without_map_proof_is_not_selected(candidates):
    for _, data in candidates:
        data["instances"][0]["name"] = None
    assert edges(candidates, {}) == []
