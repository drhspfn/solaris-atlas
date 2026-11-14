import asyncio
import base64
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError

from wuwa_story.visual_memory import image_source, tools
from wuwa_story.visual_memory.images import encode_png
from test_visual_memory_service import memory  # noqa: F401


@pytest.fixture
def png():
    return encode_png(Image.new("RGB", (12, 12), "red"))


@pytest.mark.parametrize("payload", [
    {}, {"image": {}}, {"image": {"url": "https://example.org/a", "asset_id": 1}},
    {"image": {"asset_id": True}}, {"image": {"reference_id": -1}},
    {"image": {"base64": "!!"}}, {"image": {"resource_uri": "file:///etc/passwd"}},
    {"image": {"base64": "eA=="}, "image_base64": "eA=="},
])
def test_requires_exactly_one_source(payload):
    with pytest.raises(ValidationError):
        tools.SearchInput.model_validate(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["legacy", "base64", "url", "asset_id", "reference_id"])
async def test_source_resolution_and_legacy_compatibility(monkeypatch, png, source):
    service = SimpleNamespace(search=AsyncMock(return_value={"candidates": []}),
                              get_asset_image_bytes=AsyncMock(return_value=png),
                              get_image_bytes=AsyncMock(return_value=png),
                              get_reference=AsyncMock(return_value={"status": "approved", "enabled_for_search": True}))
    download = AsyncMock(return_value=png)
    monkeypatch.setattr(image_source, "download_image", download)
    value = {"base64": base64.b64encode(png).decode(), "url": "https://example.org/a.png", "asset_id": 12, "reference_id": 7}
    payload = {"image_base64": value["base64"]} if source == "legacy" else {"image": {source: value[source]}}
    await tools.VisualMemoryTools(service).execute("visual_search", {**payload, "include_images": True})
    service.search.assert_awaited_once_with(png, bbox=None, kind=None, limit=10)
    assert download.await_count == (source == "url")
    if source == "asset_id":
        service.get_asset_image_bytes.assert_awaited_once_with(12)
    if source == "reference_id":
        service.get_image_bytes.assert_awaited_once_with(7, max_bytes=tools.MAX_IMAGE_BYTES)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,enabled", [("pending", True), ("rejected", False), ("approved", False)])
async def test_reference_privacy_gate(status, enabled):
    service = SimpleNamespace(get_reference=AsyncMock(return_value={"status": status, "enabled_for_search": enabled}),
                              get_image_bytes=AsyncMock())
    with pytest.raises(ValueError):
        await tools.VisualMemoryTools(service).execute("visual_identify", {"image": {"reference_id": 1}})
    service.get_image_bytes.assert_not_awaited()


def dns(monkeypatch, *addresses):
    resolver = AsyncMock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in addresses])
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolver)
    return resolver


@pytest.mark.asyncio
@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:8.8.8.8", "224.0.0.1", "0.0.0.0"])
async def test_private_and_special_dns_denied(monkeypatch, address):
    dns(monkeypatch, address)
    with pytest.raises(ValueError):
        await image_source.public_address(httpx.URL("https://example.org/a"))


@pytest.mark.asyncio
async def test_mixed_dns_denied(monkeypatch):
    dns(monkeypatch, "8.8.8.8", "10.0.0.1")
    with pytest.raises(ValueError):
        await image_source.public_address(httpx.URL("https://example.org/a"))


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://example.org/a", "file:///etc/passwd", "https://u:p@example.org/a", "https://example.org:444/a", "https://[fe80::1%25eth0]/a"])
async def test_invalid_url_never_resolves(monkeypatch, url):
    resolver = dns(monkeypatch, "8.8.8.8")
    with pytest.raises(ValueError):
        await image_source.public_address(httpx.URL(url))
    resolver.assert_not_awaited()


def client(monkeypatch, handler):
    factory = httpx.AsyncClient
    monkeypatch.setattr(image_source.httpx, "AsyncClient", lambda **kw: factory(**{**kw, "transport": httpx.MockTransport(handler)}))


@pytest.mark.asyncio
async def test_download_pins_ip_keeps_tls_host_and_accepts_real_image(monkeypatch, png):
    resolver = dns(monkeypatch, "8.8.8.8")
    def handler(request):
        assert request.url.host == "8.8.8.8"
        assert request.headers["host"] == "example.org"
        assert request.extensions["sni_hostname"] == "example.org"
        return httpx.Response(200, content=png, headers={"Content-Type": "image/png"})
    client(monkeypatch, handler)
    assert await image_source.download_image("https://example.org/a.png") == png
    resolver.assert_awaited_once()


