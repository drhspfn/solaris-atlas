"""Small HTTP adapters; native conversation items are preserved across tool turns."""

import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from wuwa_story.agents.settings import AgentSettings


class ProviderFailure(ValueError):
    """Safe error code only; provider bodies/headers can contain secrets."""


class ProviderRejected(ProviderFailure):
    def __init__(self, code: str, retry_after: float | None, limits: dict[str, float]) -> None:
        super().__init__(code)
        self.code, self.retry_after, self.limits = code, retry_after, limits
        self.retryable = code in ("rate_limit_exceeded", "slow_down")

    def diagnostic(self) -> dict[str, Any]:
        return {
            "http_status": 429,
            "code": self.code,
            "retry_after_seconds": self.retry_after,
            "limits": self.limits,
        }


def retry_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
        if seconds < 0:
            return None
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


def reset_seconds(value: str | None) -> float | None:
    if not value or not re.fullmatch(r"(?:\d+(?:\.\d+)?(?:ms|s|m|h))+", value):
        return None
    scale = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}
    seconds = sum(
        float(number) * scale[unit]
        for number, unit in re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h)", value)
    )
    return seconds if math.isfinite(seconds) else None


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    native_id: bool = True


@dataclass
class Turn:
    items: list[dict[str, Any]]
    calls: list[ToolCall]
    text: str
    input_tokens: int
    output_tokens: int
    raw: dict[str, Any]
    complete: bool


def token_usage(value: dict[str, Any], provider: str) -> tuple[int, int]:
    if provider == "gemini":
        usage = value.get("usageMetadata", {})
        source = usage.get("promptTokenCount")
        output = usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
    else:
        usage = value.get("usage", {})
        source = usage.get("input_tokens", usage.get("prompt_tokens"))
        output = usage.get("output_tokens", usage.get("completion_tokens"))
    if type(source) is not int or type(output) is not int or min(source, output) < 0:
        raise ProviderFailure("missing_or_invalid_usage")
    return source, output


def vector_values(value: Any, dimensions: int) -> list[float]:
    if not isinstance(value, list) or len(value) != dimensions:
        raise ProviderFailure("embedding_dimension_mismatch")
    if any(type(number) not in (int, float) or not math.isfinite(number) for number in value):
        raise ProviderFailure("invalid_embedding_values")
    if not any(value):
        raise ProviderFailure("zero_embedding_vector")
    return [float(number) for number in value]


