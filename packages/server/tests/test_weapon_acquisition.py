from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from wuwa_story.api.weapon_acquisition import (
    GACHA,
    PERMANENT_WEAPON_TEXTURE,
    POOL,
    VIEW,
    classify_weapon_banner_sources,
    weapon_banner_sources,
)

ITEM = 21020015


def source(path=VIEW, identity=32, release_id=5, record_id=100, **data):
    return (
        SimpleNamespace(
            id=record_id,
            source_file_id=20,
            release_id=release_id,
            data={"Id": identity, **data},
            row_index=3,
            source_key=None,
            content_hash=b"\xab\xcd",
        ),
        SimpleNamespace(id=20, release_id=release_id, logical_source_path=path),
        SimpleNamespace(id=release_id, game_version="2.0.0", resource_version="2.0.15"),
    )


def emerald():
    return source(
        Type=5,
        ShowIdList=[ITEM],
        PreviewIdList=[ITEM],
        UpList=[],
        TextTexture=PERMANENT_WEAPON_TEXTURE,
        SummaryTitle="Text_SummaryTitle32_Text",
    )


def test_emerald_is_permanent_preview_and_display_with_exact_links_and_provenance():
    (result,) = classify_weapon_banner_sources(
        [
            emerald(),
            source(POOL, GachaId=3, record_id=101),
            source(GACHA, identity=3, record_id=102),
            source(GACHA, identity=32, record_id=103),
        ],
        ITEM,
        5,
    )
    assert result["roles"] == ["preview", "display"]
    assert result["banner_type"] == "permanent_weapon"
    assert result["type_id"] == 5
    assert result["evidence"] == [
        {"field": "PreviewIdList", "role": "preview", "value": ITEM},
        {"field": "ShowIdList", "role": "display", "value": ITEM},
    ]
    assert result["banner_type_evidence"]["field"] == "TextTexture"
    assert result["banner_type_evidence"]["value"] == PERMANENT_WEAPON_TEXTURE
    assert result["source"] == {
        "record_id": 100,
        "file": VIEW,
        "row": 3,
        "source_key": None,
        "content_hash": "abcd",
        "release_id": 5,
        "game_version": "2.0.0",
        "resource_version": "2.0.15",
    }
    (link,) = result["pool_links"]
    assert (link["pool_id"], link["gacha_id"]) == (32, 3)
    assert link["pool_source"]["record_id"] == 101
    assert link["gacha_source"]["record_id"] == 102
    assert link["basis"] == "GachaViewInfo.Id = GachaPool.Id; GachaPool.GachaId = Gacha.Id"
    assert result["title_key"] == "Text_SummaryTitle32_Text"
    assert result["title"] is None
    assert not {"obtainable", "probability", "loot_table"}.intersection(result)


@pytest.mark.parametrize(
    "field,role",
    [
        ("UpList", "featured"),
        ("PreviewIdList", "preview"),
        ("ShowIdList", "display"),
    ],
)
def test_roles_are_independent_and_duplicate_membership_is_collapsed(field, role):
    (result,) = classify_weapon_banner_sources([source(**{field: [ITEM, ITEM]})], ITEM, 5)
    assert result["roles"] == [role]
    assert result["pool_links"] == []
    assert result["banner_type"] == "unknown"


@pytest.mark.parametrize(
    "data",
    [
        {"ShowIdList": [ITEM + 1, ITEM * 10]},
        {"ShowIdList": [str(ITEM)]},
        {"ShowIdList": [{"Id": ITEM}]},
        {"ShowIdList": str(ITEM)},
        {"ShowIdList": None},
        {"Other": [ITEM]},
        {"Nested": {"PreviewIdList": [ITEM]}},
        {"Id": ITEM},
    ],
)
def test_unrelated_fields_partial_ids_and_non_numeric_members_are_not_matches(data):
    assert classify_weapon_banner_sources([source(**data)], ITEM, 5) == []


def test_boolean_is_not_an_id_and_type_is_not_a_verified_enum():
    assert classify_weapon_banner_sources([source(ShowIdList=[True])], 1, 5) == []
    assert classify_weapon_banner_sources([source(ShowIdList=[1])], True, 5) == []
    (result,) = classify_weapon_banner_sources([source(Type=5, PreviewIdList=[ITEM])], ITEM, 5)
    assert result["banner_type"] == "unknown"
    assert result["banner_type_evidence"] is None


