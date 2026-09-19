"""Provider-neutral model request, response, and usage contracts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class ModelRequest:
    prompt_name: str
    prompt_version: str
    messages: tuple[ModelMessage, ...]
    max_output_tokens: int = 4_096
    json_mode: bool = True
    is_repair: bool = False
    required_capabilities: frozenset[str] = frozenset()
    estimated_input_tokens: int | None = None
    max_estimated_cost_usd: float | None = None
    deadline_ms: float | None = None
    data_residency: str | None = None
    route_affinity_key: str | None = None

    def __post_init__(self) -> None:
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        if self.estimated_input_tokens is not None and self.estimated_input_tokens < 0:
            raise ValueError("estimated_input_tokens cannot be negative")
        if self.max_estimated_cost_usd is not None and self.max_estimated_cost_usd < 0:
            raise ValueError("max_estimated_cost_usd cannot be negative")
        if self.deadline_ms is not None and self.deadline_ms <= 0:
            raise ValueError("deadline_ms must be positive")


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def __post_init__(self) -> None:
        if min(self.input_tokens, self.output_tokens, self.total_tokens) < 0:
            raise ValueError("token counts cannot be negative")

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    request_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int


class TokenUsageTracker:
    def __init__(self) -> None:
        self._request_count = 0
        self._usage = TokenUsage()

    def record(self, usage: TokenUsage) -> None:
        self._request_count += 1
        self._usage = self._usage + usage

    @property
    def snapshot(self) -> UsageSnapshot:
        return UsageSnapshot(
            request_count=self._request_count,
            input_tokens=self._usage.input_tokens,
            output_tokens=self._usage.output_tokens,
            total_tokens=self._usage.total_tokens,
        )


@dataclass(frozen=True, slots=True)
class ModelResponse:
    content: str
    model: str
    usage: TokenUsage
    request_id: str | None = None
    route: ModelRouteMetadata | None = None


@dataclass(frozen=True, slots=True)
class ModelRouteMetadata:
    """Metadata-only routing result returned by a Model Gateway."""

    route_id: str
    provider_id: str
    fallback_count: int
    attempted_provider_ids: tuple[str, ...]
    estimated_cost_usd: float


@dataclass(frozen=True, slots=True)
class ModelCallObservation:
    run_id: str | None
    prompt_name: str
    prompt_version: str
    model: str
    usage: TokenUsage
    duration_ms: float
    attempts: int
    repaired: bool
    error: str | None = None


ModelCallObserver = Callable[[ModelCallObservation], Awaitable[None]]


class ModelProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def usage(self) -> UsageSnapshot: ...

    async def complete(self, request: ModelRequest) -> ModelResponse: ...

    async def aclose(self) -> None: ...
