"""Admin runtime settings API backed by ops.app_setting and code-defined SettingSpecs."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import wuwa_story.agents.settings  # noqa: F401  # Register agent settings on import
from wuwa_story.agents.contracts import StrictModel
from wuwa_story.auth.dependencies import require_admin, require_csrf
from wuwa_story.config.app_settings import (
    get_setting_spec,
    list_setting_specs,
    parse_setting_value,
    serialize_setting,
    sync_setting_definitions,
)
from wuwa_story.db.models.auth import User
from wuwa_story.db.session import get_session

router = APIRouter(
    prefix="/admin/settings",
    tags=["admin settings"],
    dependencies=[Depends(require_admin)],
)


class SettingUpdateRequest(StrictModel):
    value: Any = None
    reset: bool = False


async def load_settings_payload(session: AsyncSession) -> dict[str, Any]:
    rows = await sync_setting_definitions(session)
    await session.commit()
    specs = list_setting_specs()
    items = [serialize_setting(spec, rows.get(spec.key)) for spec in specs]
    categories: list[str] = []
    for item in items:
        if item["category"] not in categories:
            categories.append(item["category"])
    return {"categories": categories, "settings": items}


@router.get("")
async def list_settings(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    return await load_settings_payload(session)


@router.put("/{key:path}", dependencies=[Depends(require_csrf)])
async def update_setting(
    key: str,
    body: SettingUpdateRequest,
    admin_user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    spec = get_setting_spec(key)
    if spec is None:
        raise HTTPException(404, f"Unknown setting: {key}")
    rows = await sync_setting_definitions(session)
    row = rows[spec.key]
    if body.reset or body.value is None:
        row.value = None
    else:
        try:
            _, serialized = parse_setting_value(spec, body.value)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        row.value = serialized
    row.updated_by_user_id = admin_user.id
    await session.commit()
    await session.refresh(row)
    return {"setting": serialize_setting(spec, row)}
