"""Rebuild only dungeon-context edges from retained rows; no media or snapshot imports."""

import argparse
import asyncio
import json
from types import SimpleNamespace

from sqlalchemy import select, text

from wuwa_story.db.models.core import QuestState
from wuwa_story.db.models.graph import Edge, EdgeEvidence, Node, NodeRevision
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.session import SessionFactory
from wuwa_story.ingestion.canonical_import import import_canonical_edges
from wuwa_story.ingestion.cutscene_order import rebuild as rebuild_cutscene_order
from wuwa_story.ingestion.dungeon_context import (
    DUNGEONS,
    LEVEL_NODES,
    LEVELS,
    QUEST_NODES,
    RELATION,
    dungeon_links,
    level_memberships,
    requirements,
)
from wuwa_story.ingestion.scenario_context import scenario_links
from wuwa_story.ingestion.video_package_context import (
    QUEST_VIDEO,
    VIDEO_DATA,
    package_cutscene_links,
)


async def backfill(session, release_id, dry_run=False):
    release = await session.get(GameRelease, release_id)
    if release is None:
        raise ValueError("Unknown imported release")
    await session.execute(text("SELECT pg_advisory_xact_lock(703, :id)"), {"id": release_id})

    async def rows(source):
        return list(await session.execute(
            select(SourceRecord.row_index, SourceRecord.data)
            .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
            .where(SourceRecord.release_id == release_id, SourceFile.logical_source_path == source)
            .order_by(SourceRecord.row_index)
        ))

    dungeons = {row["Id"] for _, row in await rows(DUNGEONS)}
    quest_nodes = await rows(QUEST_NODES)
    level_nodes = await rows(LEVEL_NODES)
    required = {key: value for key, value in requirements(quest_nodes).items()
                if key in dungeons}
    memberships = level_memberships(await rows(LEVELS), required)
    states = set(await session.scalars(
        select(QuestState.state_key).join(NodeRevision,
                                          NodeRevision.node_id == QuestState.node_id)
        .where(NodeRevision.release_id == release_id)
    ))
    links = list(dungeon_links(required, memberships, level_nodes, states))
    records = [{**link, "type": RELATION, "relation": "exact_join", "version": release.game_version,
                "source_repository": release.upstream_name, "source_commit": release.upstream_commit}
               for link in links]
    scenario = list(scenario_links(quest_nodes, level_nodes, states))
    scenario_quests = set(await session.scalars(
        select(Node.canonical_key).join(
            NodeRevision, NodeRevision.node_id == Node.id)
        .where(NodeRevision.release_id == release_id,
               Node.canonical_key.in_({link["from"] for link in scenario}))
    )) if scenario else set()
    records += [{**link, "type": "references_scenario_flow_state", "relation": "inference",
                 "version": release.game_version, "source_repository": release.upstream_name,
                 "source_commit": release.upstream_commit}
                for link in scenario if link["from"] in scenario_quests]
    packages = list(package_cutscene_links(await rows(QUEST_VIDEO), await rows(VIDEO_DATA)))
    package_nodes = set(await session.scalars(
        select(Node.canonical_key).join(NodeRevision, NodeRevision.node_id == Node.id)
        .where(NodeRevision.release_id == release_id,
               Node.canonical_key.in_({link[key] for link in packages for key in ("from", "to")}))
    )) if packages else set()
    records += [{**link, "version": release.game_version, "source_repository": release.upstream_name,
                 "source_commit": release.upstream_commit}
                for link in packages if link["from"] in package_nodes and link["to"] in package_nodes]
    records = list({(row["type"], row["from"], row["to"],
                   row["raw_path"]): row for row in records}.values())
    source, target = Node.__table__.alias(
        "source"), Node.__table__.alias("target")
    existing = set(await session.execute(
        select(RelationType.key, source.c.canonical_key,
               target.c.canonical_key, EdgeEvidence.source_raw_path)
        .select_from(Edge).join(RelationType, RelationType.id == Edge.relation_type_id)
        .join(source, source.c.id == Edge.from_node_id).join(target, target.c.id == Edge.to_node_id)
        .join(EdgeEvidence, EdgeEvidence.edge_id == Edge.id)
        .where(RelationType.key.in_((RELATION, "references_scenario_flow_state", "references_packaged_cutscene")), Edge.layer == "source",
               EdgeEvidence.release_id == release_id)
    ))
    missing = [row for row in records if (
        row["type"], row["from"], row["to"], row["raw_path"]) not in existing]
    if not dry_run and missing:
        await import_canonical_edges(SimpleNamespace(iter_edges=lambda: iter(missing)), session, release_id)
        await rebuild_cutscene_order(session, release_id)
    result = {"release_id": release_id, "game_version": release.game_version,
              "candidate_links": len(records), "missing_evidence": len(missing),
              "written": len(missing) if not dry_run else 0, "dry_run": dry_run}
    await session.commit()
    return result


async def main(first, last, dry_run):
    async with SessionFactory() as session:
        releases = list(await session.scalars(select(GameRelease).order_by(GameRelease.sequence)))

    def version(value): return tuple(map(int, value.split(".")))
    for release in releases:
        if version(first) <= version(release.game_version) <= version(last):
            async with SessionFactory() as session:
                print(json.dumps(await backfill(session, release.id, dry_run)), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first", default="2.0.0")
    parser.add_argument("--last", default="2.8.0")
    parser.add_argument("--dry-run", action="store_true")
    asyncio.run(main(**vars(parser.parse_args())))
