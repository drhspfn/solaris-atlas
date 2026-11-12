"""Game client version parsing, semantic comparison, and capability manifests.

Replaces hardcoded version whitelists with dynamic version validation.
Clients >= MIN_SUPPORTED_CLIENT_VERSION (3.7.0) share unified ConfigDB FlatBuffer
tables, Unreal coordinate systems, and VideoData / VideoSound layouts.
"""

from __future__ import annotations

import re

# Minimum game client version where unified ConfigDB schemas (FlatBuffer tables),
# coordinate transforms and modern cutscene video/audio structures are established.
MIN_SUPPORTED_CLIENT_VERSION = "3.7.0"


def parse_version_tuple(version: str | None) -> tuple[int, ...] | None:
    """Parse a semantic or game version string into a tuple of integers."""
    if not isinstance(version, str):
        return None
    cleaned = version.strip()
    if not re.fullmatch(r"\d+(\.\d+)*", cleaned):
        return None
    try:
        return tuple(map(int, cleaned.split(".")))
    except (ValueError, TypeError):
        return None


def is_supported_version(version: str | None, min_version: str = MIN_SUPPORTED_CLIENT_VERSION) -> bool:
    """Return True if the version is valid and at least min_version."""
    v = parse_version_tuple(version)
    min_v = parse_version_tuple(min_version)
    if v is None or min_v is None:
        return False
    max_len = max(len(v), len(min_v))
    v_padded = v + (0,) * (max_len - len(v))
    min_padded = min_v + (0,) * (max_len - len(min_v))
    return v_padded >= min_padded


def is_caption_supported(version: str | None) -> bool:
    """Captions are verified and supported on modern clients >= 3.7.0."""
    return is_supported_version(version, MIN_SUPPORTED_CLIENT_VERSION)


def is_supported_map_version(version: str | None) -> bool:
    """Map ConfigDB FlatBuffers and Unreal coordinate systems are unified for clients >= 3.7.0."""
    return is_supported_version(version, MIN_SUPPORTED_CLIENT_VERSION)


def is_supported_cutscene_version(version: str | None) -> bool:
    """Cutscene VideoData and VideoSound schemas are verified for clients >= 3.7.0."""
    return is_supported_version(version, MIN_SUPPORTED_CLIENT_VERSION)
