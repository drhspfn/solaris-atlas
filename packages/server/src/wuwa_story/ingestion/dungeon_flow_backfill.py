"""Repair explicit dungeon-flow ownership for selected releases and dungeon IDs.

Dry-run by default. --apply adds missing graph evidence without reimporting
sources/media. Normal database invalidation triggers apply; no bulk refresh is
requested. Each release commits independently.
"""

import argparse
import asyncio
import json
from types import SimpleNamespace

from sqlalchemy import or_, select, text, true

from wuwa_story.db.models.graph import Edge, EdgeEvidence, Node, NodeRevision
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.session import SessionFactory
from wuwa_story.ingestion.canonical_import import import_canonical_edges
from wuwa_story.ingestion.dungeon_context import (
    DUNGEONS,
    LEVEL_NODES,
    LEVELS,
    QUEST_NODES,
    RELATION,
    dungeon_links,
    level_memberships,
    requirements,
    walk,
)


async def backfill(session, release_id, dungeon_ids, *, apply=False, max_links=1000):
    if type(release_id) is not int or release_id <= 0:
        raise ValueError("A positive release ID is required")
    if not dungeon_ids or any(type(identity) is not int or identity <= 0 for identity in dungeon_ids):
        raise ValueError("Select at least one positive dungeon ID")
    if not 1 <= max_links <= 10000:
        raise ValueError("max_links must be between 1 and 10000")
    await session.execute(text("SET LOCAL lock_timeout = '2s'"))
    await session.execute(text("SET LOCAL statement_timeout = '30s'"))
    if apply:
        # Serialize with the existing dungeon-context repair for this release.
        await session.execute(text("SELECT pg_advisory_xact_lock(703, :id)"), {"id": release_id})
    release = await session.get(GameRelease, release_id)
    if release is None:
        raise ValueError("Unknown imported release")

    async def rows(path, *filters):
        return list(await session.execute(
            select(SourceRecord.row_index, SourceRecord.data)
            .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
            .where(SourceRecord.release_id == release_id, SourceFile.release_id == release_id,
                   SourceFile.logical_source_path == path, *filters)
            .order_by(SourceRecord.row_index)
        ))

    dungeons = {row["Id"] for _, row in await rows(
        DUNGEONS, SourceRecord.data["Id"].as_integer().in_(sorted(dungeon_ids)))
    }
    if dungeons != set(dungeon_ids):
        raise ValueError("Selected dungeon IDs are missing from this release")
    required = {identity: owners for identity, owners in requirements(await rows(
        QUEST_NODES, SourceRecord.data["Data"].as_string().contains(
            "FinishDungeon"),
    )).items() if identity in dungeons}
    members = level_memberships(await rows(LEVELS), required)
    nodes = await rows(LEVEL_NODES, or_(*(
        SourceRecord.data["Key"].as_string().startswith(
            f"{identity}_", autoescape=True)
        for identity in sorted(members)
    ))) if members else []
    possible_states = {
        f"{obj['FlowListName']}_{obj['FlowId']}_{obj['StateId']}"
        for _, row in nodes for _, obj in walk(row.get("Data"))
        if isinstance(obj.get("FlowListName"), str)
        and type(obj.get("FlowId")) is int and type(obj.get("StateId")) is int
    }
    candidates = list(dungeon_links(required, members, nodes, possible_states))
    candidates = list({(link["from"], link["to"], link["source"], link["raw_path"]): link
                       for link in candidates}.values())
    if len(candidates) > max_links:
        raise ValueError(
            f"{len(candidates)} candidate links exceed max_links={max_links}; narrow the scope")
    keys = {link[side] for link in candidates for side in ("from", "to")}
    endpoints = (select(Node.id, Node.canonical_key).where(Node.canonical_key.in_(keys))
                 .cte("dungeon_flow_endpoints").prefix_with("MATERIALIZED"))
    observed = select(NodeRevision.id).where(
        NodeRevision.node_id == endpoints.c.id, NodeRevision.release_id == release_id,
    ).limit(1).lateral()
    known = set(await session.scalars(select(endpoints.c.canonical_key).join(
        observed, true(),
    ))) if keys else set()
    unresolved = [link for link in candidates if link["from"]
                  not in known or link["to"] not in known]
    if apply and unresolved:
        raise ValueError(
            f"{len(unresolved)} links have endpoints absent from the selected snapshot")
    records = [{**link, "type": RELATION, "relation": "exact_join", "version": release.game_version,
                "source_repository": release.upstream_name, "source_commit": release.upstream_commit}
               for link in candidates if link not in unresolved]
    source, target = Node.__table__.alias(
        "source"), Node.__table__.alias("target")
    existing = set(await session.execute(
        select(source.c.canonical_key, target.c.canonical_key,
               EdgeEvidence.source_file_path, EdgeEvidence.source_raw_path)
        .select_from(Edge).join(RelationType, RelationType.id == Edge.relation_type_id)
        .join(source, source.c.id == Edge.from_node_id).join(target, target.c.id == Edge.to_node_id)
        .join(EdgeEvidence, EdgeEvidence.edge_id == Edge.id)
        .where(Edge.layer == "source", Edge.basis == "exact_join", RelationType.key == RELATION,
               EdgeEvidence.release_id == release_id, source.c.canonical_key.in_(
                   known),
               target.c.canonical_key.in_(known))
    )) if records else set()
    missing = [link for link in records if (
        link["from"], link["to"], link["source"], link["raw_path"]) not in existing]
    if apply and missing:
        # The importer commits each batch; keep this release in one bounded batch.
        await import_canonical_edges(SimpleNamespace(iter_edges=lambda: iter(missing)), session,
                                     release_id, batch_size=len(missing))
    return {"release_id": release_id, "game_version": release.game_version,
            "dungeon_ids": sorted(dungeon_ids), "candidate_links": len(candidates),
            "unresolved_links": len(unresolved), "missing_evidence": len(missing),
            "written": len(missing) if apply else 0,
            "quest_keys": sorted({link["from"].split(":")[1] for link in missing}),
            "state_keys": sorted({link["to"].removeprefix("flow_state:") for link in missing})}


async def main(args):
    for release_id in sorted(set(args.release_id)):
        async with SessionFactory() as session:
            result = await backfill(session, release_id, set(args.dungeon_id),
                                    apply=args.apply, max_links=args.max_links)
            print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", type=int,
                        action="append", required=True)
    parser.add_argument("--dungeon-id", type=int,
                        action="append", required=True)
    parser.add_argument("--max-links", type=int, default=1000)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(main(parser.parse_args()))
