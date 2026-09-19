"""Strict contracts for capability-aware Model routing."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class GatewayContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ModelCapability(StrEnum):
    JSON_MODE = "json_mode"
    TOOL_CALLING = "tool_calling"
    LONG_CONTEXT = "long_context"
    VISION = "vision"


class LatencyClass(StrEnum):
    FAST = "fast"
    STANDARD = "standard"
    BATCH = "batch"


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class RouteOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"


class ModelProfile(GatewayContract):
    profile_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    provider_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    model: str = Field(min_length=1, max_length=200)
    capabilities: set[ModelCapability]
    context_window: int = Field(ge=1_024)
    max_output_tokens: int = Field(ge=1)
    input_cost_per_million_usd: float = Field(default=0, ge=0)
    output_cost_per_million_usd: float = Field(default=0, ge=0)
    expected_latency_ms: float = Field(default=10_000, gt=0)
    latency_class: LatencyClass = LatencyClass.STANDARD
    data_residency: str = Field(default="unspecified", min_length=1, max_length=50)
    priority: int = Field(default=0, ge=-100, le=100)
    enabled: bool = True


class ModelRequirement(GatewayContract):
    required_capabilities: set[ModelCapability]
    estimated_input_tokens: int = Field(ge=0)
    max_output_tokens: int = Field(ge=1)
    max_estimated_cost_usd: float | None = Field(default=None, ge=0)
    deadline_ms: float | None = Field(default=None, gt=0)
    data_residency: str | None = None


class RouteCandidate(GatewayContract):
    provider_id: str
    model: str
    estimated_cost_usd: float = Field(ge=0)
    score: tuple[int, float, float, str]
    explanation: list[str]


class RoutePlan(GatewayContract):
    candidates: list[RouteCandidate]
    rejected: dict[str, list[str]] = Field(default_factory=dict)


class ProviderHealth(GatewayContract):
    provider_id: str
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = Field(default=0, ge=0)
    opened_at: datetime | None = None
    half_open_probe_in_flight: bool = False


class RouteAttempt(GatewayContract):
    provider_id: str
    model: str
    retryable: bool
    succeeded: bool
    duration_ms: float = Field(ge=0)
    error_type: str | None = None
    circuit_state_before: CircuitState
    circuit_state_after: CircuitState


class ModelRouteRecord(GatewayContract):
    id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str | None = None
    prompt_name: str
    prompt_version: str
    requirement: ModelRequirement
    selected_provider_id: str | None = None
    selected_model: str | None = None
    outcome: RouteOutcome
    attempts: list[RouteAttempt]
    rejected: dict[str, list[str]] = Field(default_factory=dict)
    estimated_cost_usd: float = Field(default=0, ge=0)
    fallback_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ModelRouteObserver(Protocol):
    async def __call__(self, record: ModelRouteRecord) -> None: ...
