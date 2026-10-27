"""Item progression must count authored stages, not snapshot join products."""

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from wuwa_story.api.routes.story import profiles

BREACH = "BinData/role_level/rolebreach.json"
ROLE = "BinData/role/roleinfo.json"
MATERIAL = "BinData/RoleDev/roledevprosroleitem.json"
PROJECT = "BinData/RoleDev/roledevprosproject.json"
ITEM = 42300080


def source(path, release, row, **data):
    return (
        SimpleNamespace(id=release * 10000 + row, release_id=release, row_index=row, data=data),
        SimpleNamespace(logical_source_path=path, release_id=release),
    )


def contains(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            contains(actual.get(k), v) for k, v in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and all(
            any(contains(a, e) for a in actual) for e in expected
        )
    return actual == expected


class SourceSession:
    """Small raw-table double that applies the endpoint's compiled SQL predicates."""

    def __init__(self, rows):
        self.rows = rows
        self.get = AsyncMock(
            return_value=SimpleNamespace(
                game_item_id=ITEM,
                canonical_name="Coriolus",
                name_key_id=None,
                description_key_id=None,
            )
        )
        self.scalar = AsyncMock(return_value=None)
        self.scalars = AsyncMock(return_value=[])

    async def execute(self, query):
        compiled = query.compile(dialect=postgresql.dialect())
        sql, params = str(compiled), compiled.params
        path = params.get("logical_source_path_1")
        if path not in {BREACH, ROLE, MATERIAL, PROJECT}:
            return []
        rows = [row for row in self.rows if row[1].logical_source_path == path]
        for index, table in enumerate(("source_record", "source_file")):
            if f"{table}.release_id IS NULL" in sql:
                return []
            match = re.search(rf"{table}\.release_id = %\((\w+)\)s", sql)
            if match:
                rows = [row for row in rows if row[index].release_id == params[match[1]]]
        values = list(params.values())
        checks = []
        for i, value in enumerate(values[:-1]):
            if isinstance(value, str) and value in {
                "BreachConsume",
                "BreachGroupId",
                "MaxLevel",
                "BreachId",
                "ItemGroup",
                "ItemGroupId",
                "SkillBranchIds",
                "RoleItemGroup",
                "SkillItemGroup",
                "WeaponBreachItemGroup",
                "Id",
            }:
                expected = values[i + 1]

                def matches(row, field=value, expected=expected):
                    actual = row[0].data.get(field)
                    if field == "MaxLevel":
                        return actual is not None and actual > expected
                    return contains(actual, expected)

                checks.append(matches)
        if " OR " in sql:
            rows = [row for row in rows if any(check(row) for check in checks)]
        else:
            rows = [row for row in rows if all(check(row) for check in checks)]
        rows.sort(
            key=lambda row: (
                row[0].data.get("MaxLevel", 0) if "ORDER BY CAST" in sql else 0,
                row[0].row_index,
                row[0].id,
            )
        )
        limit = re.search(r"LIMIT %\((\w+)\)s", sql)
        return rows[: params[limit[1]]] if limit else rows


async def profile(monkeypatch, rows, release):
    for name, result in {
        "get_node": SimpleNamespace(id=19875, canonical_key=f"item:{ITEM}", status="active"),
        "_release_id": release,
        "_latest_source_record": None,
        "_explicit_entity_links": [],
        "_quest_references_from_links": [],
        "entity_image_urls": {},
        "item_map_sources": [],
        "_localized": None,
    }.items():
        monkeypatch.setattr(profiles, name, AsyncMock(return_value=result))
    monkeypatch.setattr(profiles, "node_address", lambda *args: {})
    return (
        await profiles.item_profile.__wrapped__(
            f"item:{ITEM}",
            "en",
            str(release),
            SourceSession(rows),
        )
    )["progression_uses"]


@pytest.mark.parametrize("release", [1, 13, None])
async def test_coriolus_counts_one_snapshot_and_selects_nearest_next_stage(monkeypatch, release):
    rows = []
    for snapshot in range(1, 14):
        for role in (1104, 1204, 1302):
            rows.append(source(ROLE, snapshot, role, Id=role, BreachId=role))
            for stage, (cap, count) in enumerate(
                zip((50, 60, 70, 80, 90), (4, 8, 12, 16, 20), strict=True), 2
            ):
                rows.append(
                    source(
                        BREACH,
                        snapshot,
                        role * 10 + stage,
                        BreachGroupId=role,
                        BreachLevel=stage,
                        MaxLevel=cap,
                        BreachConsume=[{"Key": ITEM, "Value": count}],
                    )
                )
    uses = await profile(monkeypatch, list(reversed(rows)), release)
    if release is None:
        assert uses == []
        return
    assert len(uses) == 15
    for role in (1104, 1204, 1302):
        stages = [row for row in uses if row["role_id"] == role]
        assert len(stages) == 5
        assert sum(row["required_count"] for row in stages) == 60
        assert [
            row["next_breach"]["level_cap"] if row["next_breach"] else None for row in stages
        ] == [60, 70, 80, 90, None]
        assert {row["source"]["release_id"] for row in stages} == {release}
        assert all(
            row["source"]["record_id"]
            and row["source"]["role_source_record_id"]
            and ".BreachConsume[0]" in row["source"]["raw_path"]
            for row in stages
        )


async def test_shared_breach_roles_and_unresolved_groups_keep_source_evidence(monkeypatch):
    rows = [source(ROLE, 13, role, Id=role, BreachId=1) for role in (10, 11)]
    rows += [
        source(
            BREACH,
            13,
            group,
            BreachGroupId=group,
            BreachLevel=2,
            MaxLevel=50,
            BreachConsume=[{"Key": ITEM, "Value": 4}],
        )
        for group in (1, 2)
    ]
    uses = await profile(monkeypatch, rows, 13)
    assert [row["role_id"] for row in uses] == [10, 11, None]
    assert uses[-1]["source"]["record_id"] == 130002


async def test_skill_variants_and_project_joins_use_only_requested_snapshot(monkeypatch):
    rows = []
    for release in (1, 13):
        rows.extend(
            [
                source(ROLE, release, 0, Id=10, SkillBranchIds=[101, 102]),
                source(ROLE, release, 1, Id=11),
                source(PROJECT, release, 2, Id=11, SkillItemGroup=[103]),
            ]
        )
        for i, group in enumerate((101, 101, 102, 103)):
            rows.append(
                source(
                    MATERIAL,
                    release,
                    i + 3,
                    ItemGroupId=group,
                    ItemTypeId=i + 3,
                    ItemGroup=[{"Item1": ITEM, "Item2": 4}],
                )
            )
    uses = await profile(monkeypatch, rows, 13)
    assert [row["item_group_id"] for row in uses] == [101, 101, 102, 103]
    assert [row["item_type_id"] for row in uses] == [3, 4, 5, 6]
    assert [row["kind"] for row in uses] == [
        "skill_tree_material",
        "skill_tree_material",
        "skill_tree_material",
        "resonator_skill_material",
    ]
    assert {row["source"]["release_id"] for row in uses} == {13}
    assert uses[-1]["source"]["project_source_record_id"] == 130002


async def test_duplicate_nearest_caps_remain_ambiguous(monkeypatch):
    rows = [source(ROLE, 13, 0, Id=10, BreachId=10)]
    rows += [
        source(
            BREACH,
            13,
            i + 1,
            BreachGroupId=10,
            BreachLevel=i + 2,
            MaxLevel=cap,
            BreachConsume=[{"Key": ITEM, "Value": 4}],
        )
        for i, cap in enumerate((50, 60, 60))
    ]
    uses = await profile(monkeypatch, rows, 13)
    assert uses[0]["next_breach"] is None
