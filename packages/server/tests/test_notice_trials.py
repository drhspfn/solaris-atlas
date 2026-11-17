from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.ingestion import notices
from wuwa_story.ingestion.notice_trials import (
    SKIN,
    SKIN_ACTIVITY,
    SKIN_INFO,
    TRIAL_ACTIVITY,
    TRIAL_INFO,
    embedded_notice_activities,
    resolve_trials,
)


def event(identifier, kind="limited", client=None):
    return SimpleNamespace(id=identifier, source_id=f"activity:{identifier}", title="Trial",
                           event_kind=kind, banner_path=None, source_data={"client": client or {}})


def test_exact_client_joins_distinguish_trial_and_outfit_from_older_rows():
    banner = event(1, "banner", {"gacha_type": 2, "locales": {"en": {"featured_names": ["Lily"]}}})
    banner.source_id = "gacha:100090"
    trial, outfit, old = event(100900046), event(103500006), event(103500004)
    rows = [(TRIAL_INFO, {"Id": 1185, "GachaId": 100090}),
            (TRIAL_ACTIVITY, {"Id": 100900046, "RoleIdList": [1185]}),
            (SKIN_ACTIVITY, {"Id": 103500006, "RoleIdList": [81011108]}),
            (SKIN_ACTIVITY, {"Id": 103500004, "RoleIdList": [7]}),
            (SKIN_INFO, {"Id": 81011108, "PreviewSkinId": 81011108, "RoleId": 1108}),
            (SKIN, {"Id": 81011108, "RoleId": 1108, "Name": "role", "TitleName": "skin"})]
    trial_notice = {"text": "5-Star Resonator: Lily, during event will become available in Ascendant Aces as Trial Resonators"}
    assert resolve_trials(trial_notice, [banner, trial, outfit, old], rows, {}, 22)[0][0] is trial
    outfit_notice = {"text": "Hiyuki - Lune Noire\n✦[Glamour Couture] Outfit Tryout✦\nDuration: Version 3.8 Update - 2026-12-21 11:59 (server time)"}
    result = resolve_trials(outfit_notice, [banner, trial, outfit, old], rows, {"role": "Hiyuki", "skin": "Lune Noire"}, 22)
    assert result[0][0] is outfit
    assert notices.notice_period(result[0][1], "europe")[1].isoformat() == "2026-12-21T10:59:00+00:00"
    assert not resolve_trials(outfit_notice, [old], rows, {"role": "Hiyuki", "skin": "Lune Noire"}, 22)
    rows.append((TRIAL_ACTIVITY, {"Id": 103500004, "RoleIdList": [1185]}))
    assert not resolve_trials(trial_notice, [banner, trial, old], rows, {}, 22)


@pytest.mark.asyncio
async def test_trial_import_uses_section_duration_and_preserves_unknown_start(monkeypatch):
    from wuwa_story.db.models.notices import GameNotice

    outfit = event(103500006)
    content = {"text": "Duration: Version 3.8 Update - 2026-12-21 11:59 (server time)"}
    monkeypatch.setattr(notices, "notice_trial_matches", AsyncMock(return_value=[(outfit, content, {"basis": "outfit_trial", "release_id": 22, "skin_id": 81011108})]))
    session = AsyncMock()
    session.scalars.return_value = [outfit]
    session.scalar.return_value = None
    added = []
    session.add = added.append
    async def flush():
        for row in added:
            if isinstance(row, GameNotice):
                row.id = 47
    session.flush.side_effect = flush
    raw = {"id": "50633", "tabTitle": "[New Outfit] Special Offer", "whiteList": [],
           "content": "<p>✦Special Offer Period✦</p><p>Version 3.8 Update - 2026-12-22 03:59 (server time)</p>"}
    report = await notices.store_notices(session, "feed", "page", {"game": [raw]})
    runs = [row for row in added if not isinstance(row, GameNotice)]
    assert report["dated_runs"] == 3
    assert all(row.starts_at is None and row.game_version == "3.8" for row in runs)
    assert all(row.source_data["schedule_match"]["skin_id"] == 81011108 for row in runs)
    assert next(row for row in runs if row.server == "europe").ends_at.isoformat() == "2026-12-21T10:59:00+00:00"
    notice = next(row for row in added if isinstance(row, GameNotice))
    session.scalar.side_effect = [notice, *runs]
    added.clear()
    again = await notices.store_notices(session, "feed", "page", {"game": [raw]})
    assert again["dated_runs"] == 3
    assert added == []


def test_repeated_featured_banners_never_guess_a_trial():
    first = event(1, "banner", {"gacha_type": 2, "locales": {"en": {"featured_names": ["Qiuyuan"]}}})
    second = event(2, "banner", {"gacha_type": 2, "locales": {"en": {"featured_names": ["Qiuyuan"]}}})
    first.source_id, second.source_id = "gacha:100058", "gacha:100092"
    trial = event(100900046)
    rows = [(TRIAL_INFO, {"Id": 1187, "GachaId": 100092}),
            (TRIAL_ACTIVITY, {"Id": 100900046, "RoleIdList": [1187]})]
    content = {"text": "5-Star Resonator: Qiuyuan, will become available in Ascendant Aces as Trial Resonators"}
    assert resolve_trials(content, [first, second, trial], rows, {}, 22) == []


def test_patch_sections_keep_each_exact_duration_and_ignore_ambiguous_names():
    first = event(1)
    first.title = "Dreamfield Odyssey"
    second = event(2)
    second.title = "Whither the Wind"
    content = {"title": "Patch Notes for Version 3.8: Lilies in a Dying Dream", "text":
        "[Dreamfield Odyssey] Combat Event\nA description\n✦Duration: 2026-12-03 10:00 - 2026-12-21 03:59 (server time)\n"
        "[Whither the Wind] Limited-Time Login Event\nMore text\n✦Duration: 2026-12-07 04:00 - 2026-12-21 03:59 (server time)\n"
        "✦New Merch✦\nOther information"}
    matches = embedded_notice_activities(content, [first, second])
    assert [match[0] for match in matches] == [first, second]
    assert notices.notice_period(matches[0][1], "europe")[0].isoformat() == "2026-12-03T09:00:00+00:00"
    assert notices.notice_period(matches[1][1], "europe")[0].isoformat() == "2026-12-07T03:00:00+00:00"
    duplicate = event(3)
    duplicate.title = first.title
    assert [match[0] for match in embedded_notice_activities(content, [first, second, duplicate])] == [second]
