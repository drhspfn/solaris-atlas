"""Publish generated artifacts separately from imported facts, in one transaction."""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.agents.contracts import AnalysisResult, Citation, citation_groups
from wuwa_story.agents.evidence import hash_value
from wuwa_story.db.models.agents import AgentJob, AgentNote, ExplanationEmbedding
from wuwa_story.db.models.content import Document, DocumentHead, DocumentReference
from wuwa_story.db.models.graph import Edge, EdgeEvidence, Node, NodeType
from wuwa_story.db.models.lore import LoreChunk
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.ops import ProcessingRun
from wuwa_story.db.models.search import EmbeddingModel
from wuwa_story.db.models.story import Claim, ClaimEvidence, Event


def document_type(release_id: int) -> str:
    return f"story-explanation:{release_id}"


async def publish_analysis(
    session: AsyncSession,
    job: AgentJob,
    run: ProcessingRun,
    result: AnalysisResult,
    vectors: list[list[float]],
    *,
    source_receipts: list[dict[str, Any]] | None = None,
    source_nodes: dict[str, str] | None = None,
) -> Document:
    # Serialize document revisions across different model jobs for the same quest/locale/version.
    assert run.target_node_id is not None
    node = await session.scalar(select(Node).where(Node.id == run.target_node_id).with_for_update())
    assert node is not None
    revisit = run.metadata_json.get("revisit")
    kind = (
        f"story-recontextualization:{revisit['document_id']}:{revisit['release_id']}"
        if revisit
        else document_type(job.release_id)
    )
    revision = (
        await session.scalar(
            select(func.max(Document.revision)).where(
                Document.node_id == node.id,
                Document.locale_id == job.locale_id,
                Document.document_type == kind,
            )
        )
        or 0
    ) + 1
    reference_ids = {c.node_id for group in citation_groups(result) for c in group.citations}
    reference_ids.update(value for block in result.blocks for value in block.related_node_ids)
    reference_ids.update(
        record.node_id for block in result.blocks for record in block.related_records
    )
    reference_ids.update(
        value for link in result.links for value in (link.from_node_id, link.to_node_id)
    )
    document = Document(
        node_id=node.id,
        locale_id=job.locale_id,
        document_type=kind,
        revision=revision,
        title=result.title,
        plain_text="\n\n".join(result.search_text(i) for i in range(len(result.blocks))),
        body_ast=[block.model_dump() for block in result.blocks],
        source_hash=run.input_hash or hash_value(result.model_dump()),
        processor_run_id=run.id,
        metadata_json={
            "release_id": job.release_id,
            "schema_version": run.prompt_version,
            "generated": True,
            "revisit": revisit,
            "revisited_hooks": [review.model_dump() for review in result.revisited_hooks],
            "source_scope": "all_locales" if run.prompt_version != "story-v1" else "locale",
            "unresolved_questions": result.unresolved_questions,
            "assessment": result.assessment.model_dump() if result.assessment else None,
            "cutscene_descriptions": [
                description.model_dump() for description in result.cutscene_descriptions
            ],
            "authored_quest_type": job.checkpoint.get("authored_quest_type"),
            "narrative_function": result.narrative_function,
            "knowledge_boundary": result.knowledge_boundary.model_dump()
            if result.knowledge_boundary
            else None,
            "scene_knowledge": result.scene_knowledge.model_dump() if result.scene_knowledge else None,
            "hooks": [hook.model_dump() for hook in result.hooks],
            "review": result.review.model_dump() if result.review else None,
            "links": [link.model_dump() for link in result.links],
            "source_release_ids": run.metadata_json.get("source_release_ids", [job.release_id]),
            "source_receipts": source_receipts or [],
            "source_nodes": {
                key: value
                for key, value in (source_nodes or {}).items()
                if int(key.split(":")[1]) in reference_ids
            },
        },
    )
    session.add(document)
    await session.flush()
    await session.execute(
        insert(DocumentHead)
        .values(
            node_id=node.id, locale_id=job.locale_id, document_type=kind, document_id=document.id
        )
        .on_conflict_do_update(
            constraint="uq_document_head_identity", set_={"document_id": document.id}
        )
    )
    refs: list[tuple[int, str, str | None]] = [
        (citation.node_id, "citation", citation.quote[:512])
        for group in citation_groups(result)
        for citation in group.citations
    ]
    refs += [
        (related, "related", None) for block in result.blocks for related in block.related_node_ids
    ]
    refs += [
        (record.node_id, "related", record.label)
        for block in result.blocks
        for record in block.related_records
    ]
    refs += [
        (item.chronology_in_quest.anchor_node_id, "chronology", item.chronology_in_quest.label)
        for block in result.blocks
        for item in block.assertions
    ]
    refs += [
        (resolution.revealed_in_node_id, "later_resolution", resolution.text[:512])
        for block in result.blocks
        for item in block.assertions
        for resolution in item.later_resolution
    ]
    claim_refs: list[tuple[int, int, str]] = []

    async def link(
        source: int,
        target: int,
        relation: str,
        explanation: str,
        confidence: float,
        citations: list[Citation],
    ) -> None:
        relation_id = await session.scalar(
            select(RelationType.id).where(RelationType.key == relation)
        )
        claim = Claim(
            subject_node_id=source,
            object_node_id=target,
            predicate_type_id=relation_id,
            object_text=explanation,
            claim_kind="inference",
            confidence=confidence,
            status="generated",
            processor_run_id=run.id,
            metadata_json={
                "release_id": job.release_id,
                "locale_id": job.locale_id,
                "citations": [c.model_dump() for c in citations],
            },
        )
        session.add(claim)
        await session.flush()
        for citation_id in {c.node_id for c in citations}:
            session.add(ClaimEvidence(claim_id=claim.id, evidence_node_id=citation_id, relevance=1))
        edge_id = await session.scalar(
            insert(Edge)
            .values(
                from_node_id=source,
                to_node_id=target,
                relation_type_id=relation_id,
                layer="semantic",
                basis="inference",
                confidence=confidence,
                processor_run_id=run.id,
                created_release_id=job.release_id,
            )
            .on_conflict_do_nothing(constraint="uq_edge_identity")
            .returning(Edge.id)
        )
        if edge_id is None:
            edge_id = await session.scalar(
                select(Edge.id).where(
                    Edge.from_node_id == source,
                    Edge.to_node_id == target,
                    Edge.relation_type_id == relation_id,
                    Edge.layer == "semantic",
                    Edge.basis == "inference",
                )
            )
        for index, citation in enumerate(citations):
            session.add(
                EdgeEvidence(
                    edge_id=edge_id,
                    release_id=citation.snapshot_id or job.release_id,
                    evidence_node_id=citation.node_id,
                    source_file_path=f"agent://run/{run.id}",
                    source_raw_path=f"claim/{claim.id}/{index}",
                    explanation=explanation + "\n" + citation.quote,
                )
            )
        claim_refs.append((target, claim.id, relation))

    for item in result.links:
        if item.certainty == "theory":
            continue
        await link(
            item.from_node_id,
            item.to_node_id,
            item.relation,
            item.explanation,
            item.confidence,
            item.citations,
        )
    for event in result.events:
        key = "agent-event:" + hash_value({"run": run.id, "event": event.model_dump()}).hex()
        event_node = Node(
            type_id=await session.scalar(select(NodeType.id).where(NodeType.key == "event")),
            canonical_key=key,
            created_release_id=job.release_id,
            metadata_json={"generated": True},
        )
        session.add(event_node)
        await session.flush()
        session.add(
            Event(
                node_id=event_node.id,
                title=event.title,
                semantic_status="generated",
                processor_run_id=run.id,
                metadata_json={
                    "release_id": job.release_id,
                    "locale_id": job.locale_id,
                    "description": event.description,
                    "citations": [c.model_dump() for c in event.citations],
                },
            )
        )
        refs.append((event_node.id, "event", event.title))
        for participant in event.participant_node_ids:
            await link(
                event_node.id, participant, "INVOLVES", event.description, 1, event.citations
            )
    # References have stable contiguous ordinals within each immutable document.
    for ordinal, (target, ref_kind, label) in enumerate(refs):
        session.add(
            DocumentReference(
                document_id=document.id,
                ordinal=ordinal,
                target_node_id=target,
                reference_type=ref_kind,
                label=label,
            )
        )
    for ordinal, (target, claim_id, relation) in enumerate(claim_refs, start=len(refs)):
        session.add(
            DocumentReference(
                document_id=document.id,
                ordinal=ordinal,
                target_node_id=target,
                claim_id=claim_id,
                reference_type="inferred_link",
                label=relation,
            )
        )
    for question in result.unresolved_questions:
        session.add(
            AgentNote(
                run_id=run.id,
                node_id=node.id,
                release_id=job.release_id,
                locale_id=job.locale_id,
                kind="missing_data",
                text=question[:3000],
                citations=[],
            )
        )
    if vectors:
        key = (
            "story:"
            + hash_value(
                {
                    "provider": job.config["provider"],
                    "base_url": job.config["base_url"],
                    "model": job.config["embedding_model"],
                    "dimensions": job.config["embedding_dimensions"],
                }
            ).hex()
        )
        await session.execute(
            insert(EmbeddingModel)
            .values(
                key=key,
                provider=job.config["provider"],
                model_name=job.config["embedding_model"],
                dimensions=job.config["embedding_dimensions"],
                distance_metric="cosine",
                config={"base_url": job.config["base_url"]},
                active=True,
            )
            .on_conflict_do_nothing(index_elements=[EmbeddingModel.key])
        )
        model_id = await session.scalar(select(EmbeddingModel.id).where(EmbeddingModel.key == key))
        for ordinal, vector in enumerate(vectors):
            session.add(
                ExplanationEmbedding(
                    document_id=document.id,
                    ordinal=ordinal,
                    model_id=model_id,
                    content_hash=hash_value(result.search_text(ordinal)),
                    embedding=vector,
                )
            )
    
    if result.scene_knowledge:
        # quest_summary
        session.add(
            LoreChunk(
                source_type="quest",
                source_id=str(node.id),
                quest_id=str(node.id),
                characters=result.scene_knowledge.characters,
                chunk_type="quest_summary",
                content=result.scene_knowledge.summary,
            )
        )
        # story_facts
        for fact in result.scene_knowledge.facts:
            session.add(
                LoreChunk(
                    source_type="quest",
                    source_id=str(node.id),
                    quest_id=str(node.id),
                    characters=result.scene_knowledge.characters,
                    chunk_type="story_fact",
                    content=fact.claim,
                )
            )
        # story_events
        for event in result.scene_knowledge.events:
            session.add(
                LoreChunk(
                    source_type="quest",
                    source_id=str(node.id),
                    quest_id=str(node.id),
                    characters=event.participants,
                    chunk_type="scene_event",
                    content=event.description,
                    timestamp_start=event.timestamp_start,
                    timestamp_end=event.timestamp_end,
                )
            )

    job.document_id = document.id
    return document
