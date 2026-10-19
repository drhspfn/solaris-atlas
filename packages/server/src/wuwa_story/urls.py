"""Public addresses are independent of graph identities and relationship keys."""

from urllib.parse import quote

NODE_PATHS = {"character": "characters", "item": "items", "area": "locations",
              "location": "locations", "quest": "quests"}


def numeric_id(value: str | int) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if -(2**63) <= number < 2**63 else None


def node_url(node, kind: str) -> str:
    key = getattr(node, "slug", None)
    if not key:
        key = node.canonical_key.rsplit(
            ":", 1)[-1] if kind == "quest" else node.canonical_key
    group = NODE_PATHS.get(kind, 'nodes')
    if kind == "event" and (getattr(node, "metadata_json", None) or {}).get("generated"):
        group = "story-analysis/events"
        if not getattr(node, "slug", None):
            key = str(node.id)
    return f"/{group}/{quote(key, safe='')}"


def node_address(node, kind: str) -> dict:
    return {"slug": getattr(node, "slug", None), "href": node_url(node, kind)}


def record_url(record, group: str) -> str:
    key = getattr(record, "slug", None) or str(record.id)
    return f"/events?event={quote(key, safe='')}" if group == "events" else f"/{group}/{quote(key, safe='')}"
