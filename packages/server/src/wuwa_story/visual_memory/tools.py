"""Bounded visual-memory inputs and explicitly capability-gated agent tools."""

import asyncio
import base64
import binascii
from typing import Any, Literal

from mcp.types import Tool
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_ENCODED = 4 * ((MAX_IMAGE_BYTES + 2) // 3)
Box = tuple[float, float, float, float]


def decode_image(value: str) -> bytes:
    if not value or len(value) > MAX_ENCODED:
        raise ValueError("Image must be base64 and at most 5 MiB")
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Invalid base64 image") from exc
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image must be base64 and at most 5 MiB")
    return data


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImageSource(Input):
    base64: str | None = Field(
        default=None, min_length=1, max_length=MAX_ENCODED)
    url: str | None = Field(default=None, min_length=1, max_length=4096)
    mime_type: Literal["image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp"] | None = None
    asset_id: int | None = Field(default=None, gt=0, strict=True,
                                 description="Graph node ID of a published entity image; not a private storage file ID")
    reference_id: int | None = Field(
        default=None, gt=0, strict=True, description="Approved, search-enabled visual reference ID")

    @model_validator(mode="after")
    def one_source(self):
        if sum(value is not None for value in (self.base64, self.url, self.asset_id, self.reference_id)) != 1:
            raise ValueError("Choose exactly one image source")
        if self.base64 is not None:
            decode_image(self.base64)
        elif self.mime_type is not None:
            raise ValueError("mime_type is only supported with base64 images")
        return self


class ImageInput(Input):
    image_base64: str | None = Field(
        default=None, min_length=1, max_length=MAX_ENCODED)
    image: ImageSource | None = None
    include_images: bool = Field(
        default=False, description="MCP: include up to three approved reference thumbnails as native image content")
    kind: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=10, ge=1, le=10)

    @field_validator("image_base64")
    @classmethod
    def valid_image(cls, value: str | None) -> str | None:
        if value is not None:
            decode_image(value)
        return value

    @model_validator(mode="after")
    def one_source(self):
        if (self.image_base64 is None) == (self.image is None):
            raise ValueError("Provide image or image_base64, not both")
        return self


class SearchInput(ImageInput):
    bbox: Box | None = None


class IdentifyInput(ImageInput):
    boxes: list[Box] | None = Field(default=None, max_length=20)
    detect: bool = True


class IdentitiesInput(Input):
    q: str = Field(default="", max_length=200)
    kind: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10000)


class ReferenceIdInput(Input):
    reference_id: int = Field(gt=0)
    include_images: bool = False


class IdentityCreateInput(Input):
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=64)
    node_ids: list[int] = Field(default_factory=list, max_length=50)
    lore_id: int | None = Field(default=None, gt=0)
    description: str = Field(default="", max_length=4000)


class Link(Input):
    identity_id: int = Field(gt=0)
    appearance_id: int | None = Field(default=None, gt=0)


class ReferenceInput(Input):
    image_base64: str = Field(min_length=1, max_length=MAX_ENCODED)
    bbox: Box | None = None
    links: list[Link] = Field(min_length=1, max_length=20)
    source_type: str = Field(default="manual", max_length=100)
    source_url: str | None = Field(default=None, max_length=2048)
    source_page: str | None = Field(default=None, max_length=2048)
    rights: str | None = Field(default=None, max_length=2000)
    author: str | None = Field(default=None, max_length=512)
    quality: float = Field(default=1.0, ge=0, le=1, allow_inf_nan=False)
    status: Literal["pending", "approved", "rejected"] = "pending"
    retention_mode: Literal["image", "vector_only"] = "image"

    @field_validator("image_base64")
    @classmethod
    def valid_image(cls, value: str) -> str:
        decode_image(value)
        return value


class LinksInput(Input):
    links: list[Link] = Field(min_length=1, max_length=20)


class ReviewInput(Input):
    status: Literal["pending", "approved", "rejected"]
    enabled_for_search: bool = True
    reason: str | None = Field(default=None, max_length=2000)


class RemoveInput(Input):
    mode: Literal["reject", "vector_only"]
    reason: str | None = Field(default=None, max_length=2000)


