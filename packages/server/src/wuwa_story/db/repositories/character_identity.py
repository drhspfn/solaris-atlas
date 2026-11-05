"""Resolve public families without rewriting source role or speaker identifiers."""

from sqlalchemy import select

from wuwa_story.db.models.core import Character
from wuwa_story.db.models.graph import Node


async def character_family(session, character, node, localize):
    identity = (character.metadata_json or {}).get("public_identity") or {}
    if not identity:
        return [node.id], None
    rows = list(await session.execute(
        select(Character, Node).join(Node, Node.id == Character.node_id).where(
            Character.metadata_json["public_identity"]["family"].as_string() == identity["family"]
        ).order_by(Node.canonical_key)
    ))
    variants = []
    for member, member_node in rows:
        variant = member.metadata_json["public_identity"]
        variants.append({
            "canonical_key": member_node.canonical_key,
            "gender": variant["gender"], "element_id": variant.get("element_id"),
            "element": await localize(variant.get("element_name_key")),
        })
    return [member.node_id for member, _ in rows], {
        "canonical_key": identity["canonical_key"], "name": "Rover",
        "selected_key": node.canonical_key, "variants": variants,
    }