class Provider:
    def __init__(self, settings: AgentSettings, client: httpx.AsyncClient) -> None:
        self.settings, self.client = settings, client

    def initial(self, system: str, user: str) -> list[dict[str, Any]]:
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def output_limited(self, raw: dict[str, Any]) -> bool:
        if self.settings.provider == "responses":
            details = raw.get("incomplete_details")
            return (
                raw.get("status") == "incomplete"
                and isinstance(details, dict)
                and details.get("reason") == "max_output_tokens"
            )
        if self.settings.provider == "chat":
            candidates, field, reason = raw.get("choices"), "finish_reason", "length"
        else:
            candidates, field, reason = raw.get("candidates"), "finishReason", "MAX_TOKENS"
        return isinstance(candidates, list) and any(
            isinstance(candidate, dict) and candidate.get(field) == reason
            for candidate in candidates
        )

    def compact_request(self, history: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        if self.settings.provider != "responses":
            raise ProviderFailure("compaction_not_supported")
        return "/responses/compact", {"model": self.settings.model, "input": history}

    async def count_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        # The counter accepts input configuration, not output/runtime parameters.
        keys = {
            "model",
            "input",
            "instructions",
            "tools",
            "tool_choice",
            "reasoning",
            "text",
            "parallel_tool_calls",
            "previous_response_id",
            "conversation",
            "personality",
            "truncation",
        }
        return await self.post(
            "/responses/input_tokens", {key: value for key, value in payload.items() if key in keys}
        )

    @staticmethod
    def compact_output(raw: dict[str, Any]) -> list[dict[str, Any]]:
        output = raw.get("output")
        if (
            raw.get("error")
            or raw.get("status") not in (None, "completed")
            or not isinstance(output, list)
            or not all(isinstance(item, dict) for item in output)
            or not any(
                item.get("type") == "compaction"
                and isinstance(item.get("encrypted_content"), str)
                and item["encrypted_content"]
                for item in output
            )
        ):
            raise ProviderFailure("invalid_compaction_output")
        # The entire returned window is canonical, including retained messages/tools.
        return output

    def request(
        self, history: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> tuple[str, dict[str, Any]]:
        s = self.settings
        if s.provider == "responses":
            return "/responses", {
                "model": s.model,
                "input": history,
                "tools": [
                    {"type": "function", **tool} if "type" not in tool else tool
                    for tool in tools
                ],
                "max_output_tokens": s.max_output_tokens,
                "reasoning": {"effort": s.reasoning_effort},
                "store": False,
                "include": ["reasoning.encrypted_content"],
            }
        if s.provider == "chat":
            return "/chat/completions", {
                "model": s.model,
                "messages": history,
                "tools": [
                    {
                        "type": "function",
                        "function": {k: v for k, v in tool.items() if k not in ("type", "strict")},
                    }
                    for tool in tools
                ],
                s.chat_token_parameter: s.max_output_tokens,
            }
        system = next((item["content"] for item in history if item.get("role") == "system"), "")
        contents = [
            item if "parts" in item else {"role": "user", "parts": [{"text": item["content"]}]}
            for item in history
            if item.get("role") != "system"
        ]
        return f"/models/{s.model}:generateContent", {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "tools": [
                {
                    "functionDeclarations": [
                        {
                            "name": tool["name"],
                            "description": tool["description"],
                            "parametersJsonSchema": tool["parameters"],
                        }
                        for tool in tools
                    ]
                }
            ],
            "generationConfig": {"maxOutputTokens": s.max_output_tokens},
        }

    async def post(self, route: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = (
            {"x-goog-api-key": self.settings.api_key.get_secret_value()}
            if self.settings.provider == "gemini"
            else {"Authorization": "Bearer " + self.settings.api_key.get_secret_value()}
        )
        try:
            async with self.client.stream(
                "POST",
                self.settings.base_url.rstrip("/") + route,
                json=payload,
                headers=headers,
                timeout=self.settings.http_timeout_seconds,
            ) as response:
                if not response.is_success and response.status_code != 429:
                    raise ProviderFailure(f"provider_http_{response.status_code}")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self.settings.max_response_bytes:
                        raise ProviderFailure("provider_response_too_large")
                if response.status_code == 429:
                    try:
                        error = json.loads(body).get("error", {})
                        code = error.get("code") or error.get("type")
                    except (ValueError, TypeError, AttributeError):
                        code = None
                    allowed = {
                        "rate_limit_exceeded",
                        "slow_down",
                        "insufficient_quota",
                        "billing_hard_limit_reached",
                        "organization_spend_limit_exceeded",
                        "project_spend_limit_exceeded",
                    }
                    if code == "rate_limit_error":
                        code = "rate_limit_exceeded"
                    code = (
                        code if isinstance(code, str) and code in allowed else "provider_http_429"
                    )
                    limits = {}
                    for name in (
                        "limit-requests",
                        "limit-tokens",
                        "remaining-requests",
                        "remaining-tokens",
                        "limit-project-tokens",
                        "remaining-project-tokens",
                    ):
                        value = response.headers.get("x-ratelimit-" + name)
                        if value and len(value) <= 15 and value.isdigit():
                            limits[name] = float(value)
                    delay = retry_seconds(response.headers.get("retry-after"))
                    if delay is None:
                        resets = [
                            reset_seconds(response.headers.get("x-ratelimit-reset-" + name))
                            for name in ("requests", "tokens", "project-tokens")
                        ]
                        delay = max((value for value in resets if value is not None), default=None)
                    raise ProviderRejected(code, delay, limits)
            result = json.loads(body)
            if not isinstance(result, dict):
                raise ProviderFailure("invalid_provider_response")
            return result
        except (httpx.HTTPError, json.JSONDecodeError) as error:
            raise ProviderFailure("provider_transport_or_json_error") from error

    def parse(self, raw: dict[str, Any], *, enforce_tool_limit: bool = True) -> Turn:
        source, output = token_usage(raw, self.settings.provider)
        calls: list[ToolCall] = []
        texts: list[str] = []
        if self.settings.provider == "responses":
            if raw.get("status") != "completed":
                return Turn([], [], "", source, output, raw, False)
            items = raw.get("output", [])
            for item in items:
                if item.get("type") == "function_call":
                    calls.append(
                        ToolCall(item["call_id"], item["name"], json.loads(item["arguments"]))
                    )
                if item.get("type") == "message":
                    texts.extend(
                        part["text"]
                        for part in item.get("content", [])
                        if part.get("type") == "output_text"
                    )
            complete = raw.get("status") == "completed"
        elif self.settings.provider == "chat":
            choice = raw["choices"][0]
            item = choice["message"]
            items = [item]
            for call in item.get("tool_calls", []):
                calls.append(
                    ToolCall(
                        call["id"],
                        call["function"]["name"],
                        json.loads(call["function"]["arguments"]),
                    )
                )
            texts = [item.get("content") or ""]
            complete = choice.get("finish_reason") in ("stop", "tool_calls")
        else:
            candidate = raw["candidates"][0]
            items = [candidate["content"]]  # Keep thoughtSignature exactly as returned.
            for ordinal, part in enumerate(items[0].get("parts", [])):
                if "functionCall" in part:
                    call = part["functionCall"]
                    calls.append(
                        ToolCall(
                            call.get("id", f"call-{ordinal}"),
                            call["name"],
                            call.get("args", {}),
                            "id" in call,
                        )
                    )
                elif "text" in part and not part.get("thought"):
                    texts.append(part["text"])
            complete = candidate.get("finishReason") == "STOP"
        if (enforce_tool_limit and len(calls) > self.settings.max_tool_calls_per_step) or any(
            not isinstance(call.arguments, dict) for call in calls
        ):
            raise ProviderFailure("invalid_tool_calls")
        return Turn(items, calls, "\n".join(texts), source, output, raw, complete)

    def tool_result(self, call: ToolCall, result: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(result, ensure_ascii=False)
        if self.settings.provider == "responses":
            return {"type": "function_call_output", "call_id": call.id, "output": encoded}
        if self.settings.provider == "chat":
            return {"role": "tool", "tool_call_id": call.id, "content": encoded}
        return {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "name": call.name,
                        "response": result,
                        **({"id": call.id} if call.native_id else {}),
                    }
                }
            ],
        }

    async def embed(self, texts: list[str]) -> tuple[list[list[float]], dict[str, Any]]:
        s = self.settings
        if s.provider == "gemini":
            # Single-document requests are portable across Gemini embedding versions.
            if len(texts) != 1:
                raise ProviderFailure("gemini_embedding_requires_single_input")
            raw = await self.post(
                f"/models/{s.embedding_model}:embedContent",
                {
                    "model": "models/" + s.embedding_model,
                    "content": {"parts": [{"text": texts[0]}]},
                    "outputDimensionality": s.embedding_dimensions,
                },
            )
            values = [raw["embedding"]["values"]]
        else:
            raw = await self.post(
                "/embeddings",
                {"model": s.embedding_model, "input": texts, "dimensions": s.embedding_dimensions},
            )
            values = [row["embedding"] for row in sorted(raw["data"], key=lambda row: row["index"])]
        if len(values) != len(texts):
            raise ProviderFailure("embedding_count_mismatch")
        return [vector_values(value, s.embedding_dimensions) for value in values], raw
