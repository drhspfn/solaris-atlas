"""Serialize legacy and scene-level analysis admission for the same source."""

import hashlib

from sqlalchemy import text


async def lock_visual_sources(session, asset_ids, version: str) -> None:
    """Hold transaction locks in stable order until jobs are committed or rolled back."""
    for asset_id in sorted(set(asset_ids)):
        identity = f"cutscene-vision-source:{version}:{asset_id}".encode()
        key = int.from_bytes(hashlib.sha256(identity).digest()[:7], "big")
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
