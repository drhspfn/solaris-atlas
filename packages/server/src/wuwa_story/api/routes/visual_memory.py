"""Administrator API for the first visual-memory workflow."""

import importlib.util
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from fastapi.routing import APIRoute
from pydantic import Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.auth.dependencies import require_admin, require_csrf
from wuwa_story.db.models.auth import User
from wuwa_story.db.session import get_session
from wuwa_story.visual_memory.tools import (
    MAX_ENCODED,
    IdentifyInput,
    Input,
    LinksInput,
    ReferenceInput,
    ReindexInput,
    RemoveInput,
    ReviewInput,
    SearchInput,
    VisualMemoryTools,
    service_for,
)


class BoundedBodyRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request: Request):
            if request.method in {"POST", "PATCH"}:
                chunks = []
                size = 0
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_ENCODED + 65536:
                        raise HTTPException(413, "Request body too large")
                    chunks.append(chunk)
                request._body = b"".join(chunks)
            return await handler(request)

        return bounded


router = APIRouter(prefix="/admin/visual-memory", tags=["visual memory"],
                   dependencies=[Depends(require_admin)], route_class=BoundedBodyRoute)
write = [Depends(require_csrf)]


class IdentityInput(Input):
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=100)
    node_ids: list[int] = Field(default_factory=list, max_length=100)
    lore_id: int | None = Field(default=None, gt=0)
    description: str = Field(default="", max_length=4000)


class GameEntityInput(Input):
    node_id: int = Field(gt=0, strict=True)


class NameInput(Input):
    name: str = Field(min_length=1, max_length=200)


class IdentityUpdate(Input):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    kind: str | None = Field(default=None, min_length=1, max_length=100)
    node_ids: list[int] | None = Field(default=None, max_length=100)
    lore_id: int | None = Field(default=None, gt=0)
    description: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def reject_null_fields(self):
        if any(getattr(self, name) is None for name in self.model_fields_set - {"lore_id"}):
            raise ValueError("Only lore_id can be null")
        return self


async def call(session: AsyncSession, method: str, *args: Any, mutate: bool = False, **kwargs: Any):
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
    except IntegrityError as exc:
        if method == "create_appearance" and getattr(exc.orig, "sqlstate", None) == "23505":
            raise HTTPException(409, "Appearance already exists for this identity") from exc
        raise


async def tool_call(session: AsyncSession, user: User, name: str, data: dict, *, mutate: bool = False):
    from wuwa_story.visual_memory.inference import VisualMemoryUnavailable

    try:
        result = await VisualMemoryTools(service_for(session), allow_write=mutate, allow_moderation=mutate, actor_id=user.id).execute(name, data)
        if mutate:
            await session.commit()
        return result
    except VisualMemoryUnavailable as exc:
        raise HTTPException(
            503, "Visual model unavailable; check model configuration") from exc
    except ValueError as exc:
        raise HTTPException(422, "Invalid visual memory input") from exc


@router.get("/models")
async def models():
    def check(variable: str, modules: list[str], files: list[str]) -> dict:
        path = os.environ.get(variable)
        missing = [
            name for name in modules if importlib.util.find_spec(name) is None]
        if missing:
            return {"available": False, "reason": "Missing dependencies: " + ", ".join(missing)}
        if not path:
            return {"available": False, "reason": f"{variable} is not configured"}
        root = Path(path)
        if files and (not all((root / name).is_file() for name in files) or not any((root / name).is_file() for name in ("model.safetensors", "pytorch_model.bin"))):
            return {"available": False, "reason": "Local model files are missing"}
        if not files and not root.is_file():
            return {"available": False, "reason": "Local detector file is missing"}
        return {"available": None, "reason": "Configured; model loading has not been verified"}
    return {"embedding": check("VISUAL_MEMORY_DINO_PATH", ["torch", "transformers", "PIL"], ["config.json", "preprocessor_config.json"]),
            "detection": check("VISUAL_MEMORY_DETECTOR_PATH", ["onnxruntime", "cv2", "numpy", "PIL"], [])}


