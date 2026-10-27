"""RoleInfo also contains shared quest/resource references, not personal portraits."""


def character_artwork_scope(field: str, path: object) -> str:
    value = path.casefold() if isinstance(path, str) else ""
    if field == "RoleStand" and "/uiquest/" in value or field == "Icon" and "/iconrup/" in value:
        return "shared_resource"
    return "character"
