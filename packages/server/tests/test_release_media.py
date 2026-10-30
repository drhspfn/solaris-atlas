from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.ingestion import release_media


@pytest.mark.asyncio
async def test_complete_story_repair_marks_index_for_ordered_media_followup(monkeypatch):
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(game_version='3.7.0')
    session.scalar.side_effect = [4, None, None]
    runs = []
    session.add = runs.append
    async def flush():
        runs[-1].id = len(runs)
    session.flush.side_effect = flush
    publish = AsyncMock()
    monkeypatch.setattr(release_media, 'publish_media_job', publish)
    await release_media.enqueue_release_media(session, 9, story_structure_only=True, repair_story=True)
    payloads = [call.args[0] for call in publish.await_args_list]
    assert [payload['kind'] for payload in payloads] == ['prepare', 'tree_order']
    assert payloads[1]['repair_story'] == 1
    session.scalars.assert_not_awaited()


@pytest.mark.asyncio
async def test_story_media_followup_keeps_images_and_movie_dependencies_without_catalog_or_voice_line_imports(monkeypatch):
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(game_version='3.7.0')
    session.scalars.side_effect = [['flow_state:Story'], ['C0028']]
    session.scalar.side_effect = [4] + [None] * 5
    runs = []
    session.add = runs.append
    async def flush():
        runs[-1].id = len(runs)
    session.flush.side_effect = flush
    publish = AsyncMock()
    monkeypatch.setattr(release_media, 'publish_media_job', publish)
    await release_media.enqueue_release_media(session, 9, story_media_only=True)
    payloads = [call.args[0] for call in publish.await_args_list]
    assert [p['kind'] for p in payloads] == ['prepare', 'cutscene_assets', 'voice_packages', 'images', 'cutscene']
    assert next(p for p in payloads if p['kind'] == 'images')['targets'] == ['flow_state:Story']
    assert session.scalars.await_count == 2


@pytest.mark.asyncio
async def test_repeating_complete_repair_resumes_media_after_an_already_completed_index(monkeypatch):
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(game_version='3.7.0')
    prepare = SimpleNamespace(id=10, status='completed')
    tree = SimpleNamespace(id=11, status='completed')
    session.scalar.side_effect = [4, prepare, tree, 4, prepare]
    session.scalars.side_effect = [[], []]
    publish = AsyncMock()
    monkeypatch.setattr(release_media, 'publish_media_job', publish)
    result = await release_media.enqueue_release_media(session, 9, story_structure_only=True, repair_story=True)
    assert result['queued'] == 0
    assert result['story_media']['queued'] == 0
    assert session.scalars.await_count == 2
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_story_repair_queues_only_index_and_reuses_normal_prepare(monkeypatch):
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(game_version='3.7.0')
    session.scalar.side_effect = [4, None, None]
    runs = []
    session.add = runs.append
    async def flush():
        runs[-1].id = len(runs)
    session.flush.side_effect = flush
    publish = AsyncMock()
    monkeypatch.setattr(release_media, 'publish_media_job', publish)
    await release_media.enqueue_release_media(session, 9, story_structure_only=True)
    payloads = [call.args[0] for call in publish.await_args_list]
    assert [payload['kind'] for payload in payloads] == ['prepare', 'tree_order']
    assert payloads[1]['index_schema'] == 3
    session.scalars.assert_not_awaited()


@pytest.mark.asyncio
async def test_illustration_repair_does_not_even_scan_audio_or_movie_tables(monkeypatch):
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(game_version='3.7.0')
    session.scalar.side_effect = [4, None, None]
    session.scalars.return_value = ['flow_state:Story_1_29']
    runs = []
    session.add = runs.append
    async def flush():
        runs[-1].id = len(runs)
    session.flush.side_effect = flush
    publish = AsyncMock()
    monkeypatch.setattr(release_media, 'publish_media_job', publish)
    await release_media.enqueue_release_media(session, 9, illustrations_only=True)
    payloads = [call.args[0] for call in publish.await_args_list]
    assert [payload['kind'] for payload in payloads] == ['prepare', 'images']
    assert payloads[1]['targets'] == ['flow_state:Story_1_29']
    assert payloads[1]['illustration_schema'] == 4
    query = str(session.scalars.call_args.args[0])
    assert 'jsonb_path_exists' in query
    assert 'action_name =' not in query
    session.scalars.assert_awaited_once()


@pytest.mark.asyncio
async def test_notice_refresh_does_not_queue_unrelated_asset_work(monkeypatch):
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(game_version="3.7.0")
    session.scalar.side_effect = [4, None, None]
    runs = []
    session.add = runs.append
    async def flush():
        runs[-1].id = len(runs)
    session.flush.side_effect = flush
    publish = AsyncMock()
    monkeypatch.setattr(release_media, "publish_media_job", publish)
    await release_media.enqueue_release_media(session, 9, notices_only=True)
    payloads = [call.args[0] for call in publish.await_args_list]
    assert [payload["kind"] for payload in payloads] == ["prepare", "event_notices"]
    assert payloads[1]["parent_id"] == payloads[0]["run_id"]
    assert all(call.args[2] == release_media.EVENT_QUEUE for call in publish.await_args_list)