@router.get("/identities")
async def identities(q: str = Query("", max_length=200), kind: str | None = Query(None, max_length=100), limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=10000), session: AsyncSession = Depends(get_session)):
    return await call(session, "list_identities", q=q, kind=kind, limit=limit, offset=offset)


@router.get("/game-entities")
async def game_entities(q: str = Query("", max_length=128),
                        locale: str = Query("en", min_length=1, max_length=32),
                        limit: int = Query(20, ge=1, le=48),
                        offset: int = Query(0, ge=0, le=10000),
                        session: AsyncSession = Depends(get_session)):
    from wuwa_story.api.routes.search import search
    from wuwa_story.api.routes.story.catalog import browse_catalog

    if q.strip():
        result = await search(q=q.strip(), category=["character"], scope="entities", locale=locale,
                              game_version=None, locale_id=None, sort_by="relevance", sort_order="desc",
                              limit=limit + 1, offset=offset, character=None, character_kind="all",
                              quest_id=None, session=session)
        rows = result["results"]
        has_more = len(rows) > limit
        rows = rows[:limit]
        # Search has no count query; never invent a total for a partial page.
        total = offset + len(rows) if not has_more and (rows or offset == 0) else None
    else:
        result = await browse_catalog(category="character", quest_type_id=None, locale=locale,
                                      character_kind="all", limit=limit, offset=offset, session=session)
        rows, total = result["results"], result["total"]
        has_more = offset + len(rows) < total
    return {"items": [{"node_id": row["id"], "name": row.get("title") or row.get("alias"),
                       "kind": row.get("character_kind") or "character", "image_url": row.get("image_url"),
                       "canonical_key": row.get("canonical_key")} for row in rows],
            "total": total, "has_more": has_more, "limit": limit, "offset": offset}


@router.post("/identities/from-game", dependencies=write)
async def identity_from_game(body: GameEntityInput, locale: str = Query("en", min_length=1, max_length=32),
                             session: AsyncSession = Depends(get_session)):
    from wuwa_story.db.models.core import Character
    from wuwa_story.db.models.graph import Node, NodeType
    from wuwa_story.db.models.visual_memory import VisualIdentityNode
    from wuwa_story.ingestion.character_identity import character_kind
    from wuwa_story.story.service import _node_label

    character = await session.scalar(select(Character).join(Node, Node.id == Character.node_id)
                                     .join(NodeType, NodeType.id == Node.type_id)
                                     .where(Character.node_id == body.node_id, NodeType.key == "character"))
    if character is None:
        raise HTTPException(404, "Game character not found")
    # Two administrators selecting the same node must not create duplicate links.
    await session.execute(select(func.pg_advisory_xact_lock(
        func.hashtextextended(f"visual-identity-node:{body.node_id}", 0))))
    ids = list(await session.scalars(select(VisualIdentityNode.identity_id)
                                     .where(VisualIdentityNode.node_id == body.node_id).order_by(VisualIdentityNode.identity_id)))
    if len(ids) > 1:
        raise HTTPException(422, "Several visual identities link this entity; choose an existing identity")
    service = service_for(session)
    if ids:
        return await service._identity_dict(await service._identity(ids[0]))
    label = await _node_label(session, body.node_id, locale)
    name = label.get("label") or character.canonical_name
    if not name:
        raise HTTPException(422, "Game entity has no localized display name")
    kind = character_kind((character.metadata_json or {}).get("role_type"))
    try:
        identity = await service.create_identity(name=name, kind=kind, node_ids=[body.node_id])
        await session.commit()
        return identity
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/identities", dependencies=write)
async def create_identity(body: IdentityInput, session: AsyncSession = Depends(get_session)):
    return await call(session, "create_identity", **body.model_dump(), mutate=True)


