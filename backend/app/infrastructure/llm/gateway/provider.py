"""ModelProvider facade with bounded compatible fallback and route audit."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from time import perf_counter

from app.infrastructure.llm.errors import ModelProviderError
from app.infrastructure.llm.gateway.circuit import CircuitBreakerPool
from app.infrastructure.llm.gateway.models import (
    CircuitState,
    ModelCapability,
    ModelProfile,
    ModelRequirement,
    ModelRouteObserver,
    ModelRouteRecord,
    RouteAttempt,
    RouteOutcome,
)
from app.infrastructure.llm.gateway.router import CapabilityRouter
from app.infrastructure.llm.models import (
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelRouteMetadata,
    TokenUsageTracker,
    UsageSnapshot,
)
from app.observability import current_agent_run_id


class ModelGatewayProvider:
    """Route Model requests without weakening capability or budget constraints.

    Fallback is attempted only for ``ModelProviderError(retryable=True)`` and
    gateway timeouts. Non-retryable auth/validation failures are immediately
    surfaced so configuration defects cannot be hidden by another Provider.
    """

    def __init__(
        self,
        entries: list[tuple[ModelProfile, ModelProvider]],
        *,
        failure_threshold: int = 3,
        recovery_seconds: float = 30,
        default_deadline_ms: float = 60_000,
        default_max_estimated_cost_usd: float | None = None,
        observer: ModelRouteObserver | None = None,
    ) -> None:
        if not entries:
            raise ValueError("Model Gateway requires at least one Provider")
        profiles = [profile for profile, _ in entries]
        self.router = CapabilityRouter(profiles)
        self.providers: Mapping[str, ModelProvider] = {
            profile.provider_id: provider for profile, provider in entries
        }
        if len(self.providers) != len(entries):
            raise ValueError("Model Gateway provider_id values must be unique")
        self.breakers = CircuitBreakerPool(
            list(self.providers),
            failure_threshold=failure_threshold,
            recovery_seconds=recovery_seconds,
        )
        self.default_deadline_ms = default_deadline_ms
        self.default_max_estimated_cost_usd = default_max_estimated_cost_usd
        self._observer = observer
        self._tracker = TokenUsageTracker()
        self._affinity: dict[str, str] = {}

    @property
    def name(self) -> str:
        return "model-gateway"

    @property
    def usage(self) -> UsageSnapshot:
        return self._tracker.snapshot

    def set_observer(self, observer: ModelRouteObserver | None) -> None:
        self._observer = observer

    async def complete(self, request: ModelRequest) -> ModelResponse:
        requirement = _requirement_for(
            request,
            default_deadline_ms=self.default_deadline_ms,
            default_max_cost=self.default_max_estimated_cost_usd,
        )
        affinity_key = request.route_affinity_key
        preferred = self._affinity.get(affinity_key) if affinity_key else None
        plan = self.router.route(
            requirement,
            unavailable=self.breakers.unavailable(),
            preferred_provider_id=preferred,
        )
        if not plan.candidates:
            record = ModelRouteRecord(
                run_id=current_agent_run_id(),
                prompt_name=request.prompt_name,
                prompt_version=request.prompt_version,
                requirement=requirement,
                outcome=RouteOutcome.REJECTED,
                attempts=[],
                rejected=plan.rejected,
            )
            await self._observe(record)
            raise ModelProviderError(
                "no Model route satisfies the request",
                provider=self.name,
                retryable=False,
            )

        attempts: list[RouteAttempt] = []
        deadline_seconds = (requirement.deadline_ms or self.default_deadline_ms) / 1000
        started = perf_counter()
        for candidate in plan.candidates:
            if not self.breakers.acquire(candidate.provider_id):
                continue
            elapsed = perf_counter() - started
            remaining = deadline_seconds - elapsed
            if remaining <= 0:
                break
            attempt_started = perf_counter()
            circuit_before = self.breakers.snapshot(candidate.provider_id).state
            try:
                async with asyncio.timeout(remaining):
                    response = await self.providers[candidate.provider_id].complete(
                        request
                    )
            except TimeoutError:
                wrapped = ModelProviderError(
                    "Model Gateway deadline exceeded",
                    provider=candidate.provider_id,
                    retryable=True,
                )
                self.breakers.record_failure(candidate.provider_id, retryable=True)
                attempts.append(
                    _failed_attempt(
                        candidate.provider_id,
                        candidate.model,
                        wrapped,
                        attempt_started,
                        circuit_before,
                        self.breakers.snapshot(candidate.provider_id).state,
                    )
                )
                last_error: ModelProviderError = wrapped
                continue
            except ModelProviderError as error:
                self.breakers.record_failure(
                    candidate.provider_id, retryable=error.retryable
                )
                attempts.append(
                    _failed_attempt(
                        candidate.provider_id,
                        candidate.model,
                        error,
                        attempt_started,
                        circuit_before,
                        self.breakers.snapshot(candidate.provider_id).state,
                    )
                )
                last_error = error
                if not error.retryable:
                    await self._observe(
                        _record(
                            request,
                            requirement,
                            plan.rejected,
                            attempts,
                            RouteOutcome.FAILED,
                        )
                    )
                    raise
                continue

            self.breakers.record_success(candidate.provider_id)
            attempts.append(
                RouteAttempt(
                    provider_id=candidate.provider_id,
                    model=response.model,
                    retryable=False,
                    succeeded=True,
                    duration_ms=(perf_counter() - attempt_started) * 1_000,
                    circuit_state_before=circuit_before,
                    circuit_state_after=self.breakers.snapshot(
                        candidate.provider_id
                    ).state,
                )
            )
            if affinity_key:
                self._affinity[affinity_key] = candidate.provider_id
            self._tracker.record(response.usage)
            record = _record(
                request,
                requirement,
                plan.rejected,
                attempts,
                RouteOutcome.SUCCEEDED,
                selected_provider_id=candidate.provider_id,
                selected_model=response.model,
                estimated_cost_usd=candidate.estimated_cost_usd,
            )
            await self._observe(record)
            return ModelResponse(
                content=response.content,
                model=response.model,
                usage=response.usage,
                request_id=response.request_id,
                route=ModelRouteMetadata(
                    route_id=record.id,
                    provider_id=candidate.provider_id,
                    fallback_count=max(0, len(attempts) - 1),
                    attempted_provider_ids=tuple(
                        item.provider_id for item in attempts
                    ),
                    estimated_cost_usd=candidate.estimated_cost_usd,
                ),
            )

        record = _record(
            request,
            requirement,
            plan.rejected,
            attempts,
            RouteOutcome.FAILED,
        )
        await self._observe(record)
        if attempts:
            raise last_error
        raise ModelProviderError(
            "No Model Provider remained before the request deadline",
            provider=self.name,
            retryable=True,
        )

    async def aclose(self) -> None:
        seen: set[int] = set()
        for provider in self.providers.values():
            if id(provider) in seen:
                continue
            seen.add(id(provider))
            await provider.aclose()

    async def _observe(self, record: ModelRouteRecord) -> None:
        if self._observer is not None:
            await self._observer(record)


def _requirement_for(
    request: ModelRequest,
    *,
    default_deadline_ms: float,
    default_max_cost: float | None,
) -> ModelRequirement:
    capabilities = {ModelCapability(item) for item in request.required_capabilities}
    if request.json_mode:
        capabilities.add(ModelCapability.JSON_MODE)
    estimated_input = request.estimated_input_tokens
    if estimated_input is None:
        estimated_input = sum(len(item.content) for item in request.messages) // 3 + 1
    return ModelRequirement(
        required_capabilities=capabilities,
        estimated_input_tokens=estimated_input,
        max_output_tokens=request.max_output_tokens,
        max_estimated_cost_usd=(
            request.max_estimated_cost_usd
            if request.max_estimated_cost_usd is not None
            else default_max_cost
        ),
        deadline_ms=request.deadline_ms or default_deadline_ms,
        data_residency=request.data_residency,
    )


def _failed_attempt(
    provider_id: str,
    model: str,
    error: ModelProviderError,
    started: float,
    circuit_state_before: CircuitState,
    circuit_state_after: CircuitState,
) -> RouteAttempt:
    return RouteAttempt(
        provider_id=provider_id,
        model=model,
        retryable=error.retryable,
        succeeded=False,
        duration_ms=(perf_counter() - started) * 1_000,
        error_type=type(error).__name__,
        circuit_state_before=circuit_state_before,
        circuit_state_after=circuit_state_after,
    )


def _record(
    request: ModelRequest,
    requirement: ModelRequirement,
    rejected: dict[str, list[str]],
    attempts: list[RouteAttempt],
    outcome: RouteOutcome,
    *,
    selected_provider_id: str | None = None,
    selected_model: str | None = None,
    estimated_cost_usd: float = 0,
) -> ModelRouteRecord:
    return ModelRouteRecord(
        run_id=current_agent_run_id(),
        prompt_name=request.prompt_name,
        prompt_version=request.prompt_version,
        requirement=requirement,
        selected_provider_id=selected_provider_id,
        selected_model=selected_model,
        outcome=outcome,
        attempts=attempts,
        rejected=rejected,
        estimated_cost_usd=estimated_cost_usd,
        fallback_count=max(0, len(attempts) - 1),
    )
