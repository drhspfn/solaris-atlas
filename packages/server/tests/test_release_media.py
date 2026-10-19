from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.ingestion import release_media


@pytest.mark.asyncio
async def test_fanout_pins_dependencies_and_keeps_analysis_manual(monkeypatch):
    runs = []
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(game_version="1.0.0")
    session.scalars.side_effect = [["character:1", "skill:2"], ["M0389"], ["vo_quest_1"]]
    session.scalar.side_effect = [4] + [None] * 7
    session.add = lambda run: runs.append(run)

    async def flush():
        runs[-1].id = len(runs)
    session.flush.side_effect = flush
    published = AsyncMock()
    monkeypatch.setattr(release_media, "publish_media_job", published)
    result = await release_media.enqueue_release_media(session, 9)
    assert result["queued"] == 7
    payloads = [call.args[0] for call in published.await_args_list]
    by_kind = {payload["kind"]: payload for payload in payloads}
    assert by_kind["prepare"]["parent_id"] is None
    assert by_kind["images"]["parent_id"] == by_kind["prepare"]["run_id"]
    assert by_kind["cutscene"]["parent_id"] == by_kind["cutscene_assets"]["run_id"]
    assert by_kind["voices"]["parent_id"] == by_kind["voice_packages"]["run_id"]
    assert all(p["release_id"] == 9 and p["game_version"] == "1.0.0" for p in payloads)
    assert all(call.args[2] == "wuwa.release-media.v1" for call in published.await_args_list)
    assert session.commit.await_count == 1


@pytest.mark.asyncio
async def test_character_audio_is_published_in_independent_character_jobs(monkeypatch):
    runs = []
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(game_version="1.1.0")
    session.scalars.side_effect = [["character:1302", "character:1304"], [], []]
    session.scalar.side_effect = [4] + [None] * 5
    session.add = runs.append
    async def flush():
        runs[-1].id = len(runs)
    session.flush.side_effect = flush
    published = AsyncMock()
    monkeypatch.setattr(release_media, "publish_media_job", published)
    await release_media.enqueue_release_media(session, 2)
    payloads = [call.args[0] for call in published.await_args_list]
    characters = [payload for payload in payloads if payload["kind"] == "character_voices"]
    assert [job["targets"] for job in characters] == [["character:1302"], ["character:1304"]]
    voice_packages = next(p for p in payloads if p["kind"] == "voice_packages")
    assert all(job["parent_id"] == voice_packages["run_id"] for job in characters)


@pytest.mark.asyncio
async def test_unconfirmed_media_publication_is_recorded_for_retry(monkeypatch):
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(game_version="1.0.0")
    session.scalars.side_effect = [[], [], []]
    session.scalar.side_effect = [4, None]
    runs = []
    session.add = lambda run: runs.append(run)
    async def flush():
        runs[-1].id = 1
    session.flush.side_effect = flush
    monkeypatch.setattr(release_media, "publish_media_job", AsyncMock(side_effect=OSError("offline")))
    result = await release_media.enqueue_release_media(session, 9)
    assert result["enqueue_failed"] == 1
    assert runs[0].status == "enqueue_failed"
    assert session.commit.await_count == 2


@pytest.mark.asyncio
async def test_completed_tasks_are_reused_without_publication(monkeypatch):
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(game_version="1.0.0")
    session.scalars.side_effect = [[], [], []]
    session.scalar.side_effect = [4, SimpleNamespace(id=10, status="completed")]
    publish = AsyncMock()
    monkeypatch.setattr(release_media, "publish_media_job", publish)
    result = await release_media.enqueue_release_media(session, 9)
    assert result["parent_id"] == 10
    assert result["queued"] == 0
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_queued_import_is_not_published_again(monkeypatch):
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(game_version="1.0.0")
    session.scalars.side_effect = [[], [], []]
    session.scalar.side_effect = [4, SimpleNamespace(id=10, status="queued", metadata_json={})]
    publish = AsyncMock()
    monkeypatch.setattr(release_media, "publish_media_job", publish)
    result = await release_media.enqueue_release_media(session, 9)
    assert result["queued"] == 0
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_event_artwork_import_queues_extraction_after_client_preparation(monkeypatch):
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(game_version="1.0.0")
    session.scalars.return_value = ["1:100087@revision", "2:100700044@revision"]
    session.scalar.side_effect = [4, None, None, None]
    runs = []
    session.add = lambda run: runs.append(run)

    async def flush():
        runs[-1].id = len(runs)

    session.flush.side_effect = flush
    published = AsyncMock()
    monkeypatch.setattr(release_media, "publish_media_job", published)

    result = await release_media.enqueue_release_media(session, 9, event_artwork_only=True)

    payloads = [call.args[0] for call in published.await_args_list]
    by_kind = {payload["kind"]: payload for payload in payloads}
    assert result["queued"] == 2
    assert by_kind["event_catalog"]["targets"] == ["client-catalog:v2"]
    assert "event_images" not in by_kind
    assert by_kind["event_catalog"]["parent_id"] == by_kind["prepare"]["run_id"]
    assert [call.args[2] for call in published.await_args_list] == [
        release_media.EVENT_QUEUE,
        release_media.EVENT_QUEUE,
    ]


@pytest.mark.asyncio
async def test_event_image_fanout_is_bounded_and_retries_only_incomplete_tasks(monkeypatch):
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(id=8, processor_id=4)
    completed = SimpleNamespace(id=9, status="completed")
    failed = SimpleNamespace(id=10, status="partial")
    session.scalar.side_effect = [completed, failed]
    publish = AsyncMock()
    monkeypatch.setattr(release_media, "publish_media_job", publish)
    result = await release_media.enqueue_event_images(session,
        {"run_id": 8, "release_id": 1, "game_version": "3.7"},
        [f"/Game/Image/{index:03d}" for index in range(21)])
    assert result == {"tasks": 2, "queued": 1}
    assert publish.await_count == 1
    job = publish.await_args.args[0]
    assert job["parent_id"] == 8 and len(job["targets"]) == 1
    assert failed.raw_output is None and failed.status == "queued"
