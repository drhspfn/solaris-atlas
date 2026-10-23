"""Shared media dispatch rules for consumers and administrative queue controls."""

MEDIA_POOLS = ("coordinator", "image", "audio", "video")


def media_pool(payload: dict) -> str:
    kind = payload.get("kind")
    if payload.get("job_type") == "media.entities":
        kinds = (payload.get("request") or {}).get("kinds", [])
        return "audio" if "voice" in kinds else "image"
    if kind in {"voices", "voice_packages", "character_voices"}:
        return "audio"
    if kind in {"cutscene", "cutscene_assets"}:
        return "video"
    if kind in {"images", "event_images", "event_resources", "event_catalog"}:
        return "image"
    return "coordinator"
