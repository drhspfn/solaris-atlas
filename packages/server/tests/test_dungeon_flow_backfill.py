import json
import sqlite3
from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql, sqlite

from wuwa_story.ingestion import dungeon_flow_backfill as repair


def fixture(monkeypatch):
    rows = {
        repair.DUNGEONS: [(1, {"Id": 1555})],
        repair.QUEST_NODES: [(19042, {"Key": "114000031_231", "Data": {
            "Condition": {"Type": "FinishDungeon", "Condition": {"Type": "DungeonId", "DungeonId": 1555}},
        }})],
        repair.LEVELS: [(4338, {"LevelPlayId": 114000004, "Data": {
            "Id": 114000004, "LevelId": 1555, "Type": "Dungeon",
        }})],
        repair.LEVEL_NODES: [(65884, {"Key": "114000004_66", "Data": {
            "Id": 66, "Condition": {"Type": "PlayFlow", "Flow": {
                "FlowListName": "Story", "FlowId": 28, "StateId": 1,
            }},
        }})],
    }
    evidence = set()
    known = {"quest_node:114000031:231", "flow_state:Story_28_1"}
    session = Row(get=AsyncMock(return_value=Row(game_version="3.7.0", upstream_name="source", upstream_commit="commit")),
                  execute=AsyncMock(), scalars=AsyncMock(side_effect=lambda _: set(known)), commit=AsyncMock())

    async def execute(statement, *_args):
        query = str(statement.compile(dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True}))
        if query.startswith("SET LOCAL") or "pg_advisory_xact_lock" in query:
            return []
        if "raw.source_record" in query:
            assert "source_record.release_id = 21" in query
            assert "source_file.release_id = 21" in query
            return next(value for path, value in rows.items() if path in query)
        if "graph.edge_evidence" in query:
            assert "edge_evidence.release_id = 21" in query
            assert "edge.basis = 'exact_join'" in query
            return evidence
        raise AssertionError(query)

    session.execute.side_effect = execute

    async def importer(adapter, selected_session, release_id, *, batch_size):
        assert selected_session is session and release_id == 21
        assert batch_size == 1
        for link in adapter.iter_edges():
            assert link["type"] == repair.RELATION and link["relation"] == "exact_join"
            evidence.add((link["from"], link["to"],
                         link["source"], link["raw_path"]))
        await selected_session.commit()

    imported = AsyncMock(side_effect=importer)
    monkeypatch.setattr(repair, "import_canonical_edges", imported)
    return session, imported, known, rows


