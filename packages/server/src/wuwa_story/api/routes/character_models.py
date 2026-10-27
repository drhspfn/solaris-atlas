from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.auth.dependencies import require_admin, require_csrf
from wuwa_story.db.models.ops import ProcessingRun, Processor
from wuwa_story.db.repositories.character_models import model_release
from wuwa_story.db.repositories.nodes import get_node
from wuwa_story.db.session import get_session
from wuwa_story.ingestion.character_models import (
    PROCESSOR,
    CharacterModelRequest,
    enqueue_character_model,
    public_character_model,
)

router = APIRouter(tags=["character models"])


@router.post(
    "/admin/character-models",
    status_code=202,
    dependencies=[Depends(require_admin), Depends(require_csrf)],
)
async def create_character_model(
    request: CharacterModelRequest, session: AsyncSession = Depends(get_session)
):
    try:
        run = await enqueue_character_model(session, request)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except Exception as error:
        raise HTTPException(503, "Character model queue unavailable; retry import") from error
    return {"id": run.id, "status": run.status}


@router.get("/admin/character-models/{run_id}", dependencies=[Depends(require_admin)])
async def character_model_status(run_id: int, session: AsyncSession = Depends(get_session)):
    run = await session.get(ProcessingRun, run_id)
    processor = await session.get(Processor, run.processor_id) if run else None
    if processor is None or processor.key != PROCESSOR:
        raise HTTPException(404, "Character model job not found")
    return {"id": run.id, "status": run.status, "result": run.raw_output, "error": run.error}


@router.get("/characters/{key:path}/model")
async def character_model(
    key: str, game_version: str | None = None, session: AsyncSession = Depends(get_session)
):
    node = await get_node(session, key, ("character",))
    if node is None:
        raise HTTPException(404, "Character not found")
    release = await model_release(session, game_version)
    result = await public_character_model(session, node.id, release)
    if release is None:
        result["game_version"] = game_version
    return result
