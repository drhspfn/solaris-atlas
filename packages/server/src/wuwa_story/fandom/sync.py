"""Bridge Fandom entity bundles into the shared fact board, graph edges, and visual memory."""

import asyncio
import logging
import urllib.request
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.agents.fact_board import post_fact
from wuwa_story.db.models.graph import Node, NodeType
from wuwa_story.db.models.search import EntityAlias
from wuwa_story.fandom.models import FandomEntityBundle, LicenseFilter
from wuwa_story.visual_memory.web_enrichment import (
    ensure_visual_identity_for_node,
    validate_image_bytes,
)

logger = logging.getLogger(__name__)


def _fetch_image_bytes(url: str, referer: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": referer,
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


async def _find_node_by_name(session: AsyncSession, name: str) -> tuple[int, str, str | None, str] | None:
    normalized = " ".join(name.strip().lower().split())
    if not normalized:
        return None
    row = (
        await session.execute(
            select(Node.id, Node.canonical_key, Node.slug, NodeType.key)
            .join(EntityAlias, EntityAlias.node_id == Node.id)
            .join(NodeType, NodeType.id == Node.type_id)
            .where(func.lower(EntityAlias.normalized_alias) == normalized)
            .limit(1)
        )
    ).first()
    if row is not None:
        return tuple(row)
    slug_tail = "-".join(normalized.split())
    if not slug_tail:
        return None
    fallback = (
        await session.execute(
            select(Node.id, Node.canonical_key, Node.slug, NodeType.key)
            .join(NodeType, NodeType.id == Node.type_id)
            .where(
                Node.slug.ilike(f"%-{slug_tail}"),
                NodeType.key.in_(("character", "speaker", "quest", "item", "location", "area")),
            )
            .order_by(func.length(Node.slug), Node.id)
            .limit(1)
        )
    ).first()
    return tuple(fallback) if fallback else None


def entity_href(canonical_key: str, slug: str | None, node_type: str) -> str:
    target = slug or canonical_key
    if node_type in {"character", "speaker"}:
        return f"/characters/{target}"
    if node_type == "quest":
        return f"/quests/{target}"
    if node_type == "item":
        return f"/items/{target}"
    if node_type in {"location", "area"}:
        return f"/locations/{target}"
    if node_type == "cutscene":
        return f"/cutscenes/{target}"
    return f"/nodes/{target}"


async def sync_fandom_bundle(
    session: AsyncSession,
    bundle: FandomEntityBundle,
    *,
    subject_node_id: int,
    run_id: int | None = None,
    memory_service: Any | None = None,
    sync_facts: bool = True,
    sync_relations: bool = True,
    import_images: bool = False,
    max_images: int = 8,
    license_filter: LicenseFilter = LicenseFilter.IGNORE,
) -> dict[str, Any]:
    """Persist Fandom URL, infobox relations, and optional gallery images for an entity node."""
    created_facts: list[int] = []
    created_edges: list[int] = []
    matched_relations: list[dict[str, Any]] = []

    if sync_facts:
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

    if sync_relations:
        for rel in bundle.relations:
            target = await _find_node_by_name(session, rel.target_name)
            target_node_id = target[0] if target else None
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
                citations=[{"url": bundle.page_url,
                            "field": rel.source_field}],
                create_edge=target_node_id is not None,
            )
            if fact.get("id"):
                created_facts.append(fact["id"])
            if fact.get("edge_id"):
                created_edges.append(fact["edge_id"])
            matched_relations.append(
                {
                    "relation_type": rel.relation_type,
                    "target_name": rel.target_name,
                    "note": rel.note,
                    "node_id": target_node_id,
                    "href": entity_href(target[1], target[2], target[3]) if target else None,
                }
            )

    imported_references: list[int] = []
    if import_images and memory_service is not None and bundle.images:
        identity_id = await ensure_visual_identity_for_node(session, subject_node_id, bundle.page_title)
        for asset in bundle.images:
            if len(imported_references) >= max_images:
                break
            if license_filter == LicenseFilter.CC_BY_SA_ONLY and not asset.license.is_cc_by_sa:
                continue
            try:
                content = await asyncio.to_thread(_fetch_image_bytes, asset.url, bundle.page_url)
            except Exception as exc:
                logger.warning("fandom.image_fetch_failed url=%s error=%s", asset.url, type(exc).__name__)
                continue
            if validate_image_bytes(content) is None:
                continue
            try:
                ref = await memory_service.add_reference(
                    content,
                    links=[{"identity_id": identity_id}],
                    source_type="fandom",
                    source_url=asset.url,
                    source_page=bundle.page_url,
                    rights=asset.license.text or "CC-BY-SA",
                    status="pending",
                    retention_mode="image",
                )
            except Exception as exc:
                logger.warning("fandom.add_reference_failed url=%s error=%s", asset.url, type(exc).__name__)
                continue
            if isinstance(ref, dict) and ref.get("id"):
                imported_references.append(int(ref["id"]))

    return {
        "page_url": bundle.page_url,
        "gallery_url": bundle.gallery_url,
        "facts_count": len(created_facts),
        "edges_count": len(created_edges),
        "matched_relations": matched_relations,
        "imported_references": imported_references,
    }
