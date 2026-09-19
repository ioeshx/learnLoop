"""Stage 19 capability routing, fallback, affinity and circuit-breaker gates."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app.agent.execution import AgentRuntime, SqliteAgentRunStore
from app.config import Settings
from app.infrastructure.llm import (
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    StructuredModel,
    TokenUsage,
    UsageSnapshot,
)
from app.infrastructure.llm.gateway import (
    CapabilityRouter,
    CircuitBreakerPool,
    CircuitState,
    LatencyClass,
    ModelCapability,
    ModelGatewayProvider,
    ModelProfile,
    ModelRequirement,
    ModelRouteRecord,
    RouteOutcome,
)
from app.main import create_app
from app.observability import bind_agent_run, reset_agent_run


class ScriptedProvider:
    def __init__(
        self,
        name: str,
        outcomes: list[ModelResponse | ModelProviderError],
    ) -> None:
        self._name = name
        self.outcomes = deque(outcomes)
        self.requests: list[ModelRequest] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def usage(self) -> UsageSnapshot:
        return UsageSnapshot(0, 0, 0, 0)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        outcome = self.outcomes.popleft()
        if isinstance(outcome, ModelProviderError):
            raise outcome
        return outcome

    async def aclose(self) -> None:
        return None


def _profile(
    provider_id: str,
    *,
    capabilities: set[ModelCapability] | None = None,
    priority: int = 0,
    cost: float = 1,
    latency_ms: float = 100,
    residency: str = "cn",
) -> ModelProfile:
    return ModelProfile(
        provider_id=provider_id,
        model=f"{provider_id}-model",
        capabilities=capabilities or {ModelCapability.JSON_MODE},
        context_window=16_000,
        max_output_tokens=4_096,
        input_cost_per_million_usd=cost,
        output_cost_per_million_usd=cost,
        expected_latency_ms=latency_ms,
        latency_class=LatencyClass.FAST,
        data_residency=residency,
        priority=priority,
    )


def _response(model: str) -> ModelResponse:
    return ModelResponse(
        content='{"ok":true}',
        model=model,
        usage=TokenUsage(20, 5, 25),
    )


def _request(*, repair: bool = False) -> ModelRequest:
    return ModelRequest(
        prompt_name="gateway-test",
        prompt_version="1.0.0",
        messages=(),
        estimated_input_tokens=1_000,
        max_output_tokens=500,
        max_estimated_cost_usd=0.01,
        deadline_ms=2_000,
        data_residency="cn",
        route_affinity_key="logical-call-1",
        is_repair=repair,
    )


def test_router_filters_capability_cost_deadline_and_residency() -> None:
    valid = _profile("valid", cost=1, latency_ms=100)
    expensive = _profile("expensive", cost=100, latency_ms=100)
    slow = _profile("slow", cost=1, latency_ms=5_000)
    wrong_region = _profile("wrong-region", residency="us")
    no_json = _profile("no-json", capabilities={ModelCapability.TOOL_CALLING})
    router = CapabilityRouter(
        [valid, expensive, slow, wrong_region, no_json]
    )
    requirement = ModelRequirement(
        required_capabilities={ModelCapability.JSON_MODE},
        estimated_input_tokens=1_000,
        max_output_tokens=500,
        max_estimated_cost_usd=0.01,
        deadline_ms=2_000,
        data_residency="cn",
    )

    plan = router.route(requirement)

    assert [item.provider_id for item in plan.candidates] == ["valid"]
    assert plan.rejected["expensive"] == ["estimated_cost_exceeded"]
    assert plan.rejected["slow"] == ["deadline_preflight_failed"]
    assert plan.rejected["wrong-region"] == ["data_residency_mismatch"]
    assert plan.rejected["no-json"] == ["missing_capabilities:json_mode"]


@pytest.mark.asyncio
async def test_retryable_fallback_keeps_repair_affinity() -> None:
    primary = ScriptedProvider(
        "primary",
        [
            ModelProviderError(
                "rate limited", provider="primary", retryable=True, status_code=429
            )
        ],
    )
    fallback = ScriptedProvider(
        "fallback",
        [_response("fallback-model"), _response("fallback-model")],
    )
    records: list[ModelRouteRecord] = []

    async def observe(record: ModelRouteRecord) -> None:
        records.append(record)

    gateway = ModelGatewayProvider(
        [
            (_profile("primary", priority=10), primary),
            (_profile("fallback", priority=0), fallback),
        ],
        observer=observe,
    )

    first = await gateway.complete(_request())
    repair = await gateway.complete(_request(repair=True))

    assert first.route is not None
    assert first.route.provider_id == "fallback"
    assert first.route.fallback_count == 1
    assert first.route.attempted_provider_ids == ("primary", "fallback")
    assert repair.route is not None
    assert repair.route.provider_id == "fallback"
    assert len(primary.requests) == 1
    assert len(fallback.requests) == 2
    assert records[0].outcome == RouteOutcome.SUCCEEDED


@pytest.mark.asyncio
async def test_gateway_never_hides_non_retryable_provider_error() -> None:
    auth_error = ModelProviderError(
        "invalid credential", provider="primary", retryable=False, status_code=401
    )
    primary = ScriptedProvider("primary", [auth_error])
    fallback = ScriptedProvider("fallback", [_response("fallback-model")])
    gateway = ModelGatewayProvider(
        [
            (_profile("primary", priority=10), primary),
            (_profile("fallback"), fallback),
        ]
    )

    with pytest.raises(ModelProviderError, match="invalid credential"):
        await gateway.complete(_request())

    assert len(primary.requests) == 1
    assert fallback.requests == []


def test_circuit_breaker_opens_half_opens_and_closes() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    pool = CircuitBreakerPool(
        ["provider"],
        failure_threshold=2,
        recovery_seconds=30,
        clock=lambda: now,
    )
    pool.record_failure("provider", retryable=True)
    assert pool.snapshots()[0].state == CircuitState.CLOSED
    pool.record_failure("provider", retryable=True)
    assert pool.snapshots()[0].state == CircuitState.OPEN
    assert pool.acquire("provider") is False

    now += timedelta(seconds=31)
    assert pool.acquire("provider") is True
    assert pool.snapshots()[0].state == CircuitState.HALF_OPEN
    assert pool.acquire("provider") is False
    pool.record_success("provider")
    assert pool.snapshots()[0].state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_gateway_fails_preflight_without_invoking_provider() -> None:
    provider = ScriptedProvider("only", [_response("never-used")])
    gateway = ModelGatewayProvider(
        [(_profile("only", cost=100), provider)],
    )

    with pytest.raises(ModelProviderError, match="no Model route"):
        await gateway.complete(_request())

    assert provider.requests == []


@pytest.mark.asyncio
async def test_gateway_route_audit_and_health_api(tmp_path: Path) -> None:
    store = await SqliteAgentRunStore.open(tmp_path / "checkpoint.db")
    try:
        run, _ = await store.create_or_get(
            "daily_learning", "gateway-api", engine_version="dynamic_v2"
        )
        provider = ScriptedProvider("only", [_response("only-model")])
        gateway = ModelGatewayProvider([(_profile("only"), provider)])
        gateway.set_observer(store.save_model_route)
        run_token = bind_agent_run(run.run_id)
        try:
            response = await gateway.complete(_request())
        finally:
            reset_agent_run(run_token)
        assert response.route is not None

        runtime = AgentRuntime(
            checkpointer=object(),  # type: ignore[arg-type]
            run_store=store,
            daily_graph=object(),  # type: ignore[arg-type]
            goal_graph=object(),  # type: ignore[arg-type]
            tools=object(),  # type: ignore[arg-type]
            model=StructuredModel(gateway),
            retention_days=30,
            dynamic_kernel=None,
        )
        application = create_app(
            Settings(environment="test", data_dir=tmp_path / "data")
        )
        application.state.agent_runtime = runtime
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            profiles = await client.get("/api/v1/agent/model-gateway/profiles")
            health = await client.get("/api/v1/agent/model-gateway/health")
            routes = await client.get(
                "/api/v1/agent/model-gateway/routes",
                params={"run_id": run.run_id},
            )
        assert profiles.status_code == 200
        assert profiles.json()[0]["provider_id"] == "only"
        assert health.json()[0]["state"] == "closed"
        assert routes.json()[0]["selected_provider_id"] == "only"
        events = await store.list_events(run.run_id)
        assert events[-1].event == "model_routed"
    finally:
        await store.close()
