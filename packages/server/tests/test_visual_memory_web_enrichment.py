from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image

from wuwa_story.visual_memory.web_enrichment import (
    EnrichmentTarget,
    build_search_queries,
    collect_web_images_for_target,
    export_candidates_to_dir,
    persist_candidates_to_visual_memory,
    validate_image_bytes,
)


def _png_bytes(width: int = 320, height: int = 240, color: tuple[int, int, int] = (20, 80, 160)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def test_build_search_queries_for_playable_and_npc():
    playable = build_search_queries("Jinhsi", "playable")
    assert playable[0] == ("render", '"wuthering waves" "Jinhsi" render')
    assert ("official art", '"wuthering waves" "Jinhsi" official art') in playable

    npc = build_search_queries("Yhan", "npc")
    assert npc[0] == ("npc", '"wuthering waves" "Yhan" npc')
    assert ("screenshot", '"wuthering waves" "Yhan" screenshot') in npc
    assert ("model", '"wuthering waves" "Yhan" model') in npc


def test_validate_image_bytes_checks_dimensions_and_format():
    assert validate_image_bytes(b"not-an-image") is None
    assert validate_image_bytes(_png_bytes(100, 100), min_width=200, min_height=200) is None
    res = validate_image_bytes(_png_bytes(320, 240), min_width=200, min_height=200)
    assert res is not None
    width, height, digest = res
    assert (width, height) == (320, 240)
    assert len(digest) == 64


@pytest.mark.asyncio
async def test_collect_web_images_deduplicates_filters_and_exports(tmp_path: Path):
    png_a = _png_bytes(320, 240, (10, 20, 30))
    png_b = _png_bytes(400, 300, (90, 80, 70))

    async def fake_search(query: str, *, max_results: int = 5):
        return [
            {"image": "https://example.com/yhan_1.png", "url": "https://example.com/p1", "title": "Yhan"},
            {"image": "https://example.com/yhan_dup_bytes.png", "url": "https://example.com/p2", "title": "Yhan dup"},
            {"image": "https://example.com/characters/encore/splash.png", "url": "https://example.com/p3", "title": "Encore"},
            {"image": "https://images-ng.pixai.art/images/orig/yhan.png", "url": "https://pixai.art/model/1", "title": "Yhan AI LoRA"},
            {"image": "https://example.com/unrelated_guide.png", "url": "https://example.com/guide", "title": "Echo Guide"},
            {"image": "https://example.com/yhan_2.png", "url": "https://example.com/p4", "title": "Yhan 2"},
            {"image": "https://example.com/broken.png", "url": "https://example.com/p5", "title": "Broken"},
        ][:max_results]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/yhan_1.png" or request.url.path == "/yhan_dup_bytes.png":
            return httpx.Response(200, content=png_a, headers={"content-type": "image/png"})
        if request.url.path == "/yhan_2.png":
            return httpx.Response(200, content=png_b, headers={"content-type": "image/png"})
        return httpx.Response(403, content=b"forbidden", headers={"content-type": "text/html"})

    target = EnrichmentTarget(entity_id=1001, name="Yhan", kind="npc", role_type="4")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        collected = await collect_web_images_for_target(
            target,
            suffixes=["npc"],
            per_query=7,
            max_images=5,
            min_size=200,
            delay_seconds=0.0,
            search_fn=fake_search,
            client=client,
        )

    assert len(collected["candidates"]) == 2
    assert collected["skipped"] == 5  # wrong char + pixai + missing name + dup sha256 + 403
    out_dir = export_candidates_to_dir(tmp_path, target, collected["candidates"])
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["count"] == 2
    assert (out_dir / manifest["images"][0]["file"]).is_file()


@pytest.mark.asyncio
async def test_persist_candidates_creates_identity_and_adds_pending_references():
    png_a = _png_bytes(320, 240, (10, 20, 30))

    class FakeSession:
        def __init__(self):
            self.added = []
            self._next_id = 500

        async def scalar(self, _stmt):
            return None

        def add(self, obj):
            if getattr(obj, "id", None) is None and hasattr(obj, "name"):
                obj.id = self._next_id
                self._next_id += 1
            self.added.append(obj)

        async def flush(self):
            return None

    session = FakeSession()
    service = SimpleNamespace(add_reference=AsyncMock(return_value={"id": 1, "duplicate": False}))
    target = EnrichmentTarget(entity_id=1001, name="Yhan", kind="npc")

    from wuwa_story.visual_memory.web_enrichment import WebImageCandidate

    cand = WebImageCandidate(
        url="https://example.com/yhan.png",
        page_url="https://example.com/page",
        title="Yhan NPC",
        query="wuthering waves Yhan npc",
        suffix="npc",
        width=320,
        height=240,
        sha256="abc" * 21 + "a",
        content=png_a,
    )
    stats = await persist_candidates_to_visual_memory(session, service, target, [cand])
    assert stats["identity_id"] == 500
    assert stats["web_added"] == 1
    service.add_reference.assert_awaited_once()
    call_kwargs = service.add_reference.call_args.kwargs
    assert call_kwargs["source_type"] == "web_search"
    assert call_kwargs["status"] == "pending"
    assert call_kwargs["links"] == [{"identity_id": 500, "appearance_id": None}]
