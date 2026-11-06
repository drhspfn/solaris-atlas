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
    identities = {
        row["Id"]: {
            "family": "rover", "canonical_key": f"character:{anchor}",
            "variant_key": f"character:{row['Id']}", "selectable": True,
            "gender": "male" if row["Gender"] == 1 else "female",
            "element_id": roles[row["Id"]].get("ElementId"),
            "element_name_key": elements.get(roles[row["Id"]].get("ElementId"), {}).get("Name"),
            "source_file": MAIN_ROLE_PATH,
        }
        for row in members
    }
    # Temporary story/trial roles can explicitly reuse the main role's name key
    # and both personal portrait fields. Require all three exact references;
    # a translated name or one inherited default cannot establish identity.
    for role_id, role in roles.items():
        if role_id in identities:
            continue
        matches = [row["Id"] for row in members if all(
            isinstance(role.get(field), str) and role[field]
            and role[field] == roles[row["Id"]].get(field)
            for field in ("Name", "RoleHeadIcon", "FormationRoleCard"))]
        if len(matches) == 1:
            identities[role_id] = {**identities[matches[0]], "selectable": False,
                                   "resolution": "exact_main_role_name_and_portraits"}
    return identities



def character_kind(role_type):
    if role_type == 1:
        return "playable"
    if role_type in (2, 4, 5):
        return "story"
    if role_type == 6:
        return "npc"
    return "technical"


def is_public_representative(canonical_key, identity):
    return not identity or identity.get("canonical_key") == canonical_key
