"""Event read models and source-backed presentation policy, independent of IO."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast

CLIENT_SOURCE = "wuwa-game-client"
PREVIEW_ROLES = (
    "content",
    "tab_selected",
    "tab",
    "featured_portrait",
    "featured_weapon",
    "featured_card",
    "content_background",
    "background",
)


class EventRunNotFound(LookupError):
    def __init__(self) -> None:
        super().__init__("Event run not found")


class ClientSource(Protocol):
    @property
    def source_data(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class Event:
    id: int
    slug: str | None
    source_id: str
    title: str
    description: str | None
    event_kind: str
    banner_path: str | None
    rewards: list[dict[str, Any]] | None
    game_path: str | None
    source_url: str
    source_data: dict[str, Any]


@dataclass(frozen=True)
class EventOccurrence:
    id: int
    game_version: str | None
    starts_at: datetime | None
    ends_at: datetime | None
    season: dict[str, Any] | None
    banner_path: str | None
    source_data: dict[str, Any]


@dataclass(frozen=True)
class EventRun:
    event: Event
    occurrence: EventOccurrence | None

    @property
    def key(self) -> tuple[int, int | None]:
        return self.event.id, self.occurrence.id if self.occurrence else None

    @property
    def banner_path(self) -> str | None:
        return (self.occurrence.banner_path if self.occurrence else None) or self.event.banner_path


@dataclass(frozen=True)
class EventAnnouncement:
    id: int
    slug: str | None
    title: str
    related_event_ids: list[int]
    published_at: datetime | None
    content: dict[str, Any]


@dataclass(frozen=True)
class EventNoticeSnapshot:
    notice_id: int
    revision: str
    content: dict[str, Any]


@dataclass(frozen=True)
class EventRewardNode:
    canonical_key: str
    slug: str | None


@dataclass(frozen=True)
class EventMedia:
    source_path: str | None
    object_key: str
    metadata: dict[str, Any]


def _client_data(event: ClientSource, occurrence: ClientSource | None) -> dict[str, Any]:
    current = (event.source_data or {}).get("client", {})
    dated = ((occurrence.source_data or {})
             if occurrence else {}).get("client", {})
    if current.get("download_id") and current.get("download_id") == dated.get("download_id"):
        return cast(dict[str, Any], current)
    return cast(dict[str, Any], dated or current)


def _notice_overlaps(notice: EventAnnouncement | None, occurrence: EventOccurrence | None) -> bool:
    if not notice or not occurrence or not occurrence.starts_at or not occurrence.ends_at:
        return False
    raw = notice.content.get("raw", {})
    start, end = raw.get("startTimeMs"), raw.get("endTimeMs")
    return bool(
        start
        and end
        and occurrence.starts_at.timestamp() * 1000 < end
        and occurrence.ends_at.timestamp() * 1000 > start
    )


def _choose_preview(
    client_image: str | None,
    captured_client: bool,
    notice_snapshot: str | None,
    current_notice: str | None,
    *,
    prefer_notice: bool = False,
) -> tuple[str | None, str]:
    if notice_snapshot:
        return notice_snapshot, "notice_snapshot"
    if current_notice and prefer_notice:
        return current_notice, "current_notice"
    if client_image and captured_client:
        return client_image, "game_client"
    if current_notice:
        return current_notice, "current_notice"
    return client_image, "game_client"


def _artwork(event: ClientSource, occurrence: ClientSource | None) -> list[dict[str, Any]]:
    client = _client_data(event, occurrence)
    unique: dict[str, dict[str, Any]] = {}
    for asset in client.get("artwork", []) + client.get("prefab_artwork", []):
        # Quest illustrations and prefab defaults do not prove a banner's subject,
        # element or weapon type. Runtime bindings can replace these textures.
        if asset.get("role") in {"featured_stand", "quest_illustration"}:
            continue
        if asset.get("role") == "prefab_texture" and any(
            part in asset["path"]
            for part in ("/SkillIcon/SkillIconNor/", "/IconElementRound/", "/IconElement/")
        ):
            continue
        unique.setdefault(asset["path"], asset)
    return list(unique.values())


def _client_preview(
    artwork: list[dict[str, Any]], image_url: Callable[[str], str | None]
) -> str | None:
    for role in (
        "content",
        "tab_selected",
        "tab",
        "featured_portrait",
        "featured_weapon",
        "featured_card",
    ):
        for asset in artwork:
            if asset.get("role") == role and not asset["path"].endswith("/T_CommonNone_UI"):
                if url := image_url(asset["path"]):
                    return url
    return None


def _presentation(
    event: ClientSource, occurrence: ClientSource | None, locale: str
) -> dict[str, Any]:
    locales = _client_data(event, occurrence).get("locales", {})
    presentation = locales.get(locale) or locales.get("en") or {}
    notice = ((occurrence.source_data or {}) if occurrence else {}).get(
        "notice_presentation") or {}
    return {**presentation, **(notice.get(locale) or notice.get("en") or {})}


def _official_announcement(event: ClientSource, occurrence: ClientSource | None) -> str | None:
    """Only expose verified official links, never a guessed event/article match."""
    import re

    data = (occurrence.source_data or {}) if occurrence else {}
    url = data.get("official_url")
    if isinstance(url, str) and re.fullmatch(
        r"https://wutheringwaves\.kurogames\.com/(en|zh|ja|ko)/main/news/detail/\d+", url
    ):
        return url
    return None
