"""Evidence-backed NPC participation in authored flow-template actions."""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select

from wuwa_story.db.models.core import Character
from wuwa_story.db.models.graph import Node
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.session import SessionFactory
from wuwa_story.ingestion.canonical_import import import_canonical_edges
from wuwa_story.ingestion.releases import release_order
from wuwa_story.read_models.refresh import mark_dirty

FLOW_SOURCE = "BinData/flowState/flowstate.json"
LEVEL_SOURCE = "BinData/LevelPlayData/levelplaydata.json"
LEVEL_NODE_SOURCE = "BinData/LevelPlayNodeData/levelplaynodedata.json"


def scenario_actor_edges(npcs: list[tuple[str, dict[str, Any]]], flow_rows: Any, version: str,
                         entity_blueprints: dict[int, set[str]], *,
                         placement_blueprints: dict[tuple[int, int], set[str]] | None = None,
                         flow_maps: dict[str, set[int]] | None = None):
    """Use unique EntityId-to-blueprint joins; never infer an identity from a flow."""
    by_entity: dict[int, set[str]] = defaultdict(set)
    by_placement: dict[tuple[int, int], set[str]] = defaultdict(set)
    blueprint_by_key = {key: npc.get("blueprint_type") for key, npc in npcs}
    for key, npc in npcs:
        for instance in npc.get("instances", []):
            entity = instance.get("entity_id")
            if type(entity) is int and entity > 0:
                by_entity[entity].add(key)
                map_id = instance.get("map_id")
                if type(map_id) is int and map_id > 0:
                    by_placement[(map_id, entity)].add(key)
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
                map_id = None
                maps = (flow_maps or {}).get(state, set())
                if maps:
                    if len(maps) != 1:
                        continue
                    map_id = next(iter(maps))
                    placement = (map_id, entity)
                    raw_blueprints = (placement_blueprints or {}).get(placement, set())
                    owners = by_placement.get(placement, set())
                    if (len(raw_blueprints) != 1 or len(owners) != 1 or
                            raw_blueprints != {blueprint_by_key[next(iter(owners))]}):
                        continue
                    key = next(iter(owners))
                elif (len(candidates) == 1 and entity_blueprints.get(entity) ==
                      {blueprint_by_key[next(iter(candidates))]}):
                    key = next(iter(candidates))
                else:
                    continue
                path = f"$[{row_index}].Actions<json>[{index}].Params.Actors[{actor_index}].EntityId"
                base = {"from": key, "source": FLOW_SOURCE, "raw_path": path,
                        "version": version, "relation": "explicit_reference"}
                context = (f"; source LevelPlay.LevelId={map_id}; unique (map, EntityId) blueprint"
                           if map_id is not None else "; unique NPC blueprint")
                yield {**base, "to": f"flow_state:{state}", "type": "references_flow_state",
                       "basis": f"BeginFlowTemplate actor EntityId={entity}{context}; context only"}
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


async def collect_npc_scenario_edges(session, release_id: int, version: str, entity_source: str,
                                     *, source_record_id: int | None = None):
    """Plan edges from source records pinned to one release without writing."""
    from wuwa_story.ingestion.dungeon_context import decoded
    from wuwa_story.ingestion.npc_map_scope import flow_map_context

    rows = await session.execute(select(Node.canonical_key, Character.metadata_json).join(
        Character, Character.node_id == Node.id).where(Node.canonical_key.startswith("character:npc:")))
    npcs = [(key, meta["npc"]) for key, meta in rows if isinstance((meta or {}).get("npc"), dict)]
    entity_rows = await session.stream(select(SourceRecord.data).join(
        SourceFile, SourceFile.id == SourceRecord.source_file_id).where(
        SourceRecord.release_id == release_id, SourceFile.release_id == release_id,
        SourceFile.logical_source_path == entity_source))
    entity_blueprints: dict[int, set[str]] = defaultdict(set)
    placement_blueprints: dict[tuple[int, int], set[str]] = defaultdict(set)
    async for (data,) in entity_rows:
        entity, blueprint = data.get("EntityId"), data.get("BlueprintType")
        if type(entity) is int and isinstance(blueprint, str):
            entity_blueprints[entity].add(blueprint)
            map_id = data.get("MapId")
            if type(map_id) is int and map_id > 0:
                placement_blueprints[(map_id, entity)].add(blueprint)
    flow_query = select(SourceRecord.row_index, SourceRecord.data).join(
        SourceFile, SourceFile.id == SourceRecord.source_file_id).where(
        SourceRecord.release_id == release_id, SourceFile.release_id == release_id,
        SourceFile.logical_source_path == FLOW_SOURCE).order_by(SourceRecord.row_index)
    if source_record_id is not None:
        flow_query = flow_query.where(SourceRecord.id == source_record_id)
    flow_rows = await session.stream(flow_query)
    # Canonical edge importing accepts a synchronous iterable; collect only rows
    # with the relevant action, not the entire source table.
    matching = [(index, data) async for index, data in flow_rows
                if index is not None and "BeginFlowTemplate" in str(data.get("Actions", ""))]
    if not matching:
        return []
    level_rows = await session.stream(select(SourceRecord.row_index, SourceRecord.data).join(
        SourceFile, SourceFile.id == SourceRecord.source_file_id).where(
        SourceRecord.release_id == release_id, SourceFile.release_id == release_id,
        SourceFile.logical_source_path == LEVEL_SOURCE))
    levels = []
    async for index, row in level_rows:
        data = decoded(row.get("Data"))
        if index is not None and isinstance(data, dict):
            reduced = {"LevelId": data.get("LevelId")}
            if "Id" in data:
                reduced["Id"] = data["Id"]
            levels.append((index, {"LevelPlayId": row.get("LevelPlayId", data.get("Id")),
                                   "Data": reduced}))
    maps: dict[str, set[int]] = defaultdict(set)
    if levels:
        node_rows = await session.stream(select(SourceRecord.row_index, SourceRecord.data).join(
            SourceFile, SourceFile.id == SourceRecord.source_file_id).where(
            SourceRecord.release_id == release_id, SourceFile.release_id == release_id,
            SourceFile.logical_source_path == LEVEL_NODE_SOURCE))
        batch = []
        async for index, data in node_rows:
            if index is None or "PlayFlow" not in str(data.get("Data", "")):
                continue
            batch.append((index, data))
            if len(batch) >= 128:
                for state, values in flow_map_context(levels, batch).items():
                    maps[state].update(values)
                batch.clear()
        if batch:
            for state, values in flow_map_context(levels, batch).items():
                maps[state].update(values)
    return list(scenario_actor_edges(npcs, matching, version, entity_blueprints,
                                     placement_blueprints=placement_blueprints, flow_maps=maps))


