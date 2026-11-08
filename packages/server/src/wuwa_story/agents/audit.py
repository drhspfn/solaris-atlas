"""Durable tool receipts survive caller rollback and conversation compaction."""

import json
import logging
import re
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.agent_workspace import AgentToolAudit

logger = logging.getLogger(__name__)


def redacted(value: Any, limit: int = 48000) -> dict[str, Any]:
    def clean(item: Any) -> Any:
        if isinstance(item, dict):
            return {
                str(key): "[redacted]"
                if str(key).lower() in ("image", "base64", "file", "download_url") or any(
                    part in str(key).lower()
                    for part in (
                        "password",
                        "secret",
                        "authorization",
                        "api_key",
                        "access_token",
                        "refresh_token",
                        "encrypted_content",
                        "image_base64",
                    )
                )
                else clean(value)
                for key, value in item.items()
            }
        if isinstance(item, (tuple, list)):
            return [clean(value) for value in item[:100]]
        if isinstance(item, str):
            return re.sub(r"(?i)bearer\s+[^\s\"']+", "Bearer [redacted]", item)
        return item

    payload = clean(value)
    encoded = json.dumps(payload, default=str, ensure_ascii=False)
    if len(encoded) > limit:
        return {"preview": encoded[:limit], "truncated": True, "characters": len(encoded)}
    return json.loads(encoded) if isinstance(payload, dict) else {"value": json.loads(encoded)}


async def start(
    session: AsyncSession,
    *,
    request_id: str,
    role: str,
    call_id: str,
    tool: str,
    arguments: dict[str, Any],
    run_id: int | None = None,
    step: int | None = None,
) -> int:
    async with AsyncSession(session.bind, expire_on_commit=False) as audit:
        receipt = AgentToolAudit(
            request_id=request_id,
            role=role,
            run_id=run_id,
            step=step,
            call_id=call_id[:256],
            tool=tool[:128],
            status="started",
            arguments=redacted(arguments),
            result={},
        )
        audit.add(receipt)
        await audit.commit()
        logger.info(
            "agent.tool_started request=%s role=%s run=%s step=%s tool=%s audit=%s",
            request_id,
            role,
            run_id,
            step,
            tool,
            receipt.id,
        )
        return receipt.id


async def complete(
    session: AsyncSession,
    identity: int,
    result: dict[str, Any],
    duration_ms: int,
    status: str = "completed",
) -> None:
    async with AsyncSession(session.bind) as audit:
        await audit.execute(
            update(AgentToolAudit)
            .where(AgentToolAudit.id == identity)
            .values(status=status, result=redacted(result), duration_ms=duration_ms)
        )
        await audit.commit()
    logger.info(
        "agent.tool_finished audit=%s status=%s duration_ms=%s", identity, status, duration_ms
    )
