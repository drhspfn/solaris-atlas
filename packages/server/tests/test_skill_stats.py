"""Skill stats retain source values, group identity, snapshot, and level positions."""

import json
from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from wuwa_story.api.routes.story import character_archive as archive
from wuwa_story.ingestion.skill_stats import (
    skill_description_parameters,
    skill_stat_parameters,
    skill_stat_values,
)


@pytest.mark.parametrize(("detail", "expected"), [
    (["1", "32%", "15"], ["1", "32%", "15"]),
    (["5"], ["5"]),
    (["100%", "50%", "14"], ["100%", "50%", "14"]),
    (["0", "", "16.88%*4"], ["0", "", "16.88%*4"]),
    ([], []),
    (None, []),
    ("32%", []),
    (["1", None, "15"], []),
    ([1, "32%", "15"], []),
    ([{"ArrayString": ["1", "2"]}], []),
])
def test_description_parameters_validate_without_coercion_or_reindexing(detail, expected):
    assert skill_description_parameters(detail) == expected


def test_description_parameters_do_not_alias_source():
    source = ["100%", "50%", "14"]
    result = skill_description_parameters(source)
    result[0] = "changed"
    assert source == ["100%", "50%", "14"]


@pytest.mark.parametrize(("detail", "description", "parameters", "values"), [
    ([{"ArrayString": ["16.88%*4", "18.26%*4", "31.20%*4"]}], None,
     [["16.88%*4", "18.26%*4", "31.20%*4"]], ["16.88%*4", "18.26%*4", "31.20%*4"]),
    ([{"ArrayString": ["9"]}], "{0}s", [["9"]], ["9s"]),
    ([{"ArrayString": ["39.59%", "42.84%"]}], "{0} DEF",
     [["39.59%", "42.84%"]], ["39.59% DEF", "42.84% DEF"]),
    ([{"ArrayString": ["10%", None, "20%", ""]}], "{0}s",
     [["10%", "", "20%", ""]], ["10%s", "", "20%s", ""]),
    # Unverified multi-parameter formatting is exposed losslessly as parameters.
    ([{"ArrayString": ["1", "2"]}, {"ArrayString": ["3"]}], "{0}+{1}",
     [["1", "2"], ["3"]], []),
    ([{"ArrayString": ["1", "2", "3"]}, {"ArrayString": ["4", "5"]}], None,
     [["1", "2", "3"], ["4", "5"]], []),
    ([{}, {"ArrayString": ["2"]}], None, [[], ["2"]], []),
    ([{"ArrayString": ["1"]}], "{1}s", [["1"]], []),
    ([{"ArrayString": ["1"]}], "{0:.2f}", [["1"]], []),
    ([{"ArrayString": ["1"]}], "unverified description", [["1"]], []),
    ([{"ArrayString": []}], None, [[]], []),
    ([{"ArrayString": "12"}, None], None, [[], []], []),
    (None, None, [], []),
])
def test_skill_values_preserve_source_and_missing_levels(detail, description, parameters, values):
    assert skill_stat_parameters(detail) == parameters
    assert skill_stat_values(parameters, description) == values


def test_all_authored_levels_are_preserved_without_mutating_source():
    authored = [f"{level}.00%*4" for level in range(1, 21)]
    parameters = skill_stat_parameters([{"ArrayString": authored}])
    values = skill_stat_values(parameters, None)
    assert values == authored
    # Independent of a skill's playable MaxSkillLevel=10.
    assert len(values) == 20
    values[0] = "changed"
    assert parameters[0][0] == authored[0] == "1.00%*4"


@pytest.mark.asyncio
async def test_skill_description_batch_is_snapshot_scoped_and_ordered():
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS raw")
            connection.exec_driver_sql(
                "CREATE TABLE raw.source_file (id INTEGER PRIMARY KEY, release_id INTEGER, logical_source_path TEXT)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE raw.source_record (id INTEGER PRIMARY KEY, release_id INTEGER, "
                "source_file_id INTEGER, row_index INTEGER, source_key TEXT, content_hash BLOB, "
                "data JSON, created_at DATETIME)"
            )
            path = "BinData/skill/skilldescription.json"
            connection.exec_driver_sql(
                "INSERT INTO raw.source_file VALUES (1,12,?),(2,13,?),(3,12,'BinData/skill/skill.json')",
                (path, path),
            )
            fixtures = [
                # Id deliberately differs from the configured level group.
                (1, 12, 1, 1, {"Id": 100,
                 "SkillLevelGroupId": 700, "Order": 2}),
                (2, 12, 1, 9, {"Id": 101,
                 "SkillLevelGroupId": 700, "Order": 1}),
                (3, 12, 1, 4, {"Id": 102,
                 "SkillLevelGroupId": 700, "Order": 2}),
                (4, 12, 1, 0, {"Id": 103,
                 "SkillLevelGroupId": 701, "Order": 1}),
                (5, 13, 2, 0, {"Id": 104,
                 "SkillLevelGroupId": 700, "Order": 0}),
                # Reject mismatched record/file snapshots independently.
                (6, 12, 2, 0, {"Id": 105,
                 "SkillLevelGroupId": 700, "Order": 0}),
                (7, 13, 1, 0, {"Id": 106,
                 "SkillLevelGroupId": 700, "Order": 0}),
                (8, 12, 3, 0, {"Id": 107,
                 "SkillLevelGroupId": 700, "Order": 0}),
                (9, 12, 1, 0, {"Id": 700,
                 "SkillLevelGroupId": 100, "Order": 0}),
            ]
            for identity, release, file, index, data in fixtures:
                connection.exec_driver_sql(
                    "INSERT INTO raw.source_record (id,release_id,source_file_id,row_index,data) VALUES (?,?,?,?,?)",
                    (identity, release, file, index, json.dumps(data)),
                )
            with Session(bind=connection) as orm:
                session = Row(scalars=AsyncMock(
                    side_effect=lambda query: orm.scalars(query).all()))
                result = await archive._skill_description_groups(session, 12, path, {700, 701, 702})
                assert {key: [row.data["Id"] for row in rows] for key, rows in result.items()} == {
                    700: [101, 100, 102], 701: [103], 702: [],
                }
                session.scalars.assert_awaited_once()
                assert await archive._skill_description_groups(session, 12, path, set()) == {}
                session.scalars.assert_awaited_once()
    finally:
        engine.dispose()
