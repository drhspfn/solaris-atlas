"""Reverse lookup of authored NPC shop offers."""

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.core import Character
from wuwa_story.db.models.graph import Edge, EdgeEvidence, Node
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.session import get_session
from wuwa_story.story.service import _release_id
from wuwa_story.urls import node_address

router = APIRouter(tags=["story browsing"])


def offer_evidence(value):
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) and parsed.get("type") == "authored_shop_offer" else None


@router.get("/item-npc-sellers/{item_id}")
async def item_npc_sellers(item_id: int, game_version: str | None = None,
                           limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
                           session: AsyncSession = Depends(get_session)):
    release_id = await _release_id(session, None)
    if game_version and (release_id is None or await _release_id(session, game_version) != release_id):
        raise HTTPException(404, "NPC data is not available for this snapshot")
    if release_id is None:
        return {"results": [], "total": 0, "limit": limit, "offset": offset}
    target = select(Node.id).where(Node.canonical_key == f"item:{item_id}").scalar_subquery()
    rows = await session.execute(
        select(Node, Character.canonical_name, EdgeEvidence.explanation)
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
    for node, name, explanation in rows:
        offer = offer_evidence(explanation)
        if offer is not None:
            seller = sellers.setdefault(node.canonical_key, {"canonical_key": node.canonical_key,
                **node_address(node, "character"), "name": name, "offers": []})
            seller["offers"].append({"shop_id": offer.get("shop_id"),
                                     "quantity": offer.get("quantity"), "price": offer.get("price")})
    results = list(sellers.values())
    return {"results": results[offset:offset + limit], "total": len(results),
            "limit": limit, "offset": offset}
