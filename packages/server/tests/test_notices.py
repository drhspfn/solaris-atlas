from datetime import UTC, datetime, timedelta
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
    version_start_estimate,
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
    assert notice_period(content, "asia")[0] is None
    content["text"] = "✦Duration✦\n2026-10-08 04:00 - 2026-10-26 03:59 (UTC+8)\n✦Rules✦"
    assert notice_period(content, "europe") == asia


def test_version_update_period_preserves_unknown_start_and_server_end():
    # https://wutheringwaves.kurogames.com/en/main/news/detail/5741
    content = {"text": "✦Event Duration✦\nAfter the Version 3.8 update - 2026-12-21 11:59 (Server Time)\n✦Rewards✦"}
    assert tuple(value.isoformat() if value else None for value in notice_period(content, "asia")) == (
        None, "2026-12-21T03:59:00+00:00"
    )
    assert notice_period(content, "america")[1].isoformat() == "2026-12-21T16:59:00+00:00"
    content["text"] = "✦Duration✦\nVersion 3.8 update - 2026-02-30 11:59 (server time)\n✦Rules✦"
    assert notice_period(content, "asia") is None
    content["text"] = "✦Duration✦\nAfter a future update - 2026-12-21 11:59 (server time)\n✦Rules✦"
    assert notice_period(content, "asia") is None


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

    from wuwa_story.api.routes.game_events import _choose_preview, _notice_overlaps
    from wuwa_story.ingestion.notices import notice_candidates

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


def test_38_featured_banners_do_not_guess_between_repeat_client_rows():
    def event(identifier, subject, roster):
        return SimpleNamespace(id=identifier, title=f"{subject} · Resonator Convene",
            event_kind="banner", source_data={"client": {"locales": {"en": {
                "title": f"{subject} · Resonator Convene", "featured_names": [subject],
                "up_names": roster,
            }}}})

    def notice(subject):
        return {"title": "[Campaign] Featured Resonator Convene", "text":
            f"During the event, 5-Star Resonator: {subject}, 4-Star Resonators: Youhu, Aalto, and Yangyang receive boosted drop rates!"}

    lily = event(1945, "Lily", ["Sanhua", "Baizhi", "Chixia"])
    qiuyuan = [event(i, "Qiuyuan", ["Sanhua", "Baizhi", "Chixia"])
               for i in (786, 1862, 1947)]
    assert notice_matches(notice("Lily"), [lily, *qiuyuan]) == [lily]
    assert notice_matches(notice("Qiuyuan"), [lily, *qiuyuan]) == []


@pytest.mark.parametrize(
    ("kind", "names", "gacha_type"),
    [
        ("Resonators", "Camellya, Roccia, Phoebe, Brant", 13),
        ("Weapons", "Red Spring, Tragicomedy, Luminous Hymn, Unflickering Valor", 14),
    ],
)
def test_reverb_selectable_subjects_link_only_matching_client_types_for_presentation(
    kind, names, gacha_type
):
    from wuwa_story.ingestion.notices import notice_candidates

    subject = names.split(", ")[0]
    content = {
        "title": "[Reverbs II] Reverb Convene",
        "text": f"During the event, selectable 5-Star {kind}: {names}, and 4-Star {kind}: A, B, C receive boosted drop rates!",
    }

    def event(identifier, featured, client_type):
        return SimpleNamespace(
            id=identifier, title=featured, event_kind="banner",
            source_data={"client": {"gacha_type": client_type, "locales": {"en": {
                "title": featured, "featured_names": [featured], "up_names": ["X", "Y", "Z"],
            }}}},
        )

    selected = event(1, subject, gacha_type)
    ordinary = event(2, subject, 2 if gacha_type == 13 else 3)
    other = event(3, "Unlisted", gacha_type)
    assert notice_candidates(content, [selected, ordinary, other], match_roster=False) == [selected]
    assert notice_matches(content, [selected]) == []


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
async def test_notice_import_preserves_partial_schedule_provenance():
    event = SimpleNamespace(id=7, title="Artisan's Search", event_kind="limited",
                            banner_path=None, source_data={"client": {"download_id": "test"}})
    session = AsyncMock()
    session.scalars.side_effect = [[event], [], [], [], [event], [], [], []]
    session.scalar.return_value = None
    added = []
    session.add = added.append

    async def flush():
        for row in added:
            if isinstance(row, GameNotice):
                row.id = 12

    session.flush.side_effect = flush
    raw = raw_notice(content="<p>✦Duration✦</p><p>Version 3.8 Update - 2026-12-21 03:59 (server time)</p><p>✦Rules✦</p>")
    report = await store_notices(session, "https://feed.test", "https://board.test", {"game": [raw]})
    notice, *runs = added
    assert report["dated_runs"] == 3
    assert all(row.starts_at is None and row.ends_at is not None for row in runs)
    assert all(row.source_occurrence_id == "notice:10@update" for row in runs)
    assert all(row.source_data["schedule_source"] == "https://feed.test" and
               row.source_data["schedule_revision"] == notice.revision for row in runs)
    assert notice.content["raw"] == raw
    session.scalar.side_effect = [notice, *runs]
    added.clear()
    report = await store_notices(session, "https://feed.test", "https://board.test", {"game": [raw]})
    assert report["dated_runs"] == 3
    assert added == []


