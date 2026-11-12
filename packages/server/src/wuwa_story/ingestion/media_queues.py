"""Shared media dispatch rules for consumers and administrative queue controls."""

MEDIA_POOLS = ("coordinator", "image", "illustrated", "audio", "video")


def is_illustrated_payload(payload: dict) -> bool:
    if payload.get("kind") == "images":
        return True
    targets = payload.get("targets") or []
    if any(isinstance(t, dict) and t.get("kind") == "spine" for t in targets):
        return True
    return False


def media_pool(payload: dict) -> str:
    kind = payload.get("kind")
    if payload.get("job_type") == "media.entities":
        kinds = (payload.get("request") or {}).get("kinds", [])
        if "voice" in kinds:
            return "audio"
        return "illustrated" if is_illustrated_payload(payload) else "image"
    if kind in {"voices", "voice_packages", "character_voices"}:
        return "audio"
    if kind in {"cutscene", "cutscene_assets"}:
        return "video"
    if kind == "images" or is_illustrated_payload(payload):
        return "illustrated"
    if kind in {"event_images", "event_resources", "event_catalog", "event_notices", "tree_order", "npc_catalog"}:
        return "image"
    return "coordinator"
