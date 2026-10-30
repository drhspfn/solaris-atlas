"""Event occurrence and announcement response projections."""

from fastapi import HTTPException
from sqlalchemy import select

from wuwa_story.db.repositories.read_models import ENTITY_READ_MODELS
from wuwa_story.read_models.readers import entities


def _time(value):
    from datetime import datetime

    if value is None:
        return float("-inf")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, AttributeError):
        raise entities._unready() from None


async def event_detail(session, event_id, server, occurrence_id, locale="en"):
    payload = await entities.page_payload(session, "game_event", event_id, locale, selector=None)
    runs = payload.get("occurrences")
    event = payload.get("event")
    if not isinstance(runs, list) or not isinstance(event, dict):
        raise entities._unready()
    if occurrence_id is None:
        if (
            not any(run.get("server") == server for run in runs)
            and event.get("occurrence_id") is None
        ):
            return {key: value for key, value in event.items() if key != "server"}
    else:
        matches = [
            run
            for run in runs
            if run.get("server") == server and run.get("occurrence_id") == occurrence_id
        ]
        if len(matches) == 1:
            return {key: value for key, value in matches[0].items() if key != "server"}
    raise HTTPException(404, "Event run not found")


async def events_page(session, server, kind=None, version=None, locale="en", summary=False):
    entries = await entities._scope_entries(
        session,
        "game_event",
        locale,
        "current",
        selector="calendar" if summary else None,
        card=summary,
    )
    events = []
    preview_roles = {
        "content",
        "tab_selected",
        "tab",
        "featured_portrait",
        "featured_weapon",
        "featured_card",
        "content_background",
        "background",
    }
    for entry in entries:
        payload = entry.payload
        event, runs = payload.get("event"), payload.get("occurrences")
        if not isinstance(event, dict) or not isinstance(runs, list):
            raise entities._unready()
        if kind and event.get("kind") != kind:
            continue
        server_runs = [run for run in runs if run.get("server") == server]
        selected = server_runs
        if version:
            selected = [run for run in selected if run.get("game_version") in (version, None)]
        if not server_runs:
            if "occurrence_id" not in event:
                raise entities._unready()
            selected = [event]
        for run in selected:
            result = {key: value for key, value in run.items() if key != "server"}
            if summary:
                result["artwork"] = [
                    art for art in run.get("artwork", []) if art.get("role") in preview_roles
                ]
                result["detail_url"] = (
                    f"/events/{entry.entity_id}/detail?server={server}&locale={locale}"
                )
                if run.get("occurrence_id") is not None:
                    result["detail_url"] += f"&occurrence_id={run['occurrence_id']}"
            events.append(result)
    events.sort(key=lambda event: (-_time(event.get("starts_at")), event["title"]))
    return {
        "server": server,
        "source": "wuwa-game-client",
        "source_url": "https://wutheringwaves.kurogames.com/",
        "catalog_status": "ready",
        "events": events,
    }


async def notice_detail_payload(session, identity, revision=None):
    payload = await entities.page_payload(session, "game_notice", identity, "en", selector=None)
    clean = {
        key: value
        for key, value in payload.items()
        if key not in {"card", "revision_snapshots", "revision_snapshot_format"}
    }
    if not revision or payload.get("revision") == revision:
        return clean
    if revision not in payload.get("revisions", []):
        entities._missing(await entities._require_scope(session, "game_notice", "current"))
    if payload.get("revision_snapshot_format") != "overrides-v1":
        raise entities._unready()
    overrides = payload.get("revision_snapshots", {}).get(revision)
    if not isinstance(overrides, dict):
        raise entities._unready()
    return {**clean, **overrides}


async def notices_page(session, category=None, q="", offset=0, limit=24, event_id=None):
    model = ENTITY_READ_MODELS["game_notice"]
    locale_id = await entities._locale_id(session, "en")
    scope = (model.locale_id == locale_id, model.snapshot_key == "current")
    await entities._require_scope(session, "game_notice", "current")
    notice = model.card["notice"]
    filters = list(scope)
    if category:
        filters.append(notice["category"].as_string() == category)
    if q.strip():
        filters.append(model.title.icontains(q.strip(), autoescape=True))
    if event_id:
        filters.append(notice["related_event_ids"].contains([event_id]))
    fields = (
        "id",
        "slug",
        "href",
        "source_id",
        "locale",
        "category",
        "title",
        "revision",
        "banner_url",
        "source_url",
        "available_from",
        "observed_at",
        "related_event_ids",
    )
    statement = (
        select(model.generation, *[notice[key].label(key) for key in fields])
        .where(*filters)
        .order_by(
            notice["available_from"].as_string().desc().nullslast(),
            model.entity_id.desc(),
        )
        .offset(offset)
        .limit(limit + 1)
    )
    rows = (await session.execute(statement)).mappings().all()
    if any(
        row["generation"] < 1 or any(row[key] is None for key in ("id", "title", "revision"))
        for row in rows
    ):
        raise entities._unready()
    return {
        "notices": [{key: row[key] for key in fields} for row in rows[:limit]],
        "has_more": len(rows) > limit,
        "offset": offset,
    }
