from types import SimpleNamespace

import httpx
import pytest

from wuwa_story.ingestion import queue_status as module


@pytest.mark.asyncio
async def test_queue_monitor_filters_and_does_not_expose_credentials(monkeypatch):
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(rabbitmq_url=SimpleNamespace(get_secret_value=lambda: "amqp://admin:private-password@rabbitmq:5672/%2F")))
    requests = []
    def handle(request):
        requests.append(request)
        if "/consumers/" in request.url.path:
            return httpx.Response(200, json=[{"queue": {"name": "wuwa.release-media.v1"}, "channel_details": {"connection_name": "worker:test:1"}, "prefetch_count": 1}])
        return httpx.Response(200, json=[{"name": "other"}, {"name": "wuwa.release-media.v1", "messages_ready": 5, "messages_unacknowledged": 1, "consumers": 1}])
    real_client = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    result = await module.queue_status()
    assert result["available"]
    assert len(result["queues"]) == 1
    assert result["queues"][0]["ready"] == 5
    assert result["queues"][0]["workers"][0]["name"] == "worker:test:1"
    assert "private-password" not in str(result)
    assert all("%2F" in str(request.url) for request in requests)


@pytest.mark.asyncio
async def test_monitor_failure_means_unknown_counts(monkeypatch):
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(rabbitmq_url=SimpleNamespace(get_secret_value=lambda: "amqp://admin:private-password@rabbitmq/")))
    def handle(request):
        return httpx.Response(403)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    result = await module.queue_status()
    assert not result["available"]
    assert result["queues"] == []
    assert "unknown" in result["error"]
    assert "private-password" not in str(result)
