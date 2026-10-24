"""Assemble event calendars and complete run details from read-only repositories."""

from wuwa_story.config.settings import get_settings
from wuwa_story.db.repositories import event_browsing as repository
from wuwa_story.ingestion.game_events import CLIENT_SOURCE
from wuwa_story.ingestion.notices import notice_candidates, public_notice_image
from wuwa_story.storage.s3 import S3Storage
from wuwa_story.urls import node_address, record_url


async def browse_events(
    server, kind, version, locale, session, *, summary=False, event_id=None, occurrence_id=None,
):
    rows = await repository.read_event_runs(
        session, server=server, kind=kind, version=version, locale=locale,
        summary=summary, event_id=event_id, occurrence_id=occurrence_id,
    )
    artwork = {(event.id, occurrence.id if occurrence else None): _artwork(event, occurrence)
               for event, occurrence in rows}
    def assets(event, occurrence):
        return artwork[(event.id, occurrence.id if occurrence else None)]
    announcements = await repository.read_event_announcements(session)
    notice_links = {}
    notice_banners = {notice.id: notice.content.get("banner_url") for notice in announcements}
    revisions = {(row.source_data or {}).get("notice_revision") for _, row in rows if row}
    snapshots = await repository.read_event_notice_snapshots(session, revisions - {None})
    snapshot_banners = {(snapshot.notice_id, snapshot.revision): snapshot.content.get("banner_url") for snapshot in snapshots}
    latest_notice = {}
    catalog = list({event.id: event for event, _ in rows}.values())
    for notice in announcements:
        # Presentation links may identify the same featured subject across config
        # rows; only store_notices may pin a uniquely proven run and its schedule.
        identities = notice.related_event_ids or [event.id for event in notice_candidates(notice.content, catalog, match_roster=False)]
        for event_id in identities:
            notice_links.setdefault(event_id, []).append({"id": notice.id, "title": notice.title,
                                                          "url": record_url(notice, "notices")})
            previous = latest_notice.get(event_id)
            if previous is None or notice.published_at and (not previous.published_at or notice.published_at > previous.published_at):
                latest_notice[event_id] = notice
    reward_keys = {f"item:{reward['id']}" for event, occurrence in rows
                   for reward in (_presentation(event, occurrence, locale).get("rewards") or event.rewards or [])}
    reward_nodes = await repository.read_event_reward_nodes(session, reward_keys)
    reward_addresses = {node.canonical_key: node_address(node, "item") for node in reward_nodes}
    banner_paths = {asset["path"] for event, occurrence in rows
                    for asset in assets(event, occurrence)}
    banner_paths.update(path for event, occurrence in rows
                        if (path := (occurrence.banner_path if occurrence else None) or event.banner_path))
    banner_urls: dict[tuple[str, str], str] = {}
    if banner_paths:
        settings = get_settings()
        media = await repository.read_event_media(session, banner_paths, settings.s3_bucket)
        storage = S3Storage(settings)
        for source_path, object_key, metadata in media:
            if source_path is None:
                continue
            download = (metadata or {}).get("download_id", "")
            if source_path in banner_paths:
                banner_urls.setdefault(
                    (download, source_path), storage.public_url(object_key))
            else:
                for banner_path in banner_paths:
                    if not banner_path.startswith("/Game/") and source_path.endswith("/" + banner_path):
                        banner_urls.setdefault(
                            (download, banner_path), storage.public_url(object_key))
    def image_url(event, occurrence, path):
        download = _client_data(event, occurrence).get("download_id", "")
        return banner_urls.get((download, path))
    def notice_image(event, occurrence):
        data = (occurrence.source_data or {}) if occurrence else {}
        if data.get("notice_id"):
            return snapshot_banners.get((data["notice_id"], data.get("notice_revision"))) or notice_banners.get(data["notice_id"])
        return None
    def preview(event, occurrence):
        image = _client_preview(assets(event, occurrence),
                                lambda path: image_url(event, occurrence, path))
        image = image or image_url(event, occurrence, (occurrence.banner_path if occurrence else None) or event.banner_path or "")
        notice = latest_notice.get(event.id)
        captured = ((occurrence.source_data or {}) if occurrence else {}).get("client", {}).get("download_id")
        url, provenance = _choose_preview(image, bool(captured), notice_image(event, occurrence), notice.content.get("banner_url") if notice else None,
                                          prefer_notice=_notice_overlaps(notice, occurrence))
        return {"preview_url": public_notice_image(url), "preview_provenance": provenance}
    return {
        "server": server,
        "source": CLIENT_SOURCE,
        "source_url": "https://wutheringwaves.kurogames.com/",
        "catalog_status": "ready" if rows else "not_imported",
        "events": [
            {
                **({"detail_url": f"/events/{event.id}/detail?server={server}&locale={locale}"
                     + (f"&occurrence_id={occurrence.id}" if occurrence else "")}
                   if summary else {}),
                "id": event.id,
                "slug": getattr(event, "slug", None),
                "href": record_url(event, "events"),
                "source_id": event.source_id,
                "occurrence_id": occurrence.id if occurrence else None,
                "title": _presentation(event, occurrence, locale).get("title") or event.title,
                "description": _presentation(event, occurrence, locale).get("description") or event.description,
                "kind": event.event_kind,
                "convene_type": _client_data(event, occurrence).get("convene_type"),
                "game_version": occurrence.game_version if occurrence else None,
                "starts_at": occurrence.starts_at if occurrence else None,
                "ends_at": occurrence.ends_at if occurrence else None,
                "season": occurrence.season if occurrence else None,
                "rewards": [{**reward, **reward_addresses.get(f"item:{reward['id']}", {})}
                            for reward in (_presentation(event, occurrence, locale).get("rewards") or [])],
                "featured_names": _presentation(event, occurrence, locale).get("featured_names", []),
                "up_names": _presentation(event, occurrence, locale).get("up_names", []),
                "banner_path": (occurrence.banner_path if occurrence else None) or event.banner_path,
                "banner_url": image_url(event, occurrence,
                    (occurrence.banner_path if occurrence else None) or event.banner_path or ""
                ),
                "artwork": [{**asset, "url": image_url(event, occurrence, asset["path"])}
                            for asset in assets(event, occurrence)],
                **preview(event, occurrence),
                "catalog_version": _client_data(event, occurrence).get("asset_version"),
                "schedule_status": "recorded" if occurrence and occurrence.starts_at else "server_controlled",
                "hidden_in_client": _client_data(event, occurrence).get("hidden", False),
                "game_path": event.game_path,
                "source_url": event.source_url,
                "official_url": _official_announcement(event, occurrence),
                "notices": notice_links.get(event.id, []),
            }
            for event, occurrence in rows
        ],
    }


