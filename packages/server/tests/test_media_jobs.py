from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from wuwa_story.api.routes.media_jobs import create_media_job, router
from wuwa_story.auth.dependencies import require_admin, require_csrf
from wuwa_story.ingestion.entity_media import MediaRequest


def request():
    return MediaRequest(
        download_id="a" * 64,
        asset_version="3.7.0",
        game_version="1.1.0",
        entities=["character:1205"],
    )


def test_jobs_require_admin_and_csrf():
    post = next(route for route in router.routes if "POST" in route.methods)
    assert require_admin in [d.call for d in post.dependant.dependencies]
    assert require_csrf in [d.call for d in post.dependant.dependencies]


def test_download_job_identity_and_kind_are_bounded():
    with pytest.raises(ValidationError):
        request().model_validate({**request().model_dump(), "download_id": "../../keys"})
    with pytest.raises(ValidationError):
        request().model_validate({**request().model_dump(), "kinds": ["arbitrary_command"]})


@pytest.mark.asyncio
async def test_unconfirmed_broker_publish_is_not_success(monkeypatch):
    monkeypatch.setattr(
        "wuwa_story.api.routes.media_jobs.enqueue_entity_media",
        AsyncMock(side_effect=OSError("offline")),
    )
    with pytest.raises(HTTPException) as error:
        await create_media_job(request(), AsyncMock())
    assert error.value.status_code == 503

@pytest.mark.asyncio
@pytest.mark.parametrize('kind,queue',[('image','wuwa.entity-images.v1'),('spine','wuwa.entity-images.v1'),('voice','wuwa.entity-media.v1')])
async def test_images_have_slots_independent_of_long_audio_jobs(monkeypatch, kind, queue):
    from unittest.mock import Mock

    from wuwa_story.ingestion import media_jobs
    db=AsyncMock()
    db.scalar.side_effect=[1,None]
    db.add=Mock(side_effect=lambda run:setattr(run,'id',100))
    monkeypatch.setattr(media_jobs,'entity_media_targets',AsyncMock(return_value=[{'kind':kind}]))
    publish=AsyncMock()
    monkeypatch.setattr(media_jobs,'publish_media_job',publish)
    run=await media_jobs.enqueue_entity_media(db,request())
    assert publish.await_args.args[2]==queue
    assert run.metadata_json['queue']==queue


@pytest.mark.asyncio
async def test_completed_job_is_rechecked_and_workspace_membership_is_durable(monkeypatch):
    from types import SimpleNamespace

    from wuwa_story.ingestion import media_jobs

    db = AsyncMock()
    existing = SimpleNamespace(id=100, status="completed")
    db.scalar.side_effect = [1, existing]
    monkeypatch.setattr(media_jobs, "entity_media_targets", AsyncMock(return_value=[{"kind": "image"}]))
    publish = AsyncMock()
    monkeypatch.setattr(media_jobs, "publish_media_job", publish)
    value = request().model_copy(update={"workspace_id": 4})
    run = await media_jobs.enqueue_entity_media(db, value)
    assert run is existing and run.status == "queued"
    assert run.metadata_json["request"]["workspace_id"] == 4
    assert db.execute.await_args_list[0].args[1] == {"key": 4}
    assert "pg_advisory_xact_lock_shared(706" in str(db.execute.await_args_list[0].args[0])
    publish.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['queued', 'waiting_dependency', 'running'])
async def test_explicit_recovery_redelivers_same_job_without_resetting_state(monkeypatch, status):
    from types import SimpleNamespace
    from wuwa_story.ingestion import media_jobs

    payload = {'run_id': 100, 'pinned': True}
    existing = SimpleNamespace(id=100, status=status,
        metadata_json={'payload': payload, 'queue': 'wuwa.entity-images.v1'})
    db = AsyncMock()
    db.scalar.side_effect = [1, existing]
    monkeypatch.setattr(media_jobs, 'entity_media_targets', AsyncMock(return_value=[{'kind': 'spine'}]))
    publish = AsyncMock()
    monkeypatch.setattr(media_jobs, 'publish_media_job', publish)
    result = await media_jobs.enqueue_entity_media(db, request(), republish_queued=True)
    assert result is existing and result.status == status
    if status == 'running':
        publish.assert_not_awaited()
    else:
        publish.assert_awaited_once()
        assert publish.await_args.args[0] is payload
        assert publish.await_args.args[2] == 'wuwa.entity-images.v1'


@pytest.mark.asyncio
async def test_illustrated_rebuild_has_isolated_durable_routing_and_identity(monkeypatch):
    from unittest.mock import Mock
    from wuwa_story.ingestion import media_jobs
    value = request().model_copy(update={"kinds": ["image"]})
    monkeypatch.setattr(media_jobs, "entity_media_targets", AsyncMock(return_value=[{"kind": "spine"}]))
    publish = AsyncMock()
    monkeypatch.setattr(media_jobs, "publish_media_job", publish)
    hashes = []
    for queue in (None, "wuwa.illustrated-backgrounds.v1"):
        db = AsyncMock()
        db.scalar.side_effect = [1, None]
        db.add = Mock(side_effect=lambda run: setattr(run, "id", 100))
        run = await media_jobs.enqueue_entity_media(db, value, execution_queue=queue)
        hashes.append(run.input_hash)
        assert run.metadata_json["queue"] == (queue or "wuwa.entity-images.v1")
        assert publish.await_args.args[2] == run.metadata_json["queue"]
    assert hashes[0] != hashes[1]


@pytest.mark.asyncio
async def test_isolated_image_queue_rejects_voice_requests_before_db_access():
    from wuwa_story.ingestion import media_jobs
    db = AsyncMock()
    with pytest.raises(ValueError, match="image requests only"):
        await media_jobs.enqueue_entity_media(db, request(), execution_queue="wuwa.illustrated-backgrounds.v1")
    db.execute.assert_not_awaited()
