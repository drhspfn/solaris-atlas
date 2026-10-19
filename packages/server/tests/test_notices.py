from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.db.models.notices import GameNotice
from wuwa_story.ingestion.notices import (
    notice_matches,
    notice_period,
    parse_notice,
    safe_notice_url,
    store_notices,
)


def raw_notice(**extra):
    return {
        "id": "10",
        "tabTitle": "[Artisan's Search] Featured Combat Event",
        "startTimeMs": 1790000000000,
        "whiteList": [],
        "tabBanner": "https://aki-gm-resources-back.aki-game.net/notice/image/test.jpg",
        "content": "<p>✦Duration✦</p><p>2026-10-08 04:00 - 2026-10-26 03:59 (server time)</p><p>✦Rewards✦</p><p>Astrite x500</p>",
        **extra,
    }


def test_notice_markup_is_data_and_never_executable_html():
    notice = parse_notice(
        raw_notice(
            content='<script>alert(1)</script><p>Reward &amp; rules</p><img src="https://aki-gm-resources-back.aki-game.net/notice/image/test.jpg" onerror="alert(1)"><img src="https://evil.test/private"><a href="javascript:alert(1)">Bad</a><a href="browser:https://www.youtube.com/watch?v=test">Video</a>'
        ),
        "game",
    )
    assert "alert" not in notice["text"]
    assert len([b for b in notice["blocks"] if b["type"] == "image"]) == 1
    assert notice["links"] == ["https://www.youtube.com/watch?v=test"]
    assert (
        safe_notice_url("https://user:password@aki-gm-resources-back.aki-game.net/x", image=True)
        is None
    )


def test_duration_uses_explicit_timezone_not_notice_display_window():
    content = parse_notice(raw_notice(), "game")
    asia = notice_period(content, "asia")
    europe = notice_period(content, "europe")
    assert asia[0].isoformat() == "2026-10-07T20:00:00+00:00"
    assert europe[0].isoformat() == "2026-10-08T03:00:00+00:00"
    content["text"] = "✦Duration✦\nVersion 3.7 update - 2026-10-22 09:59 (server time)\n✦Rules✦"
    assert notice_period(content, "asia") is None
    content["text"] = "✦Duration✦\n2026-10-08 04:00 - 2026-10-26 03:59 (UTC+8)\n✦Rules✦"
    assert notice_period(content, "europe") == asia


def test_ambiguous_client_names_do_not_create_guessed_links():
    event = SimpleNamespace(id=1, title="Artisan's Search", event_kind="limited", source_data={})
    notice = parse_notice(raw_notice(), "game")
    assert notice_matches(notice, [event]) == [event]
    assert notice_matches(notice, [event, SimpleNamespace(**{**event.__dict__, "id": 2})]) == []


def test_current_notice_art_never_replaces_a_published_historical_snapshot():
    from wuwa_story.api.routes.game_events import _choose_preview

    assert _choose_preview("old-game-art", True, None, "new-notice-art") == (
        "old-game-art",
        "game_client",
    )
    assert _choose_preview("old-game-art", True, "old-notice-art", "new-notice-art") == (
        "old-notice-art",
        "notice_snapshot",
    )
    assert _choose_preview(None, True, None, "new-notice-art") == (
        "new-notice-art",
        "current_notice",
    )


def test_authored_four_star_roster_can_disambiguate_repeat_banners():
    content = {
        "title": "[Campaign] Featured Resonator Convene",
        "text": "5-Star Resonator: Chisa, 4-Star Resonators: Buling, Taoqi, and Youhu receive boosted drop rates!",
    }

    def banner(identifier, names):
        return SimpleNamespace(
            id=identifier,
            title="Chisa · Resonator Convene",
            event_kind="banner",
            source_data={
                "client": {
                    "locales": {
                        "en": {
                            "title": "Chisa · Resonator Convene",
                            "featured_names": ["Chisa"],
                            "up_names": names,
                        }
                    }
                }
            },
        )

    first = banner(1, ["Buling", "Taoqi", "Youhu"])
    second = banner(2, ["Danjin", "Mortefi", "Chixia"])
    assert notice_matches(content, [first, second]) == [first]
    assert notice_matches(content, [second]) == [
        second
    ]  # A unique authored featured identity is enough.


