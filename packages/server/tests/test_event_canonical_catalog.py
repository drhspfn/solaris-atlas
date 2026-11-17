from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.events.catalog import catalog_rows, is_catalog_capture
from wuwa_story.events.selectable_pools import pool_match
from wuwa_story.read_models.readers import entities, events


def test_source_aliases_form_one_event_with_all_real_periods():
    rows = [
        {"id": 7, "canonical_event_id": 7, "occurrence_id": 1, "starts_at": "1900-01-01"},
        {"id": 8, "canonical_event_id": 7, "occurrence_id": 2, "starts_at": None},
        {"id": 8, "canonical_event_id": 7, "occurrence_id": 3, "starts_at": None},
        {"id": 7, "canonical_event_id": 7, "occurrence_id": 4, "_catalog_placeholder": True},
    ]
    result = catalog_rows(rows)
    assert {row["id"] for row in result} == {7}
    assert [row["occurrence_id"] for row in result] == [1, 2, 3, None]
    assert [row["source_event_id"] for row in result] == [7, 8, 8, 7]
    assert rows[-1]["_catalog_placeholder"] is True


def test_catalog_without_periods_has_one_presentation_and_no_invented_period():
    rows = [{"id": 7, "occurrence_id": i, "_catalog_placeholder": True,
             "catalog_version": version} for i, version in [(1, "3.8.0"), (2, "3.9.0")]]
    result = catalog_rows(rows)
    assert len(result) == 1
    assert result[0]["catalog_version"] == "3.9.0"
    assert result[0]["occurrence_id"] is None


def test_selectable_catalog_header_describes_all_proven_options_not_one_participant():
    rows = [{"id": identity, "canonical_event_id": 7, "occurrence_id": identity + 10,
             "_catalog_placeholder": True, "catalog_version": "3.8.0", "source_id": f"gacha:{identity}",
             "selectable_pool_id": 100, "selectable_pool_members": [7, 8],
             "convene_type": "Resonator Convene", "featured_names": [name], "title": name}
            for identity, name in [(7, "First"), (8, "Second")]]
    row, = catalog_rows(rows)
    assert row["title"] == "Selectable Resonator Convene"
    assert row["featured_names"] == ["First", "Second"]


def test_equal_names_never_bind_unrelated_events():
    assert [row["id"] for row in catalog_rows([
        {"id": 1, "occurrence_id": 10, "title": "Same"},
        {"id": 2, "occurrence_id": 20, "title": "Same"},
    ])] == [1, 2]


def test_canonical_presentation_wins_over_an_alias_in_the_same_capture():
    rows = [{"id": identity, "canonical_event_id": 8, "occurrence_id": None,
             "catalog_version": "3.8.0", "title": title}
            for identity, title in [(7, "Internal activity"), (8, "Public guide")]]
    result, = catalog_rows(rows)
    assert result["id"] == result["source_event_id"] == 8
    assert result["title"] == "Public guide"


def test_manually_edited_capture_is_a_real_independent_period_even_without_dates():
    assert is_catalog_capture("client:3.8.0", None, None, {})
    assert not is_catalog_capture("client:3.8.0", None, None, {"schedule_source": "manual"})
    assert not is_catalog_capture("client:3.8.0", None, None, {"manual_schedule": True})
    assert not is_catalog_capture("manual:one", None, None, {})
    assert pool_match({"schedule_source": "manual", "notice_match": {"basis": "gacha_pool"}}) is None


@pytest.mark.asyncio
async def test_canonical_detail_accepts_a_period_owned_by_a_source_alias(monkeypatch):
    async def payload(session, kind, identity, locale, selector=None):
        return {"event": {"id": identity, "canonical_event_id": 7}, "occurrences": [] if identity == 7 else [
            {"id": 8, "source_event_id": 8, "canonical_event_id": 7,
             "occurrence_id": 99, "server": "europe", "starts_at": None, "ends_at": None},
        ]}

    monkeypatch.setattr(entities, "page_payload", payload)
    session = SimpleNamespace(scalar=AsyncMock(return_value=8))
    result = await events.event_detail(session, 7, "europe", 99)
    assert result["id"] == 7 and result["source_event_id"] == 8 and result["occurrence_id"] == 99
    assert "coalesce" in str(session.scalar.await_args.args[0])
