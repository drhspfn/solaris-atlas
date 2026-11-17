from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from wuwa_story_worker import notices


@pytest.mark.asyncio
@pytest.mark.parametrize("http_status", [200, 503])
async def test_notice_images_are_archived_and_failures_keep_the_text(
    monkeypatch, tmp_path, http_status
):
    source = "https://aki-gm-resources-back.aki-game.net/notice/image/test.png"
    body = b"\x89PNG\r\n\x1a\nimage-from-official-source"
    definition = tmp_path / "files" / "LauncherNoticeDefine.js"
    definition.parent.mkdir()
    definition.write_text("client constants", encoding="utf-8")
    notice = SimpleNamespace(
        id=5,
        revision="a" * 64,
        content={
            "raw": {"tabBanner": source},
            "title": "Actual game notice",
            "banner_url": source,
            "fold_banner_url": None,
            "blocks": [
                {"type": "text", "text": "Actual event rules"},
                {"type": "image", "url": source, "source_url": source},
            ],
        },
    )
    snapshot = SimpleNamespace(content={})
    session = AsyncMock()
    session.scalars.return_value = [notice]
    session.scalar.side_effect = [None, SimpleNamespace(object_key="objects/actual-image")]
    session.get.return_value = snapshot
    storage = Mock()
    storage.ensure_bucket = AsyncMock()
    storage.public_url.return_value = "https://media.solarisatlas.fun/objects/actual-image"
    registration = Mock()
    registration.register_file = AsyncMock(return_value=SimpleNamespace(id=2))
    registration.register_reference = AsyncMock()

    async def register_file(_, path, *args, **kwargs):
        assert path.read_bytes() == body
        return SimpleNamespace(id=2)

    registration.register_file.side_effect = register_file
    monkeypatch.setattr(notices, "export_assets", AsyncMock(return_value=tmp_path / "receipt.json"))
    monkeypatch.setattr(notices, "tool_path", lambda _: "fmodel")
    monkeypatch.setattr(notices, "fetch_notice_feed", AsyncMock(return_value=("feed", "page", {})))
    monkeypatch.setattr(notices, "store_notices", AsyncMock(return_value={"notices": 1}))
    monkeypatch.setattr(notices, "refresh_recurring_schedules", AsyncMock(return_value=({"updated": 0}, None)))
    monkeypatch.setattr(notices, "S3Storage", Mock(return_value=storage))
    monkeypatch.setattr(notices, "FileRegistrationService", Mock(return_value=registration))
    client = httpx.AsyncClient
    requests = []

    def respond(request):
        assert session.commit.await_count == 2  # Text and read snapshot are committed before HTTP.
        requests.append(str(request.url))
        return httpx.Response(http_status, content=body, headers={"content-type": "image/png"})

    monkeypatch.setattr(
        notices.httpx,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs),
    )

    report, status = await notices.import_notices(tmp_path, session)
    assert requests == [source]  # Cover and body share a single download.
    assert notice.content["blocks"][0]["text"] == "Actual event rules"
    assert session.commit.await_count == (4 if http_status == 200 else 3)
    if http_status == 200:
        assert status == "completed" and report["images"] == 1
        assert notice.content["banner_url"] == storage.public_url.return_value
        assert snapshot.content["blocks"][1]["url"] == storage.public_url.return_value
        assert snapshot.content["blocks"][1]["source_url"] == source
        requests.clear()
        await notices.import_notices(tmp_path, session)
        assert requests == []  # A refresh does not redownload already archived media.
    else:
        assert status == "partial" and report["missing"] == 1
        assert notice.content["banner_url"] == source
        registration.register_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_recurring_schedule_failure_is_reported_without_losing_notice_work(monkeypatch):
    session = AsyncMock()
    monkeypatch.setattr(notices, "import_recurring_schedules", AsyncMock(
        side_effect=httpx.ConnectError("upstream unavailable")))
    result, failure = await notices.refresh_recurring_schedules(session)
    assert result is None
    assert failure == {"expected": "recurring_schedule", "status": "recurring_schedule_failed",
                       "error": "ConnectError"}
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_notice_refresh_reports_calculated_and_superseded_cycles(monkeypatch):
    session = AsyncMock()
    importer = AsyncMock(return_value={"source": "community", "revision": "a" * 40,
        "occurrences": [{}], "calculated_cycles": [{}, {}], "superseded_cycle_ids": [12]})
    monkeypatch.setattr(notices, "import_recurring_schedules", importer)
    report, failure = await notices.refresh_recurring_schedules(session)
    assert failure is None and report == {"source": "community", "revision": "a" * 40,
                                          "updated": 1, "calculated": 2, "superseded": 1}
    importer.assert_awaited_once_with(session, apply=True)
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_recurring_failure_makes_notice_job_retryable(monkeypatch, tmp_path):
    definition = tmp_path / "files" / "LauncherNoticeDefine.js"
    definition.parent.mkdir()
    definition.write_text("constants", encoding="utf-8")
    session = AsyncMock()
    session.scalars.return_value = []
    storage = Mock(ensure_bucket=AsyncMock())
    monkeypatch.setattr(notices, "export_assets", AsyncMock(return_value=tmp_path / "receipt.json"))
    monkeypatch.setattr(notices, "tool_path", lambda _: "fmodel")
    monkeypatch.setattr(notices, "fetch_notice_feed", AsyncMock(return_value=("feed", "page", {})))
    monkeypatch.setattr(notices, "store_notices", AsyncMock(return_value={"notices": 1}))
    monkeypatch.setattr(notices, "S3Storage", Mock(return_value=storage))
    monkeypatch.setattr(notices, "FileRegistrationService", Mock())
    failure = {"expected": "recurring_schedule", "status": "recurring_schedule_failed", "error": "ConnectError"}
    monkeypatch.setattr(notices, "refresh_recurring_schedules", AsyncMock(return_value=(None, failure)))
    report, status = await notices.import_notices(tmp_path, session)
    assert status == "partial" and report["notices"] == 1 and report["missing"] == 1
    assert report["media_report"]["entries"] == [failure]
