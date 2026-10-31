"""Complete background neighbors retain HTTP paging and evidence contracts."""

from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

import test_node_related_incident as incident_tests
from wuwa_story.api.routes import nodes
from wuwa_story.db.models.graph import Edge, Node, NodeType
from wuwa_story.read_models import source_sections
from wuwa_story.read_models.context import source_build
from wuwa_story.read_models.readers import entities
from wuwa_story.story.node_related import source_related

database = incident_tests.database


def public_items(items):
    return [{key: value for key, value in item.items() if key != "_source_file"} for item in items]


@pytest.mark.asyncio
@pytest.mark.parametrize("degree", [501, 20_000])
async def test_background_reads_all_once_and_matches_assembled_http_pages(
    database, monkeypatch, degree
):
    await database.execute(NodeType.__table__.insert().values(id=4, key="action"))
    await database.execute(
        Node.__table__.insert().values(id=6, type_id=4, canonical_key="action:6", metadata={})
    )
    await database.execute(
        Edge.__table__.insert().values(
            id=12,
            from_node_id=1,
            to_node_id=6,
            relation_type_id=2,
            layer="source",
            basis="explicit_reference",
            metadata={"edge": 12},
        )
    )
    for start in range(100, 100 + degree, 500):
        ids = range(start, min(start + 500, 100 + degree))
        await database.execute(
            Node.__table__.insert().values(
                [
                    {
                        "id": identity,
                        "type_id": 3,
                        "canonical_key": f"source:{identity}",
                        "metadata": {"source_file": "A.json", "source_row": identity},
                    }
                    for identity in ids
                ]
            )
        )
        await database.execute(
            Edge.__table__.insert().values(
                [
                    {
                        "id": identity,
                        "from_node_id": 1,
                        "to_node_id": identity,
                        "relation_type_id": 2,
                        "layer": "source",
                        "basis": "explicit_reference",
                        "metadata": {"edge": identity},
                    }
                    for identity in ids
                ]
            )
        )
    baseline = []
    first = None
    offset = 0
    with source_build(), source_sections._request_caches(database):
        while True:
            page = await incident_tests.related(database, limit=500, offset=offset)
            assert page["source_file_filter"] is None
            first = first or page
            baseline.extend(page["results"])
            if len(page["results"]) < 500:
                break
            offset += 500
    provenance = {
        "canonical_key": "location:1",
        "source_record": {"id": 7, "source_file": "A.json", "raw_path": "$[1]"},
        "resolution": "linked_by_source_record_id",
    }
    monkeypatch.setattr(
        nodes, "node_detail", AsyncMock(return_value=Row(model_dump=lambda: {"id": 1}))
    )
    monkeypatch.setattr(nodes, "node_source_record", AsyncMock(return_value=provenance))
    database.statements.clear()
    with source_build(), source_sections._request_caches(database):
        built = await source_sections._node_payload(database, "location:1", "en", 1)
    related = built["related"]
    assert public_items(related["results"]) == baseline
    assert len(baseline) == degree + 9
    ids = [item["provenance"]["edge"] for item in related["results"]]
    assert len(ids) == len(set(ids))  # Parallel edges retained, self-loop only once.
    assert any(item["node"]["type"] == "action" for item in baseline)
    assert sum(item["node"]["type"] == "source_reference" for item in baseline) == degree + 4
    assert related["source_evidence"] == first["source_evidence"]
    assert related["summary"] == f"Found {len(baseline)} linked entities."
    assert related["limit"] == len(baseline) and related["offset"] == 0
    assert related["source_file_filter"] is None
    assert built["source_record"] == provenance
    assert len(built["edges"]) == degree + 11  # Includes manual/semantic edges too.
    assert len(database.statements) == 2  # One neighbor query, one evidence query.
    assert database.statements[0]._limit_clause is None
    monkeypatch.setattr(
        entities, "node_projection", AsyncMock(return_value=Row(kind="location", entity_id=1))
    )
    monkeypatch.setattr(entities, "page_payload", AsyncMock(return_value=related))
    actual = await entities.node_related_page(
        database, "location:1", include_evidence=True, limit=24, offset=500
    )
    assert actual["results"] == baseline[500:524]
    assert actual["summary"] == f"Found {len(baseline[500:524])} linked entities."
    assert actual["source_file_filter"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("requested", [None, "", "A.json", "absent.json"])
@pytest.mark.parametrize("limit", [None, 2])
async def test_filter_echo_is_requested_value_not_neighbor_source_file(database, requested, limit):
    with source_build(), source_sections._request_caches(database):
        result = await source_related(
            database,
            1,
            "location:1",
            source_file=requested,
            include_evidence=True,
            limit=limit,
        )
    assert result["source_file_filter"] == requested
    if requested is None:
        assert result["results"]
        assert any(row["source_file"] for row in result["source_evidence"])
    if limit is None:
        assert all("_source_file" in item for item in result["results"])
        if requested == "A.json":
            assert {item["_source_file"] for item in result["results"]} == {"A.json"}
    else:
        assert all("_source_file" not in item for item in result["results"])


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["both", "in", "out"])
@pytest.mark.parametrize(
    "filters",
    [
        {},
        {"include_evidence": False},
        {"category": "character"},
        {"category": "source_reference"},
        {"source_file": "A.json"},
        {"source_file": "absent"},
        {"relation": "references"},
        {"limit": 2, "offset": 1},
        {"offset": 100},
    ],
)
async def test_projected_filtered_pages_match_source_including_self_loop_and_evidence(
    database, monkeypatch, direction, filters
):
    stored = await source_related(database, 1, "location:1", include_evidence=True, limit=None)
    monkeypatch.setattr(
        entities, "node_projection", AsyncMock(return_value=Row(kind="location", entity_id=1))
    )
    monkeypatch.setattr(entities, "page_payload", AsyncMock(return_value=stored))
    options = {"direction": direction, "include_evidence": True, "limit": 100, **filters}
    expected = await incident_tests.related(database, **options)
    actual = await entities.node_related_page(database, "location:1", **options)
    assert actual == expected
    assert all("_source_file" not in item for item in actual["results"])


@pytest.mark.asyncio
async def test_http_keeps_500_cap_and_internal_unbounded_mode_requires_zero_offset():
    app = FastAPI()
    app.include_router(nodes.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        for limit in (501, "None"):
            assert (await client.get(f"/nodes/location:1/related?limit={limit}")).status_code == 422
    with pytest.raises(ValueError, match="offset zero"):
        await source_related(None, 1, "location:1", limit=None, offset=1)
