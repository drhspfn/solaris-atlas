"""Metered role-specific chat; tool dispatch is authorized independently of prompts."""

import time
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

import httpx
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.agents import audit, providers
from wuwa_story.agents.budget import BudgetExceeded, reserve, settle
from wuwa_story.agents.capabilities import (
    INTERNAL_PROMPT,
    PUBLIC_PROMPT,
    Role,
    allowed,
    execute_internal,
    internal_definitions,
    tool,
)
from wuwa_story.agents.chat_tools import execute_read, read_definitions
from wuwa_story.agents.settings import AgentSettings, get_agent_settings


async def call_model(
    session: AsyncSession,
    settings: AgentSettings,
    provider: providers.Provider,
    history: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    step: int,
) -> providers.Turn:
    route, payload = provider.request(history, tools)
    from wuwa_story.agents.runner import request_bound

    try:
        call = await reserve(
            session,
            settings,
            run_id=None,
            step=step,
            input_bound=request_bound(payload),
            output_bound=settings.max_output_tokens,
            kind="analysis",
        )
        await session.commit()
    except (BudgetExceeded, ValueError) as error:
        await session.rollback()
        raise HTTPException(
            429, "Agent budget or configuration does not permit this request"
        ) from error
    identity = call.id
    try:
        raw = await provider.post(route, payload)
        turn = provider.parse(raw, enforce_tool_limit=False)
        within_budget = await settle(
            session, identity, settings, turn.input_tokens, turn.output_tokens, raw
        )
        await session.commit()
        if not turn.complete:
            raise HTTPException(503, "Agent response was incomplete; recorded usage is retained")
        if not within_budget:
            raise HTTPException(429, "Agent usage exceeded its reservation")
        return turn
    except HTTPException:
        raise
    except Exception as error:
        await session.rollback()
        from wuwa_story.db.models.agents import AgentCall

        recorded = await session.get(AgentCall, identity)
        if recorded:
            recorded.status = "uncertain"
            await session.commit()
        raise HTTPException(
            503, "Agent provider failed; outcome recorded for reconciliation"
        ) from error


def has_evidence(output: dict[str, Any]) -> bool:
    for key in ("results", "lines", "blocks", "items", "candidates", "detections"):
        value = output.get(key)
        if isinstance(value, str) and value.strip() and not value.strip().lower().startswith("no "):
            return True
        if isinstance(value, list) and value:
            return True
    return output.get("status") == "approved" and bool(output.get("id"))


async def run_chat(session: AsyncSession, messages: Sequence[Any], *, role: Role) -> dict[str, Any]:
    if role not in {"public", "internal"}:
        raise ValueError("Invalid chat role")
    if sum(len(message.content) for message in messages) > 48000:
        raise HTTPException(422, "Conversation is too large")
    settings = get_agent_settings()
    request_id = str(uuid4())
    tools = read_definitions() + [
        tool(
            "get_quest_lore",
            "Read a bounded quest transcript excerpt.",
            {"quest_id": {"type": "integer"}},
            ["quest_id"],
        )
    ]
    if role == "internal":
        tools += internal_definitions()
    tools = [item for item in tools if allowed(role, item["name"])]
    history = [
        {"role": "system", "content": PUBLIC_PROMPT if role == "public" else INTERNAL_PROMPT}
    ]
    history.extend({"role": message.role, "content": message.content} for message in messages)
    retrieved = False
    async with httpx.AsyncClient() as client:
        provider = providers.Provider(settings, client)
        for step in range(5 if role == "public" else 10):
            turn = await call_model(session, settings, provider, history, tools, step)
            if not turn.calls:
                reply = turn.text
                if role == "public" and not retrieved:
                    reply = "I can only help with sourced Wuthering Waves lore. Please ask about a character, quest or story event in the archive."
                return {"reply": reply, "request_id": request_id}
            history.extend(turn.items)
            for index, call in enumerate(turn.calls):
                started = time.monotonic()
                identity = await audit.start(
                    session,
                    request_id=request_id,
                    role=role,
                    call_id=getattr(call, "id", f"{step}:{index}"),
                    tool=call.name,
                    arguments={**call.arguments, "image_base64": "[redacted]"} if "image_base64" in call.arguments else call.arguments,
                    step=step,
                )
                try:
                    if not allowed(role, call.name):
                        raise ValueError("Tool is not authorized for this role")
                    if index >= settings.max_tool_calls_per_step:
                        raise ValueError("Per-turn tool limit reached")
                    if call.name in {item["name"] for item in read_definitions()} | {
                        "get_quest_lore"
                    }:
                        output = await execute_read(session, call)
                        if "error" not in output and has_evidence(output):
                            retrieved = True
                    else:
                        output = await execute_internal(
                            session, settings, call.name, call.arguments
                        )
                    import json

                    from wuwa_story.agents.trace import preview

                    if len(json.dumps(output, default=str)) > settings.tool_result_chars:
                        output = {
                            "result": preview(output, settings.tool_result_chars - 1000),
                            "truncated": True,
                        }
                    await session.commit()
                    await audit.complete(
                        session,
                        identity,
                        output,
                        round((time.monotonic() - started) * 1000),
                        "error" if "error" in output else "completed",
                    )
                except Exception as error:
                    await session.rollback()
                    output = {"error": "Tool failed", "code": type(error).__name__}
                    if isinstance(error, HTTPException):
                        output["status_code"] = error.status_code
                    await audit.complete(
                        session,
                        identity,
                        output,
                        round((time.monotonic() - started) * 1000),
                        "error",
                    )
                    if isinstance(error, HTTPException):
                        raise
                history.append(provider.tool_result(call, output))
    return {
        "reply": "Research step limit reached. Please narrow the question.",
        "request_id": request_id,
    }
