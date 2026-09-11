"""DeepSeek Chat Completions provider using its OpenAI-compatible HTTP API."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.infrastructure.llm.errors import ModelProviderError
from app.infrastructure.llm.models import (
    ModelRequest,
    ModelResponse,
    TokenUsage,
    TokenUsageTracker,
    UsageSnapshot,
)

RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class _DeepSeekMessage(BaseModel):
    content: str


class _DeepSeekChoice(BaseModel):
    message: _DeepSeekMessage


class _DeepSeekUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class _DeepSeekResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    model: str
    choices: list[_DeepSeekChoice] = Field(min_length=1)
    usage: _DeepSeekUsage = Field(default_factory=_DeepSeekUsage)


class DeepSeekModelProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "deepseek-v4-flash",
        base_url: str = "https://api.deepseek.com",
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not api_key.strip():
            raise ValueError("DeepSeek API key must not be empty")
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        self._model = model
        self._max_retries = max_retries
        self._sleep = sleep
        self._tracker = TokenUsageTracker()
        self._owns_client = client is None
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
        )

    @property
    def name(self) -> str:
        return "deepseek"

    @property
    def usage(self) -> UsageSnapshot:
        return self._tracker.snapshot

    async def complete(self, request: ModelRequest) -> ModelResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
            "max_tokens": request.max_output_tokens,
            "stream": False,
        }
        if request.json_mode:
            payload["response_format"] = {"type": "json_object"}

        for attempt in range(self._max_retries + 1):
            try:
                # async request to DeepSeek API
                response = await self._client.post(
                    "/chat/completions", json=payload, headers=self._headers
                )
            except (httpx.TimeoutException, httpx.TransportError) as error:
                if attempt >= self._max_retries:
                    raise ModelProviderError(
                        "DeepSeek request failed after retries",
                        provider=self.name,
                        retryable=True,
                    ) from error
                await self._sleep(_retry_delay(attempt))
                continue
            # if retrable status code, retry if attempts remain, else raise error
            if response.status_code in RETRYABLE_STATUS_CODES:
                if attempt < self._max_retries:
                    await self._sleep(_retry_delay(attempt))
                    continue
                raise ModelProviderError(
                    f"DeepSeek returned HTTP {response.status_code} after retries",
                    provider=self.name,
                    retryable=True,
                    status_code=response.status_code,
                )
            # if non-retriable error, raise error
            if response.is_error:
                raise ModelProviderError(
                    f"DeepSeek returned HTTP {response.status_code}: "
                    f"{response.text[:500]}",
                    provider=self.name,
                    retryable=False,
                    status_code=response.status_code,
                )
            # try to parse the response JSON into our model, raise error if invalid
            try:
                parsed = _DeepSeekResponse.model_validate(response.json())
            except ValueError as error:
                raise ModelProviderError(
                    "DeepSeek returned an invalid response envelope",
                    provider=self.name,
                    retryable=False,
                    status_code=response.status_code,
                ) from error
            # token usage tracking and return the model response
            usage = TokenUsage(
                input_tokens=parsed.usage.prompt_tokens,
                output_tokens=parsed.usage.completion_tokens,
                total_tokens=parsed.usage.total_tokens,
            )
            self._tracker.record(usage)
            return ModelResponse(
                content=parsed.choices[0].message.content,
                model=parsed.model,
                usage=usage,
                request_id=parsed.id,
            )

        raise AssertionError("retry loop exited unexpectedly")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _retry_delay(attempt: int) -> float:
    return min(0.5 * float(1 << attempt), 4.0)
