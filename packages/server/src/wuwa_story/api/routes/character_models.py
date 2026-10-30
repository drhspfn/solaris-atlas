from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.auth.dependencies import require_admin, require_csrf
from wuwa_story.db.models.ops import ProcessingRun, Processor
from wuwa_story.db.repositories.character_models import model_recipe, model_release
from wuwa_story.db.repositories.nodes import get_node
from wuwa_story.db.session import get_session
from wuwa_story.ingestion.character_models import (
    PROCESSOR,
    CharacterModelRequest,
    enqueue_character_model,
    latest_public_character_model,
    public_character_model,
)
from wuwa_story.ingestion.model_archives import CHUNK_BYTES
from wuwa_story.ingestion.model_uploads import (
    ModelUploadRequest,
    expire_uploads,
    finish_upload,
    start_upload,
    store_chunk,
)

router = APIRouter(tags=["character models"])


@router.get("/admin/character-models/recipe", dependencies=[Depends(require_admin)])
async def character_model_recipe(character_key: str, game_version: str | None = None,
                                 session: AsyncSession = Depends(get_session)):
    node = await get_node(session, character_key, ("character",))
    release = await model_release(session, game_version)
    if node is None or release is None:
        raise HTTPException(404, "Character or snapshot not found")
    try:
        recipe = await model_recipe(session, release.id, int(node.canonical_key.split(":")[1]))
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return {"format": "solaris-model-recipe-v1", "character_key": node.canonical_key,
            "game_version": release.game_version, **recipe}


@router.post("/admin/character-models/uploads", status_code=201,
             dependencies=[Depends(require_admin), Depends(require_csrf)])
async def create_model_upload(request: ModelUploadRequest, session: AsyncSession = Depends(get_session)):
    try:
        await expire_uploads(session)
        run = await start_upload(session, request)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return {"id": run.id, "status": run.status, "chunk_bytes": CHUNK_BYTES}


@router.put("/admin/character-models/uploads/{run_id}/parts/{index}",
            dependencies=[Depends(require_admin), Depends(require_csrf)])
async def receive_model_chunk(run_id: int, index: int, request: Request,
                              session: AsyncSession = Depends(get_session)):
    try:
        await store_chunk(session, run_id, index, request.stream())
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return {"id": run_id, "part": index}


@router.post("/admin/character-models/uploads/{run_id}/complete", status_code=202,
             dependencies=[Depends(require_admin), Depends(require_csrf)])
async def complete_model_upload(run_id: int, session: AsyncSession = Depends(get_session)):
    try:
        run = await finish_upload(session, run_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return {"id": run.id, "status": run.status}


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
    if game_version is None:
        return await latest_public_character_model(session, node.id)
    release = await model_release(session, game_version)
    result = await public_character_model(session, node.id, release)
    if release is None:
        result["game_version"] = game_version
    return result