def _client_data(event, occurrence):
    current = (event.source_data or {}).get("client", {})
    dated = ((occurrence.source_data or {})
             if occurrence else {}).get("client", {})
    if current.get("download_id") and current.get("download_id") == dated.get("download_id"):
        return current
    return dated or current


def _notice_overlaps(notice, occurrence):
    if not notice or not occurrence or not occurrence.starts_at or not occurrence.ends_at:
        return False
    raw = notice.content.get("raw", {})
    start, end = raw.get("startTimeMs"), raw.get("endTimeMs")
    return bool(start and end and occurrence.starts_at.timestamp() * 1000 < end
                and occurrence.ends_at.timestamp() * 1000 > start)


def _choose_preview(client_image, captured_client, notice_snapshot, current_notice, *, prefer_notice=False):
    if notice_snapshot:
        return notice_snapshot, "notice_snapshot"
    if current_notice and prefer_notice:
        return current_notice, "current_notice"
    if client_image and captured_client:
        return client_image, "game_client"
    if current_notice:
        return current_notice, "current_notice"
    return client_image, "game_client"


def _artwork(event, occurrence):
    client = _client_data(event, occurrence)
    unique = {}
    for asset in client.get("artwork", []) + client.get("prefab_artwork", []):
        # Quest illustrations and prefab defaults do not prove a banner's subject,
        # element or weapon type. Runtime bindings can replace these textures.
        if asset.get("role") in {"featured_stand", "quest_illustration"}:
            continue
        if asset.get("role") == "prefab_texture" and any(part in asset["path"] for part in (
            "/SkillIcon/SkillIconNor/", "/IconElementRound/", "/IconElement/")):
            continue
        unique.setdefault(asset["path"], asset)
    return list(unique.values())


def _client_preview(artwork, image_url):
    for role in ("content", "tab_selected", "tab", "featured_portrait", "featured_weapon", "featured_card"):
        for asset in artwork:
            if asset.get("role") == role and not asset["path"].endswith("/T_CommonNone_UI"):
                if url := image_url(asset["path"]):
                    return url
    return None


def _presentation(event, occurrence, locale):
    locales = _client_data(event, occurrence).get("locales", {})
    presentation = locales.get(locale) or locales.get("en") or {}
    notice = ((occurrence.source_data or {}) if occurrence else {}).get("notice_presentation") or {}
    return {**presentation, **(notice.get(locale) or notice.get("en") or {})}


def _official_announcement(event, occurrence):
    """Only expose verified official links, never a guessed event/article match."""
    import re

    data = (occurrence.source_data or {}) if occurrence else {}
    url = data.get("official_url")
    if isinstance(url, str) and re.fullmatch(
        r"https://wutheringwaves\.kurogames\.com/(en|zh|ja|ko)/main/news/detail/\d+", url
    ):
        return url
    return None
