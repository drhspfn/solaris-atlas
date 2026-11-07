"""Read public NPC families without changing their authored configurations."""

import json

from sqlalchemy import select

from wuwa_story.db.models.core import Character
from wuwa_story.db.models.graph import Node


def combine_npc_records(rows):
    """Keep per-instance provenance and all interactions when presenting one person."""
    result = {"instances": [], "flow_references": [], "shops": [], "name_variants": [],
              "owner_ids": [], "member_keys": []}
    seen = {field: set() for field in ("instances", "flow_references", "shops", "name_variants")}
    for character, node in rows:
        npc = (character.metadata_json or {}).get("npc")
        if not isinstance(npc, dict):
            continue
        if not result["owner_ids"]:
            result.update({key: npc.get(key) for key in ("blueprint_id", "blueprint_type")})
        result["owner_ids"].append(node.id)
        result["member_keys"].append(node.canonical_key)
        for field in seen:
            for entry in npc.get(field) or []:
                if not isinstance(entry, dict):
                    continue
                value = dict(entry)
                if field in ("instances", "flow_references"):
                    value["character_key"] = node.canonical_key
                if field == "instances":
                    # The same placed actor can be present in several blueprint exports.
                    key = (value.get("map_id"), value.get("entity_id"))
                    if None in key:
                        key = (node.canonical_key, json.dumps(value, sort_keys=True))
                elif field == "shops":
                    key = json.dumps(value, sort_keys=True)
                else:
                    key = json.dumps(value, sort_keys=True)
                if key not in seen[field]:
                    seen[field].add(key)
                    result[field].append(value)
    return result


async def npc_family_records(session, character, node):
    identity = (character.metadata_json or {}).get("public_identity") or {}
    if not identity.get("family"):
        return [(character, node)] if (character.metadata_json or {}).get("npc") else []
    return list(await session.execute(select(Character, Node).join(
        Node, Node.id == Character.node_id).where(
        Character.metadata_json["public_identity"]["family"].as_string() == identity["family"],
        Character.metadata_json["role_type"].as_integer() == 6,
    ).order_by(Node.id)))
