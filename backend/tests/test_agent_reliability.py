"""Stage 21 seeded replay, fault, grading, aggregation and API gates."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.agent.execution import AgentRuntime, SqliteAgentRunStore
from app.agent.reliability import (
    AgentVariant,
    ContractScenarioExecutor,
    EnvironmentSnapshot,
    FaultController,
    FaultKind,
    FaultSpec,
    InjectedFault,
    ReliabilityGrader,
    ReliabilityRunner,
    ReliabilityRunRequest,
    ReliabilityScenario,
    TrajectoryStep,
    TrialManifest,
    TrialOutcome,
    build_manifest,
)
from app.config import Settings
from app.main import create_app


def _environment() -> EnvironmentSnapshot:
    return EnvironmentSnapshot(
        runtime_version="v3-stage-21",
        policy_version="agent-trust-1.0.0",
        prompt_versions={"planner": "3.0.0"},
        provider_profile_versions={"fixture": "1.0.0"},
        dataset_version="reliability-test-1.0.0",
    )


def _scenario(*, faults: list[FaultSpec] | None = None) -> ReliabilityScenario:
    return ReliabilityScenario(
        id="test.research_recovery",
        version="1.0.0",
        title="Recover a grounded research task",
        task_kind="research",
        role="lead",
        variant=AgentVariant.TEAM,
        required_evidence_ids=["evidence:one"],
        max_tokens=1_000,
        max_estimated_cost_usd=0.1,
        faults=faults or [],
    )


def _request(
    scenario: ReliabilityScenario, *, trials: int = 3, seed: int = 17
) -> ReliabilityRunRequest:
    return ReliabilityRunRequest(
        scenarios=[scenario],
        trials_per_scenario=trials,
        base_seed=seed,
        environment=_environment(),
    )


def test_manifest_is_seeded_content_addressed_and_replayable() -> None:
    scenario = _scenario(
        faults=[
            FaultSpec(
                id="timeout-1",
                kind=FaultKind.TIMEOUT,
                target="tool.search",
                probability=0.5,
            )
        ]
    )
    first = build_manifest(
        scenario, trial_index=0, seed=41, environment=_environment()
    )
    replay = build_manifest(
        scenario, trial_index=0, seed=41, environment=_environment()
    )
    changed = build_manifest(
        scenario, trial_index=1, seed=42, environment=_environment()
    )

    assert first.manifest_hash == replay.manifest_hash
    assert first.faults == replay.faults
    assert first.manifest_hash != changed.manifest_hash


def test_fault_controller_honors_bounded_occurrences() -> None:
    scenario = _scenario(
        faults=[
            FaultSpec(
                id="tool-flap",
                kind=FaultKind.TRANSIENT_TOOL,
                target="tool.retrieve",
                occurrence=2,
            )
        ]
    )
    manifest = build_manifest(
        scenario, trial_index=0, seed=1, environment=_environment()
    )
    controller = FaultController(manifest)

    for occurrence in (1, 2):
        with pytest.raises(InjectedFault) as raised:
            controller.hit(FaultKind.TRANSIENT_TOOL, "tool.retrieve")
        assert raised.value.record.occurrence == occurrence
    assert controller.hit(FaultKind.TRANSIENT_TOOL, "tool.retrieve") is None


@pytest.mark.asyncio
async def test_contract_executor_covers_six_faults_and_blocks_injection() -> None:
    faults = [
        FaultSpec(
            id=f"fault-{kind.value}",
            kind=kind,
            target="context.ingress" if kind == FaultKind.PROMPT_INJECTION else "step",
            recoverable=True,
            safety_critical=kind == FaultKind.PROMPT_INJECTION,
        )
        for kind in FaultKind
    ]
    report = await ReliabilityRunner(ContractScenarioExecutor()).run(
        _request(_scenario(faults=faults), trials=2)
    )

    assert report.fixture_only is True
    assert report.metrics.pass_at_k == 1
    assert report.metrics.pass_power_k == 1
    assert report.metrics.recovery_rate == 1
    assert report.metrics.safety_rate == 1
    assert {record.kind for record in report.trials[0].outcome.faults} == set(
        FaultKind
    )
    assert any(
        step.status == "blocked" for step in report.trials[0].outcome.trajectory
    )


def test_safety_and_budget_are_non_compensable_grading_gates() -> None:
    outcome = TrialOutcome(
        final_status="completed",
        actual_evidence_ids=["evidence:one"],
        trajectory=[
            TrajectoryStep(
                index=0,
                action="unsafe_context_import",
                status="succeeded",
                evidence_ids=["evidence:one"],
                tokens=1_001,
                safety_violations=["prompt_injection_executed"],
            )
        ],
        faults=[],
        total_tokens=1_001,
        estimated_cost_usd=0.01,
    )

    grade = ReliabilityGrader().grade(_scenario(), outcome)

    assert grade.task_success is True
    assert grade.safety_passed is False
    assert grade.trajectory_invariants_passed is False
    assert grade.score == 0
    assert grade.passed is False
    assert set(grade.violations) == {
        "prompt_injection_executed",
        "token_budget_exceeded",
    }


class AlternatingExecutor:
    """Deterministic test double that makes pass@k differ from pass^k."""

    async def execute(
        self,
        scenario: ReliabilityScenario,
        manifest: TrialManifest,
        faults: FaultController,
    ) -> TrialOutcome:
        passed = manifest.trial_index % 2 == 0
        return TrialOutcome(
            final_status="completed" if passed else "failed",
            actual_evidence_ids=scenario.required_evidence_ids if passed else [],
            trajectory=[
                TrajectoryStep(
                    index=0,
                    action="alternate",
                    status="succeeded" if passed else "failed",
                    duplicate=not passed,
                    tokens=100 + manifest.trial_index,
                    estimated_cost_usd=0.01 * (manifest.trial_index + 1),
                )
            ],
            faults=faults.records(),
            total_tokens=100 + manifest.trial_index,
            estimated_cost_usd=0.01 * (manifest.trial_index + 1),
        )


@pytest.mark.asyncio
async def test_pass_k_cost_percentile_redundancy_and_slices() -> None:
    report = await ReliabilityRunner(AlternatingExecutor()).run(
        _request(_scenario(), trials=3)
    )

    assert report.metrics.pass_at_k == 1
    assert report.metrics.pass_power_k == 0
    assert report.metrics.trial_pass_rate == pytest.approx(2 / 3)
    assert report.metrics.redundancy_rate == pytest.approx(1 / 3)
    assert report.metrics.tokens_p95 == 102
    assert report.metrics.cost_p95_usd == pytest.approx(0.03)
    assert {item.dimension for item in report.slices} == {
        "fault",
        "task_kind",
        "role",
        "variant",
    }


@pytest.mark.asyncio
async def test_report_persistence_api_and_admin_limit(tmp_path: Path) -> None:
    store = await SqliteAgentRunStore.open(tmp_path / "checkpoint.db")
    runner = ReliabilityRunner(
        ContractScenarioExecutor(), observer=store.save_reliability_report
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
        reliability=runner,
        reliability_admin_enabled=True,
        reliability_max_trials_per_scenario=2,
    )
    application = create_app(Settings(environment="test", data_dir=tmp_path / "data"))
    application.state.agent_runtime = runtime
    transport = httpx.ASGITransport(app=application)
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            created = await client.post(
                "/api/v1/agent/reliability/run",
                json=_request(_scenario(), trials=2).model_dump(mode="json"),
            )
            listed = await client.get("/api/v1/agent/reliability/reports")
            fetched = await client.get(
                f"/api/v1/agent/reliability/reports/{created.json()['id']}"
            )
            too_many = await client.post(
                "/api/v1/agent/reliability/run",
                json=_request(_scenario(), trials=3).model_dump(mode="json"),
            )
            runtime.reliability_admin_enabled = False
            forbidden = await client.post(
                "/api/v1/agent/reliability/run",
                json=_request(_scenario(), trials=1).model_dump(mode="json"),
            )

        assert created.status_code == 200
        assert listed.json()[0]["id"] == created.json()["id"]
        assert fetched.json()["trials"][0]["manifest"]["manifest_hash"]
        assert too_many.status_code == 422
        assert forbidden.status_code == 403
    finally:
        await store.close()
