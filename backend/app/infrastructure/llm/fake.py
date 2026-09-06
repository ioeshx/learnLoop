"""Deterministic provider used by tests and key-free local demonstrations."""

import json
from collections import deque
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from app.infrastructure.llm.errors import ModelProviderError
from app.infrastructure.llm.models import (
    ModelRequest,
    ModelResponse,
    TokenUsage,
    TokenUsageTracker,
    UsageSnapshot,
)

FakeOutput = str | Mapping[str, Any] | BaseModel | ModelResponse


class FakeModelProvider:
    """Return queued responses by prompt name and retain requests for assertions."""

    def __init__(
        self,
        responses: Mapping[str, Sequence[FakeOutput]],
        *,
        model: str = "fake-model",
        usage_per_response: TokenUsage | None = None,
    ) -> None:
        self._responses = {name: deque(values) for name, values in responses.items()}
        self._model = model
        self._usage_per_response = usage_per_response or TokenUsage(10, 5, 15)
        self._tracker = TokenUsageTracker()
        self.requests: list[ModelRequest] = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def usage(self) -> UsageSnapshot:
        return self._tracker.snapshot

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        queue = self._responses.get(request.prompt_name)
        if not queue:
            raise ModelProviderError(
                f"no fake response configured for prompt '{request.prompt_name}'",
                provider=self.name,
                retryable=False,
            )
        configured = queue.popleft()
        if isinstance(configured, ModelResponse):
            response = configured
        else:
            response = ModelResponse(
                content=_serialize(configured),
                model=self._model,
                usage=self._usage_per_response,
            )
        self._tracker.record(response.usage)
        return response

    async def aclose(self) -> None:
        return None


def _serialize(value: str | Mapping[str, Any] | BaseModel) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump_json()
    return json.dumps(value, ensure_ascii=False)
