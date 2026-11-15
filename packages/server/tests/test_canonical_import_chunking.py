import pytest
from sqlalchemy.dialects.postgresql.dml import Insert

from wuwa_story.ingestion.canonical_import import _insert_typed_entities


class _RecordingSession:
    def __init__(self):
        self.executed = []

    async def execute(self, statement):
        if isinstance(statement, Insert):
            self.executed.append(statement)
        return []

    async def scalars(self, statement):
        return []


@pytest.mark.asyncio
async def test_insert_typed_entities_chunks_below_postgres_param_limit():
    session = _RecordingSession()
    records = [
        {
            "id": f"action:state_1:{i}",
            "state_key": "state_1",
            "action_index": i,
            "action_id": str(i),
            "name": "ShowTalk",
            "raw": {"Params": {}},
        }
        for i in range(4000)
    ]
    node_map = {f"action:state_1:{i}": (i + 1, 1) for i in range(4000)}
    node_id_cache = {"flow_state:state_1": 999999}

    await _insert_typed_entities(
        session=session,
        kind="action",
        records=records,
        node_map=node_map,
        source_ids={},
        newer_release_ids=None,
        node_id_cache=node_id_cache,
    )

    assert len(session.executed) == 2
    row_counts = [len(stmt._multi_values[0]) for stmt in session.executed]
    assert sum(row_counts) == 4000
    assert all(count * 9 <= 30000 for count in row_counts)
