from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import release_media


@pytest.mark.asyncio
async def test_prepare_records_running_before_client_discovery(monkeypatch):
    events = []
    async def update(*args, **kwargs):
        events.append((args, kwargs))
    def inspect(*args):
        assert events[0][0] == (4, "release_media", "running")
        assert events[0][1]["raw_output"]["stage"] == "client_discovery"
        raise RuntimeError("discovery fixture")
    monkeypatch.setattr(release_media, "update_admin_run", update)
    monkeypatch.setattr(release_media, "inspect_installed_clients", inspect)
    with pytest.raises(RuntimeError, match="discovery fixture"):
        await release_media.prepare(SimpleNamespace(id=4, metadata_json={}), AsyncMock())
