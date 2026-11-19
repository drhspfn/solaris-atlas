"""Admin endpoints for checking entity Fandom/visual sync state and running targeted Fandom sync."""

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.auth.dependencies import require_admin, require_csrf
from wuwa_story.db.models.agent_workspace import AgentFactBoard
from wuwa_story.db.models.graph import Node, NodeType
from wuwa_story.db.models.visual_memory import (
    VisualIdentity,
    VisualIdentityNode,
    VisualReference,
    VisualReferenceLink,
)
from wuwa_story.db.repositories.nodes import get_node
from wuwa_story.db.session import get_session
from wuwa_story.fandom import FandomClient, FandomEntityKind, LicenseFilter, sync_fandom_bundle
from wuwa_story.fandom.sync import entity_href
from wuwa_story.story.service import _node_label
from wuwa_story.visual_memory.tools import service_for

router = APIRouter(
    prefix="/admin/fandom",
    tags=["admin", "fandom"],
    dependencies=[Depends(require_admin)],
)


class FandomSyncRequest(BaseModel):
    node_id: int | None = Field(default=None, gt=0)
    canonical_key: str | None = Field(
        default=None, min_length=1, max_length=256)
    entity_kind: FandomEntityKind | None = None
    page_title_override: str | None = Field(default=None, max_length=200)
    actions: list[
        Literal["sync_facts", "sync_relations",
                "import_images", "run_visual_analysis"]
    ] = Field(default_factory=lambda: ["sync_facts"])
    license_filter: LicenseFilter = LicenseFilter.IGNORE
    max_images: int = Field(default=8, ge=1, le=24)
    locale: str = Field(default="en", min_length=1, max_length=32)


async def _resolve_target_node(
    session: AsyncSession,
    *,
    node_id: int | None,
    canonical_key: str | None,
) -> tuple[Node, str]:
    if node_id is not None:
        row = (
            await session.execute(
                select(Node, NodeType.key)
                .join(NodeType, NodeType.id == Node.type_id)
                .where(Node.id == node_id)
            )
        ).first()
        if row is not None:
            return row[0], row[1]
    if canonical_key:
        node = await get_node(session, canonical_key)
        if node is not None:
            nt = await session.scalar(select(NodeType.key).where(NodeType.id == node.type_id))
            return node, nt or "other"
    raise HTTPException(404, "Entity node not found")


