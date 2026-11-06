"""Evidence-backed NPC participation in authored flow-template actions."""
from __future__ import annotations

import json
from collections import defaultdict
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select

from wuwa_story.db.models.core import Character
from wuwa_story.db.models.graph import Node
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.ingestion.canonical_import import import_canonical_edges

FLOW_SOURCE = "BinData/flowState/flowstate.json"


def scenario_actor_edges(npcs: list[tuple[str, dict[str, Any]]], flow_rows: Any, version: str,
                         entity_blueprints: dict[int, set[str]]):
    """Use unique EntityId-to-blueprint joins; never infer an identity from a flow."""
    by_entity: dict[int, set[str]] = defaultdict(set)
    blueprint_by_key = {key: npc.get("blueprint_type") for key, npc in npcs}
    for key, npc in npcs:
        for instance in npc.get("instances", []):
            entity = instance.get("entity_id")
            if type(entity) is int and entity > 0:
                by_entity[entity].add(key)
    for row_index, row in flow_rows:
        state = row.get("StateKey")
        if not isinstance(state, str) or not state:
            continue
        actions = row.get("Actions") or []
        if isinstance(actions, str):
            try:
                actions = json.loads(actions)
            except ValueError:
                continue
        if not isinstance(actions, list):
            continue
        for index, action in enumerate(actions):
            if not isinstance(action, dict) or action.get("Name") != "BeginFlowTemplate":
                continue
            params = action.get("Params") or {}
            actors = params.get("Actors") if isinstance(params, dict) else None
            if not isinstance(actors, list):
                continue
            talker_counts: dict[int, int] = defaultdict(int)
            for actor in actors:
                if isinstance(actor, dict) and type(actor.get("TalkerId")) is int:
                    talker_counts[actor["TalkerId"]] += 1
            for actor_index, actor in enumerate(actors):
                if not isinstance(actor, dict) or actor.get("IsPlayer"):
                    continue
                entity = actor.get("EntityId")
                candidates = by_entity.get(entity, set()) if type(entity) is int else set()
                if (len(candidates) != 1 or entity_blueprints.get(entity) !=
                        {blueprint_by_key[next(iter(candidates))]}):
                    continue
                key = next(iter(candidates))
                path = f"$[{row_index}].Actions<json>[{index}].Params.Actors[{actor_index}].EntityId"
                base = {"from": key, "source": FLOW_SOURCE, "raw_path": path,
                        "version": version, "relation": "explicit_reference"}
                yield {**base, "to": f"flow_state:{state}", "type": "references_flow_state",
                       "basis": f"BeginFlowTemplate actor EntityId={entity}; unique NPC blueprint"}
                # Only the immediately following ShowTalk is covered. Control-flow,
                # template changes and later actions cannot extend this proof.
                if index + 1 >= len(actions):
                    continue
                following = actions[index + 1]
                talker = actor.get("TalkerId")
                if (not isinstance(following, dict) or following.get("Name") != "ShowTalk"
                        or type(talker) is not int or talker <= 0 or talker_counts[talker] != 1):
                    continue
                talk_params = following.get("Params") or {}
                talks = talk_params.get("TalkItems") if isinstance(talk_params, dict) else None
                if not isinstance(talks, list):
                    continue
                for talk_index, talk in enumerate(talks):
                    if (isinstance(talk, dict) and talk.get("Type") == "Talk"
                            and talk.get("WhoId") == talker):
                        yield {**base, "to": f"talk_item:{state}:{index + 1}:{talk_index}",
                               "type": "presents_talk_item",
                               "basis": f"adjacent ShowTalk WhoId={talker} equals actor TalkerId; EntityId={entity}; context only"}


async def import_npc_scenario_edges(session, release_id: int, version: str, entity_source: str) -> int:
    """Join the latest NPC supplement to already-imported raw flow records."""
    rows = await session.execute(select(Node.canonical_key, Character.metadata_json).join(
        Character, Character.node_id == Node.id).where(Node.canonical_key.startswith("character:npc:")))
    npcs = [(key, meta["npc"]) for key, meta in rows if isinstance((meta or {}).get("npc"), dict)]
    entity_rows = await session.stream(select(SourceRecord.data).join(
        SourceFile, SourceFile.id == SourceRecord.source_file_id).where(
        SourceRecord.release_id == release_id, SourceFile.release_id == release_id,
        SourceFile.logical_source_path == entity_source))
    entity_blueprints: dict[int, set[str]] = defaultdict(set)
    async for (data,) in entity_rows:
        entity, blueprint = data.get("EntityId"), data.get("BlueprintType")
        if type(entity) is int and isinstance(blueprint, str):
            entity_blueprints[entity].add(blueprint)
    flow_rows = await session.stream(select(SourceRecord.row_index, SourceRecord.data).join(
        SourceFile, SourceFile.id == SourceRecord.source_file_id).where(
        SourceRecord.release_id == release_id, SourceFile.release_id == release_id,
        SourceFile.logical_source_path == FLOW_SOURCE).order_by(SourceRecord.row_index))
    # Canonical edge importing accepts a synchronous iterable; collect only rows
    # with the relevant action, not the entire source table.
    matching = [(index, data) async for index, data in flow_rows
                if index is not None and "BeginFlowTemplate" in str(data.get("Actions", ""))]
    edges = list(scenario_actor_edges(npcs, matching, version, entity_blueprints))
    return await import_canonical_edges(SimpleNamespace(iter_edges=lambda: iter(edges)), session,
                                        release_id, batch_size=250, skip_missing_endpoints=True)