class ReindexInput(Input):
    reference_ids: list[int] = Field(min_length=1, max_length=20)

    @field_validator("reference_ids")
    @classmethod
    def positive_ids(cls, values: list[int]) -> list[int]:
        if any(value <= 0 for value in values):
            raise ValueError("reference IDs must be positive")
        return values


def service_for(session: Any):
    from wuwa_story.config.settings import get_settings
    from wuwa_story.storage.s3 import S3Storage
    from wuwa_story.visual_memory.service import VisualMemoryService

    return VisualMemoryService(session, storage=S3Storage(get_settings()))


TOOLS = [
    Tool(name="visual_search", description="Search visual identities. Provide image: {url: public HTTPS URL}, {asset_id: published entity node ID}, {reference_id: approved visual reference ID}, or {base64: encoded image}; legacy image_base64 is supported. Maximum 5 MiB. Similarity is not probability.",
         inputSchema=SearchInput.model_json_schema()),
    Tool(name="visual_identify", description="Detect and identify characters in an image. Provide image: {url: public HTTPS URL}, {asset_id: published entity node ID}, {reference_id: approved visual reference ID}, or {base64: encoded image}; legacy image_base64 is supported. Maximum 5 MiB. Results may be unknown.",
         inputSchema=IdentifyInput.model_json_schema()),
    Tool(name="visual_list_identities", description="List visual identities with optional name and kind filters.",
         inputSchema=IdentitiesInput.model_json_schema()),
    Tool(name="visual_get_reference", description="Get an approved, search-enabled visual reference by ID.",
         inputSchema=ReferenceIdInput.model_json_schema()),
]

# The public MCP transport has no user identity. One inference per process, no queue.
_public_inference = asyncio.Semaphore(1)


class VisualMemoryTools:
    def __init__(self, service: Any, *, allow_write: bool = False, allow_moderation: bool = False, actor_id: int | None = None, agent_source: bool = False):
        if agent_source and allow_moderation:
            raise ValueError(
                "An agent cannot be granted moderation capability")
        self.service = service
        self.allow_write = allow_write
        self.allow_moderation = allow_moderation
        self.actor_id = actor_id
        self.agent_source = agent_source

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "visual_list_identities":
            return await self.service.list_identities(**IdentitiesInput.model_validate(arguments).model_dump())
        if name == "visual_get_reference":
            result = await self.service.get_reference(ReferenceIdInput.model_validate(arguments).reference_id)
            if result.get("status") != "approved" or result.get("enabled_for_search") is not True:
                raise ValueError("Reference unavailable")
            return result
        if name in {"visual_search", "visual_identify"}:
            model = SearchInput if name == "visual_search" else IdentifyInput
            data = model.model_validate(arguments).model_dump()
            legacy = data.pop("image_base64")
            source = data.pop("image")
            data.pop("include_images")
            if legacy is not None:
                image = decode_image(legacy)
            elif source["base64"] is not None:
                image = decode_image(source["base64"])
            elif source["url"] is not None:
                from wuwa_story.visual_memory.image_source import download_image
                image = await download_image(source["url"])
            elif source["reference_id"] is not None:
                reference = await self.service.get_reference(source["reference_id"])
                if reference.get("status") != "approved" or reference.get("enabled_for_search") is not True:
                    raise ValueError("Reference unavailable")
                image = await self.service.get_image_bytes(source["reference_id"], max_bytes=MAX_IMAGE_BYTES)
            else:
                image = await self.service.get_asset_image_bytes(source["asset_id"])
            if len(image) > MAX_IMAGE_BYTES:
                raise ValueError("Query image exceeds 5 MiB")
            return await getattr(self.service, name.removeprefix("visual_"))(image, **data)
        if not self.allow_write:
            raise PermissionError("Visual memory write capability required")
        if self.actor_id is None and not (self.agent_source and name in {"visual_create_identity", "visual_add_reference"}):
            raise PermissionError("Visual memory writes require an actor")
        data = dict(arguments)
        if name == "visual_create_identity":
            return await self.service.create_identity(**IdentityCreateInput.model_validate(data).model_dump())
        if name == "visual_add_reference":
            data = ReferenceInput.model_validate(data).model_dump()
            if data["status"] == "approved" and not self.allow_moderation:
                raise PermissionError(
                    "Visual memory moderation capability required")
            if self.agent_source:
                if data["status"] != "pending":
                    raise PermissionError(
                        "Agent references must remain pending")
                data["source_type"] = "agent_observation"
            image = decode_image(data.pop("image_base64"))
            return await self.service.add_reference(image, **data, actor_id=self.actor_id)
        if name in {"visual_link_reference", "visual_review_reference", "visual_remove_reference"}:
            if (name == "visual_review_reference" or name == "visual_remove_reference" and data.get("mode") == "reject") and not self.allow_moderation:
                raise PermissionError(
                    "Visual memory moderation capability required")
            reference_id = data.pop("reference_id", None)
            if type(reference_id) is not int or reference_id <= 0:
                raise ValueError("reference_id must be positive")
            model = {"visual_link_reference": LinksInput, "visual_review_reference": ReviewInput,
                     "visual_remove_reference": RemoveInput}[name]
            return await getattr(self.service, name.removeprefix("visual_"))(reference_id, **model.model_validate(data).model_dump(), actor_id=self.actor_id)
        if name == "visual_reindex":
            return await self.service.reindex(**ReindexInput.model_validate(data).model_dump(), actor_id=self.actor_id)
        raise ValueError("Unknown visual memory tool")


