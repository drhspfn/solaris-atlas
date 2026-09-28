from typing import Any


def resolve_categories(categories: list[str] | None) -> set[str] | None:
    if not categories:
        return None
    allowed = {
        "character",
        "organization",
        "location",
        "event",
        "item",
        "term",
        "concept",
        "story",
        "speaker",
        "quest",
        "scene",
    }
    unknown = set(categories) - allowed
    if unknown:
        raise ValueError(f"Unknown search categories: {sorted(unknown)}")
    return set(categories)


def semantic_resolution_available(_query: str) -> dict[str, Any]:
    return {"available": False, "reason": "semantic resolver is reserved for a later phase"}