@router.patch("/identities/{identity_id}", dependencies=write)
async def update_identity(identity_id: int, body: IdentityUpdate, session: AsyncSession = Depends(get_session)):
    return await call(session, "update_identity", identity_id, **body.model_dump(exclude_unset=True), mutate=True)


@router.get("/lore-identities")
async def list_lore_identities(session: AsyncSession = Depends(get_session)):
    return await call(session, "list_lore_identities")


@router.post("/lore-identities", dependencies=write)
async def create_lore(body: NameInput, session: AsyncSession = Depends(get_session)):
    return await call(session, "create_lore_identity", name=body.name, mutate=True)


@router.post("/identities/{identity_id}/appearances", dependencies=write)
async def create_appearance(identity_id: int, body: NameInput, session: AsyncSession = Depends(get_session)):
    return await call(session, "create_appearance", identity_id, body.name, mutate=True)


@router.get("/identities/{identity_id}/appearances")
async def list_appearances(identity_id: int, session: AsyncSession = Depends(get_session)):
    return await call(session, "list_appearances", identity_id)


@router.get("/references")
async def references(q: str = Query("", max_length=200), kind: str | None = Query(None, max_length=100), status: str | None = Query(None, max_length=30), model_key: str | None = Query(None, max_length=200), source_type: str | None = Query(None, max_length=100), quality_min: float | None = Query(None, ge=0, le=1, allow_inf_nan=False), limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=10000), session: AsyncSession = Depends(get_session)):
    return await call(session, "list_references", q=q, kind=kind, status=status, model_key=model_key, source_type=source_type, quality_min=quality_min, limit=limit, offset=offset)


@router.post("/references/reindex", dependencies=write)
async def reindex(body: ReindexInput, user: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    return await tool_call(session, user, "visual_reindex", body.model_dump(), mutate=True)


@router.get("/references/{reference_id}")
async def reference(reference_id: int, session: AsyncSession = Depends(get_session)):
    return await call(session, "get_reference", reference_id)


@router.get("/references/{reference_id}/history")
async def review_history(reference_id: int, session: AsyncSession = Depends(get_session)):
    return await call(session, "get_review_history", reference_id)


@router.get("/references/{reference_id}/image")
async def reference_image(reference_id: int, original: bool = False, session: AsyncSession = Depends(get_session)):
    data = await call(session, "get_image_bytes", reference_id, original=original)
    # Let browsers decode only standard image formats; never execute returned bytes as HTML.
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        media_type = "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        media_type = "image/jpeg"
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        media_type = "image/webp"
    elif data.startswith((b"GIF87a", b"GIF89a")):
        media_type = "image/gif"
    else:
        raise HTTPException(415, "Unsupported image format")
    return Response(content=data, media_type=media_type, headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


@router.post("/references", dependencies=write)
async def add_reference(body: ReferenceInput, user: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    return await tool_call(session, user, "visual_add_reference", body.model_dump(), mutate=True)


@router.post("/references/{reference_id}/links", dependencies=write)
async def link_reference(reference_id: int, body: LinksInput, user: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    return await tool_call(session, user, "visual_link_reference", {"reference_id": reference_id, **body.model_dump()}, mutate=True)


@router.post("/references/{reference_id}/review", dependencies=write)
async def review_reference(reference_id: int, body: ReviewInput, user: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    return await tool_call(session, user, "visual_review_reference", {"reference_id": reference_id, **body.model_dump()}, mutate=True)


@router.post("/references/{reference_id}/remove", dependencies=write)
async def remove_reference(reference_id: int, body: RemoveInput, user: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    return await tool_call(session, user, "visual_remove_reference", {"reference_id": reference_id, **body.model_dump()}, mutate=True)


@router.post("/identify", dependencies=write)
async def identify(body: IdentifyInput, user: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    return await tool_call(session, user, "visual_identify", body.model_dump())


@router.post("/search", dependencies=write)
async def search(body: SearchInput, user: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    return await tool_call(session, user, "visual_search", body.model_dump())
