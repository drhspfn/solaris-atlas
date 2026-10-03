import json

import httpx
import pytest

from wuwa_story.agents.providers import Provider, ProviderFailure, vector_values
from wuwa_story.agents.settings import AgentSettings


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
