"""Bounded operational traces, independent of compacted conversation history."""

import hashlib
import json
from typing import Any


def preview(value: Any, limit: int = 4000) -> str:
    def clean(item):
        if isinstance(item, dict):
            return {key: ("[redacted]" if any(part in key.lower() for part in
                    ("api_key", "authorization", "password", "secret", "encrypted_content"))
                else clean(val)) for key, val in item.items()}
        if isinstance(item, list):
            return [clean(val) for val in item[:50]]
        return item
    content = value if isinstance(value, str) else json.dumps(
        clean(value), ensure_ascii=False, default=str)
    return content[:limit] + (" … [truncated]" if len(content) > limit else "")


def signature(name: str, arguments: dict) -> str:
    return hashlib.sha256(json.dumps([name, arguments], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validation_message(error: Exception) -> str:
    if hasattr(error, "errors"):
        # Pydantic's default string includes the full rejected input payload.
        return preview([{"path": list(e["loc"]), "type": e["type"], "message": e["msg"]}
                        for e in error.errors(include_input=False, include_url=False)], 2000)
    return preview(str(error), 2000)


def legacy_trace(raw: dict) -> dict:
    """Expose only visible model messages/calls, never reasoning or native payloads."""
    tools, messages = [], []
    for item in raw.get("output", []):
        if item.get("type") == "function_call":
            tools.append({"name": item.get("name"), "arguments": preview(item.get("arguments", "")),
                          "status": "unknown", "result": "Tool result was not retained separately."})
        elif item.get("type") == "message":
            messages.extend(p.get("text", "") for p in item.get(
                "content", []) if p.get("type") == "output_text")
    for choice in raw.get("choices", []):
        message = choice.get("message", {})
        if isinstance(message.get("content"), str):
            messages.append(message["content"])
        for call in message.get("tool_calls", []):
            function = call.get("function", {})
            tools.append({"name": function.get("name"), "arguments": preview(function.get("arguments", "")),
                          "status": "unknown", "result": "Tool result was not retained separately."})
    for candidate in raw.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            if part.get("thought"):
                continue
            if "text" in part:
                messages.append(part["text"])
            if "functionCall" in part:
                call = part["functionCall"]
                tools.append({"name": call.get("name"), "arguments": preview(call.get("args", {})),
                              "status": "unknown", "result": "Tool result was not retained separately."})
    return {"recorded": False, "text": preview("\n".join(messages), 8000), "tools": tools}