async def dispatch(name: str, arguments: dict[str, Any], session: Any) -> dict[str, Any]:
    if name not in {tool.name for tool in TOOLS}:
        raise ValueError("Unknown visual memory tool")

    async def execute():
        try:
            return await VisualMemoryTools(service_for(session)).execute(name, arguments)
        except ValueError:
            return {"error": {"code": "invalid_input", "message": "Invalid input or reference unavailable."}}
        except Exception as exc:
            from wuwa_story.visual_memory.inference import VisualMemoryUnavailable
            if isinstance(exc, VisualMemoryUnavailable):
                return {"error": {"code": "unavailable", "message": "Visual model is unavailable."}}
            raise
    if name in {"visual_list_identities", "visual_get_reference"}:
        return await execute()
    if _public_inference.locked():
        return {"error": {"code": "busy", "message": "Visual inference is busy; retry later."}}
    async with _public_inference:
        return await execute()


async def image_contents(name: str, arguments: dict, result: dict, session: Any) -> list:
    """Native MCP thumbnails; only retained and currently approved references."""
    if arguments.get("include_images") is not True or "error" in result:
        return []
    if _public_inference.locked():
        from mcp.types import TextContent
        return [TextContent(type="text", text="Reference thumbnails are busy; retry include_images later.")]
    async with _public_inference:
        return await _reference_contents(name, arguments, result, session)


async def _reference_contents(name: str, arguments: dict, result: dict, session: Any) -> list:
    ids = []
    if name == "visual_get_reference":
        ids.append(arguments["reference_id"])
    else:
        groups = [result] if name == "visual_search" else result.get(
            "detections", [])
        for group in groups:
            candidates = group.get("candidates", group.get(
                "identification", {}).get("candidates", []))
            for candidate in candidates:
                for evidence in candidate.get("evidence", []):
                    if evidence.get("reference_id") not in ids:
                        ids.append(evidence.get("reference_id"))
    import io
    from mcp.types import ImageContent, TextContent
    from wuwa_story.visual_memory.images import load_image

    service = service_for(session)
    contents = []
    for reference_id in [value for value in ids if type(value) is int][:3]:
        reference = await service.get_reference(reference_id)
        if reference.get("status") != "approved" or reference.get("enabled_for_search") is not True:
            continue
        try:
            data = await service.get_image_bytes(reference_id, max_bytes=MAX_IMAGE_BYTES)

            def thumbnail():
                image = load_image(data, max_bytes=MAX_IMAGE_BYTES)
                image.thumbnail((512, 512))
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=85)
                return base64.b64encode(output.getvalue()).decode("ascii")
            encoded = await asyncio.to_thread(thumbnail)
        except ValueError:
            continue
        contents.extend([TextContent(type="text", text=f"Visual reference #{reference_id}"),
                         ImageContent(type="image", data=encoded, mimeType="image/jpeg")])
    return contents
