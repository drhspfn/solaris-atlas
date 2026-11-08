import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from wuwa_story.visual_memory import image_source, tools


@pytest.mark.asyncio
async def test_mcp_file_params_match_openai_contract():
    from wuwa_story.api.routes.mcp import handle_list_tools

    descriptors = {tool.name: tool.model_dump(by_alias=True, exclude_none=True) for tool in await handle_list_tools()}
    for name in ("visual_search", "visual_identify"):
        descriptor = descriptors[name]
        assert descriptor["_meta"]["openai/fileParams"] == ["file"]
        schema = descriptor["inputSchema"]
        assert schema["properties"]["file"]["$ref"] == "#/$defs/OpenAIFile"
        file = schema["$defs"]["OpenAIFile"]
        assert file["type"] == "object"
        assert set(file["required"]) == {"download_url", "file_id"}
        for field in ("download_url", "file_id", "mime_type", "file_name"):
            assert file["properties"][field]["type"] == "string"
        # Legacy inputs remain valid.
        assert "file" not in schema.get("required", [])
        assert descriptor["annotations"]["readOnlyHint"] is True
        assert descriptor["annotations"]["destructiveHint"] is False


@pytest.mark.parametrize("payload", [
    {"file": {}}, {"file": {"file_id": "file_test"}},
    {"file": {"download_url": "https://example.org/a.png"}},
    {"file": {"file_id": "", "download_url": "https://example.org/a.png"}},
    {"file": "/tmp/photo.png"},
    {"file": {"file_id": "file_test",
              "download_url": "https://example.org/a.png", "file_name": 123}},
    {"file": {"file_id": "file_test", "download_url": "https://example.org/a.png"},
        "image": {"reference_id": 1}},
    {"file": {"file_id": "file_test",
              "download_url": "https://example.org/a.png"}, "image_base64": "eA=="},
])
def test_rejects_incomplete_files_and_multiple_sources(payload):
    with pytest.raises(ValidationError):
        tools.SearchInput.model_validate(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["visual_search", "visual_identify"])
@pytest.mark.parametrize("metadata", [{}, {"mime_type": "image/png", "file_name": "attachment.png"}])
async def test_file_download_reuses_query_pipeline(monkeypatch, name, metadata):
    service = SimpleNamespace(search=AsyncMock(return_value={"candidates": []}),
                              identify=AsyncMock(return_value={"detections": []}))
    download = AsyncMock(return_value=b"test image bytes")
    monkeypatch.setattr(image_source, "download_image", download)
    await tools.VisualMemoryTools(service).execute(name, {
        "file": {"download_url": "https://example.org/a.png?signature=private", "file_id": "file_test", **metadata},
        "include_images": True,
    })
    download.assert_awaited_once_with(
        "https://example.org/a.png?signature=private")
    called = service.search if name == "visual_search" else service.identify
    assert called.await_args.args == (b"test image bytes",)
    assert not {"file", "image", "image_base64",
                "include_images"} & called.await_args.kwargs.keys()


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://localhost/a", "https://127.0.0.1/a"])
async def test_file_parameter_cannot_bypass_url_protection(monkeypatch, url):
    service = SimpleNamespace(search=AsyncMock())
    monkeypatch.setattr(tools, "service_for", lambda _: service)
    result = await tools.dispatch("visual_search", {
        "file": {"download_url": url, "file_id": "file_test"},
    }, None)
    assert result["error"]["code"] == "invalid_input"
    service.search.assert_not_awaited()


def test_signed_file_metadata_is_redacted_in_structured_and_legacy_traces():
    from wuwa_story.agents.audit import redacted
    from wuwa_story.agents.trace import preview

    payload = {"file": {"download_url": "https://example.org/private?signature=secret-value",
                        "file_id": "private-file-id", "file_name": "private-photo.png"}}
    for rendered in (redacted(payload), preview(payload), preview(json.dumps(payload)),
                     preview({"arguments": json.dumps(payload)})):
        assert "private" not in str(
            rendered) and "secret-value" not in str(rendered)
    assert "secret-value" not in str(redacted(payload["file"]))


@pytest.mark.asyncio
async def test_expired_file_link_is_not_echoed_or_sent_to_inference(monkeypatch):
    service = SimpleNamespace(search=AsyncMock())
    monkeypatch.setattr(tools, "service_for", lambda _: service)
    factory = httpx.AsyncClient

    def handler(request):
        return httpx.Response(403, text="private signed URL expired", request=request)
    monkeypatch.setattr(image_source, "public_address",
                        AsyncMock(return_value="8.8.8.8"))
    monkeypatch.setattr(image_source.httpx, "AsyncClient", lambda **
                        kw: factory(**{**kw, "transport": httpx.MockTransport(handler)}))
    result = await tools.dispatch("visual_search", {
        "file": {"download_url": "https://example.org/a?signature=secret-value", "file_id": "file_test"},
    }, None)
    assert result["error"]["code"] == "invalid_input"
    assert "private" not in str(result) and "secret-value" not in str(result)
    service.search.assert_not_awaited()