def test_release_file_and_source_key_cannot_supply_a_false_match():
    wrong_file_release = emerald()
    wrong_file_release[1].release_id = 4
    wrong_version = emerald()
    wrong_version[2].id = 4
    wrong_file_id = emerald()
    wrong_file_id[1].id = 99
    wrong_key = source()
    wrong_key[0].source_key = str(ITEM)
    assert (
        classify_weapon_banner_sources(
            [
                source(release_id=4, ShowIdList=[ITEM]),
                wrong_file_release,
                wrong_version,
                wrong_file_id,
                wrong_key,
                source(path="Other/gachaviewinfo.json", ShowIdList=[ITEM]),
                source(POOL, ShowIdList=[ITEM]),
            ],
            ITEM,
            5,
        )
        == []
    )


@pytest.mark.parametrize(
    "pool,gacha",
    [
        (source(POOL, identity=320, GachaId=3), source(GACHA, identity=3)),
        (source(POOL, GachaId=3), source(GACHA, identity=32)),
        (source(POOL, GachaId="3"), source(GACHA, identity=3)),
        (source(POOL, GachaId=3), source(GACHA, identity="3")),
        (source(POOL, GachaId=3), source(GACHA, identity=3, release_id=4)),
        (source(POOL, GachaId=3, release_id=4), source(GACHA, identity=3)),
        (source(POOL), source(GACHA, identity=3)),
    ],
)
def test_only_complete_exact_same_release_links_are_reported(pool, gacha):
    (result,) = classify_weapon_banner_sources([emerald(), pool, gacha], ITEM, 5)
    assert result["pool_links"] == []


def test_distinct_view_records_are_preserved_and_sorted():
    rows = [emerald(), source(identity=31, record_id=99, UpList=[ITEM])]
    results = classify_weapon_banner_sources(rows, ITEM, 5)
    assert [result["view_id"] for result in results] == [31, 32]
    assert classify_weapon_banner_sources(reversed(rows), ITEM, 5) == results


@pytest.mark.asyncio
async def test_helper_batches_exact_release_queries_and_requested_locale_title():
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                [emerald()],
                [source(POOL, GachaId=3)],
                [source(GACHA, identity=3)],
                [("Text_SummaryTitle32_Text", "Emerald banner", 777)],
            ]
        )
    )
    (result,) = await weapon_banner_sources(session, ITEM, 5, "en")
    assert result["title"] == {
        "content": "Emerald banner",
        "locale": "en",
        "release_id": 5,
        "source_record_id": 777,
    }
    calls = session.execute.call_args_list
    assert len(calls) == 4
    compiled = [call.args[0].compile(dialect=postgresql.dialect()) for call in calls]
    for statement, path in zip(compiled[:3], (VIEW, POOL, GACHA), strict=True):
        assert path in statement.params.values()
        assert "source_record.release_id =" in str(statement)
        assert "source_file.release_id =" in str(statement)
        assert 5 in statement.params.values()
    for field in ("ShowIdList", "PreviewIdList", "UpList"):
        assert {field: [ITEM]} in compiled[0].params.values()
    assert {"Id": 32} in compiled[1].params.values()
    assert {"Id": 3} in compiled[2].params.values()
    assert "en" in compiled[3].params.values()
    assert "localization_value.release_id =" in str(compiled[3])


@pytest.mark.asyncio
async def test_no_matches_stop_after_first_select():
    session = SimpleNamespace(execute=AsyncMock(return_value=[]))
    assert await weapon_banner_sources(session, ITEM, 5, "en") == []
    assert session.execute.await_count == 1


@pytest.mark.asyncio
async def test_missing_links_and_translation_preserve_view_evidence():
    session = SimpleNamespace(execute=AsyncMock(side_effect=[[emerald()], [], []]))
    (result,) = await weapon_banner_sources(session, ITEM, 5, "uk")
    assert result["title"] is None
    assert result["roles"] == ["preview", "display"]
    assert result["pool_links"] == []
    assert session.execute.await_count == 3
