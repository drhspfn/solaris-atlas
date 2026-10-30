"""Administrative visibility and explicit rebuilds of durable public projections."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import case, func, select

from wuwa_story.auth.dependencies import require_admin, require_csrf
from wuwa_story.db.models.ops import GameRelease, ImportRun
from wuwa_story.db.models.read_refresh import (
    READ_PROJECTION_KINDS,
    ReadProjectionDirty,
    ReadProjectionScope,
)
from wuwa_story.db.session import get_session
from wuwa_story.read_models.policy import HISTORICAL_KINDS
from wuwa_story.read_models.refresh import mark_dirty

router = APIRouter(
    prefix="/admin/read-projections",
    tags=["data operations"],
    dependencies=[Depends(require_admin)],
)


class RebuildProjection(BaseModel):
    kinds: list[str] = Field(min_length=1, max_length=20)
    snapshot_key: str = Field(default="current", pattern=r"^(current|\d+\.\d+\.\d+)$")

    @field_validator("kinds")
    @classmethod
    def known_kinds(cls, value):
        if not set(value) <= set(READ_PROJECTION_KINDS):
            raise ValueError("Unknown public projection kind")
        return sorted(set(value))


@router.get("")
async def projection_status(session=Depends(get_session)):
    scopes = list(
        await session.scalars(
            select(ReadProjectionScope).order_by(
                ReadProjectionScope.kind,
                ReadProjectionScope.snapshot_key,
            )
        )
    )
    dirty = ReadProjectionDirty
    rows = await session.execute(
        select(
            dirty.kind,
            dirty.snapshot_key,
            func.count()
            .filter(dirty.requested_generation > dirty.processed_generation)
            .label("pending"),
            func.count().filter(dirty.lease_until > func.clock_timestamp()).label("leased"),
            func.sum(case((dirty.last_error.is_not(None), 1), else_=0)).label("failed"),
        ).group_by(dirty.kind, dirty.snapshot_key)
    )
    return {
        "scopes": [
            {
                "kind": scope.kind,
                "snapshot_key": scope.snapshot_key,
                "status": scope.status,
                "generation": scope.generation,
                "published_generation": scope.published_generation,
                "published_at": scope.published_at,
            }
            for scope in scopes
        ],
        "work": [dict(row) for row in rows.mappings()],
    }


@router.post("/rebuild", dependencies=[Depends(require_csrf)])
async def rebuild_projection(payload: RebuildProjection, session=Depends(get_session)):
    if payload.snapshot_key != "current":
        if not set(payload.kinds) <= HISTORICAL_KINDS:
            raise HTTPException(400, "Selected projection does not support historical snapshots")
        release = await session.scalar(
            select(GameRelease.id)
            .join(ImportRun, ImportRun.release_id == GameRelease.id)
            .where(
                GameRelease.game_version == payload.snapshot_key,
                ImportRun.status == "succeeded",
            )
            .limit(1)
        )
        if release is None:
            raise HTTPException(404, "Game version has not been imported")
    for kind in payload.kinds:
        await mark_dirty(session, kind, [0], payload.snapshot_key)
    await session.commit()
    return {"queued_scopes": len(payload.kinds), "snapshot_key": payload.snapshot_key}
