"""Stage 18 Trust labels, authorization, taint, and audit gates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel

from app.agent.dynamic.context import ContextCompiler, ContextReferenceError
from app.agent.dynamic.models import (
    AgentPlan,
    BudgetUsage,
    DynamicAgentState,
    Observation,
    PlanStep,
    RunBudget,
)
from app.agent.dynamic.tools import ToolExecutor, ToolInvocation, ToolRegistry
from app.agent.execution import AgentRuntime, SqliteAgentRunStore
from app.agent.policy import (
    AgentPolicyEngine,
    AgentPolicyService,
    CapabilityGrant,
    DataLabel,
    DataSource,
    InjectionSignal,
    IntegrityLevel,
    PolicyAction,
    PolicyEffect,
    PolicyReason,
    PolicyRequest,
    PolicySubject,
    Sensitivity,
    TrustLevel,
    join_labels,
)
from app.config import Settings
from app.main import create_app


def _label(
    *,
    trust: TrustLevel = TrustLevel.VERIFIED,
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
    injection: InjectionSignal | None = None,
) -> DataLabel:
    return DataLabel(
        source=DataSource.RESOURCE,
        source_ref="resource:test",
        trust=trust,
        sensitivity=sensitivity,
        integrity=IntegrityLevel.HASHED,
        injection_signals=[injection] if injection is not None else [],
    )


def _request(
    *,
    labels: list[DataLabel] | None = None,
    grants: list[CapabilityGrant] | None = None,
    read_only: bool = True,
    risk: str = "low",
    approved: bool = False,
) -> PolicyRequest:
    subject = PolicySubject(
        id="run:1:lead", kind="lead_agent", run_id="run-1"
    )
    return PolicyRequest(
        subject=subject,
        action=PolicyAction.TOOL_EXECUTE,
        capability="tool:test.write" if not read_only else "tool:test.read",
        resource="run:run-1:step:one:tool:test",
        read_only=read_only,
        risk=risk,
        approved=approved,
        labels=labels or [],
        grants=grants or [],
        request_ref="test-request",
    )


def _grant(*, capability: str = "tool:test.read") -> CapabilityGrant:
    return CapabilityGrant(
        subject_id="run:1:lead",
        capability=capability,
        resource_pattern="run:run-1:step:*",
        source="plan:1:step:one",
    )


def test_label_join_is_monotonic_and_system_trust_is_guarded() -> None:
    trusted = _label(trust=TrustLevel.VERIFIED)
    untrusted = _label(
        trust=TrustLevel.UNTRUSTED,
        sensitivity=Sensitivity.PERSONAL,
        injection=InjectionSignal.INSTRUCTION_LIKE,
    )
    joined = join_labels(
        [trusted, untrusted],
        source=DataSource.TOOL,
        source_ref="tool-result:test",
    )

    assert joined.trust == TrustLevel.UNTRUSTED
    assert joined.sensitivity == Sensitivity.PERSONAL
    assert joined.integrity == IntegrityLevel.HASHED
    assert joined.injection_signals == [InjectionSignal.INSTRUCTION_LIKE]
    assert set(joined.parent_label_ids) == {trusted.id, untrusted.id}
    with pytest.raises(ValueError, match="Only the system source"):
        DataLabel(
            source=DataSource.MODEL,
            source_ref="forged",
            trust=TrustLevel.SYSTEM,
            sensitivity=Sensitivity.PUBLIC,
        )


def test_policy_engine_denies_missing_grant_secret_and_injection_side_effect() -> None:
    engine = AgentPolicyEngine()
    missing = engine.evaluate(_request())
    assert missing.effect == PolicyEffect.DENY
    assert missing.reason == PolicyReason.NO_CAPABILITY_GRANT

    secret = engine.evaluate(
        _request(
            read_only=False,
            labels=[_label(sensitivity=Sensitivity.SECRET)],
            grants=[_grant(capability="tool:test.write")],
        )
    )
    assert secret.reason == PolicyReason.SECRET_TO_TOOL

    injected = engine.evaluate(
        _request(
            read_only=False,
            labels=[_label(injection=InjectionSignal.INSTRUCTION_LIKE)],
            grants=[_grant(capability="tool:test.write")],
        )
    )
    assert injected.reason == PolicyReason.INJECTION_TO_SIDE_EFFECT


def test_policy_engine_requires_approval_and_checks_expiry_and_depth() -> None:
    engine = AgentPolicyEngine()
    high_risk = engine.evaluate(
        _request(grants=[_grant()], risk="high", approved=False)
    )
    assert high_risk.effect == PolicyEffect.REQUIRE_APPROVAL
    assert high_risk.reason == PolicyReason.HIGH_RISK_REQUIRES_APPROVAL
    assert (
        engine.evaluate(_request(grants=[_grant()], risk="high", approved=True)).effect
        == PolicyEffect.ALLOW
    )

    expired = _grant().model_copy(
        update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    assert (
        engine.evaluate(_request(grants=[expired])).reason
        == PolicyReason.GRANT_EXPIRED
    )
    subject = _request(grants=[_grant()]).subject.model_copy(
        update={"delegation_depth": 1}
    )
    deep = _request(grants=[_grant()]).model_copy(update={"subject": subject})
    assert (
        engine.evaluate(deep).reason == PolicyReason.DELEGATION_DEPTH_EXCEEDED
    )


class EmptyInput(BaseModel):
    pass


@pytest.mark.asyncio
async def test_tool_executor_persists_idempotent_policy_audit_and_taint(
    tmp_path: Path,
) -> None:
    store = await SqliteAgentRunStore.open(tmp_path / "checkpoint.db")
    try:
        run, _ = await store.create_or_get(
            "daily_learning", "policy-tool", engine_version="dynamic_v2"
        )
        policy = AgentPolicyService(AgentPolicyEngine(), store)
        registry = ToolRegistry()

        async def handler(_: BaseModel, __: ToolInvocation) -> object:
            return {"status": "ok", "credential": "not-recorded-in-audit"}

        registry.register(
            name="test.read",
            description="Read deterministic test state.",
            input_type=EmptyInput,
            handler=handler,
        )
        executor = ToolExecutor(registry, policy=policy)
        subject = PolicySubject(
            id=f"run:{run.run_id}:lead",
            kind="lead_agent",
            run_id=run.run_id,
        )
        grant = CapabilityGrant(
            id="stable-grant",
            subject_id=subject.id,
            capability="tool:test.read",
            resource_pattern=f"run:{run.run_id}:step:one:tool:test.read",
            source="plan:1:step:one",
        )
        untrusted = _label(trust=TrustLevel.UNTRUSTED)
        first = await executor.execute(
            name="test.read",
            arguments={},
            allowed_tools={"test.read"},
            run_id=run.run_id,
            plan_step_id="one",
            subject=subject,
            grants=[grant],
            input_labels=[untrusted],
        )
        second = await executor.execute(
            name="test.read",
            arguments={},
            allowed_tools={"test.read"},
            run_id=run.run_id,
            plan_step_id="one",
            subject=subject,
            grants=[grant],
            input_labels=[untrusted],
        )
        assert first.succeeded is True
        assert first.policy_decision_id == second.policy_decision_id
        assert first.data_labels[0].trust == TrustLevel.UNTRUSTED
        audits = await policy.list_decisions(run_id=run.run_id)
        assert len(audits) == 1
        assert "credential" not in audits[0].model_dump_json()
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_context_compiler_fails_closed_for_secret_observation() -> None:
    observation = Observation(
        id="secret-observation",
        action_id="action-1",
        plan_step_id="one",
        source="tool.secret",
        succeeded=True,
        summary="Secret result exists but must not enter Model Context.",
        data={"artifact": "redacted"},
        data_labels=[_label(sensitivity=Sensitivity.SECRET)],
    )
    plan = AgentPlan(
        objective="Test secret filtering",
        steps=[
            PlanStep(
                id="one",
                objective="Use evidence safely",
                success_criteria=["No secret reaches the model"],
                evidence_ids=[observation.id],
            ),
            PlanStep(
                id="two",
                objective="Finish",
                dependencies=["one"],
                success_criteria=["Finished"],
            ),
        ],
    )
    state = DynamicAgentState(
        run_id="run-secret",
        plan=plan,
        budget=RunBudget(),
        usage=BudgetUsage(),
        observations=[observation],
    )
    compiler = ContextCompiler()
    request = compiler.request_for(state, plan.steps[0], [])

    with pytest.raises(ContextReferenceError, match="secret-labeled"):
        await compiler.compile(request, state, plan.steps[0], [])


@pytest.mark.asyncio
async def test_context_compiler_persists_central_model_policy_decision(
    tmp_path: Path,
) -> None:
    store = await SqliteAgentRunStore.open(tmp_path / "checkpoint.db")
    try:
        run, _ = await store.create_or_get(
            "daily_learning", "policy-context", engine_version="dynamic_v2"
        )
        secret = Observation(
            id="secret-observation",
            action_id="action-1",
            plan_step_id="one",
            source="tool.secret",
            succeeded=True,
            summary="Secret result exists.",
            data={"artifact": "redacted"},
            data_labels=[_label(sensitivity=Sensitivity.SECRET)],
        )
        plan = AgentPlan(
            objective="Test central Context authorization",
            steps=[
                PlanStep(
                    id="one",
                    objective="Use evidence safely",
                    success_criteria=["No secret reaches the model"],
                    evidence_ids=[secret.id],
                ),
                PlanStep(
                    id="two",
                    objective="Finish",
                    dependencies=["one"],
                    success_criteria=["Finished"],
                ),
            ],
        )
        state = DynamicAgentState(
            run_id=run.run_id,
            plan=plan,
            budget=RunBudget(),
            usage=BudgetUsage(),
            observations=[secret],
        )
        service = AgentPolicyService(AgentPolicyEngine(), store)
        compiler = ContextCompiler(policy=service)
        request = compiler.request_for(state, plan.steps[0], [])

        with pytest.raises(ContextReferenceError, match="secret_to_model"):
            await compiler.compile(request, state, plan.steps[0], [])

        decisions = await service.list_decisions(run_id=run.run_id)
        assert len(decisions) == 1
        assert decisions[0].action == PolicyAction.MODEL_CONTEXT
        assert decisions[0].effect == PolicyEffect.DENY
        assert decisions[0].reason == PolicyReason.SECRET_TO_MODEL
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_policy_api_lists_audit_and_admin_guards_simulation(
    tmp_path: Path,
) -> None:
    store = await SqliteAgentRunStore.open(tmp_path / "checkpoint.db")
    try:
        run, _ = await store.create_or_get(
            "daily_learning", "policy-api", engine_version="dynamic_v2"
        )
        service = AgentPolicyService(AgentPolicyEngine(), store)
        request = _request(grants=[_grant()]).model_copy(
            update={
                "subject": PolicySubject(
                    id=f"run:{run.run_id}:lead",
                    kind="lead_agent",
                    run_id=run.run_id,
                ),
                "grants": [
                    _grant().model_copy(
                        update={
                            "subject_id": f"run:{run.run_id}:lead",
                            "resource_pattern": (
                                f"run:{run.run_id}:step:*"
                            ),
                        }
                    )
                ],
                "resource": f"run:{run.run_id}:step:one:tool:test",
            }
        )
        await service.decide(request)
        runtime = AgentRuntime(
            checkpointer=object(),  # type: ignore[arg-type]
            run_store=store,
            daily_graph=object(),  # type: ignore[arg-type]
            goal_graph=object(),  # type: ignore[arg-type]
            tools=object(),  # type: ignore[arg-type]
            model=None,
            retention_days=30,
            dynamic_kernel=None,
            agent_policy=service,
            agent_policy_admin_enabled=False,
        )
        application = create_app(
            Settings(environment="test", data_dir=tmp_path / "data")
        )
        application.state.agent_runtime = runtime
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            listed = await client.get(
                "/api/v1/agent/policy/decisions", params={"run_id": run.run_id}
            )
            blocked = await client.post(
                "/api/v1/agent/policy/simulate",
                json={"request": request.model_dump(mode="json")},
            )
            runtime.agent_policy_admin_enabled = True
            simulated = await client.post(
                "/api/v1/agent/policy/simulate",
                json={"request": request.model_dump(mode="json")},
            )
        assert listed.status_code == 200
        assert listed.json()[0]["run_id"] == run.run_id
        assert blocked.status_code == 403
        assert simulated.status_code == 200
        assert simulated.json()["effect"] == "allow"
        assert len(await service.list_decisions(run_id=run.run_id)) == 1
    finally:
        await store.close()
