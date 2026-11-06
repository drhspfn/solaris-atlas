"""Remove legacy name-derived NPC speaker associations.

Call repair_npc_identity(session), commit, then rebuild the current character
lexical index. The helper queues current character projections and removes
affected search documents in the same transaction. It never changes raw rows.
The CLI performs all three steps; rerun it after an interrupted index rebuild.
"""

import argparse
import asyncio
import json

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import array

from wuwa_story.db.models.core import Character
from wuwa_story.db.models.graph import Edge, EdgeEvidence, Node
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.search import SearchDocument
from wuwa_story.db.session import SessionFactory
from wuwa_story.ingestion.releases import release_order
from wuwa_story.read_models.refresh import mark_dirty
from wuwa_story.search.indexer import build_lexical_index

NAME_BASIS = "exact NPC name key equals Speaker_<Id>_Name"


async def repair_npc_identity(session):
    """Queue affected projections; caller commits and rebuilds character search."""
    npc_nodes = select(Node.id).where(Node.canonical_key.startswith("character:npc:"))
    relation = select(RelationType.id).where(RelationType.key == "has_speaker")
    candidates = select(Edge.id).where(
        Edge.from_node_id.in_(npc_nodes), Edge.relation_type_id.in_(relation),
        Edge.layer == "source", Edge.basis == "explicit_reference")
    evidence_edges = select(EdgeEvidence.edge_id).where(
        EdgeEvidence.edge_id.in_(candidates), EdgeEvidence.explanation == NAME_BASIS)
    affected = set(await session.scalars(select(Edge.from_node_id).where(
        Edge.id.in_(evidence_edges))))
    removed_evidence = (await session.execute(delete(EdgeEvidence).where(
        EdgeEvidence.edge_id.in_(candidates), EdgeEvidence.explanation == NAME_BASIS
    ).returning(EdgeEvidence.id))).all()
    # Do not delete an edge if another source still supports it, even when its
    # first-import metadata records the retired name-based basis.
    removed_edges = (await session.execute(delete(Edge).where(
        Edge.id.in_(candidates), Edge.metadata_json["source_basis"].as_string() == NAME_BASIS,
        ~select(EdgeEvidence.id).where(EdgeEvidence.edge_id == Edge.id).exists(),
    ).returning(Edge.from_node_id))).scalars().all()
    affected.update(removed_edges)
    metadata = Character.metadata_json
    cleaned = (await session.execute(update(Character).where(
        Character.node_id.in_(npc_nodes), metadata["role_type"].as_integer() == 6,
        metadata["npc"]["speakers"].is_not(None),
        metadata["npc"]["speakers"] != func.jsonb_build_array(),
    ).values(metadata_json=func.jsonb_set(metadata, array(["npc", "speakers"]), func.jsonb_build_array()))
        .returning(Character.node_id))).scalars().all()
    affected.update(cleaned)
    for offset in range(0, len(affected), 1000):
        ids = sorted(affected)[offset:offset + 1000]
        await session.execute(delete(SearchDocument).where(
            SearchDocument.target_node_id.in_(ids), SearchDocument.category == "character"))
        await mark_dirty(session, "character", ids)
    return {"affected_npcs": len(affected), "removed_evidence": len(removed_evidence),
            "removed_edges": len(removed_edges), "cleaned_metadata": len(cleaned)}


async def main():
    async with SessionFactory() as session:
        releases = list(await session.scalars(select(GameRelease)))
        if not releases:
            raise ValueError("No game release is available for the search rebuild")
        release = max(releases, key=release_order)
        result = await repair_npc_identity(session)
        await session.commit()
        result["indexed"] = await build_lexical_index(
            session, release.game_version, categories={"character"}, release_id=release.id)
        print(json.dumps(result))


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    asyncio.run(main())
