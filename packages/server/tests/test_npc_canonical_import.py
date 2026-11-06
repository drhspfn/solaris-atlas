import pytest
from sqlalchemy.dialects.postgresql import insert

from wuwa_story.db.models.graph import Node, NodeRevision
from wuwa_story.ingestion.canonical_import import _insert_typed_entities


def test_graph_bulk_inserts_accept_metadata_column():
    for model, value in ((Node, {"canonical_key": "character:npc:1", "type_id": 1,
                                  "metadata": {"source_file": "fixture"}}),
                         (NodeRevision, {"node_id": 1, "release_id": 1, "revision": 1,
                                         "content_hash": b"x", "metadata": {"source_file": "fixture"}})):
        statement = insert(model.__table__).values([value])
        assert "metadata" in str(statement.compile())


@pytest.mark.asyncio
async def test_npc_projection_uses_idempotent_character_upsert():
    class Empty:
        def all(self):
            return []

        def __iter__(self):
            return iter(())

    class Session:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return Empty()

    session = Session()
    record = {"id": "character:npc:17290", "game_id": 17290,
              "name": None, "raw": {"RoleType": 1},
              "npc": {"blueprint_id": 17290, "source": {"asset_version": "3.7.1"}}}
    for _ in range(2):
        await _insert_typed_entities(session, "character", [record],
                                     {record["id"]: (42, 1)}, {})
    inserts = [statement for statement in session.statements if getattr(
        statement, "is_insert", False)]
    assert len(inserts) == 2
    for statement in inserts:
        compiled = statement.compile()
        assert "ON CONFLICT (node_id) DO UPDATE" in str(compiled)
        metadata = next(value for value in compiled.params.values()
                        if isinstance(value, dict) and value.get("npc"))
        assert metadata["role_type"] == 6
        assert metadata["npc"] == record["npc"]