@pytest.mark.asyncio
async def test_fanout_pins_dependencies_and_keeps_analysis_manual(monkeypatch):
    runs = []
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(game_version="1.0.0")
    session.scalars.side_effect = [["character:1", "skill:2"], ["M0389"], ["vo_quest_1"]]
    session.scalar.side_effect = [4] + [None] * 8
    session.add = lambda run: runs.append(run)

    async def flush():
        runs[-1].id = len(runs)
    session.flush.side_effect = flush
    published = AsyncMock()
    monkeypatch.setattr(release_media, "publish_media_job", published)
    result = await release_media.enqueue_release_media(session, 9)
    assert result["queued"] == 8
    payloads = [call.args[0] for call in published.await_args_list]
    by_kind = {payload["kind"]: payload for payload in payloads}
    assert by_kind["prepare"]["parent_id"] is None
    assert by_kind["tree_order"]["parent_id"] == by_kind["prepare"]["run_id"]
    assert by_kind["images"]["parent_id"] == by_kind["prepare"]["run_id"]
    assert by_kind["cutscene"]["parent_id"] == by_kind["cutscene_assets"]["run_id"]
    assert by_kind["voices"]["parent_id"] == by_kind["voice_packages"]["run_id"]
    assert all(p["release_id"] == 9 and p["game_version"] == "1.0.0" for p in payloads)
    assert all(call.args[2] == ("wuwa.release-images.v1" if call.args[0]["kind"] == "images" else "wuwa.release-media.v1") for call in published.await_args_list)
    assert session.commit.await_count == 1


@pytest.mark.asyncio
async def test_character_audio_is_published_in_independent_character_jobs(monkeypatch):
    runs = []
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(game_version="1.1.0")
    session.scalars.side_effect = [["character:1302", "character:1304"], [], []]
    session.scalar.side_effect = [4] + [None] * 6
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
    session.scalar.side_effect = [4, None, None]
    runs = []
    session.add = lambda run: runs.append(run)
    async def flush():
        runs[-1].id = 1
    session.flush.side_effect = flush
    monkeypatch.setattr(release_media, "publish_media_job", AsyncMock(side_effect=OSError("offline")))
    result = await release_media.enqueue_release_media(session, 9)
    assert result["enqueue_failed"] == 2
    assert runs[0].status == "enqueue_failed"
    assert session.commit.await_count == 3


@pytest.mark.asyncio
async def test_completed_tasks_are_reused_without_publication(monkeypatch):
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(game_version="1.0.0")
    session.scalars.side_effect = [[], [], []]
    session.scalar.side_effect = [4, SimpleNamespace(id=10, status="completed"), SimpleNamespace(id=11, status="completed")]
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
    session.scalar.side_effect = [4, SimpleNamespace(id=10, status="queued", metadata_json={}), SimpleNamespace(id=11, status="queued", metadata_json={})]
    publish = AsyncMock()
    monkeypatch.setattr(release_media, "publish_media_job", publish)
    result = await release_media.enqueue_release_media(session, 9)
    assert result["queued"] == 0
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleaned_workspace_reinitializes_prerequisites_and_rechecks_completed_files(monkeypatch, tmp_path):
    monkeypatch.setenv("WUWA_ASSET_WORKSPACE", str(tmp_path))
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(game_version="1.4.0")
    session.scalars.side_effect = [["character:1603"], [], []]
    prepare = SimpleNamespace(id=10, status="completed", metadata_json={},
                              raw_output={"workspace_id": 10, "asset_version": "3.7.0"})
    voice = SimpleNamespace(id=11, status="completed", metadata_json={}, raw_output={})
    images = SimpleNamespace(id=12, status="completed", metadata_json={}, raw_output={"entity_run_id": 90})
    character = SimpleNamespace(id=13, status="cancelled", metadata_json={}, raw_output=None)
    tree = SimpleNamespace(id=14, status="completed", metadata_json={}, raw_output={})
    session.scalar.side_effect = [4, prepare, voice, tree, images, character]
    publish = AsyncMock()
    monkeypatch.setattr(release_media, "publish_media_job", publish)
    result = await release_media.enqueue_release_media(session, 4)
    assert result["queued"] == 5
    assert all(call.args[0]["recheck_files"] for call in publish.await_args_list)
    assert all(run.status == "queued" for run in [prepare, voice, images, character])
    assert images.raw_output is None
    assert any(len(call.args) > 1 and call.args[1] == {"key": 10}
               for call in session.execute.await_args_list)


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
