"""The public in-game Notice feed. No account, device identifier or login is used."""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urlencode, urlparse

import httpx
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from wuwa_story.db.models.game_events import GameEvent, GameEventOccurrence
from wuwa_story.db.models.notices import GameNotice, GameNoticeRevision
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.ingestion.game_events import CLIENT_SOURCE
from wuwa_story.ingestion.notice_trials import embedded_notice_activities, notice_trial_matches
from wuwa_story.notices.matching import notice_candidates as notice_candidates
from wuwa_story.notices.matching import notice_matches as notice_matches
from wuwa_story.storage.notice_images import public_notice_image as public_notice_image

NOTICE_HOSTS = {"aki-gm-resources-oversea.aki-game.net", "aki-gm-resources-back.aki-game.net"}
SERVER_OFFSETS = {"asia": 8, "europe": 1, "america": -5}


def safe_notice_url(value, *, image=False):
    if not isinstance(value, str):
        return None
    value = value.removeprefix("browser:").strip()
    try:
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port
        ):
            return None
    except ValueError:
        return None
    if image and not (
        parsed.hostname in NOTICE_HOSTS
        or parsed.hostname.endswith((".kurogame.com", ".kurogames.com", ".aki-game.net"))
    ):
        return None
    return value


class NoticeBody(HTMLParser):
    """Convert untrusted markup to text/media blocks; never render source HTML."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks, self.links, self.pending = [], [], []
        self.ignored = 0

    def flush(self):
        value = re.sub(r"[ \t\xa0]+", " ", "".join(self.pending)).strip()
        if value:
            self.blocks.append({"type": "text", "text": value})
        self.pending = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.ignored += 1
        if self.ignored:
            return
        attrs = dict(attrs)
        if tag in {"div", "p", "br", "tr", "li", "h1", "h2", "h3"}:
            self.flush()
        if tag == "img" and (url := safe_notice_url(attrs.get("src"), image=True)):
            self.flush()
            self.blocks.append({"type": "image", "url": url, "source_url": url})
        if tag in {"a", "iframe", "video", "source"}:
            url = safe_notice_url(attrs.get("href") if tag == "a" else attrs.get("src"))
            if url and url not in self.links:
                self.links.append(url)

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.ignored = max(0, self.ignored - 1)
        if not self.ignored and tag in {"div", "p", "tr", "li", "h1", "h2", "h3"}:
            self.flush()

    def handle_data(self, value):
        if not self.ignored:
            self.pending.append(value)


def parse_notice(raw, category):
    parser = NoticeBody()
    parser.feed(raw.get("content", ""))
    parser.flush()
    return {
        "title": re.sub(r"\s+", " ", str(raw["tabTitle"])).strip(),
        "category": category,
        "blocks": parser.blocks,
        "links": parser.links,
        "banner_url": safe_notice_url(raw.get("tabBanner"), image=True),
        "fold_banner_url": safe_notice_url(raw.get("foldBanner"), image=True),
        "text": "\n".join(b["text"] for b in parser.blocks if b["type"] == "text"),
    }


def notice_period(content, server):
    """Extract stated timestamps without guessing the time of a version update."""
    section = re.search(r"Duration[:✦\s]*(.*?)(?:✦|$)", content["text"], re.S)
    if not section:
        return None
    match = re.search(
        r"(?:(\d{4}-\d{2}-\d{2} \d{2}:\d{2})|(?:After\s+the\s+)?Version\s+\d+\.\d+\s+update)\s*[-–]\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s*\((server time|UTC[+-]\d{1,2})\)",
        section[1],
        re.I,
    )
    if not match:
        return None
    offset = SERVER_OFFSETS[server] if match[3].lower() == "server time" else int(match[3][3:])
    if not -12 <= offset <= 14:
        return None
    zone = timezone(timedelta(hours=offset))
    try:
        start, end = [
            datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=zone).astimezone(UTC)
            if value else None
            for value in (match[1], match[2])
        ]
    except ValueError:
        return None
    return (start, end) if start is None or end > start else None


async def version_start_estimate(session, content, server, published_at):
    """Use only nearby ends from the immediately preceding numbered version."""
    section = re.search(r"Duration[✦\s]*(.*?)(?:✦|$)", content["text"], re.S)
    version = re.search(r"(?:After\s+the\s+)?Version\s+(\d+)\.(\d+)\s+update\s*[-–]", section[1], re.I) if section else None
    if not version or not published_at or int(version[2]) == 0:
        return None
    previous = f"{version[1]}.{int(version[2]) - 1}"
    candidates = list(await session.scalars(
        select(GameEventOccurrence).join(GameEvent, GameEvent.id == GameEventOccurrence.event_id)
        .where(
            GameEvent.source == CLIENT_SOURCE,
            GameEvent.event_kind == "banner",
            GameEvent.source_data["client"]["gacha_type"].as_integer().in_([2, 3]),
            GameEventOccurrence.game_version == previous,
            GameEventOccurrence.server == server,
            GameEventOccurrence.ends_at >= published_at - timedelta(days=14),
            GameEventOccurrence.ends_at <= published_at + timedelta(days=14),
        ).order_by(GameEventOccurrence.ends_at.desc(), GameEventOccurrence.id)
    ))
    if not candidates:
        return None
    latest = candidates[0]
    # Different nearby boundaries are ambiguous; second/millisecond truncation is normal.
    if any(timedelta(minutes=5) < latest.ends_at - row.ends_at < timedelta(days=1)
           for row in candidates[1:]):
        return None
    return latest


async def selectable_banner_matches(session, content, events):
    """Require a complete selectable roster and an explicit, same-release pool join."""
    match = re.search(
        r"selectable 5-Star (Resonators|Weapons):\s*(.*?),\s*and 4-Star ",
        content["text"],
    )
    if not match:
        return [], None
    kind = "Resonator" if match[1] == "Resonators" else "Weapon"
    if f"Reverb {kind} Convene" not in content["title"]:
        return [], None
    def normalize(value):
        return re.sub(r"[^\w]+", "", value.casefold())
    names = [normalize(name) for name in re.split(r",|\band\b", match[2]) if name.strip()]
    if len(names) < 2 or len(names) != len(set(names)):
        return [], None
    candidates = [event for event in notice_candidates(content, events, match_roster=False)
                  if event.event_kind == "banner"
                  and (event.source_data or {}).get("client", {}).get("gacha_type") == (13 if kind == "Resonator" else 14)
                  and len((event.source_data or {}).get("client", {}).get("locales", {}).get("en", {}).get("featured_names", [])) == 1
                  and normalize(event.source_data["client"]["locales"]["en"]["featured_names"][0]) in names]
    if len(candidates) != len(names) or {normalize(event.source_data["client"]["locales"]["en"]["featured_names"][0]) for event in candidates} != set(names):
        return [], None
    versions = {event.source_data["client"].get("asset_version") for event in candidates}
    ids = [event.source_id.removeprefix("gacha:") for event in candidates]
    if len(versions) != 1 or None in versions or any(not event.source_id.startswith("gacha:") or not identifier.isdecimal() for event, identifier in zip(candidates, ids, strict=True)):
        return [], None
    rows = (await session.execute(
        select(SourceRecord.release_id, SourceRecord.data)
        .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .join(GameRelease, GameRelease.id == SourceRecord.release_id)
        .where(SourceFile.release_id == SourceRecord.release_id,
               SourceFile.logical_source_path == "BinData/gacha/gachapool.json",
               GameRelease.game_version == next(iter(versions)))
    )).all()
    groups = {}
    for release_id, data in rows:
        if type(data.get("Id")) is int and type(data.get("GachaId")) is int:
            groups.setdefault((release_id, data["GachaId"]), []).append(str(data["Id"]))
    proven = [(release, pool) for (release, pool), members in groups.items()
              if len(members) == len(ids) and set(members) == set(ids)]
    if len(proven) != 1:
        return [], None
    release, pool = proven[0]
    if any(sum(identifier in members for (group_release, _), members in groups.items()
               if group_release == release) != 1 for identifier in ids):
        return [], None
    return candidates, {"basis": "gacha_pool", "release_id": release, "gacha_id": pool}


async def fetch_notice_feed(launcher_define):
    """Discover the deployed Notice web application's API origin from client constants."""

    def constant(name):
        match = re.search(rf'{name}="([^"]+)"', launcher_define)
        if not match:
            raise ValueError(f"Client Notice definition missing {name}")
        return match[1]

    page = constant("NOTICE_PRE_URL_GLOBAL")
    server = constant("SERVER_ID_GLOBAL")
    if not re.fullmatch(r"[a-f0-9]{32}", server) or not safe_notice_url(page, image=True):
        raise ValueError("Unexpected public Notice configuration")
    if urlparse(page).hostname not in NOTICE_HOSTS:
        raise ValueError("Unexpected Notice web host")
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        response = await client.get(page)
        response.raise_for_status()
        script = re.search(r'src="\./(assets/main-[\w-]+\.js)"', response.text)
        if not script:
            raise ValueError("Notice web application entry not found")
        response = await client.get(page.rsplit("/", 1)[0] + "/" + script[1])
        response.raise_for_status()
        origin = re.search(r'VITE_APP_AXIOS_BASE_URL_GLOBAL_FIRST:"([^"]+)"', response.text)
        if (
            not origin
            or urlparse(origin[1]).hostname not in NOTICE_HOSTS
            or not safe_notice_url(origin[1])
        ):
            raise ValueError("Unexpected public Notice API origin")
        url = f"{origin[1]}/gamenotice/G153/{server}/en.json"
        response = await client.get(url)
        response.raise_for_status()
        if len(response.content) > 10 * 1024 * 1024:
            raise ValueError("Notice feed exceeds the import limit")
        document = response.json()
    if not isinstance(document, dict) or not any(
        isinstance(document.get(k), list) and document[k] for k in ("game", "activity", "recommend")
    ):
        raise ValueError("Public Notice feed contains no supported categories")
    board = (
        page
        + "?"
        + urlencode(
            {
                "server_id": server,
                "game_id": "G153",
                "svr_area": "global",
                "platform": "PC",
                "lang": "en",
                "role_id": "1000000000",
                "user_id": "before_login",
            }
        )
    )
    return url, board, document


