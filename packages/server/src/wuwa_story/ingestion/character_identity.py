"""Source-backed public identities; source role nodes remain intact for provenance."""

MAIN_ROLE_PATH = "BinData/main_role_change/mainroleconfig.json"


def main_role_identities(configs, roles, elements=()):
    """Discover new Rover elements from the client table, never translated names/ID ranges."""
    roles = {row["Id"]: row for row in roles if isinstance(row.get("Id"), int)}
    elements = {row.get("Id"): row for row in elements}
    members = [row for row in configs if row.get("Id") in roles and row.get("Gender") in (0, 1)]
    # Keep the existing default role address stable as additional elements arrive.
    defaults = [row for row in members if row.get("UnlockCondition") == 0]
    if not defaults:
        return {}
    anchor = min(defaults, key=lambda row: (row["Gender"] != 1, row["Id"]))["Id"]
    return {
        row["Id"]: {
            "family": "rover", "canonical_key": f"character:{anchor}",
            "gender": "male" if row["Gender"] == 1 else "female",
            "element_id": roles[row["Id"]].get("ElementId"),
            "element_name_key": elements.get(roles[row["Id"]].get("ElementId"), {}).get("Name"),
            "source_file": MAIN_ROLE_PATH,
        }
        for row in members
    }


def character_kind(role_type):
    if role_type == 1:
        return "playable"
    if role_type in (2, 4, 5):
        return "story"
    return "technical"


def is_public_representative(canonical_key, identity):
    return not identity or identity.get("canonical_key") == canonical_key
