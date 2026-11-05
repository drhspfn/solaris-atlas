"""RoleInfo also contains shared quest/resource references, not personal portraits."""

# RoleInfo reuses these portraits as placeholders on unrelated playable roles.
# Match the actual package name, not translated character names or fuzzy aliases.
_PORTRAIT_OWNERS = {
    "t_activityroleyangyang": 1402,
    "t_activityrolebailian": 1103,
    "t_activityrolechangli": 1205,
    "t_activityrolexiangliyao": 1305,
}


def character_artwork_scope(field: str, path: object, role_id: int | None = None) -> str:
    value = path.casefold() if isinstance(path, str) else ""
    if (
        field in {"RoleStand", "RolePortrait"}
        and "/uiquest/" in value
        or field == "Icon"
        and "/iconrup/" in value
    ):
        return "shared_resource"
    if field == "RolePortrait" and role_id is not None:
        package = value.rsplit("/", 1)[-1].split(".", 1)[0]
        owner = _PORTRAIT_OWNERS.get(package)
        if owner is not None and owner != role_id:
            return "shared_resource"
    return "character"


def inherited_character_fields(role, template):
    """A foreign skill template is not evidence of personal portrait ownership."""
    if (role.get("RoleType") == 1 or not template
            or role.get("Id") == template.get("Id")
            or role.get("RoleHeadIcon") == template.get("RoleHeadIcon")):
        return set()
    return {field for field in ("FormationRoleCard", "RolePortrait")
            if role.get(field) and role.get(field) == template.get(field)}
