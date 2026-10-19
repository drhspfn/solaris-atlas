"""Batch public addresses for nested entity references, without changing their IDs."""

from functools import wraps
from inspect import signature

from sqlalchemy import select

from wuwa_story.db.models.core import Quest
from wuwa_story.db.models.graph import Node, NodeType
from wuwa_story.urls import node_address

SOURCE_FIELDS = {"raw", "raw_record", "metadata",
                 "metadata_json", "provenance", "source"}


async def attach_addresses(session, payload):
    references = []
    quest_lists = []
    titled = []
    keys, quest_ids = set(), set()

    def collect(value):
        if isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, dict):
            if type(value.get("node_id")) is int and "title" in value:
                titled.append(value)
            if isinstance(value.get("quest_ids"), list):
                identifiers = [q for q in value["quest_ids"] if type(q) is int]
                quest_ids.update(identifiers)
                quest_lists.append((value, identifiers))
            key = value.get("canonical_key")
            quest = value.get("game_quest_id", value.get("quest_id"))
            key = key if isinstance(key, str) else None
            quest = quest if type(quest) is int else None
            if not key and isinstance(value.get("quest"), str) and value["quest"].startswith("quest:"):
                key = value["quest"]
            if isinstance(key, str) and key:
                keys.add(key)
            if isinstance(quest, int) and not isinstance(quest, bool):
                quest_ids.add(quest)
            if key or quest:
                references.append((value, key, quest))
            for field, child in value.items():
                if field not in SOURCE_FIELDS:
                    collect(child)

    collect(payload)
    nodes, quests = {}, {}
    sorted_keys, sorted_quests = sorted(keys), sorted(quest_ids)
    for offset in range(0, len(sorted_keys), 500):
        for node, kind in await session.execute(select(Node, NodeType.key).join(NodeType).where(
                Node.canonical_key.in_(sorted_keys[offset:offset + 500]))):
            nodes[node.canonical_key] = node_address(node, kind)
    for offset in range(0, len(sorted_quests), 500):
        for quest_id, node in await session.execute(select(Quest.game_quest_id, Node).join(
                Node, Node.id == Quest.node_id).where(Quest.game_quest_id.in_(sorted_quests[offset:offset + 500]))):
            quests[quest_id] = node_address(node, "quest")
    for value, key, quest in references:
        if key in nodes:
            value.update(nodes[key])
        if quest in quests:
            address = quests[quest]
            value.update(
                {"quest_slug": address["slug"], "quest_href": address["href"]})
            if "game_quest_id" in value and not key:
                value.update(address)
    for value, identifiers in quest_lists:
        value.setdefault("quests", [{"game_quest_id": identifier, **quests.get(identifier, {})}
                                    for identifier in identifiers])
    identifiers = sorted({value["node_id"] for value in titled})
    by_id = {}
    for offset in range(0, len(identifiers), 500):
        for node, kind in await session.execute(select(Node, NodeType.key).join(NodeType).where(
            Node.id.in_(identifiers[offset:offset + 500]))):
            by_id[node.id] = node_address(node, kind)
    for value in titled:
        value.update(by_id.get(value["node_id"], {}))
    return payload


def public_addresses(endpoint):
    """Explicit response projection for public endpoints with graph references."""
    parameters = signature(endpoint)
    @wraps(endpoint)
    async def wrapped(*args, **kwargs):
        payload = await endpoint(*args, **kwargs)
        return await attach_addresses(parameters.bind(*args, **kwargs).arguments["session"], payload)
    return wrapped