@pytest.mark.asyncio
async def test_scoped_repair_preview_and_repeat_are_idempotent(monkeypatch):
    session, imported, _, _ = fixture(monkeypatch)
    preview = await repair.backfill(session, 21, {1555})
    assert preview["missing_evidence"] == 1 and preview["written"] == 0
    assert preview["quest_keys"] == ["114000031"]
    assert preview["state_keys"] == ["Story_28_1"]
    imported.assert_not_awaited()
    session.commit.assert_not_awaited()
    assert (await repair.backfill(session, 21, {1555}, apply=True))["written"] == 1
    assert (await repair.backfill(session, 21, {1555}, apply=True))["written"] == 0
    imported.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_level_node_filter_extracts_prefix_once_for_many_members(monkeypatch):
    session, imported, _, rows = fixture(monkeypatch)
    rows[repair.LEVELS].extend((index, {"LevelPlayId": index, "Data": {
        "Id": index, "LevelId": 1555, "Type": "Dungeon",
    }}) for index in range(1000, 1080))
    original_execute = session.execute.side_effect
    selected_rows = []
    with sqlite3.connect(":memory:") as db:
        db.create_function("split_part", 3, lambda value, delimiter, part:
                           None if value is None else (value.split(delimiter) + [""] * part)[part - 1])
        db.execute("ATTACH DATABASE ':memory:' AS raw")
        db.execute("CREATE TABLE raw.source_file (id INTEGER, release_id INTEGER, logical_source_path TEXT)")
        db.execute("CREATE TABLE raw.source_record (release_id INTEGER, source_file_id INTEGER, row_index INTEGER, data JSON)")
        db.executemany("INSERT INTO raw.source_file VALUES (?, ?, ?)", [
            (1, 21, repair.LEVEL_NODES), (2, 22, repair.LEVEL_NODES),
        ])
        original = rows[repair.LEVEL_NODES][0][1]
        for index, (release, file, key) in enumerate([
            (21, 1, "114000004_66"),
            (21, 1, "1140000040_66"),  # A longer numeric prefix must not match.
            (21, 1, "11400000_66"),
            (21, 1, "114000004"),  # Selected by prefix, rejected by the parser.
            (21, 1, "114000004_66_extra"),
            (21, 2, "114000004_66"),  # Mismatched file snapshot.
            (22, 1, "114000004_66"),  # Mismatched record snapshot.
        ]):
            db.execute("INSERT INTO raw.source_record VALUES (?, ?, ?, ?)",
                       (release, file, index, json.dumps({**original, "Key": key})))

        async def execute(statement, *args):
            query = str(statement.compile(dialect=postgresql.dialect(),
                        compile_kwargs={"literal_binds": True}))
            if repair.LEVEL_NODES in query:
                assert query.count("split_part(") == query.count("'Key'") == 1
                assert " IN (" in query and "'1000'" in query and "'1079'" in query
                assert " LIKE " not in query and " OR " not in query
                compiled = str(statement.compile(dialect=sqlite.dialect(),
                               compile_kwargs={"literal_binds": True}))
                result = [(index, json.loads(data)) for index, data in db.execute(compiled)]
                selected_rows.extend(index for index, _ in result)
                return result
            return await original_execute(statement, *args)

        session.execute.side_effect = execute
        result = await repair.backfill(session, 21, {1555})
    assert selected_rows == [0, 3, 4]
    assert result["candidate_links"] == result["missing_evidence"] == 1
    assert result["state_keys"] == ["Story_28_1"]
    imported.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_scoped_repair_refuses_endpoints_absent_from_snapshot(monkeypatch):
    session, imported, known, _ = fixture(monkeypatch)
    known.remove("flow_state:Story_28_1")
    assert (await repair.backfill(session, 21, {1555}))["unresolved_links"] == 1
    with pytest.raises(ValueError, match="absent from the selected snapshot"):
        await repair.backfill(session, 21, {1555}, apply=True)
    imported.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_scoped_repair_budget_is_checked_before_writes(monkeypatch):
    session, imported, _, rows = fixture(monkeypatch)
    rows[repair.LEVEL_NODES][0][1]["Data"]["FinishActions"] = [{
        "Name": "PlayFlow", "Params": {"FlowListName": "Story", "FlowId": 29, "StateId": 1},
    }]
    with pytest.raises(ValueError, match="exceed max_links"):
        await repair.backfill(session, 21, {1555}, apply=True, max_links=1)
    imported.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("dungeons", [set(), {-1}, {True}, {1556}])
async def test_scoped_repair_requires_existing_explicit_dungeon_selection(monkeypatch, dungeons):
    session, imported, _, _ = fixture(monkeypatch)
    with pytest.raises(ValueError):
        await repair.backfill(session, 21, dungeons)
    imported.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_edge_importer_accepts_database_metadata_column(monkeypatch):
    from wuwa_story.ingestion import canonical_import

    source = "BinData/QuestNodeData/questnodedata.json"
    path = "$[19042].Data.Condition"
    record = {"from": "quest_node:114000031:231", "to": "flow_state:Story_28_1",
              "type": repair.RELATION, "relation": "exact_join", "source": source,
              "raw_path": path, "basis": "Explicit FinishDungeon and PlayFlow binding",
              "version": "3.7.0"}
    captured = []

    async def execute(statement):
        # Exercise the real Insert construction/compilation without a database.
        query = str(statement.compile(dialect=postgresql.dialect()))
        captured.append(query)
        if "SELECT graph.node.id" in query:
            return [(1, record["from"]), (2, record["to"])]
        if "SELECT graph.edge.id" in query:
            return [(10, 1, 3, 2, "source", "exact_join")]
        return []

    session = Row(scalars=AsyncMock(return_value=[Row(id=3, key=repair.RELATION)]),
                  execute=AsyncMock(side_effect=execute), commit=AsyncMock())
    monkeypatch.setattr(canonical_import, "_source_record_ids", AsyncMock(return_value={(source, 19042): 99}))
    assert await canonical_import.import_canonical_edges(
        Row(iter_edges=lambda: iter([record])), session, 21,
    ) == 1
    assert any("INSERT INTO graph.edge (" in query and "metadata" in query for query in captured)
    assert any("INSERT INTO graph.edge_evidence" in query for query in captured)
    session.commit.assert_awaited_once()
