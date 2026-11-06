"""NPC presentation using explicitly authored speaker and shop evidence."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.api.public_addresses import public_addresses
from wuwa_story.db.models.core import Character, DialogueLine, Item, Quest, QuestState
from wuwa_story.db.models.graph import Edge, EdgeEvidence, Node
from wuwa_story.db.models.i18n import Locale, LocalizationKey, LocalizationValue
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.search import SearchDocument
from wuwa_story.db.repositories.character_identity import dialogue_participation
from wuwa_story.db.repositories.nodes import get_node
from wuwa_story.db.repositories.quest_scope import quest_state_links
from wuwa_story.db.session import get_session
from wuwa_story.story.service import (
    _localized,
    _node_label,
    _quest_info,
    _release_id,
    prefetch_localizations,
    prefetch_node_labels,
)
from wuwa_story.urls import node_address

router = APIRouter(tags=["story browsing"])


def source_edge(owner_id, target, relation, release_id):
    return select(Edge.id).join(RelationType, RelationType.id == Edge.relation_type_id).where(
        Edge.from_node_id == owner_id, Edge.to_node_id == target, Edge.layer == "source",
        RelationType.key == relation,
        select(EdgeEvidence.id).where(EdgeEvidence.edge_id == Edge.id,
                                     EdgeEvidence.release_id == release_id).exists(),
    ).exists()


def interaction_dialogue(release_id, state_ids):
    """Include authored lines without a speaker as well as spoken lines in interaction states."""
    action_edge, talk_edge, spoken = (Edge.__table__.alias(name) for name in
                                       ("npc_actions", "npc_talks", "npc_spoken"))

    def evidenced(edge, relation):
        return and_(edge.c.layer == "source", edge.c.relation_type_id == select(RelationType.id).where(
            RelationType.key == relation).scalar_subquery(), select(EdgeEvidence.id).where(
            EdgeEvidence.edge_id == edge.c.id, EdgeEvidence.release_id == release_id).exists())

    return (select(talk_edge.c.to_node_id.label("line_id"), action_edge.c.from_node_id.label("state_id"),
                   spoken.c.to_node_id.label("speaker_id"))
            .select_from(action_edge).join(talk_edge, talk_edge.c.from_node_id == action_edge.c.to_node_id)
            .outerjoin(spoken, and_(spoken.c.from_node_id == talk_edge.c.to_node_id,
                                    evidenced(spoken, "spoken_by")))
            .join(DialogueLine, DialogueLine.node_id == talk_edge.c.to_node_id)
            .where(action_edge.c.from_node_id.in_(state_ids), evidenced(action_edge, "contains_action"),
                   evidenced(talk_edge, "presents_talk_item")).distinct())


async def npc_flow_state_ids(session, node_id, npc, release_id):
    refs = [entry.get("flow") for entry in npc.get("flow_references") or []
            if isinstance(entry, dict) and entry.get("basis") == "explicit_flow_triple"]
    valid = [flow for flow in refs if isinstance(flow, list) and len(flow) == 3
             and isinstance(flow[0], str) and type(flow[1]) is int and type(flow[2]) is int]
    states = set(await session.scalars(select(QuestState.node_id).where(
        source_edge(node_id, QuestState.node_id, "references_flow_state", release_id))))
    for offset in range(0, len(valid), 100):
        conditions = [and_(QuestState.flow_list_name == name, QuestState.flow_id == flow_id,
                           QuestState.state_id == state_id) for name, flow_id, state_id in valid[offset:offset + 100]]
        states.update(await session.scalars(select(QuestState.node_id).where(or_(*conditions))))
    return sorted(states)


async def npc_record(session, identity, game_version=None):
    node = await get_node(session, identity, ("character",))
    character = await session.get(Character, node.id) if node else None
    metadata = (character.metadata_json or {}) if character else {}
    if metadata.get("role_type") != 6 or not isinstance(metadata.get("npc"), dict):
        raise HTTPException(404, "NPC not found")
    current = await _release_id(session, None)
    if game_version and (not current or await _release_id(session, game_version) != current):
        raise HTTPException(404, "NPC data is not available for this snapshot")
    return node, character, metadata["npc"], current


def price_pairs(value):
    if isinstance(value, dict):
        return [(int(key), amount) for key, amount in value.items()
                if str(key).isdigit() and type(amount) is int]
    if isinstance(value, list):
        return [(row["Key"], row["Value"]) for row in value if isinstance(row, dict)
                and type(row.get("Key")) is int and type(row.get("Value")) is int]
    return []


async def npc_profile(session, node, character, npc, release_id, locale):
    instances = npc.get("instances") or []
    name = await _localized(session, character.name_key_id, locale, release_id)
    variants = []
    scoped_name = False
    if character.name_key_id is None:
        row = (await session.execute(select(SearchDocument.title)
            .join(Locale, Locale.id == SearchDocument.locale_id)
            .where(SearchDocument.target_node_id == node.id, SearchDocument.category == "character",
                   Locale.code == locale))).first()
        if row and row.title:
            name = {"content": row.title}
            variants = [row.title]
            scoped_name = True
    name_keys = {entry.get("name", {}).get("key") for entry in npc.get("name_variants") or []
                 if isinstance(entry, dict) and isinstance(entry.get("name"), dict)} - {None}
    if name_keys:
        key_ids = list(await session.scalars(select(LocalizationKey.id).where(LocalizationKey.key.in_(name_keys))))
        await prefetch_localizations(session, key_ids, locale, release_id)
        for key_id in key_ids:
            localized = await _localized(session, key_id, locale, release_id)
            text = (localized or {}).get("content")
            if text and text not in variants:
                variants.append(text)
    return {"character": {"canonical_key": node.canonical_key, **node_address(node, "character"),
                          "canonical_name": character.canonical_name,
                          "name": name,
                          "aliases": [],
                          "description": character.description, "playable": False},
            "npc": {"blueprint_id": npc.get("blueprint_id"), "blueprint_type": npc.get("blueprint_type"),
                    "speakers": [],
                    "name_scope": "instance" if scoped_name else "blueprint",
                    "name_variants": variants,
                    "flow_reference_count": len(npc.get("flow_references") or []),
                    "instances": [{"map_id": item.get("map_id"), "entity_id": item.get("entity_id"),
                                   "name": item.get("name")} for item in instances[:10]],
                    "instance_count": len(instances), "shop_count": len(npc.get("shops") or [])},
            "media": {"status": "asset_not_extracted", "image_url": None}}


@router.get("/npcs/{canonical_key:path}/shops")
async def npc_shops(canonical_key: str, locale: str = "en", game_version: str | None = None,
                    limit: int = Query(50, ge=1, le=50), offset: int = Query(0, ge=0),
                    session: AsyncSession = Depends(get_session)):
    _, _, npc, release_id = await npc_record(session, canonical_key, game_version)
    shops = [shop for shop in npc.get("shops") or [] if isinstance(shop, dict)]
    page = shops[offset:offset + limit]
    keys = {row.get("ShopName") for shop in page for row in shop.get("shopinfo") or []
            if isinstance(row, dict) and isinstance(row.get("ShopName"), str) and row.get("ShopName")}
    key_ids = dict((await session.execute(select(LocalizationKey.key, LocalizationKey.id)
                    .where(LocalizationKey.key.in_(keys)))).all()) if keys else {}
    await prefetch_localizations(session, list(key_ids.values()), locale, release_id)
    results = []
    for shop in page:
        info = next((row for row in shop.get("shopinfo") or [] if isinstance(row, dict)), {})
        key = info.get("ShopName")
        results.append({"id": shop.get("id"),
                        "name": await _localized(session, key_ids.get(key), locale, release_id),
                        "offer_count": len(shop.get("shopfixed") or []), "reset": None})
    return {"results": results, "total": len(shops), "limit": limit, "offset": offset}


@router.get("/npcs/{canonical_key:path}/shops/{shop_id}/offers")
async def npc_shop_offers(canonical_key: str, shop_id: int, locale: str = "en",
                          game_version: str | None = None, limit: int = Query(50, ge=1, le=50),
                          offset: int = Query(0, ge=0), session: AsyncSession = Depends(get_session)):
    _, _, npc, _ = await npc_record(session, canonical_key, game_version)
    shop = next((row for row in npc.get("shops") or [] if isinstance(row, dict) and row.get("id") == shop_id), None)
    if shop is None:
        raise HTTPException(404, "Shop not found")
    offers = [row for row in shop.get("shopfixed") or [] if isinstance(row, dict)]
    page = offers[offset:offset + limit]
    item_ids = {row.get("ItemId") for row in page if type(row.get("ItemId")) is int}
    item_ids.update(item_id for row in page for field in ("Price", "OriginalPrice")
                    for item_id, _ in price_pairs(row.get(field)))
    item_nodes = dict((await session.execute(select(Item.game_item_id, Node.id).join(
        Node, Node.id == Item.node_id).where(Item.game_item_id.in_(item_ids)))).all()) if item_ids else {}
    await prefetch_node_labels(session, list(item_nodes.values()), locale)
    labels = {identity: await _node_label(session, node_id, locale) for identity, node_id in item_nodes.items()}
    def item(identity):
        return labels.get(identity) or {"game_item_id": identity, "label": f"Item {identity}"}
    results = []
    for row in page:
        results.append({"id": row.get("Id"), "item": item(row.get("ItemId")), "count": row.get("ItemNum"),
                        "price": [{"item": item(key), "amount": amount} for key, amount in price_pairs(row.get("Price"))],
                        "original_price": [{"item": item(key), "amount": amount} for key, amount in price_pairs(row.get("OriginalPrice"))],
                        "limit": row.get("LimitNum"), "conditions": {key: row.get(key) for key in
                            ("Show", "CondId", "VisableCondition", "DisableCondition")}})
    return {"results": results, "total": len(offers), "limit": limit, "offset": offset, "reset": None}


@router.get("/npcs/{canonical_key:path}/flow-participants")
async def npc_flow_participants(canonical_key: str, locale: str = "en", game_version: str | None = None,
                                session: AsyncSession = Depends(get_session)):
    node, _, npc, release_id = await npc_record(session, canonical_key, game_version)
    if release_id is None:
        return {"results": [], "truncated": False}
    states = await npc_flow_state_ids(session, node.id, npc, release_id)
    if not states:
        return {"results": [], "truncated": False}
    participation = dialogue_participation(release_id, state_ids=states[:100])
    rows = list(await session.execute(select(Node.id, QuestState.state_key)
        .join(participation, participation.c.speaker_id == Node.id)
        .join(QuestState, QuestState.node_id == participation.c.state_id)
        .distinct().order_by(QuestState.state_key, Node.id).limit(101)))
    await prefetch_node_labels(session, [speaker for speaker, _ in rows[:100]], locale)
    return {"results": [{"speaker": await _node_label(session, speaker, locale), "flow_state": state}
                        for speaker, state in rows[:100]],
            "truncated": len(states) > 100 or len(rows) > 100}


@router.get("/npcs/{canonical_key:path}/dialogue")
@public_addresses
async def npc_dialogue(canonical_key: str, locale: str = "en", game_version: str | None = None,
                       q: str = Query("", max_length=128), limit: int = Query(20, ge=1, le=50),
                       offset: int = Query(0, ge=0), session: AsyncSession = Depends(get_session)):
    node, _, npc, release_id = await npc_record(session, canonical_key, game_version)
    locale_id = await session.scalar(select(Locale.id).where(Locale.code == locale))
    if locale_id is None:
        raise HTTPException(400, "unknown locale")
    if release_id is None:
        return {"results": [], "total": 0, "limit": limit, "offset": offset}
    # Older imports may contain name-derived speakers; only flow context is
    # supported until there is a separately evidenced NPC identity crosswalk.
    state_ids = await npc_flow_state_ids(session, node.id, npc, release_id)
    if not state_ids:
        return {"results": [], "total": 0, "limit": limit, "offset": offset}
    selected = interaction_dialogue(release_id, state_ids).subquery()
    query = select(selected.c.line_id, selected.c.state_id, selected.c.speaker_id).join(
        DialogueLine, DialogueLine.node_id == selected.c.line_id)
    if q.strip():
        match = select(LocalizationValue.key_id).where(
            LocalizationValue.key_id == DialogueLine.localization_key_id,
            LocalizationValue.locale_id == locale_id, LocalizationValue.release_id == release_id,
            LocalizationValue.content.icontains(q.strip(), autoescape=True)).exists()
        query = query.where(or_(DialogueLine.inline_text.icontains(q.strip(), autoescape=True), match))
    rows = query.subquery()
    total = await session.scalar(select(func.count()).select_from(rows)) or 0
    page = list(await session.execute(select(DialogueLine, Node, QuestState, rows.c.speaker_id,
        source_edge(node.id, DialogueLine.node_id, "presents_talk_item", release_id)).join(
        rows, rows.c.line_id == DialogueLine.node_id).join(Node, Node.id == DialogueLine.node_id).join(
        QuestState, QuestState.node_id == rows.c.state_id).order_by(Node.canonical_key, QuestState.state_key)
        .offset(offset).limit(limit)))
    results = []
    await prefetch_node_labels(session, [speaker_id for _, _, _, speaker_id, _ in page if speaker_id is not None], locale)
    await prefetch_localizations(session, [line.localization_key_id for line, _, _, _, _ in page], locale, release_id)
    states = {state.node_id for _, _, state, _, _ in page}
    quests_by_state = {state_id: [] for state_id in states}
    if states:
        links = quest_state_links(release_id, state_ids=states)
        associations = list(await session.execute(select(links.c.quest_id, links.c.state_id)
                                                  .order_by(links.c.state_id, links.c.quest_id)))
        quest_ids = {quest_id for quest_id, _ in associations}
        quests = list(await session.scalars(select(Quest).where(Quest.node_id.in_(quest_ids)))) if quest_ids else []
        quest_cache = {quest.node_id: await _quest_info(session, quest, locale, game_version) for quest in quests}
        for quest_id, state_id in associations:
            if quest_id in quest_cache:
                quests_by_state[state_id].append(quest_cache[quest_id])
    for line, line_node, state, speaker_id, actor_context in page:
        results.append({"canonical_key": line_node.canonical_key, "flow_state": state.state_key,
                        "speaker": await _node_label(session, speaker_id, locale) if speaker_id is not None else None,
                        "speaker_relation": "npc_actor_context" if actor_context else "interaction_participant",
                        "text": await _localized(session, line.localization_key_id, locale, release_id)
                                or {"inline_text": line.inline_text}, "quests": quests_by_state[state.node_id]})
    return {"results": results, "total": total, "limit": limit, "offset": offset}


@router.get("/npcs/{canonical_key:path}/quests")
@public_addresses
async def npc_quests(canonical_key: str, locale: str = "en", game_version: str | None = None,
                     limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0),
                     session: AsyncSession = Depends(get_session)):
    node, _, npc, release_id = await npc_record(session, canonical_key, game_version)
    if release_id is None:
        return {"results": [], "total": 0, "limit": limit, "offset": offset}
    state_ids = set(await npc_flow_state_ids(session, node.id, npc, release_id))
    if not state_ids:
        return {"results": [], "total": 0, "limit": limit, "offset": offset}
    links = quest_state_links(release_id, state_ids=state_ids)
    matching = select(links.c.quest_id).distinct().subquery()
    total = await session.scalar(select(func.count()).select_from(matching)) or 0
    quests = await session.scalars(select(Quest).join(matching, matching.c.quest_id == Quest.node_id)
                                   .order_by(Quest.game_quest_id, Quest.node_id).offset(offset).limit(limit))
    return {"results": [await _quest_info(session, quest, locale, game_version) for quest in quests],
            "total": total, "limit": limit, "offset": offset}
