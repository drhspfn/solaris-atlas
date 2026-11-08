"""Bounded visual-memory inputs and explicitly capability-gated agent tools."""

import asyncio
import base64
import binascii
from typing import Any, Literal

from mcp.types import Tool
from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class ImageInput(Input):
    image_base64: str = Field(min_length=1, max_length=MAX_ENCODED)
    kind: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=10, ge=1, le=10)

    @field_validator("image_base64")
    @classmethod
    def valid_image(cls, value: str) -> str:
        decode_image(value)
        return value


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
    Tool(name="visual_search", description="Search visual identities using a base64-encoded image (maximum 5 MiB). Similarity is not probability.",
         inputSchema=SearchInput.model_json_schema()),
    Tool(name="visual_identify", description="Detect and identify visual identities from a base64-encoded image (maximum 5 MiB). Results may be unknown.",
         inputSchema=IdentifyInput.model_json_schema()),
    Tool(name="visual_list_identities", description="List visual identities with optional name and kind filters.", inputSchema=IdentitiesInput.model_json_schema()),
    Tool(name="visual_get_reference", description="Get an approved, search-enabled visual reference by ID.", inputSchema=ReferenceIdInput.model_json_schema()),
]

# The public MCP transport has no user identity. One inference per process, no queue.
_public_inference = asyncio.Semaphore(1)


class VisualMemoryTools:
    def __init__(self, service: Any, *, allow_write: bool = False, allow_moderation: bool = False, actor_id: int | None = None, agent_source: bool = False):
        if agent_source and allow_moderation:
            raise ValueError("An agent cannot be granted moderation capability")
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
            image = decode_image(data.pop("image_base64"))
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
                raise PermissionError("Visual memory moderation capability required")
            if self.agent_source:
                if data["status"] != "pending":
                    raise PermissionError("Agent references must remain pending")
                data["source_type"] = "agent_observation"
            image = decode_image(data.pop("image_base64"))
            return await self.service.add_reference(image, **data, actor_id=self.actor_id)
        if name in {"visual_link_reference", "visual_review_reference", "visual_remove_reference"}:
            if (name == "visual_review_reference" or name == "visual_remove_reference" and data.get("mode") == "reject") and not self.allow_moderation:
                raise PermissionError("Visual memory moderation capability required")
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