async def import_npc_scenario_edges(session, release_id: int, version: str, entity_source: str) -> int:
    """Join the latest NPC supplement to already-imported raw flow records."""
    edges = await collect_npc_scenario_edges(session, release_id, version, entity_source)
    return await import_canonical_edges(SimpleNamespace(iter_edges=lambda: iter(edges)), session,
                                        release_id, batch_size=250, skip_missing_endpoints=True)


async def main(args):
    async with SessionFactory() as session:
        release = await session.get(GameRelease, args.release_id)
        if release is None:
            raise ValueError("Unknown release")
        releases = list(await session.scalars(select(GameRelease)))
        if not releases or max(releases, key=release_order).id != release.id:
            raise ValueError("NPC scenario repair requires the latest release")
        if not (args.entity_source.startswith("ClientData/") and
                args.entity_source.endswith("/BinData/level_entity/levelentityconfig.json")):
            raise ValueError("Entity source must be a pinned native ClientData table")
        if not await session.scalar(select(SourceFile.id).where(
                SourceFile.release_id == release.id, SourceFile.logical_source_path == args.entity_source)):
            raise ValueError("Entity source is not retained for the selected release")
        if args.source_record_id is not None:
            if args.source_record_id <= 0 or not await session.scalar(select(SourceRecord.id).join(
                    SourceFile, SourceFile.id == SourceRecord.source_file_id).where(
                    SourceRecord.id == args.source_record_id, SourceRecord.release_id == release.id,
                    SourceFile.release_id == release.id, SourceFile.logical_source_path == FLOW_SOURCE)):
                raise ValueError("Flow source record is not retained for the selected release")
        edges = await collect_npc_scenario_edges(session, release.id, release.game_version,
                                                 args.entity_source, source_record_id=args.source_record_id)
        owners = sorted({edge["from"] for edge in edges})
        result = {"release_id": release.id, "entity_source": args.entity_source,
                  "applied": args.apply, "candidate_edges": len(edges),
                  "source_record_id": args.source_record_id, "owner_count": len(owners),
                  "owner_keys": owners[:100], "owner_keys_truncated": len(owners) > 100,
                  "processed_edges": 0}
        if args.apply and edges:
            result["processed_edges"] = await import_canonical_edges(
                SimpleNamespace(iter_edges=lambda: iter(edges)), session, release.id,
                batch_size=250, skip_missing_endpoints=True)
            ids, anchors = set(), set()
            for offset in range(0, len(owners), 500):
                rows = await session.execute(select(Node.id, Character.metadata_json["public_identity"]
                    ["canonical_key"].as_string()).join(Character, Character.node_id == Node.id)
                    .where(Node.canonical_key.in_(owners[offset:offset + 500])))
                for identity, anchor in rows:
                    ids.add(identity)
                    if anchor:
                        anchors.add(anchor)
            anchors = sorted(anchors)
            for offset in range(0, len(anchors), 500):
                ids.update(await session.scalars(select(Node.id).where(
                    Node.canonical_key.in_(anchors[offset:offset + 500]))))
            ids = sorted(ids)
            for offset in range(0, len(ids), 1000):
                await mark_dirty(session, "character", ids[offset:offset + 1000])
            await session.commit()
            result["refresh_count"] = len(ids)
            result["refresh_ids"] = ids[:100]
            result["refresh_ids_truncated"] = len(ids) > 100
        print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", type=int, required=True)
    parser.add_argument("--entity-source", required=True)
    parser.add_argument("--source-record-id", type=int)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(main(parser.parse_args()))
