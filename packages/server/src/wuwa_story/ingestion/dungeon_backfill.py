"""Rebuild only dungeon-context edges from retained rows; no media or snapshot imports."""

import argparse
import asyncio
import json
from types import SimpleNamespace

from sqlalchemy import delete, or_, select, text

from wuwa_story.db.models.core import AuthoredTreeOrder, Quest, QuestState
from wuwa_story.db.models.graph import Edge, EdgeEvidence, Node, NodeRevision
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.repositories.quest_scope import quest_state_links
from wuwa_story.db.session import SessionFactory
from wuwa_story.ingestion.authored_flow_context import (
    FLOWS,
    PLOT,
    RECIPE,
    STATES,
    authored_flow_context,
)
from wuwa_story.ingestion.canonical_import import import_canonical_edges
from wuwa_story.ingestion.cutscene_order import rebuild as rebuild_cutscene_order
from wuwa_story.ingestion.dungeon_context import (
    DUNGEONS,
    LEVEL_NODES,
    LEVELS,
    QUEST_NODES,
    RELATION,
    decoded,
    dungeon_links,
    level_memberships,
    requirements,
)
from wuwa_story.ingestion.level_interactions import (
    interaction_links,
    membership_hash,
    required_level_links,
)
from wuwa_story.ingestion.runtime_media import flow_movie_links, quest_runtime_links
from wuwa_story.ingestion.scenario_context import scenario_links
from wuwa_story.ingestion.video_package_context import (
    QUEST_VIDEO,
    VIDEO_DATA,
    package_cutscene_links,
)


async def invalidate_repaired_quests(session, release, records):
    """Dirty only exact quest owners of changed source evidence in this release."""
    from wuwa_story.read_models.refresh import mark_dirty

    owners = [record['from'].split(':') for record in records]
    identities = {int(parts[1]) for parts in owners if parts[0] in {'quest', 'quest_node'}
                  and len(parts) >= 2 and parts[1].isdecimal()}
    quests = set(await session.scalars(select(Quest.node_id).join(
        NodeRevision, NodeRevision.node_id == Quest.node_id).where(
        NodeRevision.release_id == release.id, Quest.game_quest_id.in_(identities)))) if identities else set()
    keys = {record['state_key'] for record in records if record.get('state_key')}
    if keys:
        states = list(await session.scalars(select(QuestState.node_id).join(NodeRevision,
            NodeRevision.node_id == QuestState.node_id).where(
            NodeRevision.release_id == release.id, QuestState.state_key.in_(keys))))
        if states:
            links = quest_state_links(release.id, state_ids=states)
            quests.update(await session.scalars(select(links.c.quest_id).distinct()))
    quests = sorted(quests)
    if quests:
        await mark_dirty(session, 'quest', quests)
        await mark_dirty(session, 'quest', quests, release.game_version)
    wrapper_keys = {record['to'] for record in records if record.get('to', '').startswith('flow_state:')}
    wrappers = set(await session.scalars(select(Node.id).join(
        NodeRevision, NodeRevision.node_id == Node.id).where(
        NodeRevision.release_id == release.id, Node.canonical_key.in_(wrapper_keys)))) if wrapper_keys else set()

    async def children(parents, relations):
        if not parents:
            return set()
        return set(await session.scalars(select(Edge.to_node_id).join(
            RelationType, RelationType.id == Edge.relation_type_id).where(
            Edge.layer == 'source', Edge.from_node_id.in_(parents), RelationType.key.in_(relations),
            select(EdgeEvidence.id).where(EdgeEvidence.edge_id == Edge.id,
                EdgeEvidence.release_id == release.id).exists())))

    actions = await children(wrappers, ('contains_action',))
    cutscenes = await children(actions, ('plays_cutscene',))
    cutscenes.update(await session.scalars(select(Node.id).join(
        NodeRevision, NodeRevision.node_id == Node.id).where(
        NodeRevision.release_id == release.id,
        Node.canonical_key.in_({record['to'] for record in records
                               if record.get('to', '').startswith('cutscene:')}))))
    captions = await children(cutscenes, ('has_transcript_state',))
    caption_actions = await children(captions, ('contains_action',))
    dialogues = await children(actions | caption_actions, ('presents_talk_item',))
    for kind, identities in (('cutscene', cutscenes), ('dialogue', dialogues)):
        if identities:
            await mark_dirty(session, kind, sorted(identities))
            await mark_dirty(session, kind, sorted(identities), release.game_version)
    return quests


