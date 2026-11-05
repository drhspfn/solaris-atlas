import json
from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from wuwa_story.db.repositories import story_browsing as repository
from wuwa_story.story import service
from wuwa_story.story.domain import StoryBrowsingError


@pytest.mark.asyncio
@pytest.mark.parametrize("release_id", [None, 15])
async def test_root_source_query_can_be_pinned_without_changing_latest_default(release_id):
    session = Row(execute=AsyncMock(return_value=Row(first=lambda: None)))
    await repository.latest_source_row(session, 27486, release_id=release_id)
    query = session.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    sql = str(query)
    assert query.params["node_id_1"] == 27486
    if release_id is None:
        assert "node_revision.release_id =" not in sql
    else:
        assert "node_revision.release_id =" in sql
        assert 15 in query.params.values()


@pytest.mark.asyncio
async def test_historical_profile_returns_actual_requested_source_not_latest(monkeypatch):
    session, quest = Row(info={}), Row(node_id=27486, game_quest_id=11000012)
    monkeypatch.setattr(service, "_release_id", AsyncMock(return_value=15))
    for name in ("quest_node_rows", "quest_hierarchy_rows", "quest_states", "quest_actions",
                 "quest_scenes", "quest_requirement_records"):
        monkeypatch.setattr(repository, name, AsyncMock(return_value=[]))
    # A hierarchy edge assigns the existing tree-source walrus; it must not
    # overwrite the independently selected root source in the serialized profile.
    monkeypatch.setattr(repository, "quest_node_rows", AsyncMock(return_value=[(
        Row(node_id=1, game_node_id="1", node_type="ChildQuest", metadata_json={"data": {}}),
        Row(id=1, canonical_key="quest_node:1"), Row(row_index=1),
        Row(logical_source_path="questnodes.json"),
    )]))
    monkeypatch.setattr(repository, "quest_hierarchy_rows", AsyncMock(return_value=[(
        Row(from_node_id=1, basis="explicit", metadata_json={}), "parent_of",
        Row(canonical_key="quest_node:2"), None,
    )]))
    monkeypatch.setattr(service, "_quest_page_state_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_explicit_entity_links", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_quest_info", AsyncMock(return_value={"game_quest_id": 11000012}))

    async def source_row(_session, node_id, *, release_id=None):
        assert node_id == 27486
        version = "3.1.0" if release_id == 15 else "3.7.0"
        return (Row(metadata_json={}), Row(row_index=4, data={"authored": version}),
                Row(logical_source_path="quest.json"), Row(game_version=version))

    source = AsyncMock(side_effect=source_row)
    monkeypatch.setattr(repository, "latest_source_row", source)
    historical = await service.quest_profile_payload(session, quest, game_version="3.1.0")
    assert historical["source"]["version"] == "3.1.0"
    assert historical["source"]["raw_record"] == {"authored": "3.1.0"}
    assert json.loads(json.dumps(historical))["source"]["version"] == "3.1.0"
    source.assert_awaited_once_with(session, 27486, release_id=15)
    current = await service.quest_profile_payload(session, quest)
    assert current["source"]["version"] == "3.7.0"


@pytest.mark.asyncio
@pytest.mark.parametrize("release_id", [None, 15])
async def test_missing_historical_root_cannot_fall_back_to_future_source(monkeypatch, release_id):
    source = AsyncMock(return_value=None)
    monkeypatch.setattr(service, "_release_id", AsyncMock(return_value=release_id))
    monkeypatch.setattr(service, "_latest_source_record", source)
    with pytest.raises(StoryBrowsingError) as caught:
        await service.quest_profile_payload(Row(info={}), Row(node_id=27486, game_quest_id=11000012),
                                            game_version="3.1.0")
    assert caught.value.status_code == 404
    if release_id is None:
        source.assert_not_awaited()
    else:
        source.assert_awaited_once()
