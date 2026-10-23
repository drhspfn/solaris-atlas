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
@pytest.mark.parametrize('kind,queue',[('image','wuwa.entity-images.v1'),('voice','wuwa.entity-media.v1')])
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