@pytest.mark.asyncio
async def test_version_anchor_uses_latest_previous_banner_and_rejects_ambiguous_boundaries():
    from sqlalchemy.dialects import postgresql

    content = {"text": "✦Duration✦\nAfter the Version 3.8 update - 2026-12-21 11:59 (Server Time)\n✦Rules✦"}
    published = datetime(2026, 11, 10, tzinfo=UTC)
    latest = SimpleNamespace(id=2, ends_at=datetime(2026, 11, 11, 10, 59, 59, 999000, tzinfo=UTC), game_version="3.7")
    earlier = SimpleNamespace(id=1, ends_at=latest.ends_at.replace(second=0, microsecond=0), game_version="3.7")
    session = AsyncMock()
    session.scalars.return_value = [latest, earlier]
    assert await version_start_estimate(session, content, "europe", published) is latest
    query = session.scalars.await_args.args[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    sql = str(query)
    assert "3.7" in sql and "3.8" not in sql and "europe" in sql
    assert "14" in str(query) or "2026-10-27" in sql
    assert "gacha_type" in sql and "IN (2, 3)" in sql
    earlier.ends_at = latest.ends_at - timedelta(hours=2)
    assert await version_start_estimate(session, content, "europe", published) is None
    assert await version_start_estimate(session, content, "europe", None) is None
    assert await version_start_estimate(session, {"text": content["text"].replace("3.8", "4.0")}, "europe", published) is None


@pytest.mark.asyncio
async def test_version_estimate_is_shared_across_categories_and_preserves_notice_provenance():
    banner = SimpleNamespace(id=7, title="Lily", event_kind="banner", banner_path=None, source_data={"client": {}})
    school = SimpleNamespace(id=8, title="School", event_kind="limited", banner_path=None, source_data={"client": {}})
    anchor = SimpleNamespace(id=99, ends_at=datetime(2026, 11, 11, 10, 59, 59, 999000, tzinfo=UTC), game_version="3.7")
    session = AsyncMock()
    session.scalars.side_effect = [[banner, school], [anchor], [anchor], [anchor]]
    session.scalar.return_value = None
    added = []
    session.add = added.append

    async def flush():
        for row in added:
            if isinstance(row, GameNotice):
                row.id = 12

    session.flush.side_effect = flush
    duration = "<p>✦Duration✦</p><p>Version 3.8 Update - 2026-12-21 03:59 (server time)</p><p>✦Rules✦</p>"
    raw = [raw_notice(id=str(i), tabTitle=f"[{title}] Event", content=duration,
                      startTimeMs=int(datetime(2026, 11, 10, tzinfo=UTC).timestamp() * 1000))
           for i, title in [(10, "Lily"), (11, "School")]]
    await store_notices(session, "feed", "board", {"game": raw})
    runs = [row for row in added if not isinstance(row, GameNotice)]
    assert len(runs) == 6
    for row in runs:
        assert row.starts_at == anchor.ends_at
        assert row.source_data["start_estimate"] == {"basis": "previous_version_banner_end", "occurrence_id": 99, "version": "3.7", "server": row.server}
        assert row.source_data["schedule_source"] == "feed"
        assert row.source_data["schedule_revision"]
    assert session.scalars.await_count == 4


@pytest.mark.asyncio
async def test_targeted_announcements_are_not_republished():
    session = AsyncMock()
    session.scalars.return_value = []
    result = await store_notices(
        session, "feed", "board", {"game": [raw_notice(whiteList=["12345"])]}
    )
    assert result["notices"] == 0
    session.add.assert_not_called()
