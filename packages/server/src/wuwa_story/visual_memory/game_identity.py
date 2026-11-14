"""Use the game's evidenced Rover family, rather than element names, for visual identity."""

from sqlalchemy import func, select

from wuwa_story.db.models import Character, Node, VisualIdentityNode

ROVER_APPEARANCES = (
    {"key": "male", "name": "Male Rover"},
    {"key": "female", "name": "Female Rover"},
)


def is_rover(character):
    return ((character.metadata_json or {}).get("public_identity") or {}).get("family") == "rover"


async def rover_identity(session, character, service):
    """One identity and two appearances; adding new source elements discovers them automatically."""
    identity = character.metadata_json["public_identity"]
    canonical_key = identity.get("canonical_key")
    if not canonical_key:
        raise ValueError("Rover family has no canonical game record")
    # Every gender/element shares the same transaction lock, including first creation.
    await session.execute(select(func.pg_advisory_xact_lock(
        func.hashtextextended(f"visual-rover:{canonical_key}", 0))))
    members = list(await session.scalars(
        select(Character.node_id).where(
            Character.metadata_json["public_identity"]["family"].as_string() == "rover",
            Character.metadata_json["public_identity"]["canonical_key"].as_string() == canonical_key,
        ).order_by(Character.node_id)
    ))
    anchor = await session.scalar(select(Node.id).where(Node.canonical_key == canonical_key))
    if anchor not in members:
        raise ValueError("Canonical Rover is not a member of the evidenced family")
    ids = list(await session.scalars(select(VisualIdentityNode.identity_id)
                                    .where(VisualIdentityNode.node_id.in_(members)).distinct()))
    if len(ids) > 1:
        raise ValueError("Several visual identities link Rover's family; reconcile them before adding references")
    if ids:
        result = await service._identity_dict(await service._identity(ids[0]))
        result = await service.update_identity(
            result["id"], name="Rover", kind="playable",
            node_ids=sorted(set(result["node_ids"]) | set(members)),
        )
    else:
        result = await service.create_identity(name="Rover", kind="playable", node_ids=members)
    existing = {item["name"] for item in result["appearances"]}
    for appearance in ROVER_APPEARANCES:
        if appearance["name"] not in existing:
            await service.create_appearance(result["id"], appearance["name"])
    # Existing reference links retain their appearance (including unknown); never infer it
    # from the element/gender of the canonical game record used to create this identity.
    return await service._identity_dict(await service._identity(result["id"]))
