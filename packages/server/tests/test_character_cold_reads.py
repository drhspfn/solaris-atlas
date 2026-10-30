"""Cold character reads batch related tables and preserve source-backed output."""

from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from wuwa_story.api.routes.story import character_archive as archive
from wuwa_story.api.routes.story import shared


def sql(statement):
    return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


@pytest.mark.asyncio
async def test_archive_batches_dependent_tables_and_keeps_configured_group_ids(monkeypatch):
    role = Row(row_index=4, data={
        "Id": 7, "ElementId": 2, "QualityId": 5, "SkillId": 70,
        "SkillTreeGroupId": 71, "ResonantChainGroupId": 72, "Icon": "/Game/Icon",
    })
    skill = Row(row_index=9, data={"Id": 100, "SkillName": "skill.name"})
    chain = Row(row_index=3, data={"Id": 200, "NodeName": "chain.name"})
    session = Row(
        get=AsyncMock(return_value=object()),
        scalars=AsyncMock(return_value=[role]),
        execute=AsyncMock(side_effect=[
            [(chain, "BinData/resonate_chain/resonantchain.json"),
             (skill, "BinData/skill/skill.json")], [], [],
        ]),
    )
    monkeypatch.setattr(archive, "get_node", AsyncMock(return_value=Row(id=1, canonical_key="character:7")))
    monkeypatch.setattr(archive, "_release_id", AsyncMock(return_value=12))
    monkeypatch.setattr(archive, "_localized_strings", AsyncMock(return_value={
        "skill.name": "Skill", "chain.name": "Chain",
    }))
    monkeypatch.setattr(archive, "get_settings", lambda: Row(s3_bucket="test"))
    monkeypatch.setattr(archive, "S3Storage", lambda _: Row())

    result = await archive.character_archive.__wrapped__("1-character-slug", session=session)

    assert result["canonical_key"] == "character:7"
    assert result["source"] == {"file": "BinData/role/roleinfo.json", "row": 4, "raw_path": "$[4]"}
    assert result["skills"][0]["name"] == "Skill"
    assert result["skills"][0]["source"]["row"] == 9
    assert result["resonance_chain"][0]["name"] == "Chain"
    assert result["skill_tree"] == result["voice_lines"] == []
    # One role lookup, one query for all eight dependent source tables.
    assert session.scalars.await_count == 1
    assert session.execute.await_count == 3  # groups, cost items, media
    role_sql = sql(session.scalars.await_args.args[0])
    grouped_sql = sql(session.execute.await_args_list[0].args[0])
    assert "source_file.release_id = 12" in role_sql
    assert "source_file.release_id = 12" in grouped_sql
    assert "source_record.release_id = 12" in grouped_sql
    for field, value in [("SkillGroupId", 70), ("NodeGroup", 71), ("GroupId", 72), ("RoleId", 7)]:
        assert f"CAST((raw.source_record.data ->> '{field}') AS INTEGER) = {value}" in grouped_sql


@pytest.mark.asyncio
async def test_archive_grouping_preserves_row_order_and_missing_tables():
    first, second = Row(row_index=1), Row(row_index=5)
    session = Row(execute=AsyncMock(return_value=[(first, "skills"), (second, "skills")]))
    result = await archive._record_groups(session, 12, {"skills": ("GroupId", 7), "voices": ("RoleId", 7)})
    assert result == {"skills": [first, second], "voices": []}
    assert "ORDER BY raw.source_record.row_index" in sql(session.execute.await_args.args[0])
    assert await archive._record_groups(session, 12, {}) == {}
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_archive_localization_joins_text_and_preserves_numeric_release_fallback():
    session = Row(
        scalar=AsyncMock(return_value=2),
        get=AsyncMock(return_value=Row(game_version="3.10")),
        execute=AsyncMock(side_effect=[
            [(1, "3.9", 99), (2, "3.11", 100), (3, "3.10", 2), (4, "3.10", 3)],
            [("name", "reimported"), ("fallback", "previous")],
        ]),
    )
    result = await archive._localized_strings(session, 12, "ja", ["name", "fallback", "name", None])
    assert result == {"name": "reimported", "fallback": "previous"}
    query = sql(session.execute.await_args.args[0])
    assert "JOIN i18n.localization_content" in query
    assert "row_number() OVER" in query and "anon_1.rank = 1" in query
    assert "localization_value.release_id IN (1, 3, 4)" in query
    assert "localization_value.status = 'resolved_nonempty'" in query
    assert await archive._localized_strings(session, 12, "ja", []) == {}
    assert session.execute.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat", [1, 25])
async def test_progression_resolves_unique_items_once_and_preserves_all_uses(monkeypatch, repeat):
    role = Row(row_index=0, data={"BreachId": 9, "SkillBranchIds": [10]})
    project = Row(data={"RoleItemGroup": [10]})
    group = Row(row_index=2, data={"ItemGroupId": 10, "ItemGroup": [
        *[{"Item1": 1, "Item2": 5}] * repeat,
        {"Item1": 999, "Item2": 3}, {"Item1": "invalid", "Item2": 1},
    ]})
    breach = Row(row_index=3, data={"MaxLevel": 40, "BreachLevel": 1, "BreachConsume": [
        {"Key": 1, "Value": 2}, {"Key": 2, "Value": 4},
    ]})
    role_file, item_file, breach_file = [Row(logical_source_path=path) for path in ("role", "items", "breach")]
    items = [Row(id=11, canonical_key="item:1"), Row(id=12, canonical_key="item:2")]
    session = Row(
        execute=AsyncMock(side_effect=[[(role, role_file)], [(group, item_file)], [(breach, breach_file)]]),
        scalars=AsyncMock(side_effect=[[project], items]),
    )
    labels = AsyncMock()
    monkeypatch.setattr(shared, "prefetch_node_labels", labels)
    monkeypatch.setattr(shared, "_node_label", AsyncMock(side_effect=lambda _s, identity, _l: {"id": identity}))

    result = await shared._character_progression_materials(session, 7, "ja", 12)

    assert len(result) == 2 + (repeat + 1) * 2
    assert [(use["item_id"], use["required_count"]) for use in result[:2]] == [(1, 2), (2, 4)]
    assert result[0]["level_cap"] == 40
    assert result[0]["source"]["raw_path"] == "$[3].BreachConsume[0]"
    assert result[2]["source"]["raw_path"] == "$[2].ItemGroup[0]"
    assert {use["kind"] for use in result[2:]} == {"resonator_level_material", "skill_tree_material"}
    assert all(use["item"] is None for use in result if use["item_id"] == 999)
    assert all(use["item"] == {"id": 11} for use in result if use["item_id"] == 1)
    labels.assert_awaited_once_with(session, [11, 12], "ja")
    assert session.execute.await_count == 3
    assert session.scalars.await_count == 2  # development project and unique item nodes
    item_sql = sql(session.scalars.await_args.args[0])
    assert all(f"'item:{identity}'" in item_sql for identity in (1, 2, 999))
    assert "invalid" not in item_sql
