"""Resolve public families without rewriting source role or speaker identifiers."""

from sqlalchemy import and_, or_, select

from wuwa_story.db.models.core import Character
from wuwa_story.db.models.graph import Node, NodeRevision
from wuwa_story.ingestion.character_identity import character_kind


async def character_family(session, character, node, localize, release_id=None):
    identity = (character.metadata_json or {}).get("public_identity") or {}
    if not identity:
        return [node.id], None
    statement = (
        select(Character, Node).join(Node, Node.id == Character.node_id).where(
            Character.metadata_json["public_identity"]["family"].as_string() == identity["family"]
        ).order_by(Node.canonical_key)
    )
    if release_id is not None:
        statement = statement.where(select(NodeRevision.id).where(
            NodeRevision.node_id == Node.id, NodeRevision.release_id == release_id).exists())
    rows = list(await session.execute(statement))
    variants = []
    for member, member_node in rows:
        variant = member.metadata_json["public_identity"]
        if not variant.get("selectable", True):
            continue
        variants.append({
            "canonical_key": member_node.canonical_key,
            "gender": variant["gender"], "element_id": variant.get("element_id"),
            "element": await localize(variant.get("element_name_key")),
        })
    return [member.node_id for member, _ in rows], {
        "canonical_key": identity["canonical_key"], "name": "Rover",
        "selected_key": identity.get("variant_key", node.canonical_key), "variants": variants,
    }


def public_character_filter(role_type, identity, canonical_key, kind="playable"):
    """Apply category and family paging before LIMIT, in both search backends."""
    category = {
        "playable": role_type == 1,
        "story": role_type.in_((2, 4, 5)),
        "technical": or_(role_type.is_(None), role_type.not_in((1, 2, 4, 5))),
        "all": True,
    }[kind]
    primary = identity["canonical_key"].as_string()
    return and_(category, or_(primary.is_(None), primary == canonical_key))


async def annotate_character_cards(session, results):
    ids = [row["id"] for row in results if row.get("node_type") == "character"]
    if not ids:
        return
    metadata = dict((await session.execute(select(Character.node_id, Character.metadata_json).where(Character.node_id.in_(ids)))).all())
    for row in results:
        if row["id"] not in metadata:
            continue
        info = metadata[row["id"]] or {}
        row["character_kind"] = character_kind(info.get("role_type"))
        if info.get("public_identity"):
            row["title"] = "Rover"