@pytest.mark.asyncio
async def test_redirect_checks_new_dns_and_never_connects_private_address(monkeypatch):
    resolver = dns(monkeypatch, "8.8.8.8")
    resolver.side_effect = [resolver.return_value, [(2, 1, 6, "", ("127.0.0.1", 443))]]
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://internal.example/a"})
    client(monkeypatch, handler)
    with pytest.raises(ValueError):
        await image_source.download_image("https://example.org/a")
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["size", "stream_size", "html", "fake_png", "redirect_loop", "compressed"])
async def test_download_rejects_oversize_non_images_and_redirect_loops(monkeypatch, png, case):
    dns(monkeypatch, "8.8.8.8")
    def handler(request):
        if case == "redirect_loop":
            return httpx.Response(302, headers={"Location": "/again"})
        content, headers = png, {"Content-Type": "image/png"}
        if case == "size":
            headers["Content-Length"] = str(tools.MAX_IMAGE_BYTES + 1)
        elif case == "stream_size":
            content = b"x" * (tools.MAX_IMAGE_BYTES + 1)
        elif case == "html":
            headers["Content-Type"] = "text/html"
        elif case == "fake_png":
            content = b"<html>not a png</html>"
        elif case == "compressed":
            import gzip
            content = gzip.compress(png)
            headers["Content-Encoding"] = "gzip"
        return httpx.Response(200, content=content, headers=headers)
    client(monkeypatch, handler)
    with pytest.raises(ValueError):
        await image_source.download_image("https://example.org/a")


@pytest.mark.asyncio
async def test_native_mcp_thumbnails_and_disabled_references(monkeypatch, png):
    service = SimpleNamespace(get_reference=AsyncMock(return_value={"status": "approved", "enabled_for_search": True}),
                              get_image_bytes=AsyncMock(return_value=png))
    monkeypatch.setattr(tools, "service_for", lambda _: service)
    result = {"detections": [{"identification": {"candidates": [{"evidence": [{"reference_id": 1}]}]}}]}
    assert await tools.image_contents("visual_identify", {}, result, None) == []
    contents = await tools.image_contents("visual_identify", {"include_images": True}, result, None)
    assert [item.type for item in contents] == ["text", "image"]
    assert contents[1].mimeType == "image/jpeg"
    service.get_reference.return_value["enabled_for_search"] = False
    assert await tools.image_contents("visual_identify", {"include_images": True}, result, None) == []


def test_image_sources_are_redacted():
    from wuwa_story.agents.audit import redacted
    from wuwa_story.agents.trace import preview
    payload = {"image": {"url": "https://example.org/a?token=private"}, "image_base64": "private"}
    assert "private" not in str(redacted(payload))
    assert "private" not in preview(payload)
    import json
    assert "private" not in preview(json.dumps(payload))
    assert "private" not in preview({"arguments": json.dumps(payload)})


@pytest.mark.asyncio
async def test_thumbnail_decode_obeys_shared_concurrency_bound(monkeypatch):
    service = SimpleNamespace(get_reference=AsyncMock())
    monkeypatch.setattr(tools, "service_for", lambda _: service)
    async with tools._public_inference:
        result = await tools.image_contents("visual_get_reference", {"reference_id": 1, "include_images": True}, {}, None)
    assert "busy" in result[0].text
    service.get_reference.assert_not_awaited()


@pytest.mark.asyncio
async def test_registered_asset_only_reads_published_images(memory, png):
    from sqlalchemy import select
    from wuwa_story.db.models import FileLocation, FileReference, Node, NodeType

    service, session, _ = memory
    node = Node(type_id=await session.scalar(select(NodeType.id).limit(1)), canonical_key="visual-source-test")
    session.add(node)
    await session.flush()
    file_id = await service._store_image(Image.new("RGB", (12, 12), "red"), "query.png", png)
    ref = FileReference(owner_node_id=node.id, file_id=file_id, reference_type="private_image")
    session.add(ref)
    await session.flush()
    with pytest.raises(ValueError, match="unavailable"):
        await service.get_asset_image_bytes(node.id)
    ref.reference_type = "entity_image"
    await session.flush()
    assert await service.get_asset_image_bytes(node.id) == png
    location = await session.scalar(select(FileLocation).where(FileLocation.file_id == file_id))
    location.available = False
    await session.flush()
    with pytest.raises(ValueError, match="unavailable"):
        await service.get_asset_image_bytes(node.id)
    location.available = True
    await session.flush()
    (service.storage.root / location.object_key).write_bytes(b"wrong data")
    with pytest.raises(ValueError, match="registered content"):
        await service.get_asset_image_bytes(node.id)


@pytest.mark.asyncio
async def test_transport_redacts_signed_urls_before_client_logging(monkeypatch):
    request = httpx.Request("GET", "https://8.8.8.8/private-path?signature=private-query")
    upstream = AsyncMock(return_value=httpx.Response(200))
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", upstream)
    async with image_source._ImageTransport(trust_env=False) as transport:
        await transport.handle_async_request(request)
    assert str(request.url) == "https://8.8.8.8/"
