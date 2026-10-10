"""Import the public, versioned event schedule compiled from WuWa client data."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.game_events import GameEvent, GameEventOccurrence

SOURCE = "Sanma5657/wuwa-wiki-public"
SOURCE_COMMIT_URL = f"https://api.github.com/repos/{SOURCE}/commits/main"
SOURCE_RAW_ROOT = f"https://raw.githubusercontent.com/{SOURCE}"
SERVER_KEYS = ("asia", "europe", "america")


async def import_game_events(session: AsyncSession) -> dict[str, Any]:
    """Fetch a pinned upstream snapshot and idempotently synchronize its event schedules."""
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        response = await client.get(
            SOURCE_COMMIT_URL, headers={"Accept": "application/vnd.github+json"}
        )
        response.raise_for_status()
        revision = response.json().get("sha")
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("The upstream event archive did not return a valid commit SHA")
        source_root = f"{SOURCE_RAW_ROOT}/{revision}"
        documents = {}
        for filename in ("events.json", "eventsinfo.json"):
            response = await client.get(f"{source_root}/{filename}")
            response.raise_for_status()
            documents[filename] = response.json()

    details = {str(item["id"]): item for item in documents["eventsinfo.json"]}
    event_rows: dict[str, dict[str, Any]] = {}
    occurrence_rows: list[dict[str, Any]] = []
    source_url = f"https://github.com/{SOURCE}/blob/{revision}/events.json"

    for group in documents["events.json"].get("list", []):
        group_id = str(group.get("id", "unknown"))
        for item in group.get("child", []):
            item_id = str(item.get("id", "unknown"))
            canonical_source_id = str(item.get("sourceId") or item_id)
            event_source_id = f"{group_id}:{canonical_source_id}"
            asset = item.get("img")
            path = item.get("path")
            event_info = details.get(str(item.get("detailId")))
            title = _title(path, asset, item_id)
            if event_source_id not in event_rows:
                event_rows[event_source_id] = {
                    "source": SOURCE,
                    "source_id": event_source_id,
                    "title": title,
                    "description": event_info.get("description") if event_info else None,
                    "event_kind": _event_kind(group_id, item),
                    "group_label_key": group.get("label"),
                    "game_path": path,
                    "banner_path": asset,
                    "rewards": item.get("drops")
                    or (event_info.get("drops") if event_info else None),
                    "source_url": source_url,
                    "source_revision": revision,
                    "source_data": {"group_id": group_id, "item": item, "event_info": event_info},
                }
            for index, region_time in enumerate(item.get("time", [])):
                region = SERVER_KEYS[index] if index < len(SERVER_KEYS) else f"region_{index + 1}"
                start = region_time[0] if region_time else None
                end = region_time[1] if len(region_time) > 1 else None
                versions = item.get("ver") or []
                version = next((v for v in versions if re.fullmatch(r"\d+\.\d+", str(v))), None)
                occurrence_rows.append(
                    {
                        "source_id": event_source_id,
                        "source_occurrence_id": item_id,
                        "game_version": version,
                        "server": region,
                        "starts_at": _datetime(start),
                        "ends_at": _datetime(end),
                        "banner_path": asset,
                        "rewards": item.get("drops"),
                        "season": item.get("season"),
                        "source_data": {"time": region_time, "versions": versions},
                    }
                )

    if event_rows:
        statement = insert(GameEvent).values(list(event_rows.values()))
        statement = statement.on_conflict_do_update(
            constraint="uq_game_event_source_identity",
            set_={
                key: getattr(statement.excluded, key)
                for key in (
                    "title",
                    "description",
                    "event_kind",
                    "group_label_key",
                    "game_path",
                    "banner_path",
                    "rewards",
                    "source_url",
                    "source_revision",
                    "source_data",
                )
            },
        )
        await session.execute(statement)

    if not event_rows:
        raise ValueError("The upstream event archive contains no event records")

    existing_ids = dict(
        (
            await session.execute(
                select(GameEvent.source_id, GameEvent.id).where(GameEvent.source == SOURCE)
            )
        ).all()
    )
    stale_ids = set(existing_ids.values()) - set(event_rows)
    if stale_ids:
        stale_event_ids = [existing_ids[source_id] for source_id in stale_ids]
        await session.execute(
            delete(GameEventOccurrence).where(GameEventOccurrence.event_id.in_(stale_event_ids))
        )
        await session.execute(delete(GameEvent).where(GameEvent.id.in_(stale_event_ids)))

    event_ids = dict(
        (
            await session.execute(
                select(GameEvent.source_id, GameEvent.id).where(
                    GameEvent.source == SOURCE,
                    GameEvent.source_id.in_(event_rows.keys()),
                )
            )
        ).all()
    )
    values = [
        {**row, "event_id": event_ids[row["source_id"]]}
        for row in occurrence_rows
        if row["source_id"] in event_ids
    ]
    if event_ids:
        await session.execute(
            delete(GameEventOccurrence).where(
                GameEventOccurrence.event_id.in_(event_ids.values())
            )
        )
    if values:
        statement = insert(GameEventOccurrence).values(values)
        statement = statement.on_conflict_do_update(
            constraint="uq_game_event_occurrence",
            set_={
                key: getattr(statement.excluded, key)
                for key in (
                    "game_version",
                    "starts_at",
                    "ends_at",
                    "banner_path",
                    "rewards",
                    "season",
                    "source_data",
                )
            },
        )
        await session.execute(statement)
    await session.flush()
    return {
        "events": len(event_rows),
        "occurrences": len(values),
        "source": SOURCE,
        "source_revision": revision,
    }


def _datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _event_kind(group_id: str, item: dict[str, Any]) -> str:
    if group_id == "1":
        return "banner"
    if group_id == "3":
        return "recurring"
    if group_id == "4":
        return "permanent"
    return "limited"


def _title(path: Any, asset: Any, item_id: str) -> str:
    if isinstance(path, str) and path.startswith("events/"):
        slug = path.rsplit("/", 1)[-1]
        known = {"matrix": "Endstate Matrix", "towerofadversity": "Tower of Adversity"}
        return known.get(slug, _humanize(slug))
    if isinstance(asset, str):
        leaf = asset.rsplit("/", 1)[-1]
        for prefix, label in (
            ("T_RoleShare_", "Resonator Convene"),
            ("T_WeaponShare_", "Weapon Convene"),
        ):
            if leaf.startswith(prefix):
                return f"{_humanize(leaf.removeprefix(prefix))} · {label}"
    return f"In-game activity · {item_id}"


def _humanize(value: str) -> str:
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value.replace("_", " ")).strip().title()
