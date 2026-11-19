"""Shared cross-agent fact board, note search, and graph-edge helper."""

import hashlib
from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.agent_workspace import AgentFactBoard, AgentWorkItem
from wuwa_story.db.models.agents import AgentNote
from wuwa_story.db.models.graph import Edge, EdgeEvidence, Node
from wuwa_story.db.models.ontology import RelationType

FACT_RELATION_TO_ONTOLOGY = {
    "appears_in": "INVOLVES",
    "uses_item": "USES_ITEM",
    "located_at": "LOCATED_AT",
    "located_in": "LOCATED_AT",
    "related_to_event": "RELATED_TO",
    "related_to_character": "RELATED_TO",
    "related_to_quest": "RELATED_TO",
    "mentions": "MENTIONS",
    "parent_of": "PARENT_OF",
    "child_of": "CHILD_OF",
    "sibling_of": "SIBLING_OF",
    "affiliated_with": "AFFILIATED_WITH",
    "character_arc_in": "CHARACTER_ARC_IN",
}


def _fact_key(
    *,
    fact_type: str,
    title: str,
    subject_node_id: int | None,
    object_node_id: int | None,
    relation_type: str | None,
    cutscene_node_id: int | None,
    quest_node_id: int | None,
) -> str:
    raw = "|".join(
        [
            fact_type,
            str(subject_node_id or 0),
            str(object_node_id or 0),
            relation_type or "",
            str(cutscene_node_id or 0),
            str(quest_node_id or 0),
            title.strip().lower(),
        ]
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"fact:{fact_type}:{digest}"


async def ensure_relation_edge(
    db: AsyncSession,
    *,
    from_node_id: int,
    to_node_id: int,
    relation_key: str,
    basis: str = "inference",
    confidence: float = 0.85,
    evidence: dict[str, Any] | None = None,
) -> Edge | None:
    """Create or reinforce a semantic graph edge between two existing nodes."""
    if from_node_id == to_node_id:
        return None
    existing_nodes = set(
        await db.scalars(select(Node.id).where(Node.id.in_([from_node_id, to_node_id])))
    )
    if {from_node_id, to_node_id} - existing_nodes:
        return None

    rel_key = FACT_RELATION_TO_ONTOLOGY.get(relation_key, relation_key.upper())
    rel_id = await db.scalar(select(RelationType.id).where(RelationType.key == rel_key))
    if rel_id is None:
        await db.execute(text("LOCK TABLE ontology.relation_type IN SHARE ROW EXCLUSIVE MODE"))
        await db.execute(
            insert(RelationType)
            .values(
                id=select(func.coalesce(
                    func.max(RelationType.id), 0) + 1).scalar_subquery(),
                key=rel_key,
                label=rel_key.replace("_", " ").title(),
                description=f"Agent-discovered relation {rel_key}",
            )
            .on_conflict_do_nothing(index_elements=[RelationType.key])
        )
        rel_id = await db.scalar(select(RelationType.id).where(RelationType.key == rel_key))

    _VALID_BASES = {
        "explicit_reference",
        "exact_join",
        "authored_order",
        "source_array_adjacency",
        "semantic_extraction",
        "inference",
        "manual",
    }
    edge_basis = basis if basis in _VALID_BASES else "inference"
    edge = await db.scalar(
        select(Edge).where(
            Edge.from_node_id == from_node_id,
            Edge.to_node_id == to_node_id,
            Edge.relation_type_id == rel_id,
            Edge.layer == "semantic",
        )
    )
    if edge is None:
        edge = Edge(
            from_node_id=from_node_id,
            to_node_id=to_node_id,
            relation_type_id=rel_id,
            layer="semantic",
            basis=edge_basis,
            confidence=confidence,
        )
        db.add(edge)
        await db.flush()
    if evidence:
        db.add(
            EdgeEvidence(
                edge_id=edge.id,
                evidence_node_id=evidence.get("evidence_node_id"),
                source_file_path=evidence.get("source_file_path"),
                explanation=str(evidence.get("excerpt") or "")[:2000] or None,
            )
        )
    return edge


async def post_fact(
    db: AsyncSession,
    *,
    fact_type: str,
    title: str,
    description: str,
    key: str | None = None,
    importance: int = 3,
    confidence: str = "observed",
    source_kind: str = "quest_analysis",
    status: str = "active",
    subject_node_id: int | None = None,
    object_node_id: int | None = None,
    relation_type: str | None = None,
    quest_node_id: int | None = None,
    cutscene_node_id: int | None = None,
    question_id: int | None = None,
    note_id: int | None = None,
    run_id: int | None = None,
    tags: list[str] | None = None,
    citations: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
    create_edge: bool = True,
) -> dict[str, Any]:
    """Upsert a structured fact on the shared fact board and optionally link nodes in graph.edge."""
    importance = max(1, min(int(importance), 5))
    fact_key = key or _fact_key(
        fact_type=fact_type,
        title=title,
        subject_node_id=subject_node_id,
        object_node_id=object_node_id,
        relation_type=relation_type,
        cutscene_node_id=cutscene_node_id,
        quest_node_id=quest_node_id,
    )
    stmt = (
        insert(AgentFactBoard)
        .values(
            key=fact_key[:256],
            fact_type=fact_type[:48],
            title=title[:240],
            description=description,
            importance=importance,
            confidence=confidence[:24],
            source_kind=source_kind[:48],
            status=status[:24],
            subject_node_id=subject_node_id,
            object_node_id=object_node_id,
            relation_type=relation_type[:64] if relation_type else None,
            quest_node_id=quest_node_id,
            cutscene_node_id=cutscene_node_id,
            question_id=question_id,
            note_id=note_id,
            run_id=run_id,
            tags=list(tags or []),
            citations=list(citations or []),
            metadata_json=dict(metadata or {}),
        )
        .on_conflict_do_update(
            index_elements=[AgentFactBoard.key],
            set_={
                "title": title[:240],
                "description": description,
                "importance": importance,
                "confidence": confidence[:24],
                "status": status[:24],
                "question_id": func.coalesce(AgentFactBoard.question_id, question_id),
                "note_id": func.coalesce(AgentFactBoard.note_id, note_id),
                "tags": list(tags or []),
                "citations": list(citations or []),
                "metadata_json": dict(metadata or {}),
                "updated_at": func.now(),
            },
        )
        .returning(AgentFactBoard.id)
    )
    fact_id = await db.scalar(stmt)

    edge_id: int | None = None
    if create_edge and confidence != "theory":
        target_id = object_node_id or cutscene_node_id or quest_node_id
        if subject_node_id and target_id and relation_type:
            edge = await ensure_relation_edge(
                db,
                from_node_id=subject_node_id,
                to_node_id=target_id,
                relation_key=relation_type,
                basis=source_kind,
                confidence=0.9 if confidence == "observed" else 0.75,
                evidence={"source_type": source_kind,
                          "fact_id": fact_id, "excerpt": title[:300]},
            )
            edge_id = edge.id if edge else None

    return {"id": fact_id, "key": fact_key, "edge_id": edge_id, "status": status}


async def search_facts(
    db: AsyncSession,
    *,
    query: str | None = None,
    fact_type: str | None = None,
    subject_node_id: int | None = None,
    object_node_id: int | None = None,
    quest_node_id: int | None = None,
    cutscene_node_id: int | None = None,
    question_id: int | None = None,
    min_importance: int = 1,
    status: str | None = "active",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search the shared cross-agent fact board."""
    limit = max(1, min(int(limit), 50))
    stmt = select(AgentFactBoard).where(
        AgentFactBoard.importance >= int(min_importance))
    if status:
        stmt = stmt.where(AgentFactBoard.status == status)
    if fact_type:
        stmt = stmt.where(AgentFactBoard.fact_type == fact_type)
    if subject_node_id is not None:
        stmt = stmt.where(
            or_(
                AgentFactBoard.subject_node_id == subject_node_id,
                AgentFactBoard.object_node_id == subject_node_id,
            )
        )
    if object_node_id is not None:
        stmt = stmt.where(AgentFactBoard.object_node_id == object_node_id)
    if quest_node_id is not None:
        stmt = stmt.where(AgentFactBoard.quest_node_id == quest_node_id)
    if cutscene_node_id is not None:
        stmt = stmt.where(AgentFactBoard.cutscene_node_id == cutscene_node_id)
    if question_id is not None:
        stmt = stmt.where(AgentFactBoard.question_id == question_id)
    if query and query.strip():
        needle = f"%{query.strip()}%"
        stmt = stmt.where(
            or_(
                AgentFactBoard.title.ilike(needle),
                AgentFactBoard.description.ilike(needle),
            )
        )
    rows = list(
        await db.scalars(
            stmt.order_by(AgentFactBoard.importance.desc(),
                          AgentFactBoard.updated_at.desc()).limit(limit)
        )
    )
    return [
        {
            "id": row.id,
            "key": row.key,
            "fact_type": row.fact_type,
            "title": row.title,
            "description": row.description,
            "importance": row.importance,
            "confidence": row.confidence,
            "source_kind": row.source_kind,
            "status": row.status,
            "subject_node_id": row.subject_node_id,
            "object_node_id": row.object_node_id,
            "relation_type": row.relation_type,
            "quest_node_id": row.quest_node_id,
            "cutscene_node_id": row.cutscene_node_id,
            "question_id": row.question_id,
            "note_id": row.note_id,
            "tags": row.tags,
            "citations": row.citations,
        }
        for row in rows
    ]


async def search_notes(
    db: AsyncSession,
    *,
    query: str | None = None,
    node_id: int | None = None,
    kind: str | None = None,
    status: str | None = "open",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search ops.agent_note records so agents can inspect missing_data/contradiction alerts."""
    limit = max(1, min(int(limit), 50))
    stmt = select(AgentNote)
    if status:
        stmt = stmt.where(AgentNote.status == status)
    if kind:
        stmt = stmt.where(AgentNote.kind == kind)
    if node_id is not None:
        stmt = stmt.where(AgentNote.node_id == node_id)
    if query and query.strip():
        stmt = stmt.where(AgentNote.text.ilike(f"%{query.strip()}%"))
    rows = list(await db.scalars(stmt.order_by(AgentNote.created_at.desc()).limit(limit)))
    return [
        {
            "id": row.id,
            "run_id": row.run_id,
            "node_id": row.node_id,
            "kind": row.kind,
            "status": row.status,
            "text": row.text,
            "citations": row.citations,
        }
        for row in rows
    ]


async def link_fact_to_question(db: AsyncSession, *, fact_id: int, question_id: int) -> dict[str, Any]:
    """Attach an existing AgentFactBoard entry to an open question in ops.agent_work_item."""
    fact = await db.get(AgentFactBoard, fact_id)
    if fact is None:
        return {"error": f"Fact {fact_id} not found"}
    question = await db.get(AgentWorkItem, question_id)
    if question is None or question.kind != "question":
        return {"error": f"Question {question_id} not found"}
    fact.question_id = question.id
    await db.flush()
    return {"fact_id": fact.id, "question_id": question.id, "linked": True}
