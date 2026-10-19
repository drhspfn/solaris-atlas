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
from wuwa_story.ingestion.game_events import CLIENT_SOURCE

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
    """Only explicit full timestamps in the Duration section define a dated run."""
    section = re.search(r"Duration[✦\s]*(.*?)(?:✦|$)", content["text"], re.S)
    if not section:
        return None
    match = re.search(
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s*[-–]\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s*\((server time|UTC[+-]\d{1,2})\)",
        section[1],
    )
    if not match:
        return None
    offset = SERVER_OFFSETS[server] if match[3] == "server time" else int(match[3][3:])
    if not -12 <= offset <= 14:
        return None
    zone = timezone(timedelta(hours=offset))
    try:
        start, end = [
            datetime.strptime(match[i], "%Y-%m-%d %H:%M").replace(tzinfo=zone).astimezone(UTC)
            for i in (1, 2)
        ]
    except ValueError:
        return None
    return (start, end) if end > start else None


def notice_matches(content, events):
    def normalized(value):
        return re.sub(r"[^\w]+", "", value.casefold())

    title = re.search(r"\[([^]]+)\]", content["title"])
    name = normalized(title[1]) if title else None
    featured = re.search(r"5-Star (Resonator|Weapon):\s*([^,\n]+)", content["text"])
    rate_up = re.search(
        r"4-Star (?:Resonators|Weapons):\s*(.*?)\s+receive boosted drop rates",
        content["text"],
        re.S,
    )
    rate_up_names = (
        {normalized(n) for n in re.split(r",|\band\b", rate_up[1]) if n.strip()}
        if rate_up
        else None
    )
    matches = []
    for event in events:
        english = (event.source_data or {}).get("client", {}).get("locales", {}).get("en", {})
        exact_title = name and normalized(english.get("title", event.title)) == name
        exact_featured = (
            featured
            and event.event_kind == "banner"
            and any(
                normalized(n) == normalized(featured[2]) for n in english.get("featured_names", [])
            )
            and ((featured[1] == "Weapon") == ("Weapon Convene" in english.get("title", "")))
        )
        if exact_title or exact_featured:
            matches.append(event)
    if len(matches) > 1 and rate_up_names:
        matches = [
            event
            for event in matches
            if rate_up_names
            == {
                normalized(n)
                for n in (event.source_data or {})
                .get("client", {})
                .get("locales", {})
                .get("en", {})
                .get("up_names", [])
            }
        ]
    # Repeated titles/featured characters are not proof of which client row is used.
    return matches if len(matches) == 1 else []


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
            if notice.revision == revision and notice.related_event_ids:
                matched = [event for event in events if event.id in notice.related_event_ids]
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
                    period = notice_period(content, server)
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
                        identity = f"notice:{notice.source_id}@{start.isoformat()}"
                        row = await session.scalar(
                            select(GameEventOccurrence)
                            .where(
                                GameEventOccurrence.event_id == event.id,
                                GameEventOccurrence.server == server,
                                GameEventOccurrence.starts_at == start,
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
                    snapshot = (row.source_data or {}).get("client") or event.source_data.get(
                        "client", {}
                    )
                    row.source_data = {
                        **(row.source_data or {}),
                        "client": snapshot,
                        "notice_id": notice.id,
                        "notice_revision": revision,
                        "notice_presentation": {
                            "en": {"title": content["title"], "description": content["text"]}
                        },
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
