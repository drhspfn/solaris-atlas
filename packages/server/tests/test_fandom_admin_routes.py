from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from wuwa_story.api.routes import fandom as fandom_routes
from wuwa_story.auth.dependencies import require_admin, require_csrf
from wuwa_story.db.session import get_session
from wuwa_story.fandom.models import FandomEntityBundle, FandomEntityKind, FandomLicenseInfo


@pytest.mark.asyncio
async def test_fandom_status_and_sync_routes(monkeypatch):
    api = FastAPI()
    api.include_router(fandom_routes.router)
    session = MagicMock()
    session.commit = AsyncMock()
    api.dependency_overrides[get_session] = lambda: session
    api.dependency_overrides[require_admin] = lambda: SimpleNamespace(id=1, role="admin")
    api.dependency_overrides[require_csrf] = lambda: None

    node = SimpleNamespace(id=7872446, canonical_key="speaker:1696", slug="7872446-cassandra", type_id=2)
    monkeypatch.setattr(fandom_routes, "_resolve_target_node", AsyncMock(return_value=(node, "speaker")))
    status_payload = {
        "entity": {
            "node_id": 7872446,
            "canonical_key": "speaker:1696",
            "slug": "7872446-cassandra",
            "node_type": "speaker",
            "label": "Cassandra",
        },
        "synced": True,
        "fandom_fact": {"id": 10, "title": "Fandom: Cassandra", "summary": "Commander", "page_url": "https://wutheringwaves.fandom.com/wiki/Cassandra"},
        "relations": [{"id": 11, "title": "Cassandra parent_of Liam", "href": "/characters/liam"}],
        "visual": {"has_identity": False, "references_count": 0, "approved_count": 0, "preview_urls": [], "admin_url": "/admin/visual-memory"},
    }
    monkeypatch.setattr(fandom_routes, "_build_status_payload", AsyncMock(return_value=status_payload))

    bundle = FandomEntityBundle(
        entity_kind=FandomEntityKind.SPEAKER,
        query_name="Cassandra",
        page_title="Cassandra",
        page_url="https://wutheringwaves.fandom.com/wiki/Cassandra",
        license=FandomLicenseInfo(text="CC-BY-SA", is_cc_by_sa=True),
    )
    monkeypatch.setattr(fandom_routes.FandomClient, "resolve_for_db_entity", AsyncMock(return_value=bundle))
    monkeypatch.setattr(
        fandom_routes,
        "sync_fandom_bundle",
        AsyncMock(return_value={"page_url": bundle.page_url, "gallery_url": None, "facts_count": 1, "edges_count": 1, "matched_relations": [], "imported_references": []}),
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        st = await client.get("/admin/fandom/status?canonical_key=speaker:1696")
        assert st.status_code == 200
        assert st.json()["entity"]["label"] == "Cassandra"

        res = await client.post(
            "/admin/fandom/sync",
            json={"node_id": 7872446, "actions": ["sync_facts", "sync_relations"]},
        )
        assert res.status_code == 200
        assert res.json()["sync_result"]["facts_count"] == 1
        assert res.json()["sync_result"]["edges_count"] == 1
