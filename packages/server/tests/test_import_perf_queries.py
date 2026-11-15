"""Query-shape tests for snapshot import and search indexing optimizations."""

from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from wuwa_story.db.models import Character
from wuwa_story.search.indexer import _historical_source_rows, _source_rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _RecordingSession:
    def __init__(self, responder):
        self._responder = responder
        self.queries: list[str] = []

    async def execute(self, stmt):
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        self.queries.append(sql)
        return _FakeResult(self._responder(sql, len(self.queries) - 1))


async def test_source_rows_uses_two_step_node_keyset_and_point_localization_lookup():
    calls = {"entity": 0}

    def responder(sql: str, _idx: int):
        if "FROM core.character" in sql:
            calls["entity"] += 1
            if calls["entity"] == 1:
                return [(10, 100), (20, 200)]
            if calls["entity"] == 2:
                return [(30, 300)]
            return []
        if "FROM i18n.localization_value" in sql:
            if calls["entity"] == 1:
                return [(100, 1, "Rover"), (100, 2, "Ровер"), (200, 1, "Yangyang")]
            return [(300, 1, "Chixia")]
        return []

    session = _RecordingSession(responder)
    docs = [
        doc
        async for doc in _source_rows(
            session,
            Character,
            "name_key_id",
            "character",
            release_id=22,
            batch_size=2,
        )
    ]

    assert [(d["target_node_id"], d["locale_id"], d["category"], d["body"]) for d in docs] == [
        (10, 1, "character", "Rover"),
        (10, 2, "character", "Ровер"),
        (20, 1, "character", "Yangyang"),
        (30, 1, "character", "Chixia"),
    ]

    entity_queries = [q for q in session.queries if "FROM core.character" in q]
    loc_queries = [q for q in session.queries if "FROM i18n.localization_value" in q]
    assert len(entity_queries) == 3
    assert len(loc_queries) == 2
    for sql in entity_queries:
        assert "JOIN i18n.localization_value" not in sql
        assert "locale_id" not in sql
        assert "ORDER BY core.character.node_id" in sql
        assert "LIMIT" in sql
    for sql in loc_queries:
        assert "ORDER BY" not in sql


async def test_historical_source_rows_uses_exists_without_distinct_join():
    def responder(sql: str, _idx: int):
        if "FROM core.character" in sql:
            if "core.character.node_id >" in sql:
                # first batch returns node 5, second returns empty
                if responder.calls == 0:
                    responder.calls += 1
                    return [(5, 500)]
                return []
        if "FROM i18n.localization_value" in sql:
            return [(500, 1, 21, "OldName"), (500, 1, 22, "NewName")]
        return []

    responder.calls = 0
    releases = [
        SimpleNamespace(id=21, game_version="2.0", sequence=21),
        SimpleNamespace(id=22, game_version="2.1", sequence=22),
    ]
    session = _RecordingSession(responder)
    docs = [
        doc
        async for doc in _historical_source_rows(
            session,
            Character,
            "name_key_id",
            "character",
            release_id=22,
            releases=releases,
            batch_size=10,
        )
    ]

    assert [(d["target_node_id"], d["locale_id"], d["category"], d["body"]) for d in docs] == [
        (5, 1, "character", "NewName")
    ]
    entity_queries = [q for q in session.queries if "FROM core.character" in q]
    assert len(entity_queries) == 2
    assert "EXISTS" in entity_queries[0]
    assert "SELECT DISTINCT" not in entity_queries[0]
    assert "JOIN graph.node_revision" not in entity_queries[0]
