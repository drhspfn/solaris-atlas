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


_HEAD_FIELDS = ("RoleHeadIcon", "RoleHeadIconBig", "RoleHeadIconLarge",
                "RoleHeadIconCircle", "Card")
# The 53 head set belongs to playable role 1210; 115 is a widely reused
# technical-role creature default. Other monster heads can be authored art.
_AEMEATH_HEADS = {
    f"t_iconrolehead{size}_53_ui" for size in ("80", "150", "256", "circle256", "175")
}


def inherited_character_fields(role, template):
    """A foreign skill template is not evidence of personal portrait ownership."""
    if role.get("RoleType") in (None, 1):
        return set()
    template = template or {}
    same_person = (
        role.get("Name") and role.get("Name") == template.get("Name")
        and role.get("Introduction") and role.get("Introduction") == template.get("Introduction")
        and role.get("FormationRoleCard")
        and role.get("FormationRoleCard") == template.get("FormationRoleCard")
    )
    excluded = set()
    for field in _HEAD_FIELDS:
        path = role.get(field)
        package = path.rsplit("/", 1)[-1].split(".", 1)[0].casefold() if isinstance(path, str) else ""
        if package == "t_iconmonsterhead_115_ui" or (
            package in _AEMEATH_HEADS
            and not (same_person and path == template.get(field))
        ):
            excluded.add(field)
    if not template or role.get("Id") == template.get("Id") or same_person:
        return excluded
    fields = ("FormationRoleCard", "RolePortrait")
    if (isinstance(role.get("Name"), str) and isinstance(template.get("Name"), str)
            and role["Name"] != template["Name"]):
        fields += _HEAD_FIELDS
    elif role.get("RoleHeadIcon") == template.get("RoleHeadIcon"):
        return excluded
    return excluded | {field for field in fields
                       if role.get(field) and role.get(field) == template.get(field)}
