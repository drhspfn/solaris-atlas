"""Bridge Fandom entity bundles into the shared fact board, graph edges, and visual memory."""

from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.agents.fact_board import post_fact
from wuwa_story.db.models.search import EntityAlias
from wuwa_story.fandom.models import FandomEntityBundle, LicenseFilter
from wuwa_story.visual_memory.web_enrichment import (
    ensure_visual_identity_for_node,
    validate_image_bytes,
)


async def _find_node_id_by_name(session: AsyncSession, name: str) -> int | None:
    normalized = " ".join(name.strip().lower().split())
    if not normalized:
        return None
    return await session.scalar(
        select(EntityAlias.node_id)
        .where(func.lower(EntityAlias.normalized_alias) == normalized)
        .limit(1)
    )


async def sync_fandom_bundle(
    session: AsyncSession,
    bundle: FandomEntityBundle,
    *,
    subject_node_id: int,
    run_id: int | None = None,
    memory_service: Any | None = None,
    import_images: bool = False,
    max_images: int = 8,
    license_filter: LicenseFilter = LicenseFilter.IGNORE,
) -> dict[str, Any]:
    """Persist Fandom URL, infobox relations, and optional gallery images for an entity node."""
    created_facts: list[int] = []
    created_edges: list[int] = []

    summary_text = bundle.summary or f"Fandom reference page: {bundle.page_url}"
    main_fact = await post_fact(
        session,
        key=f"fandom:page:{subject_node_id}:{bundle.page_title}",
        fact_type="fandom_fact",
        title=f"Fandom: {bundle.page_title}",
        description=summary_text,
        importance=3,
        confidence="fandom",
        source_kind="fandom",
        subject_node_id=subject_node_id,
        run_id=run_id,
        tags=[bundle.entity_kind.value, "fandom"],
        citations=[{"url": bundle.page_url,
                    "title": bundle.page_title, "license": bundle.license.text}],
        metadata={
            "page_url": bundle.page_url,
            "gallery_url": bundle.gallery_url,
            "infobox_template": bundle.infobox.template if bundle.infobox else None,
            "infobox_fields": bundle.infobox.fields if bundle.infobox else {},
        },
        create_edge=False,
    )
    if main_fact.get("id"):
        created_facts.append(main_fact["id"])

    for rel in bundle.relations:
        target_node_id = await _find_node_id_by_name(session, rel.target_name)
        fact = await post_fact(
            session,
            key=f"fandom:rel:{subject_node_id}:{rel.relation_type}:{rel.target_name.lower()}",
            fact_type="relationship",
            title=f"{bundle.page_title} {rel.relation_type} {rel.target_name}",
            description=f"Fandom infobox ({rel.source_field}): {rel.target_name}"
            + (f" ({rel.note})" if rel.note else ""),
            importance=4 if rel.relation_type in {
                "parent_of", "child_of", "affiliated_with"} else 3,
            confidence="fandom",
            source_kind="fandom",
            subject_node_id=subject_node_id,
            object_node_id=target_node_id,
            relation_type=rel.relation_type,
            run_id=run_id,
            tags=["fandom", rel.relation_type],
            citations=[{"url": bundle.page_url, "field": rel.source_field}],
            create_edge=target_node_id is not None,
        )
        if fact.get("id"):
            created_facts.append(fact["id"])
        if fact.get("edge_id"):
            created_edges.append(fact["edge_id"])

    imported_references: list[int] = []
    if import_images and memory_service is not None and bundle.images:
        identity_id = await ensure_visual_identity_for_node(session, subject_node_id, bundle.page_title)
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as http:
            for asset in bundle.images:
                if len(imported_references) >= max_images:
                    break
                if license_filter == LicenseFilter.CC_BY_SA_ONLY and not asset.license.is_cc_by_sa:
                    continue
                try:
                    resp = await http.get(asset.url, headers={"Referer": bundle.page_url})
                    resp.raise_for_status()
                except Exception:
                    continue
                if validate_image_bytes(resp.content) is None:
                    continue
                ref = await memory_service.add_reference(
                    resp.content,
                    identity_ids=[identity_id],
                    source_type="fandom",
                    source_url=asset.url,
                    source_page=bundle.page_url,
                    rights=asset.license.text or "CC-BY-SA",
                    status="pending",
                    store_image=True,
                )
                if isinstance(ref, dict) and ref.get("id"):
                    imported_references.append(int(ref["id"]))

    return {
        "page_url": bundle.page_url,
        "gallery_url": bundle.gallery_url,
        "facts_count": len(created_facts),
        "edges_count": len(created_edges),
        "imported_references": imported_references,
    }
