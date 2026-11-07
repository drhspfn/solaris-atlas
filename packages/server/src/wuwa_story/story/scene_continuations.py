"""Explain packaged scenes owned by a source-linked later quest."""

from sqlalchemy import select

from wuwa_story.db.models.graph import Edge, EdgeEvidence
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.repositories import cutscenes
from wuwa_story.story.service import _localized


async def scene_continuations(session, scene_ids, quest_node_id, release_id, locale):
    if not scene_ids:
        return {}
    owners = [row for row in await cutscenes.owners(session, scene_ids, release_id)
              if not row[3] and row[1].node_id != quest_node_id]
    if not owners:
        return {}
    links = list(await session.execute(select(Edge.from_node_id, EdgeEvidence.source_file_path,
                                              EdgeEvidence.source_raw_path).join(RelationType, RelationType.id == Edge.relation_type_id)
                                       .join(EdgeEvidence, EdgeEvidence.edge_id == Edge.id).where(
        Edge.from_node_id.in_(
            {quest.node_id for _, quest, _, _ in owners}),
        Edge.to_node_id == quest_node_id, Edge.layer == 'source', RelationType.key == 'requires_quest',
        EdgeEvidence.release_id == release_id).distinct()))
    prerequisites = {identity: {'file': file, 'raw_path': path, 'release_id': release_id}
                     for identity, file, path in links}
    result = {}
    for scene, quest, _slug, _inferred in owners:
        if quest.node_id not in prerequisites:
            continue
        title = await _localized(session, quest.name_key_id, locale, release_id) if quest.name_key_id else None
        result.setdefault(scene, []).append({'game_quest_id': quest.game_quest_id,
                                             'title': title, 'basis': 'owning_quest_requires_current_quest',
                                             'source': prerequisites[quest.node_id]})
    return result