async def store_notices(session, feed, page, document):
    await session.execute(text("SELECT pg_advisory_xact_lock(705, 2)"))
    events = list(await session.scalars(select(GameEvent).where(GameEvent.source == CLIENT_SOURCE)))
    report = {"notices": 0, "linked": 0, "dated_runs": 0}
    anchors = {}
    direct_titles = {
        re.sub(r"[^\w]+", "", title[1].casefold())
        for category in ("recommend", "game", "activity")
        for raw in document.get(category, []) if not raw.get("whiteList")
        if (title := re.search(r"\[([^]]+)\]", str(raw.get("tabTitle", ""))))
    }
    for category in ("recommend", "game", "activity"):
        for raw in document.get(category, []):
            if raw.get("whiteList"):
                continue  # Do not republish targeted/player-specific announcements.
            content = parse_notice(raw, category)
            revision = hashlib.sha256(
                json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            notice = await session.scalar(
                select(GameNotice).where(
                    GameNotice.feed == feed,
                    GameNotice.locale == "en",
                    GameNotice.source_id == str(raw["id"]),
                )
            )
            if notice is None:
                notice = GameNotice(feed=feed, locale="en", source_id=str(raw["id"]))
                session.add(notice)
            matched = notice_matches(content, events)
            match_evidence = None
            if not matched:
                matched, match_evidence = await selectable_banner_matches(session, content, events)
            if (notice.revision == revision and notice.related_event_ids
                and "selectable 5-Star" not in content["text"]):
                matched = [event for event in events if event.id in notice.related_event_ids]
            sections = embedded_notice_activities(content, events)
            sections = [section for section in sections if re.sub(r"[^\w]+", "", section[0].title.casefold()) not in direct_titles]
            if re.search(r"Patch Notes for Version\s+\d+\.\d+", content["title"], re.I):
                matched = []
            trials = [*sections, *(await notice_trial_matches(session, content, events))]
            trial_details = {event.id: (trial_content, evidence) for event, trial_content, evidence in trials}
            if ("will become available in Ascendant Aces as Trial Resonators" in content["text"]
                or "[Glamour Couture] Outfit Tryout" in content["text"]):
                matched = [event for event in matched if (event.source_data or {}).get("client", {}).get("activity_type") not in (8, 35) or event.id in trial_details]
            matched = list({event.id: event for event in [*matched, *(trial[0] for trial in trials)]}.values())
            notice.title, notice.category = content["title"], category
            if notice.revision != revision:
                notice.content = {
                    **content,
                    "raw": raw,
                    "related_event_ids": [event.id for event in matched],
                }
            notice.source_url, notice.revision = page, revision
            notice.related_event_ids = [event.id for event in matched]
            notice.published_at = (
                datetime.fromtimestamp(raw["startTimeMs"] / 1000, UTC)
                if raw.get("startTimeMs")
                else None
            )
            notice.observed_at = datetime.now(UTC)
            await session.flush()
            await session.execute(
                insert(GameNoticeRevision)
                .values(notice_id=notice.id, revision=revision, content=notice.content)
                .on_conflict_do_nothing()
            )
            for event in matched:
                for server in SERVER_OFFSETS:
                    trial = trial_details.get(event.id)
                    period = notice_period(trial[0] if trial else content, server)
                    if trial and not period:
                        continue
                    if not period:
                        # A display window can disambiguate an existing run, but
                        # must never create or replace that run's schedule.
                        if not raw.get("startTimeMs") or not raw.get("endTimeMs"):
                            continue
                        visible_start = datetime.fromtimestamp(raw["startTimeMs"] / 1000, UTC)
                        visible_end = datetime.fromtimestamp(raw["endTimeMs"] / 1000, UTC)
                        candidates = list(
                            await session.scalars(
                                select(GameEventOccurrence)
                                .where(
                                    GameEventOccurrence.event_id == event.id,
                                    GameEventOccurrence.server == server,
                                    GameEventOccurrence.starts_at < visible_end,
                                    GameEventOccurrence.ends_at > visible_start,
                                )
                                .with_for_update()
                            )
                        )
                        if len(candidates) != 1:
                            continue
                        row = candidates[0]
                    else:
                        start, end = period
                        if trial and trial[1]["basis"] == "notice_section":
                            existing = await session.scalar(select(GameEventOccurrence.id).where(
                                GameEventOccurrence.event_id == event.id,
                                GameEventOccurrence.server == server,
                                GameEventOccurrence.ends_at == end,
                            ).limit(1))
                            if existing is not None:
                                continue
                        identity = (
                            f"trial:{end.isoformat()}" if trial else
                            f"notice:{notice.source_id}@{start.isoformat()}" if start
                            else f"notice:{notice.source_id}@update"
                        )
                        row = await session.scalar(
                            select(GameEventOccurrence)
                            .where(
                                GameEventOccurrence.event_id == event.id,
                                GameEventOccurrence.server == server,
                                (GameEventOccurrence.starts_at == start if start and not trial else
                                 GameEventOccurrence.source_occurrence_id == identity),
                            )
                            .with_for_update()
                        )
                        if row is None:
                            row = GameEventOccurrence(
                                event_id=event.id,
                                server=server,
                                source_occurrence_id=identity,
                                starts_at=start,
                                ends_at=end,
                                source_data={},
                            )
                            session.add(row)
                        row.ends_at = end
                        if trial:
                            version = (re.search(r"Version\s+(\d+\.\d+)\s+update", trial[0]["text"], re.I)
                                       or re.search(r"Patch Notes for Version\s+(\d+\.\d+)", content["title"], re.I))
                            if version:
                                row.game_version = version[1]
                        if start is None and not trial:
                            version = re.search(r"Version\s+(\d+\.\d+)\s+update", content["text"], re.I)
                            key = (version[1], server) if version else None
                            if key and key not in anchors:
                                anchors[key] = await version_start_estimate(session, content, server, notice.published_at)
                            anchor = anchors.get(key)
                            old_estimate = (row.source_data or {}).get("start_estimate")
                            if row.starts_at is None or old_estimate:
                                row.starts_at = anchor.ends_at if anchor and anchor.ends_at < end else None
                                row.source_data = {**(row.source_data or {})}
                                row.source_data.pop("start_estimate", None)
                                if row.starts_at:
                                    row.source_data["start_estimate"] = {
                                        "basis": "previous_version_banner_end",
                                        "occurrence_id": anchor.id,
                                        "version": anchor.game_version,
                                        "server": server,
                                    }
                    snapshot = (row.source_data or {}).get("client") or event.source_data.get(
                        "client", {}
                    )
                    row.source_data = {
                        **(row.source_data or {}),
                        "client": snapshot,
                        "notice_id": notice.id,
                        "notice_revision": revision,
                        **({"notice_match": match_evidence} if match_evidence else {}),
                        **({"schedule_match": trial[1]} if trial else {}),
                        **({"notice_presentation": {
                            "en": {"title": content["title"], "description": content["text"]}
                        }} if not trial else {}),
                        **(
                            {"schedule_source": feed, "schedule_revision": revision}
                            if period
                            else {}
                        ),
                    }
                    row.banner_path = row.banner_path or event.banner_path
                    report["dated_runs"] += 1
            report["notices"] += 1
            report["linked"] += bool(matched)
    await session.flush()
    return report
