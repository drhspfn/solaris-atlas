"""Administrator controls for the existing, checkpointed visual worker."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.agents.contracts import StrictModel
from wuwa_story.agents.cutscene_vision import enqueue_visual_job, resume_visual_job
from wuwa_story.agents.settings import get_agent_settings
from wuwa_story.auth.dependencies import require_admin, require_csrf
from wuwa_story.db.models.graph import Node
from wuwa_story.db.models.ops import ProcessingRun, Processor
from wuwa_story.db.models.storage import FileReference
from wuwa_story.db.session import get_session

router = APIRouter(prefix="/cutscenes", dependencies=[Depends(require_admin)])


class VisualRequest(StrictModel):
    asset_node_ids: list[int] = Field(min_length=1, max_length=20)
    game_version: str = Field(min_length=1, max_length=64)


class VisualResumeRequest(StrictModel):
    output_tokens: int | None = Field(default=None, ge=128, le=16000)


def public_job(run: ProcessingRun) -> dict[str, Any]:
    checkpoint = run.raw_output or {}
    batches = checkpoint.get("batches", [])
    return {
        "id": run.id,
        "asset_node_id": run.target_node_id,
        "game_version": run.metadata_json.get("asset_version"),
        "status": run.status,
        "step": checkpoint.get("step", 0),
        "frames_done": sum(len(batch.get("events", [])) for batch in batches),
        "frames_remaining": sum(len(batch) for batch in checkpoint.get("pending", [])),
        "error": run.error,
        "tokens_input": run.tokens_input,
        "tokens_output": run.tokens_output,
        "cost_usd": run.cost,
        "output_tokens": run.metadata_json.get("settings", {}).get("vision_output_tokens"),
    }


@router.get("/assets")
async def assets(
    q: str = Query("", max_length=128),
    before: int | None = None,
    limit: int = Query(30, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    latest = (
        select(FileReference.id)
        .where(
            FileReference.reference_type == "cutscene_video",
            FileReference.owner_node_id.is_not(None),
        )
        .distinct(FileReference.owner_node_id, FileReference.metadata_json["asset_version"].astext)
        .order_by(
            FileReference.owner_node_id,
            FileReference.metadata_json["asset_version"].astext,
            FileReference.id.desc(),
        )
    )
    statement = (
        select(FileReference, Node.canonical_key)
        .join(Node, Node.id == FileReference.owner_node_id)
        .where(FileReference.id.in_(latest))
        .order_by(FileReference.id.desc())
        .limit(limit)
    )
    if before is not None:
        statement = statement.where(FileReference.id < before)
    if q:
        statement = statement.where(Node.canonical_key.icontains(q, autoescape=True))
    rows = (await session.execute(statement)).all()
    return {
        "enabled": get_agent_settings().vision_enabled,
        "assets": [
            {
                "id": ref.owner_node_id,
                "reference": key,
                "game_version": ref.metadata_json["asset_version"],
            }
            for ref, key in rows
        ],
        "next_before": rows[-1][0].id if len(rows) == limit else None,
    }


@router.post("/jobs", status_code=202, dependencies=[Depends(require_csrf)])
async def create_visual_jobs(request: VisualRequest, session: AsyncSession = Depends(get_session)):
    settings = get_agent_settings()
    if not settings.vision_enabled:
        raise HTTPException(409, "Enable AGENT_VISION_ENABLED on the API and visual worker")
    ids = set(request.asset_node_ids)
    if any(asset_id <= 0 for asset_id in ids):
        raise HTTPException(422, "Asset IDs must be positive")
    references = list(
        await session.scalars(
            select(FileReference)
            .where(
                FileReference.reference_type == "cutscene_video",
                FileReference.owner_node_id.in_(ids),
                FileReference.metadata_json["asset_version"].astext == request.game_version,
            )
            .distinct(FileReference.owner_node_id)
            .order_by(FileReference.owner_node_id, FileReference.id.desc())
        )
    )
    if {ref.owner_node_id for ref in references} != ids:
        raise HTTPException(404, "An imported video is missing for the selected version")
    try:
        runs = [await enqueue_visual_job(session, ref, settings) for ref in references]
        await session.commit()
    except ValueError as error:
        await session.rollback()
        raise HTTPException(422, str(error)) from error
    return {"jobs": [public_job(run) for run in runs]}


@router.get("/jobs")
async def visual_jobs(
    before: int | None = None,
    limit: int = Query(30, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    statement = (
        select(ProcessingRun, Node.canonical_key)
        .join(Processor, Processor.id == ProcessingRun.processor_id)
        .outerjoin(Node, Node.id == ProcessingRun.target_node_id)
        .where(Processor.key == "cutscene_vision")
        .order_by(ProcessingRun.id.desc())
        .limit(limit)
    )
    if before is not None:
        statement = statement.where(ProcessingRun.id < before)
    rows = (await session.execute(statement)).all()
    return {
        "jobs": [{**public_job(run), "reference": key} for run, key in rows],
        "next_before": rows[-1][0].id if len(rows) == limit else None,
    }


@router.post("/jobs/{run_id}/resume", dependencies=[Depends(require_csrf)])
async def resume_visual(
    run_id: int, request: VisualResumeRequest, session: AsyncSession = Depends(get_session)
):
    try:
        run = await resume_visual_job(session, run_id, request.output_tokens)
        await session.commit()
    except ValueError as error:
        await session.rollback()
        raise HTTPException(422, str(error)) from error
    return public_job(run)
