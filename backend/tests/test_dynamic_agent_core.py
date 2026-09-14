"""Deterministic tests for the Stage 11 dynamic Agent kernel."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from app.agent.dynamic.budget import BudgetLedger
from app.agent.dynamic.context import ContextPackage, MinimalContextCompiler
from app.agent.dynamic.kernel import DynamicAgentKernel
from app.agent.dynamic.models import (
    ActionType,
    AgentAction,
    AgentPlan,
    BudgetUsage,
    DynamicAgentState,
    PlanStep,
    ReplanProposal,
    RunBudget,
    StepStatus,
    ToolRisk,
)
from app.agent.dynamic.policy import PolicyResult
from app.agent.dynamic.tools import ToolExecutor, ToolInput, ToolRegistry
from app.agent.dynamic.verifier import DeterministicVerifier, apply_replan
from app.agent.execution.comparison import compare_runs
from app.agent.execution.models import AgentEvent, AgentRun
from app.infrastructure.llm import TokenUsage


def _plan() -> AgentPlan:
    return AgentPlan(
        objective="完成当前学习 Session",
        steps=[
            PlanStep(
                id="inspect",
                objective="读取 Session 状态",
                success_criteria=["获得状态 Observation"],
                allowed_tools=["state.read"],
            ),
            PlanStep(
                id="teach",
                objective="向学习者展示内容",
                dependencies=["inspect"],
                success_criteria=["内容已展示"],
            ),
        ],
    )


def test_plan_rejects_cycles_and_replan_preserves_completed_evidence() -> None:
    with pytest.raises(ValueError, match="acyclic"):
        AgentPlan(
            objective="bad",
            steps=[
                PlanStep(
                    id="a",
                    objective="a",
                    dependencies=["b"],
                    success_criteria=["a"],
                ),
                PlanStep(
                    id="b",
                    objective="b",
                    dependencies=["a"],
                    success_criteria=["b"],
                ),
            ],
        )

    plan = _plan()
    completed = plan.model_copy(
        update={
            "steps": [
                plan.steps[0].model_copy(
                    update={
                        "status": StepStatus.COMPLETED,
                        "evidence_ids": ["observation-1"],
                    }
                ),
                plan.steps[1],
            ]
        }
    )
    with pytest.raises(ValueError, match="cannot remove"):
        apply_replan(
            completed,
            ReplanProposal(
                reason="hide history",
                steps=[
                    PlanStep(
                        id="replacement",
                        objective="replace everything",
                        success_criteria=["done"],
                    ),
                    PlanStep(
                        id="finish",
                        objective="finish",
                        dependencies=["replacement"],
                        success_criteria=["done"],
                    ),
                ],
            ),
        )


def test_budget_is_deterministic_and_stops_before_extra_calls() -> None:
    budget = RunBudget(
        max_steps=2,
        max_model_calls=1,
        max_tool_calls=1,
        max_input_tokens=1_000,
        max_output_tokens=500,
        max_total_tokens=1_500,
    )
    ledger = BudgetLedger(budget, BudgetUsage())
    assert ledger.preflight("model").allowed is True
    ledger.record_model(TokenUsage(10, 5, 15))
    decision = ledger.preflight("model")
    assert decision.allowed is False
    assert decision.terminal_reason == "budget_exhausted"


class ReadInput(ToolInput):
    key: str


@pytest.mark.asyncio
async def test_tool_executor_enforces_allowlist_and_schema() -> None:
    registry = ToolRegistry()

    async def read(value: BaseModel, _: str) -> object:
        parsed = ReadInput.model_validate(value)
        return {"value": parsed.key}

    registry.register(
        name="state.read",
        description="Read test state.",
        input_type=ReadInput,
        handler=read,
        risk=ToolRisk.LOW,
    )
    executor = ToolExecutor(registry)
    denied = await executor.execute(
        name="state.read",
        arguments={"key": "x"},
        allowed_tools=set(),
        run_id="run",
        plan_step_id="step",
    )
    invalid = await executor.execute(
        name="state.read",
        arguments={"unknown": "x"},
        allowed_tools={"state.read"},
        run_id="run",
        plan_step_id="step",
    )
    assert denied.succeeded is False
    assert denied.error and denied.error.kind == "permission_denied"
    assert invalid.succeeded is False
    assert invalid.error and invalid.error.kind == "invalid_arguments"


class ScriptedPolicy:
    """Fake policy derives evidence ids from Context, like a structured model would."""

    def __init__(self, *, interrupt_first: bool = False) -> None:
        self.interrupt_first = interrupt_first

    async def create_plan(self, **_: Any) -> PolicyResult[AgentPlan]:
        return PolicyResult(_plan(), TokenUsage(10, 5, 15))

    async def decide(
        self, context: ContextPackage
    ) -> PolicyResult[AgentAction]:
        current = context.values["current_step"]
        assert isinstance(current, dict)
        step_id = str(current["id"])
        observations = context.values["recent_observations"]
        assert isinstance(observations, list)
        sources = [item["source"] for item in observations if isinstance(item, dict)]

        if self.interrupt_first and "user.input" not in sources:
            action = AgentAction(
                action=ActionType.REQUEST_INPUT,
                plan_step_id=step_id,
                reason_summary="需要学习者答案",
                expected_observation="获得答案",
                content="请给出你的答案。",
                input_type="answer",
            )
        elif step_id == "inspect" and "state.read" not in sources:
            action = AgentAction(
                action=ActionType.CALL_TOOL,
                plan_step_id=step_id,
                reason_summary="读取状态",
                expected_observation="返回状态",
                tool_name="state.read",
                arguments={"key": "session"},
            )
        elif step_id == "teach" and "agent.content" not in sources:
            action = AgentAction(
                action=ActionType.PRESENT_CONTENT,
                plan_step_id=step_id,
                reason_summary="展示讲解",
                expected_observation="内容已展示",
                content="BFS 使用 queue 逐层访问节点。",
            )
        else:
            matching = next(
                item
                for item in reversed(observations)
                if isinstance(item, dict)
                and (
                    item["source"] == "state.read"
                    if step_id == "inspect"
                    else item["source"] == "agent.content"
                )
            )
            action = AgentAction(
                action=ActionType.COMPLETE_STEP,
                plan_step_id=step_id,
                reason_summary="证据满足成功标准",
                expected_observation="Verifier 通过",
                evidence_ids=[str(matching["id"])],
            )
        return PolicyResult(action, TokenUsage(10, 5, 15))

    async def replan(self, **_: Any) -> PolicyResult[ReplanProposal]:
        raise AssertionError("happy path must not invoke Replanner")


class MemoryRunStore:
    """Kernel contract fake；只保存公开 durable state 和 Trace。"""

    def __init__(self, run: AgentRun) -> None:
        self.run = run
        self.state: str | None = None
        self.events: list[AgentEvent] = []
        self.plans: list[tuple[int, str]] = []
        self.artifacts: dict[str, dict[str, object]] = {}
        self.context_snapshots: dict[str, dict[str, object]] = {}

    async def get(self, _: str) -> AgentRun:
        return self.run

    async def set_status(
        self, _: str, status: str, *, terminal_reason: str | None = None, **__: Any
    ) -> AgentRun:
        self.run = replace(
            self.run,
            status=status,  # type: ignore[arg-type]
            terminal_reason=terminal_reason,  # type: ignore[arg-type]
            version=self.run.version + 1,
        )
        return self.run

    async def append_event(
        self,
        run_id: str,
        event: str,
        *,
        node: str | None = None,
        data: dict[str, object] | None = None,
    ) -> AgentEvent:
        item = AgentEvent(
            run_id=run_id,
            sequence=len(self.events) + 1,
            event=event,  # type: ignore[arg-type]
            node=node,
            timestamp=datetime.now(UTC),
            data=data or {},
        )
        self.events.append(item)
        return item

    async def save_dynamic_state(self, _: str, state_json: str) -> None:
        self.state = state_json

    async def load_dynamic_state(self, _: str) -> str | None:
        return self.state

    async def save_plan_version(self, _: str, version: int, plan: str) -> None:
        self.plans.append((version, plan))

    async def start_tool_call(self, **_: Any) -> None:
        return None

    async def finish_tool_call(self, **_: Any) -> None:
        return None

    async def save_context_artifact(
        self, run_id: str, *, kind: str, content: object, **_: Any
    ) -> dict[str, object]:
        serialized = json.dumps(
            content,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        digest = hashlib.sha256(serialized.encode()).hexdigest()
        artifact_id = f"artifact-{len(self.artifacts) + 1}"
        created_at = datetime.now(UTC).isoformat()
        self.artifacts[artifact_id] = {
            "artifact_id": artifact_id,
            "run_id": run_id,
            "kind": kind,
            "version": 1,
            "sha256": digest,
            "content": content,
            "created_at": created_at,
            "expires_at": None,
        }
        return {
            "artifact_id": artifact_id,
            "kind": kind,
            "version": 1,
            "sha256": digest,
            "created_at": created_at,
            "expires_at": None,
        }

    async def get_context_artifact(
        self, artifact_id: str
    ) -> dict[str, object] | None:
        return self.artifacts.get(artifact_id)

    async def save_context_snapshot(
        self,
        metadata: dict[str, object],
        *,
        context_values: dict[str, object] | None = None,
    ) -> None:
        stored = dict(metadata)
        if context_values is not None:
            stored["context"] = context_values
        self.context_snapshots[str(metadata["snapshot_id"])] = stored

    async def record_context_token_observation(
        self, snapshot_id: str, observed_input_tokens: int
    ) -> None:
        self.context_snapshots[snapshot_id][
            "observed_model_input_tokens"
        ] = observed_input_tokens


def _run() -> AgentRun:
    now = datetime.now(UTC)
    return AgentRun(
        run_id="run-1",
        thread_id="thread-1",
        graph_kind="daily_learning",
        resource_id="session-1",
        engine_version="dynamic_v2",
        parent_run_id=None,
        attempt_no=1,
        status="created",
        terminal_reason=None,
        cancel_requested=False,
        version=1,
        created_at=now,
        updated_at=now,
    )


def _kernel(store: MemoryRunStore, policy: ScriptedPolicy) -> DynamicAgentKernel:
    registry = ToolRegistry()

    async def read(value: BaseModel, _: str) -> object:
        parsed = ReadInput.model_validate(value)
        return {"key": parsed.key, "status": "ready"}

    # Bootstrap uses the same contract name as production.
    class SessionInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        session_id: str

    async def session(value: BaseModel, _: str) -> object:
        parsed = SessionInput.model_validate(value)
        return {
            "session_id": parsed.session_id,
            "knowledge_node_title": "BFS",
        }

    registry.register(
        name="session.get_state",
        description="Read Session.",
        input_type=SessionInput,
        handler=session,
    )
    registry.register(
        name="state.read",
        description="Read state.",
        input_type=ReadInput,
        handler=read,
    )
    return DynamicAgentKernel(
        store=store,  # type: ignore[arg-type]
        policy=policy,
        tools=ToolExecutor(registry),
        context=MinimalContextCompiler(
            artifact_reader=store,
            max_context_tokens=4_000,
            reserved_output_tokens=512,
        ),
        verifier=DeterministicVerifier(),
        budget=RunBudget(),
    )


@pytest.mark.asyncio
async def test_kernel_completes_a_bounded_verified_plan() -> None:
    run = _run()
    store = MemoryRunStore(run)
    events = [event async for event in _kernel(store, ScriptedPolicy()).execute(run)]
    assert events[-1].event == "run_completed"
    assert store.run.status == "completed"
    assert store.state is not None
    state = DynamicAgentState.model_validate_json(store.state)
    assert state.plan.complete is True
    assert state.usage.model_calls == 5
    assert store.context_snapshots
    assert {item["purpose"] for item in store.context_snapshots.values()} >= {
        "planner",
        "decision",
    }
    assert all("artifact" in item.data for item in state.observations)
    assert any(event.event == "verification_completed" for event in events)
    assert any(event.event == "context_snapshot_created" for event in events)


@pytest.mark.asyncio
async def test_kernel_persists_interrupt_and_resumes_with_matching_id() -> None:
    run = _run()
    store = MemoryRunStore(run)
    kernel = _kernel(store, ScriptedPolicy(interrupt_first=True))
    first = [event async for event in kernel.execute(run)]
    assert first[-1].event == "run_paused"
    assert store.state is not None
    paused = DynamicAgentState.model_validate_json(store.state)
    assert paused.pending_interrupt is not None

    resumed_run = store.run
    resumed = [
        event
        async for event in kernel.execute(
            resumed_run,
            resume={
                "interrupt_id": paused.pending_interrupt.id,
                "value": {"selected_options": ["queue"]},
            },
        )
    ]
    assert resumed[-1].event == "run_completed"
    assert store.run.status == "completed"


def test_paired_run_comparison_rejects_unrelated_resources() -> None:
    fixed = replace(_run(), engine_version="fixed_v1", resource_id="session-a")
    dynamic = replace(_run(), resource_id="session-b")
    with pytest.raises(ValueError, match="same graph and resource"):
        compare_runs(fixed, [], [], [], dynamic, [], [], [])
