"""Exact generic-node and cutscene sections consumed by projection-only routes."""

from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from wuwa_story.read_models import source_sections as sections


@pytest.mark.asyncio
async def test_node_edges_keep_public_schema_and_all_rows_beyond_http_limit():
    edges = [
        Row(
            id=identity,
            from_node_id=7,
            to_node_id=identity + 1000,
            relation_type_id=2,
            layer="source",
            basis="explicit_reference",
            confidence=None,
            metadata_json={"source_file": "roles.json"},
        )
        for identity in range(700)
    ]
    session = Row(scalars=AsyncMock(return_value=edges))
    result = await sections._node_edges(session, 7)
    assert len(result) == 700 and result[-1]["id"] == 699
    assert result[0]["metadata_json"] == {"source_file": "roles.json"}
    statement = str(session.scalars.await_args.args[0].compile(dialect=postgresql.dialect()))
    assert "LIMIT" not in statement


@pytest.mark.asyncio
@pytest.mark.parametrize("version,containing_release", [(None, 15), ("3.10.0", 90)])
async def test_cutscene_catalog_preserves_source_order_and_actual_containing_snapshot(
    monkeypatch, version, containing_release
):
    row = Row(
        node_id=7,
        cg_name="AuthoredMovie",
        first_observed_game_version="3.0.0",
        ordering_basis="authored_tree",
        latest_release_id=15,
        latest_game_version="3.9.0",
        cursor_version_0=3,
        cursor_version_1=0,
        cursor_version_2=0,
        cursor_index=17,
    )
    session = Row(
        execute=AsyncMock(return_value=Row(one_or_none=lambda: row)),
        scalar=AsyncMock(side_effect=[77, True]),
    )
    monkeypatch.setattr(sections.service, "_release_id", AsyncMock(return_value=90))
    summarize = AsyncMock(return_value=[{"node_id": 7, "title": "Scene", "name": "AuthoredMovie"}])
    monkeypatch.setattr(sections, "cutscene_summaries", summarize)
    monkeypatch.setattr(
        sections.cutscene_repository,
        "imported_versions",
        AsyncMock(return_value=["3.9.0", "3.10.0"]),
    )
    result = await sections._cutscene_catalog(session, 7, "ja", version)
    summarize.assert_awaited_once_with(session, [row], containing_release, "ja")
    assert result["cursor_values"] == [77, 3, 0, 0, 17, "AuthoredMovie", 7]
    assert result["item"]["ordering_basis"] == "authored_tree"
    assert result["item"]["first_observed_game_version"] == "3.0.0"
    assert result["item"]["game_version"] == (version or "3.9.0")
    assert result["playable"] is True
    assert result["release_id"] == (90 if version else None)


@pytest.mark.asyncio
async def test_missing_cutscene_revision_fails_instead_of_inventing_cursor_order(monkeypatch):
    session = Row(
        execute=AsyncMock(return_value=Row(one_or_none=lambda: object())),
        scalar=AsyncMock(return_value=None),
    )
    with pytest.raises(ValueError, match="order revision"):
        await sections._cutscene_catalog(session, 7, "en", None)
