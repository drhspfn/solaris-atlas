"""Administrator endpoints for reviewing registered cutscene video sources."""

from typing import Literal
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import RedirectResponse, Response
from pydantic import Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.api.routes.visual_memory import BoundedBodyRoute
from wuwa_story.auth.dependencies import require_admin, require_csrf
from wuwa_story.db.models.auth import User
from wuwa_story.db.session import get_session
from wuwa_story.visual_memory.tools import Input, Link

router = APIRouter(
    prefix="/admin/visual-memory/videos", tags=["visual memory video"],
    dependencies=[Depends(require_admin)], route_class=BoundedBodyRoute,
)
write = [Depends(require_csrf)]


class ScanInput(Input):
    asset_node_id: int = Field(gt=0, strict=True)
    asset_version: str | None = Field(
        default=None, min_length=1, max_length=64)
    game_version: str | None = Field(default=None, min_length=1, max_length=64)
    cutscene_key: str | None = Field(default=None, min_length=1, max_length=512)
    source_reference_id: int | None = Field(default=None, gt=0, strict=True)
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    interval_seconds: float = Field(default=3, ge=0.25, le=60, allow_inf_nan=False)
    frame_batch_size: int = Field(default=12, ge=1, le=48, strict=True)
    force: bool = Field(default=False, strict=True)


class ResolveInput(Input):
    decision: Literal["approve", "approved", "reject", "pending"]
    links: list[Link] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_links(self):
        if self.decision == "reject" and self.links:
            raise ValueError("Rejected observations cannot have links")
        if self.decision != "reject" and not self.links:
            raise ValueError(
                "Accepted observations require at least one identity link")
        return self


def service_for(session: AsyncSession):
    from wuwa_story.visual_memory.video import VideoReviewService

    return VideoReviewService(session)


async def call(session: AsyncSession, method: str, *args, mutate: bool = False, **kwargs):
    from wuwa_story.visual_memory.inference import VisualMemoryUnavailable

    try:
        result = await getattr(service_for(session), method)(*args, **kwargs)
        if mutate:
            await session.commit()
        return result
    except VisualMemoryUnavailable as exc:
        raise HTTPException(
            503, "Visual model unavailable; check model configuration") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/catalog")
async def catalog(q: str = Query("", max_length=128),
                  game_version: str | None = Query(None, max_length=64),
                  locale: str = Query("en", min_length=1, max_length=32),
                  limit: int = Query(12, ge=1, le=48),
                  offset: int = Query(0, ge=0, le=10000),
                  session: AsyncSession = Depends(get_session)):
    return await call(session, "catalog", q=q, game_version=game_version, locale=locale,
                      limit=limit, offset=offset)


@router.get("/sources")
async def sources(cutscene_key: str = Query(..., min_length=1, max_length=512),
                  game_version: str | None = Query(None, max_length=64),
                  locale: str = Query("en", min_length=1, max_length=32),
                  session: AsyncSession = Depends(get_session)):
    return await call(session, "sources", cg_name=cutscene_key, game_version=game_version, locale=locale)


def validated_media_url(url: str) -> str:
    """Redirect only to the configured public storage origin and path prefix."""
    from wuwa_story.config.settings import get_settings
    from wuwa_story.storage.s3 import S3Storage

    if not isinstance(url, str) or not url:
        raise HTTPException(422, "Published media URL is unavailable")
    base = urlsplit(S3Storage(get_settings()).public_base_url)
    target = urlsplit(url)
    path = unquote(target.path)
    for _ in range(2):
        path = unquote(path)
    prefix = unquote(base.path).rstrip("/") + "/"
    if (base.scheme not in {"http", "https"} or target.scheme != base.scheme
            or target.netloc != base.netloc or target.username or target.password
            or not path.startswith(prefix) or any(part in {".", ".."} for part in path.split("/"))
            or "\\" in path or any(ord(char) < 32 for char in url)
            or target.query or target.fragment):
        raise HTTPException(422, "Invalid published media location")
    return url


@router.get("/sources/{reference_id}/video")
async def source_video(reference_id: int = Path(gt=0),
                       game_version: str | None = Query(None, min_length=1, max_length=64),
                       asset_version: str | None = Query(None, min_length=1, max_length=64),
                       cutscene_key: str | None = Query(None, min_length=1, max_length=512),
                       session: AsyncSession = Depends(get_session)):
    context = {key: value for key, value in {"game_version": game_version, "asset_version": asset_version,
                                              "cutscene_key": cutscene_key}.items() if value is not None}
    source = await call(session, "source_by_reference", reference_id, **context)
    return RedirectResponse(validated_media_url(source["url"]), status_code=307,
                            headers={"Cache-Control": "private, no-store"})


@router.post("/scans", dependencies=write)
async def enqueue(body: ScanInput, session: AsyncSession = Depends(get_session)):
    data = body.model_dump()
    if data["cutscene_key"] is None:
        data.pop("cutscene_key")
    return await call(session, "enqueue", **data, mutate=True)


@router.get("/scans/{scan_id}")
async def get_scan(scan_id: int = Path(gt=0), frame_offset: int = Query(0, ge=0),
                   frame_limit: int = Query(12, ge=1, le=48),
                   session: AsyncSession = Depends(get_session)):
    return await call(session, "get_scan", scan_id, frame_offset=frame_offset, frame_limit=frame_limit)


@router.get("/scans/{scan_id}/frames/{frame_index}/image")
async def frame_image(scan_id: int = Path(gt=0), frame_index: int = Path(ge=0),
                      session: AsyncSession = Depends(get_session)):
    data = await call(session, "frame_bytes", scan_id, frame_index)
    if not data.startswith(b"\xff\xd8\xff"):
        raise HTTPException(415, "Stored frame is not a JPEG image")
    return Response(data, media_type="image/jpeg", headers={"X-Content-Type-Options": "nosniff",
                                                            "Cache-Control": "private, no-store"})


@router.post("/scans/{scan_id}/observations/{frame_index}/{detection_index}/resolve", dependencies=write)
async def resolve(body: ResolveInput, scan_id: int = Path(gt=0),
                  frame_index: int = Path(ge=0), detection_index: int = Path(ge=0, le=19),
                  user: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    data = body.model_dump()
    if data["decision"] == "approved":
        data["decision"] = "approve"
    return await call(session, "resolve", scan_id, frame_index, detection_index,
                      **data, actor_id=user.id, mutate=True)


@router.post("/scans/{scan_id}/reject-all", dependencies=write)
async def reject_all(scan_id: int = Path(gt=0), user: User = Depends(require_admin),
                     session: AsyncSession = Depends(get_session)):
    return await call(session, "reject_all", scan_id, actor_id=user.id, mutate=True)


@router.post("/scans/{scan_id}/reviewed", dependencies=write)
async def reviewed(scan_id: int = Path(gt=0), user: User = Depends(require_admin),
                   session: AsyncSession = Depends(get_session)):
    return await call(session, "mark_reviewed", scan_id, actor_id=user.id, mutate=True)