async def backfill(session, release_id, dry_run=False, quest_ids=None):
    if quest_ids is not None and (not quest_ids or any(type(identity) is not int or identity <= 0 for identity in quest_ids)):
        raise ValueError('Select positive quest IDs')
    release = await session.get(GameRelease, release_id)
    if release is None:
        raise ValueError("Unknown imported release")
    await session.execute(text("SELECT pg_advisory_xact_lock(703, :id)"), {"id": release_id})

    async def rows(source):
        return list(await session.execute(
            select(SourceRecord.row_index, SourceRecord.data)
            .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
            .where(SourceRecord.release_id == release_id, SourceFile.release_id == release_id,
                   SourceFile.logical_source_path == source)
            .order_by(SourceRecord.row_index)
        ))

    dungeons = {row["Id"] for _, row in await rows(DUNGEONS)}
    quest_nodes = await rows(QUEST_NODES)
    level_nodes = await rows(LEVEL_NODES)
    required = {key: value for key, value in requirements(quest_nodes).items()
                if key in dungeons}
    level_rows = await rows(LEVELS)
    memberships = level_memberships(level_rows, required)
    states = set(await session.scalars(
        select(QuestState.state_key).join(NodeRevision,
                                          NodeRevision.node_id == QuestState.node_id)
        .where(NodeRevision.release_id == release_id)
    ))
    links = list(dungeon_links(required, memberships, level_nodes, states))
    # Newer clients may recover missing entity tables, but only with matching
    # scenario membership and node records in this imported snapshot.
    from wuwa_story.ingestion.quest_tree_order import records_match
    level_records = {}
    for _, row in level_nodes:
        identity, _, node = row.get('Key', '').partition('_')
        if identity.isdecimal() and node.isdecimal() and isinstance(row.get('Data'), dict):
            level_records.setdefault(int(identity), {})[int(node)] = row['Data']
    indexes = {}
    verified_trees = set()
    memberships_by_id = {row['LevelPlayId']: row['Data'] for _, row in level_rows if isinstance(row.get('Data'), dict)}
    trees = list(await session.scalars(select(AuthoredTreeOrder).where(
            AuthoredTreeOrder.tree_kind == 'level_play', AuthoredTreeOrder.tree_id.in_(level_records))))
    for tree in sorted(trees, key=lambda tree: (tuple(map(int, tree.asset_version.split('.'))), tree.download_id)):
        if records_match(tree.nodes, level_records[tree.tree_id]):
            verified_trees.add(tree.tree_id)
            root = next((node for node in tree.nodes if node.get('path') == []), {})
            if (tree.tree_id in memberships_by_id and root.get('interaction_membership_sha256')
                    == membership_hash(memberships_by_id[tree.tree_id])):
                indexes[tree.tree_id] = tree.nodes
    links.extend(interaction_links(quest_nodes, level_rows, indexes, states))
    links.extend(required_level_links(quest_nodes, level_nodes, verified_trees, states))
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
    state_rows = await rows(STATES)
    context = list(authored_flow_context(quest_nodes, await rows(FLOWS), state_rows, records, await rows(PLOT)))
    context_quests = set(await session.scalars(select(Node.canonical_key).join(
        NodeRevision, NodeRevision.node_id == Node.id).where(
        NodeRevision.release_id == release_id,
        Node.canonical_key.in_({link['from'] for link in context})))) if context else set()
    records += [{**link, 'version': release.game_version,
                 'source_repository': release.upstream_name, 'source_commit': release.upstream_commit}
                for link in context if link['from'] in context_quests
                and link['to'].removeprefix('flow_state:') in states]
    packages = list(package_cutscene_links(await rows(QUEST_VIDEO), await rows(VIDEO_DATA)))
    package_nodes = set(await session.scalars(
        select(Node.canonical_key).join(NodeRevision, NodeRevision.node_id == Node.id)
        .where(NodeRevision.release_id == release_id,
               Node.canonical_key.in_({link[key] for link in packages for key in ("from", "to")}))
    )) if packages else set()
    records += [{**link, "version": release.game_version, "source_repository": release.upstream_name,
                 "source_commit": release.upstream_commit}
                for link in packages if link["from"] in package_nodes and link["to"] in package_nodes]
    known_cutscenes = {
        key for key in await session.scalars(select(Node.canonical_key).join(
            NodeRevision, NodeRevision.node_id == Node.id).where(
            NodeRevision.release_id == release_id, Node.canonical_key.like('cutscene:%')))
    }
    runtime_candidates = list(quest_runtime_links(quest_nodes, package_nodes | known_cutscenes))
    runtime_owners = set(await session.scalars(select(Node.canonical_key).where(
        Node.canonical_key.in_({link['from'] for link in runtime_candidates})))) if runtime_candidates else set()
    records += [{**link, 'version': release.game_version, 'source_repository': release.upstream_name,
                 'source_commit': release.upstream_commit}
                for link in runtime_candidates if link['from'] in runtime_owners]
    records = list({(row["type"], row["from"], row["to"],
                   row["raw_path"]): row for row in records}.values())
    if quest_ids is not None:
        records = [row for row in records if int(row['from'].split(':')[1]) in quest_ids]
    state_owners = [row for row in records if row['to'].startswith('flow_state:')]
    selected_states = {row['to'].removeprefix('flow_state:') for row in state_owners}
    movie_candidates = list(flow_movie_links(
        [(index, row) for index, row in state_rows if row.get('StateKey') in selected_states],
        {key.removeprefix('cutscene:') for key in known_cutscenes}))
    media_keys = {row[side] for row in movie_candidates for side in ('from', 'to')}
    observed_media = set(await session.scalars(select(Node.canonical_key).join(
        NodeRevision, NodeRevision.node_id == Node.id).where(NodeRevision.release_id == release_id,
        Node.canonical_key.in_(media_keys)))) if media_keys else set()
    records.extend({**row, 'version': release.game_version, 'source_repository': release.upstream_name,
                    'source_commit': release.upstream_commit} for row in movie_candidates
                   if row['from'] in observed_media and row['to'] in observed_media)
    source, target = Node.__table__.alias(
        "source"), Node.__table__.alias("target")
    owner_scope = []
    if quest_ids is not None:
        owners = select(Node.id).where(or_(
            Node.canonical_key.in_([f'quest:{identity}' for identity in sorted(quest_ids)]
                                  + sorted({row['from'] for row in records if row['from'].startswith('action:')})),
            *(Node.canonical_key.startswith(f'quest_node:{identity}:', autoescape=True)
              for identity in sorted(quest_ids)),
        ))
        owner_scope = [Edge.from_node_id.in_(owners)]
    existing_rows = list(await session.execute(
        select(EdgeEvidence.id, RelationType.key, source.c.canonical_key,
               target.c.canonical_key, EdgeEvidence.source_file_path, EdgeEvidence.source_raw_path)
        .select_from(Edge).join(RelationType, RelationType.id == Edge.relation_type_id)
        .join(source, source.c.id == Edge.from_node_id).join(target, target.c.id == Edge.to_node_id)
        .join(EdgeEvidence, EdgeEvidence.edge_id == Edge.id)
        .where(*owner_scope, RelationType.key.in_((RELATION, "references_scenario_flow_state", "references_packaged_cutscene", "plays_cutscene")), Edge.layer == "source",
               EdgeEvidence.release_id == release_id)
    ))
    # Refresh only this recipe's evidence in this release and requested quest scope.
    stored_context = list(await session.execute(select(
        EdgeEvidence.id, source.c.canonical_key, target.c.canonical_key,
        EdgeEvidence.source_file_path, EdgeEvidence.source_raw_path,
        EdgeEvidence.explanation).select_from(Edge).join(
        RelationType, RelationType.id == Edge.relation_type_id).join(
        source, source.c.id == Edge.from_node_id).join(target, target.c.id == Edge.to_node_id).join(
        EdgeEvidence, EdgeEvidence.edge_id == Edge.id).where(
        *owner_scope, Edge.layer == 'source', Edge.basis == 'inference', RelationType.key == 'references_scenario_flow_state',
        EdgeEvidence.release_id == release_id)))
    current_context = {(row['from'], row['to'], row['source'], row['raw_path'], row['basis'])
                       for row in records if isinstance(proof := decoded(row['basis']), dict)
                       and proof.get('recipe') == RECIPE}
    stale_context = []
    for evidence_id, owner, target_key, file, path, explanation in stored_context:
        if quest_ids is not None and int(owner.split(':')[1]) not in quest_ids:
            continue
        try:
            proof = json.loads(explanation)
        except (TypeError, ValueError):
            continue
        if isinstance(proof, dict) and proof.get('recipe') == RECIPE and (owner, target_key, file, path, explanation) not in current_context:
            stale_context.append({'id': evidence_id, 'from': owner, 'to': target_key,
                                  'source': file, 'raw_path': path})
    obsolete_ids = {row['id'] for row in stale_context}
    existing = {tuple(row[1:]) for row in existing_rows if row[0] not in obsolete_ids}
    missing = [row for row in records if (
        row['type'], row['from'], row['to'], row['source'], row['raw_path']) not in existing]
    if not dry_run and (missing or stale_context):
        if obsolete_ids:
            await session.execute(delete(EdgeEvidence).where(
                EdgeEvidence.release_id == release_id, EdgeEvidence.id.in_(obsolete_ids)))
        # Edges may retain other releases/recipes. Keep them; source consumers
        # require release evidence, so an edge without evidence grants no context.
        if missing:
            await import_canonical_edges(SimpleNamespace(iter_edges=lambda: iter(missing)), session,
                                         release_id, batch_size=len(missing))
        if any(row['type'] != 'references_scenario_flow_state'
               or not isinstance(proof := decoded(row['basis']), dict)
               or proof.get('recipe') != RECIPE for row in missing):
            await rebuild_cutscene_order(session, release_id)
        repaired_states = {row['from'].removeprefix('action:').rsplit(':', 1)[0]
                           for row in missing if row['from'].startswith('action:')}
        await invalidate_repaired_quests(session, release,
            [row for row in missing + stale_context if not row['from'].startswith('action:')]
            + [row for row in state_owners if row['to'].removeprefix('flow_state:') in repaired_states])
    result = {"release_id": release_id, "game_version": release.game_version,
              "candidate_links": len(records), "missing_evidence": len(missing),
              "written": len(missing) if not dry_run else 0, "dry_run": dry_run}
    result['authored_flow_context_links'] = len(current_context)
    result['stale_context_evidence'] = stale_context
    result['removed_context_evidence'] = len(stale_context) if not dry_run else 0
    result['flow_movie_links'] = sum(row['from'].startswith('action:') for row in records)
    await session.commit()
    return result


async def main(first, last, dry_run, quest_id=None):
    async with SessionFactory() as session:
        releases = list(await session.scalars(select(GameRelease).order_by(GameRelease.sequence)))

    def version(value): return tuple(map(int, value.split(".")))
    for release in releases:
        if version(first) <= version(release.game_version) <= version(last):
            async with SessionFactory() as session:
                print(json.dumps(await backfill(session, release.id, dry_run, set(quest_id) if quest_id else None)), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first", default="2.0.0")
    parser.add_argument("--last", default="2.8.0")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument('--quest-id', type=int, action='append')
    asyncio.run(main(**vars(parser.parse_args())))
