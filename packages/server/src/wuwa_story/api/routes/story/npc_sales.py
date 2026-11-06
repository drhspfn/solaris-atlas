"""Reverse lookup of authored NPC shop offers."""

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.core import Character
from wuwa_story.db.models.graph import Edge, EdgeEvidence, Node
from wuwa_story.db.models.i18n import Locale
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.search import SearchDocument
from wuwa_story.db.session import get_session
from wuwa_story.story.service import _localized, _release_id, prefetch_localizations
from wuwa_story.urls import node_address

router = APIRouter(tags=["story browsing"])


def offer_evidence(value):
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) and parsed.get("type") == "authored_shop_offer" else None


@router.get("/item-npc-sellers/{item_id}")
async def item_npc_sellers(item_id: int, game_version: str | None = None, locale: str = "en",
                           limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
                           session: AsyncSession = Depends(get_session)):
    release_id = await _release_id(session, None)
    if game_version and (release_id is None or await _release_id(session, game_version) != release_id):
        raise HTTPException(404, "NPC data is not available for this snapshot")
    if release_id is None:
        return {"results": [], "total": 0, "limit": limit, "offset": offset}
    target = select(Node.id).where(Node.canonical_key == f"item:{item_id}").scalar_subquery()
    rows = await session.execute(
        select(Node, Character.canonical_name, Character.name_key_id, EdgeEvidence.explanation)
        .join(Character, Character.node_id == Node.id)
        .join(Edge, Edge.from_node_id == Node.id)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .join(EdgeEvidence, EdgeEvidence.edge_id == Edge.id)
        .where(Edge.to_node_id == target, Edge.layer == "source",
               RelationType.key == "references_item", EdgeEvidence.release_id == release_id,
               Node.canonical_key.startswith("character:npc:"))
        .order_by(Node.canonical_key, EdgeEvidence.id))
    # Filter on evidence, because other references_item edges need not be shop offers.
    sellers = {}
    for node, name, name_key_id, explanation in rows:
        offer = offer_evidence(explanation)
        if offer is not None:
            seller = sellers.setdefault(node.canonical_key, {"canonical_key": node.canonical_key,
                **node_address(node, "character"), "name": name, "_node_id": node.id,
                "_name_key_id": name_key_id, "offers": []})
            seller["offers"].append({"shop_id": offer.get("shop_id"),
                                     "quantity": offer.get("quantity"), "price": offer.get("price")})
    results = list(sellers.values())
    page = results[offset:offset + limit]
    await prefetch_localizations(session, [row["_name_key_id"] for row in page], locale, release_id)
    ambiguous = [row["_node_id"] for row in page if row["_name_key_id"] is None]
    indexed = dict((await session.execute(select(SearchDocument.target_node_id, SearchDocument.title)
        .join(Locale, Locale.id == SearchDocument.locale_id).where(
            SearchDocument.target_node_id.in_(ambiguous), SearchDocument.category == "character",
            Locale.code == locale))).all()) if ambiguous else {}
    for row in page:
        localized = await _localized(session, row.pop("_name_key_id"), locale, release_id)
        node_id = row.pop("_node_id")
        row["name"] = (localized or {}).get("content") or indexed.get(node_id) or row["name"] or row["canonical_key"]
    return {"results": page, "total": len(results),
            "limit": limit, "offset": offset}