def test_current_poster_can_identify_subject_without_assigning_ambiguous_schedule():
    from datetime import UTC, datetime
    from wuwa_story.ingestion.notices import notice_candidates
    from wuwa_story.api.routes.game_events import _choose_preview, _notice_overlaps

    content = {"title": "[Moongazer's Sigil] Featured Weapon Convene",
               "text": "5-Star Weapon: Moongazer's Sigil, 4-Star Weapons: A, B, and C receive boosted drop rates!"}
    event = SimpleNamespace(id=1, event_kind="banner", title="Moongazer's Sigil · Weapon Convene",
        source_data={"client": {"locales": {"en": {
            "title": "Moongazer's Sigil · Weapon Convene", "featured_names": ["Moongazer's Sigil"],
            "up_names": ["D", "E", "F"],
        }}}})
    duplicate = SimpleNamespace(**{**event.__dict__, "id": 2})
    assert notice_matches(content, [event, duplicate]) == []
    assert notice_candidates(content, [event, duplicate], match_roster=False) == [event, duplicate]
    now = datetime(2026, 10, 19, tzinfo=UTC)
    occurrence = SimpleNamespace(starts_at=now, ends_at=datetime(2026, 10, 22, tzinfo=UTC))
    notice = SimpleNamespace(content={"raw": {"startTimeMs": now.timestamp() * 1000,
        "endTimeMs": datetime(2026, 11, 1, tzinfo=UTC).timestamp() * 1000}})
    assert _notice_overlaps(notice, occurrence)
    assert _choose_preview("client-icon", True, None, "full-poster", prefer_notice=True) == ("full-poster", "current_notice")
    assert _choose_preview("client-icon", True, "archived-poster", "full-poster", prefer_notice=True) == ("archived-poster", "notice_snapshot")
    occurrence.ends_at = datetime(2026, 9, 1, tzinfo=UTC)
    assert not _notice_overlaps(notice, occurrence)


def test_archived_notice_images_use_public_cdn_without_changing_official_sources(monkeypatch):
    from wuwa_story.config import settings
    from wuwa_story.ingestion.notices import public_notice_image

    monkeypatch.setattr(settings, "get_settings", lambda: SimpleNamespace(
        s3_endpoint_url="https://storage.example", s3_bucket="game",
        media_public_base_url="https://cdn.example"))
    assert public_notice_image("https://storage.example/game/objects/ab/image") == "https://cdn.example/objects/ab/image"
    official = "https://aki-gm-resources-back.aki-game.net/notice/image/full.jpg"
    assert public_notice_image(official) == official
    assert public_notice_image(None) is None


@pytest.mark.asyncio
async def test_notice_import_archives_revision_and_adds_server_specific_runs():
    event = SimpleNamespace(
        id=7,
        title="Artisan's Search",
        event_kind="limited",
        banner_path="/Game/Artisan",
        source_data={"client": {"download_id": "test"}},
    )
    session = AsyncMock()
    session.scalars.return_value = [event]
    session.scalar.return_value = None
    added = []
    session.add = added.append

    async def flush():
        for row in added:
            if isinstance(row, GameNotice):
                row.id = 12

    session.flush.side_effect = flush
    report = await store_notices(
        session, "https://feed.test", "https://board.test", {"game": [raw_notice()]}
    )
    assert report == {"notices": 1, "linked": 1, "dated_runs": 3}
    assert len(added) == 4
    assert added[1].starts_at != added[2].starts_at
    assert added[1].source_data["notice_id"] == 12
    assert all("DELETE" not in str(call.args[0]) for call in session.execute.await_args_list)


@pytest.mark.asyncio
async def test_targeted_announcements_are_not_republished():
    session = AsyncMock()
    session.scalars.return_value = []
    result = await store_notices(
        session, "feed", "board", {"game": [raw_notice(whiteList=["12345"])]}
    )
    assert result["notices"] == 0
    session.add.assert_not_called()
