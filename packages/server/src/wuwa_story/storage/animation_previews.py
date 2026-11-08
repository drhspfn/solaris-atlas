"""Public native animation contract; archive references remain the source bundle."""

from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError


class AnimationPreviewVariant(BaseModel):
    animation: str
    rover: Literal['male', 'female'] | None = None
    url: str
    file_id: int = Field(gt=0)
    mime_type: Literal['video/webm']
    duration_seconds: float = Field(ge=0, allow_inf_nan=False)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    loop: bool


class AnimationPreview(BaseModel):
    recipe: Literal['riglyph-native-v1', 'riglyph-native-v2']
    status: Literal['ready']
    variants: list[AnimationPreviewVariant] = Field(min_length=1)
    default_animation: str | None = None


def animation_preview(metadata: dict[str, Any] | None, enabled: bool) -> dict[str, Any] | None:
    policy = (metadata or {}).get('animation_import') or {}
    if not enabled or policy.get('display', 'animated') != 'animated':
        return None
    try:
        return AnimationPreview.model_validate((metadata or {}).get('animation_preview')).model_dump(exclude_unset=True)
    except ValidationError:
        return None


def spine_bundle(url: str, file_id: int | None, size_bytes: int | None,
                 metadata: dict[str, Any] | None, enabled: bool) -> dict[str, Any]:
    metadata = metadata or {}
    return {'url': url, 'file_id': file_id, 'size_bytes': size_bytes,
            'asset_version': metadata.get('asset_version'),
            'riglyph_file_id': metadata.get('riglyph_file_id'),
            'animation_preview': animation_preview(metadata, enabled)}


def apply_animation_display_flag(payload: Any, enabled: bool) -> Any:
    """A projection published while enabled must respect a later flag disable."""
    if enabled:
        return payload
    if isinstance(payload, dict):
        return {key: None if key == 'animation_preview' else apply_animation_display_flag(value, False)
                for key, value in payload.items()}
    if isinstance(payload, list):
        return [apply_animation_display_flag(value, False) for value in payload]
    return payload
