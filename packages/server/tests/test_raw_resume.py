import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from wuwa_story.ingestion import raw


@pytest.mark.asyncio
@pytest.mark.parametrize("expected", [None, 100])
async def test_resume_raw_fallback_counts_retained_records(tmp_path, monkeypatch, expected):
    path = "BinData/Furniture/furniture.json"
    source = tmp_path / "raw-evidence" / path
    source.parent.mkdir(parents=True)
    source.write_text("malformed source", encoding="utf-8")
    (tmp_path / "coverage.json").write_text(json.dumps({
        "tables": [{"file": path, "record_count": expected}],
    }))
    (tmp_path / "raw-evidence/index.json").write_text(json.dumps([
        {"file": path, "sha256": "unchanged"},
    ]))
    file = SimpleNamespace(id=9, metadata_json={
        "source_sha256": "unchanged", "ingestion_state": "raw_fallback",
    })
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [(9, 17)])),
        scalar=AsyncMock(return_value=file),
    )
    parse = Mock(side_effect=AssertionError("retained source must not be exported again"))
    monkeypatch.setattr(raw, "_iter_records", parse)
    assert await raw.import_raw_snapshot(tmp_path, session, 19) == (17, 0)
    parse.assert_not_called()
    session.execute.assert_awaited_once()
