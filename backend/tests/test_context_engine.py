"""Deterministic Stage 12 Context Engine contract and persistence tests."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.agent.dynamic.context import (
    ConservativeTokenCounter,
    ContextBudgetError,
    ContextCompiler,
    ContextReferenceError,
    token_counter_for,
)
from app.agent.dynamic.models import (
    AgentPlan,
    BudgetUsage,
    DynamicAgentState,
    Observation,
    PlanStep,
    RunBudget,
    StepStatus,
    ToolRisk,
    ToolSpec,
)
from app.agent.execution.store import SqliteAgentRunStore
from app.agent.memory.models import MemoryQuery, MemoryRecall
from app.domain.memory import MemoryKind, MemoryTrust


def _hash(value: object) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


class ArtifactReader:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, object]] = {}

    async def get_context_artifact(
        self, artifact_id: str
    ) -> dict[str, object] | None:
        return self.values.get(artifact_id)


class MemoryRetriever:
    def __init__(self) -> None:
        self.queries: list[MemoryQuery] = []

    async def retrieve(self, query: MemoryQuery) -> list[MemoryRecall]:
        self.queries.append(query)
        return [
            MemoryRecall(
                memory_id="memory-1",
                memory_key="preference:format",
                kind=MemoryKind.SEMANTIC,
                content="Learner prefers examples before definitions.",
                attributes={"profile_change": True},
                score=0.88,
                confidence=1.0,
                importance=0.8,
                trust=MemoryTrust.USER_ASSERTED,
                goal_id=None,
                knowledge_node_id=None,
                valid_from=datetime.now(UTC),
                expires_at=None,
                evidence=[{"source_type": "user_correction", "source_id": "ui"}],
            )
        ]


def _tool(name: str = "state.read") -> ToolSpec:
    return ToolSpec(
        name=name,
        description="Read deterministic test state.",
        risk=ToolRisk.LOW,
        read_only=True,
        idempotent=True,
        input_schema={
            "type": "object",
            "properties": {"key": {"type": "string", "title": "Key"}},
            "required": ["key"],
        },
    )


def _state(observations: list[Observation]) -> tuple[DynamicAgentState, PlanStep]:
    evidence_ids = [item.id for item in observations if item.action_id == "evidence"]
    completed = PlanStep(
        id="inspect",
        objective="Inspect state",
        success_criteria=["State exists"],
        allowed_tools=["state.read"],
        status=StepStatus.COMPLETED,
        evidence_ids=evidence_ids,
    )
    current = PlanStep(
        id="teach",
        objective="Teach the learner",
        dependencies=["inspect"],
        success_criteria=["Content shown"],
        allowed_tools=["state.read"],
        status=StepStatus.ACTIVE,
    )
    return (
        DynamicAgentState(
            run_id="run-1",
            plan=AgentPlan(
                objective="Learn BFS", steps=[completed, current]
            ),
            budget=RunBudget(),
            usage=BudgetUsage(),
            observations=observations,
        ),
        current,
    )


@pytest.mark.asyncio
async def test_compiler_preserves_mandatory_partitions_and_reports_eviction() -> None:
    observations = [
        Observation(
            action_id="evidence" if index == 0 else f"action-{index}",
            plan_step_id="inspect" if index == 0 else "teach",
            source="state.read",
            succeeded=True,
            summary=f"Observation {index}",
            data={"payload": f"value-{index}-" + ("x" * 1_200)},
        )
        for index in range(8)
    ]
    state, step = _state(observations)
    compiler = ContextCompiler(
        max_context_tokens=1_600,
        reserved_output_tokens=256,
        max_recent_observations=20,
    )
    request = compiler.request_for(state, step, [_tool()])
    package = await compiler.compile(request, state, step, [_tool()])

    assert package.snapshot.total_input_tokens <= package.snapshot.input_token_limit
    assert package.values["evidence"]
    assert package.values["available_tools"]
    assert package.snapshot.omitted_source_ids
    assert any(
        item.reason == "input_token_budget"
        for item in package.snapshot.truncations
    )
    assert package.snapshot.input_hash != package.snapshot.output_hash


@pytest.mark.asyncio
async def test_active_memory_is_a_traceable_optional_context_partition() -> None:
    retriever = MemoryRetriever()
    state, step = _state([])
    state = state.model_copy(
        update={
            "user_id": "user-1",
            "goal_id": "goal-1",
            "knowledge_node_id": "node-1",
        }
    )
    compiler = ContextCompiler(memory_retriever=retriever, memory_enabled=True)

    package = await compiler.compile(
        compiler.request_for(state, step, [_tool()]), state, step, [_tool()]
    )

    assert retriever.queries[0].goal_id == "goal-1"
    assert package.values["memory"][0]["memory_id"] == "memory-1"
    assert "memory:memory-1" in package.snapshot.source_ids
    memory_partition = next(
        item for item in package.snapshot.partitions if item.name == "memory"
    )
    assert memory_partition.mandatory is False
    assert memory_partition.item_count == 1


@pytest.mark.asyncio
async def test_artifact_is_loaded_just_in_time_and_integrity_is_checked() -> None:
    reader = ArtifactReader()
    content = {"lesson": "BFS uses a queue", "revision": 3}
    digest = _hash(content)
    reader.values["artifact-1"] = {
        "artifact_id": "artifact-1",
        "run_id": "run-1",
        "kind": "tool_result",
        "version": 1,
        "sha256": digest,
        "content": content,
    }
    observation = Observation(
        action_id="evidence",
        plan_step_id="inspect",
        source="state.read",
        succeeded=True,
        summary="Loaded lesson state",
        data={
            "artifact": {
                "artifact_id": "artifact-1",
                "kind": "tool_result",
                "version": 1,
                "sha256": digest,
                "created_at": datetime.now(UTC).isoformat(),
                "expires_at": None,
            }
        },
    )
    state, step = _state([observation])
    compiler = ContextCompiler(artifact_reader=reader)
    package = await compiler.compile(
        compiler.request_for(state, step, [_tool()]), state, step, [_tool()]
    )
    evidence = package.values["evidence"]
    assert isinstance(evidence, list)
    assert evidence[0]["data"] == content

    reader.values["artifact-1"]["version"] = 2
    with pytest.raises(ContextReferenceError, match="version changed"):
        await compiler.compile(
            compiler.request_for(state, step, [_tool()]), state, step, [_tool()]
        )


@pytest.mark.asyncio
async def test_compaction_keeps_evidence_and_surfaces_source_conflicts() -> None:
    first = Observation(
        action_id="evidence",
        plan_step_id="inspect",
        source="state.read",
        succeeded=True,
        summary="First value",
        data={"status": "draft"},
    )
    duplicate = Observation(
        action_id="duplicate",
        plan_step_id="teach",
        source="state.read",
        succeeded=True,
        summary="Duplicate value",
        data={"status": "draft"},
    )
    changed = Observation(
        action_id="changed",
        plan_step_id="teach",
        source="state.read",
        succeeded=True,
        summary="Changed value",
        data={"status": "ready"},
    )
    state, step = _state([first, duplicate, changed])
    compiler = ContextCompiler()
    package = await compiler.compile(
        compiler.request_for(state, step, [_tool()]), state, step, [_tool()]
    )

    assert first.id in package.snapshot.source_ids
    assert duplicate.id in package.snapshot.omitted_source_ids
    assert package.snapshot.conflicts[0].field == "status"
    assert set(package.snapshot.conflicts[0].source_ids) == {first.id, changed.id}


@pytest.mark.asyncio
async def test_expired_optional_source_is_removed_but_missing_evidence_fails() -> None:
    stale = Observation(
        action_id="stale",
        plan_step_id="teach",
        source="state.read",
        succeeded=True,
        summary="Stale state",
        data={"status": "old"},
        created_at=datetime.now(UTC) - timedelta(hours=2),
    )
    state, step = _state([stale])
    compiler = ContextCompiler(source_ttl_seconds=60)
    package = await compiler.compile(
        compiler.request_for(state, step, [_tool()]), state, step, [_tool()]
    )
    assert stale.id in package.snapshot.omitted_source_ids
    assert package.snapshot.truncations[0].reason == "source_expired"

    broken = state.model_copy(
        update={
            "plan": state.plan.model_copy(
                update={
                    "steps": [
                        state.plan.steps[0].model_copy(
                            update={"evidence_ids": ["missing"]}
                        ),
                        state.plan.steps[1],
                    ]
                }
            )
        }
    )
    with pytest.raises(ContextReferenceError, match="evidence Observations"):
        await compiler.compile(
            compiler.request_for(broken, step, [_tool()]),
            broken,
            step,
            [_tool()],
        )


@pytest.mark.asyncio
async def test_provider_tokenizer_and_mandatory_overflow_are_explicit() -> None:
    class Provider:
        name = "exact-test"

        @staticmethod
        def count_tokens(value: str) -> int:
            return len(value.split())

    counter = token_counter_for(Provider())
    assert counter.exact is True
    assert counter.name == "exact-test.count_tokens"
    assert token_counter_for(None).exact is False
    assert isinstance(token_counter_for(None), ConservativeTokenCounter)

    state, step = _state([])
    huge_tool = _tool("state.read").model_copy(
        update={"description": "word " * 20_000}
    )
    compiler = ContextCompiler(max_context_tokens=1_000, reserved_output_tokens=128)
    request = compiler.request_for(state, step, [huge_tool])
    with pytest.raises(ContextBudgetError, match="mandatory Context"):
        await compiler.compile(request, state, step, [huge_tool])

    exhausted = state.model_copy(
        update={
            "usage": state.usage.model_copy(
                update={
                    "output_tokens": state.budget.max_output_tokens - 100,
                    "total_tokens": state.budget.max_output_tokens - 100,
                }
            )
        }
    )
    with pytest.raises(ContextBudgetError, match="output token budget"):
        compiler.request_for(exhausted, step, [_tool()])


@pytest.mark.asyncio
async def test_context_snapshot_persistence_is_metadata_only_by_default(
    tmp_path: Path,
) -> None:
    store = await SqliteAgentRunStore.open(tmp_path / "context.db")
    try:
        run, _ = await store.create_or_get("daily_learning", "session-1")
        metadata: dict[str, object] = {
            "snapshot_id": "snapshot-1",
            "run_id": run.run_id,
            "purpose": "decision",
            "plan_version": 1,
            "step_id": "teach",
            "total_input_tokens": 20,
            "created_at": datetime.now(UTC).isoformat(),
        }
        await store.save_context_snapshot(
            metadata, context_values={"secret": "learner answer"}
        )
        await store.record_context_token_observation("snapshot-1", 35)

        public = await store.list_context_snapshots(run.run_id)
        debug = await store.list_context_snapshots(
            run.run_id, include_content=True
        )
        assert "context" not in public[0]
        assert debug[0]["context"] == {"secret": "learner answer"}
        assert public[0]["token_delta"] == 15
    finally:
        await store.close()
