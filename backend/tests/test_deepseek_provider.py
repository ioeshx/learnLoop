"""DeepSeek transport tests without external network calls."""

import json

import httpx
import pytest

from app.infrastructure.llm import (
    DeepSeekModelProvider,
    ModelMessage,
    ModelProviderError,
    ModelRequest,
)


def _request() -> ModelRequest:
    return ModelRequest(
        prompt_name="exercise",
        prompt_version="1.0.0",
        messages=(ModelMessage(role="user", content="Return JSON"),),
    )


@pytest.mark.asyncio
async def test_deepseek_provider_retries_and_records_usage() -> None:
    calls = 0
    delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.headers["Authorization"] == "Bearer secret"
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}
        if calls == 1:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(
            200,
            json={
                "id": "request-1",
                "model": "deepseek-v4-flash",
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 4,
                    "total_tokens": 15,
                },
            },
        )

    async def no_wait(delay: float) -> None:
        delays.append(delay)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.deepseek.com"
    ) as client:
        provider = DeepSeekModelProvider(
            api_key="secret", client=client, max_retries=2, sleep=no_wait
        )
        response = await provider.complete(_request())

    assert response.content == '{"ok":true}'
    assert response.usage.total_tokens == 15
    assert provider.usage.request_count == 1
    assert calls == 2
    assert delays == [0.5]


@pytest.mark.asyncio
async def test_deepseek_provider_does_not_retry_authentication_error() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": "invalid key"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.deepseek.com"
    ) as client:
        provider = DeepSeekModelProvider(api_key="bad", client=client)
        with pytest.raises(ModelProviderError) as captured:
            await provider.complete(_request())

    assert captured.value.retryable is False
    assert captured.value.status_code == 401
    assert calls == 1
