"""Event browsing use cases, with persistence and URL policies injected."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from wuwa_story.events.catalog import catalog_rows, is_catalog_capture
from wuwa_story.events.domain import (
    CLIENT_SOURCE,
    SHARED_ACTIVITY_BACKGROUNDS,
    EventAnnouncement,
    EventRun,
    EventRunNotFound,
    _animation_eligible,
    _artwork,
    _choose_preview,
    _client_data,
    _client_preview,
    _notice_overlaps,
    _notice_schedule_evidence,
    _official_announcement,
    _presentation,
)
from wuwa_story.events.repository import EventRepository
from wuwa_story.events.selectable_pools import group_selectable_pools, pool_key, pool_match
from wuwa_story.notices.matching import notice_candidates
from wuwa_story.storage.animation_previews import spine_bundle
from wuwa_story.urls import node_address, record_url


@dataclass
class _Announcements:
    links: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    banners: dict[int, str | None] = field(default_factory=dict)
    snapshots: dict[tuple[int, str], str | None] = field(default_factory=dict)
    latest: dict[int, EventAnnouncement] = field(default_factory=dict)

    def pinned_image(self, run: EventRun) -> str | None:
        data = (run.occurrence.source_data or {}) if run.occurrence else {}
        if data.get("notice_id"):
            revision = data.get("notice_revision")
            snapshot = (
                self.snapshots.get((data["notice_id"], revision)) if revision is not None else None
            )
            return snapshot or self.banners.get(data["notice_id"])
        return None


class EventService:
    def __init__(
        self,
        repository: EventRepository,
        media_url: Callable[[str], str],
        notice_image_url: Callable[[str | None], str | None],
        *,
        animation_enabled: bool = False,
    ) -> None:
        self.repository = repository
        self.media_url = media_url
        self.notice_image_url = notice_image_url
        self.animation_enabled = animation_enabled

    async def browse(
        self,
        server: str,
        kind: str | None = None,
        version: str | None = None,
        locale: str = "en",
        *,
        summary: bool = False,
        event_id: int | None = None,
        occurrence_id: int | None = None,
        _include_pool_match: bool = False,
    ) -> dict[str, Any]:
        runs = await self.repository.runs(
            server=server,
            kind=kind,
            version=version,
            locale=locale,
            summary=summary,
            event_id=event_id,
            occurrence_id=occurrence_id,
        )
        artwork = {run.key: _artwork(run.event, run.occurrence) for run in runs}
        announcements = await self._announcements(runs, event_id)
        if version:
            # Selected snapshots may use a pinned notice revision, never today's
            # unrelated notice artwork as a replacement for missing old media.
            announcements.latest.clear()
            announcements.banners.clear()
        presentations = {run.key: _presentation(run.event, run.occurrence, locale) for run in runs}
        reward_keys = {
            f"item:{reward['id']}"
            for run in runs
            for reward in (presentations[run.key].get("rewards") or run.event.rewards or [])
        }
        reward_nodes = await self.repository.reward_nodes(reward_keys)
        reward_addresses = {node.canonical_key: node_address(node, "item") for node in reward_nodes}
        images = await self._images(runs, artwork)
        captures = {
            (run.event.source_id, client['download_id'])
            for run in runs
            if self.animation_enabled
            and (client := _client_data(run.event, run.occurrence)).get('download_id')
            and _animation_eligible(client)
        }
        spine: dict[tuple[str | None, str | None], dict[str, Any]] = {}
        if captures:
            for bundle in await self.repository.spine(captures):
                capture = (bundle.source_path, bundle.metadata.get('download_id'))
                if capture in captures:
                    spine.setdefault(capture, spine_bundle(
                        self.media_url(bundle.object_key), bundle.file_id, bundle.size_bytes,
                        bundle.metadata, self.animation_enabled))
        rendered = [
            self._render(run, server, locale, summary, artwork[run.key], presentations[run.key],
                         announcements, reward_addresses, images, spine)
            for run in runs
        ]
        if version and summary:
            for row in rendered:
                row["detail_url"] += f"&version={version}"
        if event_id is None:
            rendered = group_selectable_pools(rendered, server, locale)
            rendered = catalog_rows(rendered)
        if not _include_pool_match:
            for row in rendered:
                row.pop("_pool_match", None)
        return {
            "server": server,
            "source": CLIENT_SOURCE,
            "source_url": "https://wutheringwaves.kurogames.com/",
            "catalog_status": "ready" if runs else "not_imported",
            "events": rendered,
        }

    async def detail(
        self,
        event_id: int,
        server: str,
        occurrence_id: int | None = None,
        locale: str = "en",
        version: str | None = None,
    ) -> dict[str, Any]:
        response = await self.browse(
            server,
            version=version,
            locale=locale,
            event_id=event_id,
            occurrence_id=occurrence_id,
            _include_pool_match=True,
        )
        if not response["events"]:
            raise EventRunNotFound()
        result = dict(response["events"][0])
        if pool_key(result):
            identities = await self.repository.pool_occurrences(
                server=server, proof=result["_pool_match"],
                starts_at=result["starts_at"], ends_at=result["ends_at"],
            )
            candidates = []
            if len(identities) <= 64:
                for sibling_id, sibling_occurrence_id in identities:
                    sibling = await self.browse(server, locale=locale, summary=True,
                        version=version,
                        event_id=sibling_id, occurrence_id=sibling_occurrence_id,
                        _include_pool_match=True)
                    candidates.extend(sibling["events"])
            for row in group_selectable_pools(candidates, server, locale):
                if any(member["id"] == event_id and member["occurrence_id"] == occurrence_id
                       for member in row.get("selectable_members", [])):
                    result["featured_names"] = row["featured_names"]
                    result["selectable_members"] = row["selectable_members"]
                    break
        result.pop("_pool_match", None)
        result["id"] = result.get("canonical_event_id") or result["id"]
        if result.pop("_catalog_placeholder", False):
            result["occurrence_id"] = None
        return result

    async def _announcements(self, runs: list[EventRun], event_id: int | None) -> _Announcements:
        notice_ids = {
            identity
            for run in runs
            if run.occurrence
            if (identity := (run.occurrence.source_data or {}).get("notice_id")) is not None
        }
        notices = await self.repository.announcements(event_id=event_id, notice_ids=notice_ids)
        revisions = {
            revision
            for run in runs
            if run.occurrence
            if (revision := (run.occurrence.source_data or {}).get("notice_revision")) is not None
        }
        snapshots = await self.repository.notice_snapshots(revisions)
        result = _Announcements(
            banners={notice.id: notice.content.get("banner_url") for notice in notices},
            snapshots={
                (snapshot.notice_id, snapshot.revision): snapshot.content.get("banner_url")
                for snapshot in snapshots
            },
        )
        catalog = list({run.event.id: run.event for run in runs}.values())
        for notice in notices:
            # These are presentation links; ingestion alone pins a uniquely proven schedule.
            identities = notice.related_event_ids or [
                event.id for event in notice_candidates(notice.content, catalog, match_roster=False)
            ]
            for identity in identities:
                result.links.setdefault(identity, []).append(
                    {
                        "id": notice.id,
                        "title": notice.title,
                        "url": record_url(notice, "notices"),
                    }
                )
                previous = result.latest.get(identity)
                if (
                    previous is None
                    or notice.published_at
                    and (not previous.published_at or notice.published_at > previous.published_at)
                ):
                    result.latest[identity] = notice
        return result

    async def _images(
        self,
        runs: list[EventRun],
        artwork: dict[tuple[int, int | None], list[dict[str, Any]]],
    ) -> dict[tuple[str, str], str]:
        paths = {asset["path"] for run in runs for asset in artwork[run.key]}
        paths.update(run.banner_path for run in runs if run.banner_path)
        if not paths:
            return {}
        captures = {
            (_client_data(run.event, run.occurrence).get("download_id", ""), path)
            for run in runs
            for path in [*(asset["path"] for asset in artwork[run.key]), run.banner_path]
            if path
        }
        media = await self.repository.media(paths, captures=captures)
        images: dict[tuple[str, str], str] = {}
        for image in media:
            if image.source_path is None:
                continue
            download = image.metadata.get("download_id", "")
            if image.source_path in paths:
                images.setdefault((download, image.source_path), self.media_url(image.object_key))
            else:
                for path in paths:
                    if not path.startswith("/Game/") and image.source_path.endswith("/" + path):
                        images.setdefault((download, path), self.media_url(image.object_key))
        return images

    def _render(
        self,
        run: EventRun,
        server: str,
        locale: str,
        summary: bool,
        artwork: list[dict[str, Any]],
        presentation: dict[str, Any],
        announcements: _Announcements,
        reward_addresses: dict[str, dict[str, Any]],
        images: dict[tuple[str, str], str],
        spine: dict[tuple[str | None, str | None], dict[str, Any]],
    ) -> dict[str, Any]:
        event, occurrence = run.event, run.occurrence
        client = _client_data(event, occurrence)

        def image_url(path: str) -> str | None:
            return images.get((client.get("download_id", ""), path))

        banner = image_url(run.banner_path or "")
        if event.source_id.startswith("activity:") and run.banner_path in SHARED_ACTIVITY_BACKGROUNDS:
            banner = None
        image = _client_preview(artwork, image_url, event.source_id) or banner
        notice = announcements.latest.get(event.id)
        captured = (
            ((occurrence.source_data or {}) if occurrence else {})
            .get("client", {})
            .get("download_id")
        )
        preview, provenance = _choose_preview(
            image,
            bool(captured),
            announcements.pinned_image(run),
            notice.content.get("banner_url") if notice else None,
            prefer_notice=_notice_overlaps(notice, occurrence),
        )
        result = {
            "id": event.id,
            "canonical_event_id": getattr(event, "canonical_event_id", None) or event.id,
            "source_event_id": event.id,
            "slug": event.slug,
            "href": record_url(event, "events"),
            "source_id": event.source_id,
            "occurrence_id": occurrence.id if occurrence else None,
            "title": presentation.get("title") or event.title,
            "description": presentation.get("description") or event.description,
            "kind": event.event_kind,
            "convene_type": client.get("convene_type"),
            "game_version": occurrence.game_version if occurrence else None,
            "starts_at": occurrence.starts_at if occurrence else None,
            "start_estimate": (occurrence.source_data or {}).get("start_estimate") if occurrence else None,
            "ends_at": occurrence.ends_at if occurrence else None,
            "season": occurrence.season if occurrence else None,
            "rewards": [
                {**reward, **reward_addresses.get(f"item:{reward['id']}", {})}
                for reward in (presentation.get("rewards") or [])
            ],
            "featured_names": presentation.get("featured_names", []),
            "up_names": presentation.get("up_names", []),
            "banner_path": run.banner_path,
            "banner_url": banner,
            "artwork": [{**asset, "url": image_url(asset["path"])} for asset in artwork],
            "preview_url": self.notice_image_url(preview),
            "preview_provenance": provenance,
            "catalog_version": client.get("asset_version"),
            "schedule_status": (
                "estimated" if occurrence and (occurrence.source_data or {}).get("start_estimate")
                else "recorded" if occurrence and occurrence.starts_at
                else "server_controlled"
            ),
            "hidden_in_client": client.get("hidden", False),
            "game_path": event.game_path,
            "source_url": event.source_url,
            "official_url": _official_announcement(event, occurrence),
            "notices": announcements.links.get(event.id, []),
        }
        if occurrence and (match := pool_match(occurrence.source_data)):
            result["_pool_match"] = match
        if occurrence:
            result["_catalog_placeholder"] = is_catalog_capture(
                getattr(occurrence, "source_occurrence_id", None), occurrence.starts_at,
                occurrence.ends_at, occurrence.source_data)
            captured_client = (occurrence.source_data or {}).get("client")
            result["catalog_provenance"] = (
                "occurrence_snapshot" if captured_client else
                "current_catalog_fallback" if client else "unavailable"
            )
            if evidence := _notice_schedule_evidence(occurrence):
                result["schedule_evidence"] = evidence
        if presentation.get("up_names_source") == "notice":
            result["up_names_source"] = "notice"
            result["client_up_names"] = presentation["client_up_names"]
        bundle = spine.get((event.source_id, client.get('download_id')))
        if bundle and self.animation_enabled and _animation_eligible(client):
            result['spine'] = [bundle]
        if summary:
            result["detail_url"] = f"/events/{event.id}/detail?server={server}&locale={locale}" + (
                f"&occurrence_id={occurrence.id}" if occurrence else ""
            )
        return result
