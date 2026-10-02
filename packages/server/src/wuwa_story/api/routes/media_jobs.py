from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.auth.dependencies import require_admin, require_csrf
from wuwa_story.db.models.ops import ProcessingRun, Processor
from wuwa_story.db.session import get_session
from wuwa_story.ingestion.entity_media import MediaRequest
from wuwa_story.ingestion.media_jobs import enqueue_entity_media

router = APIRouter(
    prefix="/admin/media-jobs", tags=["media jobs"], dependencies=[Depends(require_admin)]
)


@router.post("", status_code=202, dependencies=[Depends(require_csrf)])
async def create_media_job(request: MediaRequest, session: AsyncSession = Depends(get_session)):
    try:
        run = await enqueue_entity_media(session, request)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except Exception as error:
        raise HTTPException(503, "Media queue unavailable; repeat the request to retry") from error
    return {"id": run.id, "status": run.status}


@router.get("/{run_id}")
async def media_job_status(run_id: int, session: AsyncSession = Depends(get_session)):
    run = await session.get(ProcessingRun, run_id)
    processor = await session.get(Processor, run.processor_id) if run else None
    if run is None or processor is None or processor.key != "entity_media":
        raise HTTPException(404, "Media job not found")
    return {"id": run.id, "status": run.status, "result": run.raw_output, "error": run.error}
