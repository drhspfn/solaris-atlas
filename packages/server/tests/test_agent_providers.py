import json

import httpx
import pytest

from wuwa_story.agents.providers import (
    Provider,
    ProviderFailure,
    ProviderRejected,
    reset_seconds,
    retry_seconds,
    vector_values,
)
from wuwa_story.agents.settings import AgentSettings


def test_compaction_preserves_the_entire_canonical_window():
    retained = {"role": "user", "content": "Keep this task"}
    output = [retained, {"type": "compaction", "encrypted_content": "opaque"}]
    assert Provider.compact_output({"output": output}) is output
    for malformed in ([], [retained], [{"type": "compaction", "encrypted_content": ""}], [None]):
        with pytest.raises(ProviderFailure, match="invalid_compaction"):
            Provider.compact_output({"output": malformed})
    with pytest.raises(ProviderFailure):
        Provider.compact_output({"status": "incomplete", "output": output})


async def test_input_counter_preserves_context_without_unsupported_output_options():
    settings = AgentSettings(_env_file=None, api_key="test")
    history = [{"type": "compaction", "encrypted_content": "opaque"}]
    tools = [{"type": "function", "name": "read_node", "parameters": {"type": "object"}}]

    def transport(request):
        assert request.url.path == "/v1/responses/input_tokens"
        assert json.loads(request.content) == {
            "model": settings.model,
            "input": history,
            "tools": tools,
            "reasoning": {"effort": settings.reasoning_effort},
        }
        return httpx.Response(200, json={"input_tokens": 502})

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = Provider(settings, client)
        _, payload = provider.request(history, tools)
        assert await provider.count_input(payload) == {"input_tokens": 502}


def test_responses_provider_adds_required_function_type_to_legacy_tool_schemas():
    settings = AgentSettings(_env_file=None, api_key="test")
    provider = Provider(settings, None)
    tool = {
        "name": "search_lore",
        "description": "Search lore",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
    }

    _, payload = provider.request([], [tool])

    assert payload["tools"] == [{"type": "function", **tool}]


@pytest.mark.parametrize("kind", ["responses", "chat", "gemini"])
@pytest.mark.asyncio
async def test_native_reasoning_is_preserved_and_tool_results_match_protocol(kind):
    settings = AgentSettings(_env_file=None, provider=kind, api_key="never-log-this")
    if kind == "responses":
        item = {
            "type": "function_call",
            "call_id": "abc",
            "name": "read_node",
            "arguments": '{"node_id":1}',
        }
        raw = {
            "status": "completed",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "output": [{"type": "reasoning", "encrypted_content": "opaque"}, item],
        }
    elif kind == "chat":
        item = {
            "role": "assistant",
            "content": None,
            "reasoning_content": "opaque",
            "tool_calls": [
                {
                    "id": "abc",
                    "type": "function",
                    "function": {"name": "read_node", "arguments": '{"node_id":1}'},
                }
            ],
        }
        raw = {
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "choices": [{"finish_reason": "tool_calls", "message": item}],
        }
    else:
        item = {
            "role": "model",
            "parts": [
                {
                    "functionCall": {"name": "read_node", "args": {"node_id": 1}},
                    "thoughtSignature": "opaque",
                }
            ],
        }
        raw = {
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 3,
                "thoughtsTokenCount": 2,
            },
            "candidates": [{"finishReason": "STOP", "content": item}],
        }

    def transport(request):
        assert "never-log-this" not in str(request.url)
        return httpx.Response(200, json=raw)

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = Provider(settings, client)
        route, body = provider.request(provider.initial("system", "quest"), [])
        turn = provider.parse(await provider.post(route, body))
        assert turn.input_tokens == 10 and turn.output_tokens == 5
        assert item in turn.items and "opaque" in json.dumps(turn.items)
        assert turn.calls[0].arguments == {"node_id": 1}
        result = provider.tool_result(turn.calls[0], {"text": "source"})
        if kind == "gemini":
            assert "id" not in result["parts"][0]["functionResponse"]
        else:
            assert "abc" in json.dumps(result)


@pytest.mark.asyncio
async def test_errors_do_not_expose_provider_credentials_or_bodies():
    settings = AgentSettings(_env_file=None, api_key="private")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(401, text="private"))
    ) as client:
        with pytest.raises(ProviderFailure, match="provider_http_401") as error:
            await Provider(settings, client).post("/responses", {})
        assert "private" not in str(error.value)


def test_vectors_and_missing_usage_are_rejected():
    for value in ([0, 0], [1], [float("nan"), 2], [True, 1]):
        with pytest.raises(ProviderFailure):
            vector_values(value, 2)
    assert vector_values([1, 2], 2) == [1.0, 2.0]
    with httpx.Client():
        provider = Provider(AgentSettings(_env_file=None), None)
        with pytest.raises(ProviderFailure, match="usage"):
            provider.parse({"output": []})


def test_excess_tools_can_be_decoded_for_bounded_execution_without_accepting_invalid_arguments():
    provider = Provider(AgentSettings(_env_file=None, max_tool_calls_per_step=1), None)
    raw = {"status":"completed", "usage":{"input_tokens":10,"output_tokens":5}, "output":[{"type":"function_call", "call_id":str(i), "name":"read_node", "arguments":'{"node_id":1}'} for i in range(2)]}
    with pytest.raises(ProviderFailure, match="invalid_tool_calls"):
        provider.parse(raw)
    assert len(provider.parse(raw, enforce_tool_limit=False).calls) == 2
    raw["output"][0]["arguments"] = '[]'
    with pytest.raises(ProviderFailure, match="invalid_tool_calls"):
        provider.parse(raw, enforce_tool_limit=False)


@pytest.mark.parametrize(
    "code,retryable",
    [
        ("rate_limit_exceeded", True),
        ("slow_down", True),
        ("insufficient_quota", False),
        ("project_spend_limit_exceeded", False),
        ("secret-code", False),
    ],
)
@pytest.mark.asyncio
async def test_429_diagnostics_are_safe_and_quota_is_not_retryable(code, retryable):
    settings = AgentSettings(_env_file=None, api_key="private")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                429,
                json={"error": {"code": code, "message": "private account details"}},
                headers={
                    "retry-after": "56",
                    "x-ratelimit-limit-tokens": "150000",
                    "x-ratelimit-remaining-tokens": "0",
                    "x-account": "private",
                },
            )
        )
    ) as client:
        with pytest.raises(ProviderRejected) as error:
            await Provider(settings, client).post("/responses", {})
    assert error.value.retryable == retryable
    assert error.value.retry_after == 56
    assert error.value.limits["limit-tokens"] == 150000
    assert "private" not in json.dumps(error.value.diagnostic())
    assert "secret-code" not in str(error.value)


def test_retry_delays_handle_dates_units_and_invalid_headers():
    assert retry_seconds("2.5") == 2.5
    assert retry_seconds("Sat, 03 Oct 2020 21:00:00 GMT") == 0
    assert retry_seconds("invalid") is None
    assert retry_seconds("NaN") is None
    assert retry_seconds("-1") is None
    assert reset_seconds("6m0.5s") == 360.5
    assert reset_seconds("200ms") == 0.2
    assert reset_seconds("invalid") is None
