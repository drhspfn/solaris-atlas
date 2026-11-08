import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

SCRIPT = Path(__file__).parents[1] / "rebuild-story-backgrounds.py"
spec = importlib.util.spec_from_file_location("rebuild_story_backgrounds", SCRIPT)
rebuild = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rebuild)


class AsyncRows:
    def __init__(self, rows):
        self.rows = rows

    def __aiter__(self):
        self.iterator = iter(self.rows)
        return self

    async def __anext__(self):
        try:
            return next(self.iterator)
        except StopIteration:
            raise StopAsyncIteration from None


@pytest.mark.asyncio
async def test_authored_inventory_selects_exact_backgrounds_across_releases():
    spine = {"Actions": [{"Params": {"TalkItems": [
        {"BackgroundConfig": {"Type": "SpineImage", "Id": 7}}]}}]}
    image = {"Actions": json.dumps([{"Params": {"TalkItems": [
        {"BackgroundConfig": {"Type": "Image", "ImageAsset": "/Game/A.A"}}]}}])}
    clean = {"Actions": [{"BackgroundConfig": {"Type": "Clean"}}]}
    invalid = {"Actions": [{"BackgroundConfig": {"Type": "Image", "ImageAsset": "not-a-game-path"}}]}
    db = AsyncMock()
    db.stream.return_value = AsyncRows([
        ("flow_state:spine", 1, spine), ("flow_state:spine", 2, spine),
        ("flow_state:spine", 2, spine), ("flow_state:image", 2, image),
        ("flow_state:clean", 2, clean), ("flow_state:invalid", 2, invalid),
        ("flow_state:unrelated", 2, {"Note": "BackgroundConfig"}),
    ])
    assert await rebuild.authored_background_candidates(db, []) == {
        ("flow_state:spine", 1), ("flow_state:spine", 2), ("flow_state:image", 2),
    }
    statement = db.stream.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "graph.node_revision" in sql and "raw.source_record" in sql
    assert "ORDER BY node_revision_1.revision DESC" in sql
    assert "LIMIT" in sql and "LIKE" in sql
    assert statement.get_execution_options()["yield_per"] == 500


@pytest.mark.asyncio
async def test_authored_inventory_pushes_owner_filter_to_database():
    db = AsyncMock()
    db.stream.return_value = AsyncRows([])
    assert await rebuild.authored_background_candidates(db, ["flow_state:target"]) == set()
    compiled = db.stream.await_args.args[0].compile(dialect=postgresql.dialect())
    assert ["flow_state:target"] in compiled.params.values()
