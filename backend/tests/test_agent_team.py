"""Stage 20 Agent Team DAG, isolation, policy, artifact and API gates."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from app.agent.execution import AgentRuntime, SqliteAgentRunStore
from app.agent.policy import (
    AgentPolicyEngine,
    AgentPolicyService,
    DataLabel,
    DataSource,
    IntegrityLevel,
    Sensitivity,
    TrustLevel,
)
from app.agent.team import (
    AgentCard,
    AgentTeamService,
    ArtifactDraft,
    EvaluatorRoleAdapter,
    RoleRegistry,
    TeamBudget,
    TeamFailurePolicy,
    TeamPart,
    TeamRunRequest,
    TeamTask,
    TeamTaskSpec,
    TeamTaskStatus,
)
from app.config import Settings
from app.main import create_app


class RecordingAdapter:
    def __init__(self, *, block: bool = False, fail: bool = False) -> None:
        self.active = 0
        self.max_active = 0
        self.calls: list[TeamTask] = []
        self.block = block
        self.fail = fail
        self.started = asyncio.Event()

    @property
    def card(self) -> AgentCard:
        return AgentCard(
            role_id="worker",
            version="1.0.0",
            description="Test worker with one attenuated read Tool.",
            capabilities=["bounded_test_work"],
            input_contract="test-input-v1",
            output_contract="test-output-v1",
            allowed_tools=["test.read"],
            read_only=True,
        )

    async def execute(self, task: TeamTask) -> ArtifactDraft:
        self.calls.append(task)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.set()
        try:
            if self.block:
                await asyncio.Event().wait()
            await asyncio.sleep(0.02)
            if self.fail:
                raise RuntimeError("worker failed")
            return ArtifactDraft(
                parts=[
                    TeamPart(
                        kind="json",
                        value={"task_key": task.task_key},
                        labels=[
                            DataLabel(
                                source=DataSource.SUBAGENT,
                                source_ref=f"worker:{task.id}",
                                trust=TrustLevel.UNTRUSTED,
                                sensitivity=Sensitivity.INTERNAL,
                                integrity=IntegrityLevel.HASHED,
                            )
                        ],
                    )
                ],
                used_tokens=10,
            )
        finally:
            self.active -= 1


async def _service(
    tmp_path: Path, adapter: RecordingAdapter | None = None
) -> tuple[SqliteAgentRunStore, object, AgentTeamService, RecordingAdapter]:
    store = await SqliteAgentRunStore.open(tmp_path / "checkpoint.db")
    run, _ = await store.create_or_get(
        "daily_learning", "team-test", engine_version="dynamic_v2"
    )
    await store.set_status(run.run_id, "running")
    worker = adapter or RecordingAdapter()
    registry = RoleRegistry()
    registry.register(worker)
    registry.register(EvaluatorRoleAdapter())
    service = AgentTeamService(
        store=store,
        registry=registry,
        policy=AgentPolicyService(AgentPolicyEngine(), store),
        poll_seconds=0.005,
        deadline_seconds=2,
        max_parallel_children=2,
        max_total_tokens=2_000,
    )
    return store, run, service, worker


def _request(run_id: str, tasks: list[TeamTaskSpec]) -> TeamRunRequest:
    return TeamRunRequest(
        parent_run_id=run_id,
        plan_step_id="team-step",
        tasks=tasks,
        budget=TeamBudget(
            max_total_tokens=sum(item.allocated_tokens for item in tasks),
            deadline_seconds=1,
            max_parallel_children=2,
            max_children=8,
        ),
        failure_policy=TeamFailurePolicy.FAIL_FAST,
    )


@pytest.mark.asyncio
async def test_bounded_parallel_fanout_and_verified_dag_fanin(
    tmp_path: Path,
) -> None:
    store, run, service, worker = await _service(tmp_path)
    try:
        request = _request(
            run.run_id,  # type: ignore[attr-defined]
            [
                TeamTaskSpec(
                    task_key="left",
                    role_id="worker",
                    objective="Left branch",
                    allocated_tokens=200,
                ),
                TeamTaskSpec(
                    task_key="right",
                    role_id="worker",
                    objective="Right branch",
                    allocated_tokens=200,
                ),
                TeamTaskSpec(
                    task_key="evaluate",
                    role_id="evaluator",
                    objective="Inspect both verified artifacts",
                    dependency_keys=["left", "right"],
                    parts=[
                        TeamPart(
                            kind="json",
                            value={
                                "answer": "combined",
                                "rubric": ["grounded"],
                                "evidence_ids": ["left", "right"],
                            },
                        )
                    ],
                    allocated_tokens=100,
                ),
            ],
        )

        result = await service.execute(request)

        assert result.completed == 3
        assert result.failed == 0
        assert worker.max_active == 2
        evaluator_task = next(
            item for item in result.tasks if item.task_key == "evaluate"
        )
        dependency_parts = [
            item for item in evaluator_task.parts if item.kind == "evidence_ref"
        ]
        assert len(dependency_parts) == 2
        assert all(artifact.verified for artifact in result.artifacts)
        assert service.verifier.revalidate(result.artifacts[-1]) is True
        assert result.tasks[0].child_grants[0].resource_pattern.endswith(
            ":tool:test.read"
        )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_secret_delegation_is_denied_before_adapter_and_audited(
    tmp_path: Path,
) -> None:
    store, run, service, worker = await _service(tmp_path)
    try:
        secret = DataLabel(
            source=DataSource.USER,
            source_ref="credential:test",
            trust=TrustLevel.USER_ASSERTED,
            sensitivity=Sensitivity.SECRET,
        )
        result = await service.execute(
            _request(
                run.run_id,  # type: ignore[attr-defined]
                [
                    TeamTaskSpec(
                        task_key="secret",
                        role_id="worker",
                        objective="Do not run",
                        parts=[
                            TeamPart(kind="text", value="redacted", labels=[secret])
                        ],
                        allocated_tokens=200,
                    )
                ],
            )
        )

        assert result.failed == 1
        assert "secret_to_subagent" in (result.tasks[0].error_code or "")
        assert worker.calls == []
        decisions = await service.policy.list_decisions(run_id=run.run_id)  # type: ignore[attr-defined]
        assert decisions[0].reason == "secret_to_subagent"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_duplicate_team_manifest_reuses_artifacts(tmp_path: Path) -> None:
    store, run, service, worker = await _service(tmp_path)
    try:
        request = _request(
            run.run_id,  # type: ignore[attr-defined]
            [
                TeamTaskSpec(
                    task_key="once",
                    role_id="worker",
                    objective="Run once",
                    allocated_tokens=200,
                )
            ],
        )
        first = await service.execute(request)
        second = await service.execute(request)

        assert len(worker.calls) == 1
        assert second.artifacts[0].id == first.artifacts[0].id
        assert len(await service.list_tasks(parent_run_id=run.run_id)) == 1  # type: ignore[attr-defined]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_parent_cancellation_propagates_to_active_team_task(
    tmp_path: Path,
) -> None:
    adapter = RecordingAdapter(block=True)
    store, run, service, _ = await _service(tmp_path, adapter)
    try:
        execution = asyncio.create_task(
            service.execute(
                _request(
                    run.run_id,  # type: ignore[attr-defined]
                    [
                        TeamTaskSpec(
                            task_key="blocked",
                            role_id="worker",
                            objective="Wait",
                            allocated_tokens=200,
                        )
                    ],
                )
            )
        )
        await asyncio.wait_for(adapter.started.wait(), timeout=1)
        await store.request_cancel(run.run_id)  # type: ignore[attr-defined]
        result = await asyncio.wait_for(execution, timeout=1)

        assert result.cancelled == 1
        assert result.tasks[0].error_code == "parent_cancelled"
    finally:
        await store.close()


def test_team_manifest_rejects_cycles_and_budget_overcommit() -> None:
    with pytest.raises(ValidationError, match="acyclic"):
        _request(
            "run",
            [
                TeamTaskSpec(
                    task_key="a",
                    role_id="worker",
                    objective="A",
                    dependency_keys=["b"],
                    allocated_tokens=100,
                ),
                TeamTaskSpec(
                    task_key="b",
                    role_id="worker",
                    objective="B",
                    dependency_keys=["a"],
                    allocated_tokens=100,
                ),
            ],
        )
    with pytest.raises(ValidationError, match="reservations"):
        TeamRunRequest(
            parent_run_id="run",
            plan_step_id="step",
            tasks=[
                TeamTaskSpec(
                    task_key="a",
                    role_id="worker",
                    objective="A",
                    allocated_tokens=200,
                )
            ],
            budget=TeamBudget(max_total_tokens=100, deadline_seconds=1),
        )


@pytest.mark.asyncio
async def test_team_api_exposes_roles_tasks_and_artifacts(tmp_path: Path) -> None:
    store, run, service, _ = await _service(tmp_path)
    try:
        await service.execute(
            _request(
                run.run_id,  # type: ignore[attr-defined]
                [
                    TeamTaskSpec(
                        task_key="api",
                        role_id="worker",
                        objective="API task",
                        allocated_tokens=200,
                    )
                ],
            )
        )
        runtime = AgentRuntime(
            checkpointer=object(),  # type: ignore[arg-type]
            run_store=store,
            daily_graph=object(),  # type: ignore[arg-type]
            goal_graph=object(),  # type: ignore[arg-type]
            tools=object(),  # type: ignore[arg-type]
            model=None,
            retention_days=30,
            dynamic_kernel=None,
            team=service,
        )
        application = create_app(
            Settings(environment="test", data_dir=tmp_path / "data")
        )
        application.state.agent_runtime = runtime
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            roles = await client.get("/api/v1/agent/team/roles")
            tasks = await client.get(
                "/api/v1/agent/team/tasks",
                params={"parent_run_id": run.run_id},  # type: ignore[attr-defined]
            )
            artifacts = await client.get(
                "/api/v1/agent/team/artifacts",
                params={"parent_run_id": run.run_id},  # type: ignore[attr-defined]
            )
        assert roles.status_code == 200
        assert {item["role_id"] for item in roles.json()} == {
            "worker",
            "evaluator",
        }
        assert tasks.json()[0]["status"] == TeamTaskStatus.COMPLETED
        assert artifacts.json()[0]["verified"] is True
    finally:
        await store.close()