async def _build_status_payload(
    session: AsyncSession,
    node: Node,
    node_type: str,
    locale: str,
) -> dict[str, Any]:
    label = await _node_label(session, node.id, locale)
    facts = list(
        await session.scalars(
            select(AgentFactBoard)
            .where(
                AgentFactBoard.subject_node_id == node.id,
                AgentFactBoard.source_kind == "fandom",
                AgentFactBoard.status == "active",
            )
            .order_by(AgentFactBoard.importance.desc(), AgentFactBoard.updated_at.desc())
            .limit(30)
        )
    )
    page_fact = next((f for f in facts if f.fact_type == "fandom_fact"), None)
    rel_facts = [f for f in facts if f.fact_type == "relationship"]

    target_ids = [f.object_node_id for f in rel_facts if f.object_node_id]
    targets_by_id: dict[int, tuple[str, str | None, str]] = {}
    if target_ids:
        rows = (
            await session.execute(
                select(Node.id, Node.canonical_key, Node.slug, NodeType.key)
                .join(NodeType, NodeType.id == Node.type_id)
                .where(Node.id.in_(target_ids))
            )
        ).all()
        for tid, ckey, slug, ntype in rows:
            targets_by_id[tid] = (ckey, slug, ntype)

    visual_identity = await session.scalar(
        select(VisualIdentity)
        .join(VisualIdentityNode, VisualIdentityNode.identity_id == VisualIdentity.id)
        .where(VisualIdentityNode.node_id == node.id)
        .order_by(VisualIdentity.id)
        .limit(1)
    )
    visual_payload: dict[str, Any] = {
        "has_identity": visual_identity is not None,
        "identity_id": visual_identity.id if visual_identity else None,
        "identity_name": visual_identity.name if visual_identity else None,
        "kind": visual_identity.kind if visual_identity else None,
        "references_count": 0,
        "approved_count": 0,
        "preview_urls": [],
        "admin_url": "/admin/visual-memory",
    }
    if visual_identity is not None:
        ref_rows = (
            await session.execute(
                select(
                    VisualReference.id,
                    VisualReference.status,
                    VisualReference.source_url,
                )
                .join(
                    VisualReferenceLink,
                    VisualReferenceLink.reference_id == VisualReference.id,
                )
                .where(VisualReferenceLink.identity_id == visual_identity.id)
                .order_by(VisualReference.id.desc())
                .limit(24)
            )
        ).all()
        total_refs = await session.scalar(
            select(func.count(VisualReference.id))
            .join(
                VisualReferenceLink,
                VisualReferenceLink.reference_id == VisualReference.id,
            )
            .where(VisualReferenceLink.identity_id == visual_identity.id)
        )
        visual_payload["references_count"] = int(total_refs or 0)
        visual_payload["approved_count"] = sum(
            1 for _, status, _ in ref_rows if status == "approved")
        visual_payload["preview_urls"] = [
            f"/api/admin/visual-memory/references/{rid}/image"
            for rid, _, _ in ref_rows[:6]
        ]

    relations_payload = []
    for fact in rel_facts:
        tinfo = targets_by_id.get(
            fact.object_node_id) if fact.object_node_id else None
        relations_payload.append(
            {
                "id": fact.id,
                "title": fact.title,
                "description": fact.description,
                "relation_type": fact.relation_type,
                "object_node_id": fact.object_node_id,
                "href": entity_href(tinfo[0], tinfo[1], tinfo[2]) if tinfo else None,
            }
        )

    return {
        "entity": {
            "node_id": node.id,
            "canonical_key": node.canonical_key,
            "slug": node.slug,
            "node_type": node_type,
            "label": label.get("label") or node.slug or node.canonical_key,
        },
        "synced": page_fact is not None,
        "fandom_fact": (
            {
                "id": page_fact.id,
                "title": page_fact.title,
                "summary": page_fact.description,
                "page_url": (page_fact.metadata_json or {}).get("page_url"),
                "gallery_url": (page_fact.metadata_json or {}).get("gallery_url"),
                "infobox_template": (page_fact.metadata_json or {}).get("infobox_template"),
                "infobox_fields": (page_fact.metadata_json or {}).get("infobox_fields") or {},
                "updated_at": page_fact.updated_at.isoformat() if page_fact.updated_at else None,
            }
            if page_fact
            else None
        ),
        "relations": relations_payload,
        "visual": visual_payload,
    }


@router.get("/status")
async def fandom_status(
    node_id: int | None = Query(default=None, gt=0),
    canonical_key: str | None = Query(
        default=None, min_length=1, max_length=256),
    locale: str = Query(default="en", min_length=1, max_length=32),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    node, node_type = await _resolve_target_node(
        session, node_id=node_id, canonical_key=canonical_key
    )
    return await _build_status_payload(session, node, node_type, locale)


@router.post("/sync", dependencies=[Depends(require_csrf)])
async def fandom_sync(
    body: FandomSyncRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    node, node_type = await _resolve_target_node(
        session, node_id=body.node_id, canonical_key=body.canonical_key
    )
    client = FandomClient()
    actions = set(body.actions or ["sync_facts"])

    if body.page_title_override and body.page_title_override.strip():
        inferred = body.entity_kind or FandomEntityKind.CHARACTER
        bundle = await client.fetch_entity_bundle(
            inferred,
            body.page_title_override.strip(),
            include_gallery="import_images" in actions,
            license_filter=body.license_filter,
        )
    else:
        bundle = await client.resolve_for_db_entity(
            session,
            node.id,
            entity_kind=body.entity_kind,
            locale=body.locale,
            include_gallery="import_images" in actions,
            license_filter=body.license_filter,
        )
    if bundle is None:
        raise HTTPException(404, "Matching Fandom page was not found")

    memory_service = service_for(
        session) if "import_images" in actions else None
    sync_result = await sync_fandom_bundle(
        session,
        bundle,
        subject_node_id=node.id,
        memory_service=memory_service,
        sync_facts="sync_facts" in actions,
        sync_relations="sync_relations" in actions,
        import_images="import_images" in actions,
        max_images=body.max_images,
        license_filter=body.license_filter,
    )

    queued_jobs: list[dict[str, Any]] = []
    if "run_visual_analysis" in actions and node_type == "cutscene":
        from wuwa_story.api.routes.mcp_video_tools import enqueue_internal

        queued = await enqueue_internal({"asset_node_ids": [node.id]}, session)
        queued_jobs.append(queued)

    await session.commit()
    status_payload = await _build_status_payload(session, node, node_type, body.locale)
    return {
        "sync_result": {**sync_result, "queued_jobs": queued_jobs},
        "status": status_payload,
    }
