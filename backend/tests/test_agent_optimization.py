"""Stage 17 reward, Contextual Bandit, replay, and training-data gates."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.agent.dynamic.models import (
    AgentPlan,
    BudgetUsage,
    DynamicAgentState,
    PlanStep,
    RunBudget,
    StepStatus,
)
from app.agent.execution import AgentRuntime, SqliteAgentRunStore
from app.agent.experience.models import (
    ReflectionEvidence,
    ReflectionInsight,
    RunReflection,
)
from app.agent.optimization import (
    DelayedLearningOutcome,
    ExperimentManifest,
    PolicyActivationRequest,
    PolicyOptimizationService,
    PolicyRevisionRequest,
    ReplaySample,
    TeachingContext,
    TrajectoryReview,
)
from app.config import Settings
from app.main import create_app


def _plan(*, completed: bool) -> AgentPlan:
    status = StepStatus.COMPLETED if completed else StepStatus.PENDING
    return AgentPlan(
        objective="完成学习 Session：图遍历",
        steps=[
            PlanStep(
                id="teach",
                objective="讲解图遍历",
                success_criteria=["Explanation verified"],
                allowed_tools=["research.ask"],
                status=status,
                evidence_ids=["obs-1"] if completed else [],
            ),
            PlanStep(
                id="check",
                objective="检查迁移",
                dependencies=["teach"],
                success_criteria=["Transfer verified"],
                status=status,
                evidence_ids=["obs-2"] if completed else [],
            ),
        ],
    )


async def _seed_run(
    store: SqliteAgentRunStore,
    *,
    resource_id: str,
    succeeded: bool,
    unsafe: bool = False,
) -> tuple[str, DynamicAgentState]:
    run, _ = await store.create_or_get(
        "daily_learning", resource_id, engine_version="dynamic_v2"
    )
    await store.set_status(run.run_id, "running")
    state = DynamicAgentState(
        run_id=run.run_id,
        plan=_plan(completed=succeeded),
        budget=RunBudget(max_total_tokens=10_000),
        usage=BudgetUsage(tool_calls=2, total_tokens=1_000),
    )
    await store.save_dynamic_state(run.run_id, state.model_dump_json())
    await store.append_event(
        run.run_id,
        "action_decided",
        node="teach",
        data={
            "action": "present_content",
            "plan_step_id": "teach",
            "reason_summary": "teach",
        },
    )
    await store.append_event(
        run.run_id,
        "observation_recorded",
        node="teach",
        data={"id": "obs-1", "succeeded": succeeded, "summary": "observed"},
    )
    await store.append_event(
        run.run_id,
        "verification_completed",
        node="teach",
        data={
            "status": "passed" if succeeded else "failed",
            "explanation": "checked",
        },
    )
    if unsafe:
        await store.append_event(
            run.run_id,
            "action_rejected",
            node="teach",
            data={"reason": "Plan references unavailable Tools"},
        )
    if succeeded:
        await store.set_status(run.run_id, "completed", terminal_reason="completed")
        await store.append_event(
            run.run_id, "run_completed", data={"terminal_reason": "completed"}
        )
    else:
        await store.set_status(
            run.run_id, "failed", terminal_reason="verification_failed"
        )
        await store.append_event(
            run.run_id,
            "run_failed",
            data={"terminal_reason": "verification_failed"},
        )
    return run.run_id, state


@pytest.mark.asyncio
async def test_bandit_selection_is_scope_safe_and_replay_idempotent(
    tmp_path: Path,
) -> None:
    store = await SqliteAgentRunStore.open(tmp_path / "checkpoint.db")
    try:
        disabled = PolicyOptimizationService(store, enabled=False)
        candidate = await disabled.ensure_default_policy()
        assert candidate.status == "candidate"
        assert (
            await disabled.select_strategy(
                run_id="run-none",
                plan_step_id="teach",
                context=TeachingContext(
                    progress=0.5,
                    consecutive_failures=0,
                    retrieval_available=0,
                    write_step=0,
                ),
                allowed_tools=set(),
            )
            is None
        )
    finally:
        await store.close()

    store = await SqliteAgentRunStore.open(tmp_path / "checkpoint.db")
    try:
        enabled_bootstrap = PolicyOptimizationService(store, enabled=True)
        assert (await enabled_bootstrap.ensure_default_policy()).status == "active"
    finally:
        await store.close()

    store = await SqliteAgentRunStore.open(tmp_path / "enabled.db")
    try:
        service = PolicyOptimizationService(store, enabled=True)
        await service.ensure_default_policy()
        run, _ = await store.create_or_get(
            "daily_learning", "bandit", engine_version="dynamic_v2"
        )
        context = TeachingContext(
            progress=0.5,
            consecutive_failures=0,
            retrieval_available=0,
            write_step=0,
        )
        first = await service.select_strategy(
            run_id=run.run_id,
            plan_step_id="teach",
            context=context,
            allowed_tools=set(),
        )
        second = await service.select_strategy(
            run_id=run.run_id,
            plan_step_id="teach",
            context=context,
            allowed_tools=set(),
        )
        assert first is not None
        assert second == first
        assert first.arm_id != "retrieval_grounded"
        assert first.arm_id != "worked_example"
        assert 0 < first.propensity <= 1
        retry_after_failure = await service.select_strategy(
            run_id=run.run_id,
            plan_step_id="teach",
            decision_point_id="plan:1:step:teach:attempt:2",
            context=context.model_copy(update={"consecutive_failures": 1}),
            allowed_tools=set(),
        )
        assert retry_after_failure is not None
        assert retry_after_failure.id != first.id
        assert retry_after_failure.plan_step_id == first.plan_step_id
        assert retry_after_failure.decision_point_id.endswith("attempt:2")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_reward_matures_after_delayed_labels_and_updates_bandit(
    tmp_path: Path,
) -> None:
    store = await SqliteAgentRunStore.open(tmp_path / "checkpoint.db")
    try:
        service = PolicyOptimizationService(store, enabled=True)
        policy = await service.ensure_default_policy()
        run_id, _ = await _seed_run(store, resource_id="reward", succeeded=True)
        decision = await service.select_strategy(
            run_id=run_id,
            plan_step_id="teach",
            context=TeachingContext(
                progress=0.5,
                consecutive_failures=0,
                retrieval_available=1,
                write_step=0,
            ),
            allowed_tools={"research.ask"},
        )
        assert decision is not None
        provisional = await service.evaluate_terminal_run(run_id)
        assert provisional is not None
        assert provisional.status == "provisional"
        assert provisional.optimization_score is None

        mature = await service.submit_delayed_outcome(
            run_id,
            DelayedLearningOutcome(
                retention=0.9,
                transfer=0.8,
                user_feedback=1,
                evidence_reference="review-session:next-week",
            ),
        )
        assert mature.status == "mature"
        assert mature.optimization_score is not None
        stats = await store.get_bandit_statistics(policy.id, decision.arm_id)
        assert stats is not None
        assert stats["observations"] == 1
        replayed = await service.submit_delayed_outcome(
            run_id,
            DelayedLearningOutcome(
                retention=0.9,
                transfer=0.8,
                user_feedback=1,
                evidence_reference="review-session:retry",
            ),
        )
        assert replayed.id == mature.id
        stats = await store.get_bandit_statistics(policy.id, decision.arm_id)
        assert stats is not None
        assert stats["observations"] == 1
        stored_decision = await store.get_bandit_decision(run_id, "teach")
        assert stored_decision is not None
        assert mature.id in stored_decision
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_safety_gate_cannot_be_compensated_by_learning_scores(
    tmp_path: Path,
) -> None:
    store = await SqliteAgentRunStore.open(tmp_path / "checkpoint.db")
    try:
        service = PolicyOptimizationService(store, enabled=True)
        await service.ensure_default_policy()
        run_id, _ = await _seed_run(
            store, resource_id="unsafe", succeeded=True, unsafe=True
        )
        reward = await service.evaluate_terminal_run(run_id)
        assert reward is not None
        assert reward.status == "ineligible"
        assert reward.hard_gate_passed is False
        assert reward.optimization_score is None
        with pytest.raises(ValueError, match="permanently ineligible"):
            await service.submit_delayed_outcome(
                run_id,
                DelayedLearningOutcome(
                    retention=1,
                    transfer=1,
                    user_feedback=1,
                    evidence_reference="unsafe-high-score",
                ),
            )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_sft_export_requires_mature_safe_reward_and_human_review(
    tmp_path: Path,
) -> None:
    store = await SqliteAgentRunStore.open(tmp_path / "checkpoint.db")
    try:
        service = PolicyOptimizationService(store, enabled=True)
        await service.ensure_default_policy()
        run_id, _ = await _seed_run(store, resource_id="sft", succeeded=True)
        await service.submit_delayed_outcome(
            run_id,
            DelayedLearningOutcome(
                retention=0.9,
                transfer=0.9,
                evidence_reference="review-session:retention",
            ),
        )
        assert await service.export_sft() == []
        await service.save_review(
            TrajectoryReview(
                run_id=run_id,
                decision="approved",
                reviewer="evaluation-team",
                note="Public transitions and Verifier evidence reviewed.",
            )
        )
        exported = await service.export_sft()
        assert len(exported) == 1
        assert exported[0].run_id == run_id
        assert exported[0].transitions[0].action["action"] == "present_content"
        assert "chain-of-thought" not in exported[0].model_dump_json()
    finally:
        await store.close()


def _replay_samples() -> list[ReplaySample]:
    samples: list[ReplaySample] = []
    for index in range(20):
        good = index < 10
        samples.append(
            ReplaySample(
                id=f"holdout-{index}",
                split="holdout",
                logged_arm="good" if good else "bad",
                logged_propensity=0.5,
                candidate_probabilities={"good": 0.9, "bad": 0.1},
                reward=1 if good else 0,
                hard_gate_passed=True,
                tool_calls=2,
                tokens=100,
                latency_ms=100,
            )
        )
    return samples


@pytest.mark.asyncio
async def test_holdout_ope_gates_policy_revision_and_supports_rollback(
    tmp_path: Path,
) -> None:
    store = await SqliteAgentRunStore.open(tmp_path / "checkpoint.db")
    try:
        service = PolicyOptimizationService(store, enabled=True)
        active = await service.ensure_default_policy()
        manifest = ExperimentManifest(
            name="Bandit candidate holdout",
            change_type="bandit",
            baseline_version="teaching_strategy@1",
            candidate_version="teaching_strategy@2",
            dataset_version="policy-replay-1.0.0",
            model_version="fake-model@1",
            prompt_versions=["dynamic_agent_decision@5.0.0"],
            tool_registry_version="learning-tools@1",
            code_version="test-revision",
            reward_version="learning-reward-1.0.0",
            minimum_effective_sample_size=10,
            minimum_reward_lift=0.1,
        )
        report = await service.evaluate_experiment(
            manifest, _replay_samples(), split="holdout"
        )
        assert report.promotable is True
        assert report.snips_reward == pytest.approx(0.9)
        assert report.reward_lift == pytest.approx(0.4)
        assert report.effective_sample_size > 10

        validation = await service.evaluate_experiment(
            manifest.model_copy(
                update={"id": "validation-report", "name": "Validation only"}
            ),
            [
                item.model_copy(update={"split": "validation"})
                for item in _replay_samples()
            ],
            split="validation",
        )
        assert validation.promotable is False
        assert "frozen holdout" in validation.rejection_reasons[0]
        unsafe_samples = _replay_samples()
        unsafe_samples[0] = unsafe_samples[0].model_copy(
            update={"hard_gate_passed": False}
        )
        unsafe = await service.evaluate_experiment(
            manifest.model_copy(
                update={"id": "unsafe-report", "name": "Unsafe holdout"}
            ),
            unsafe_samples,
            split="holdout",
        )
        assert unsafe.promotable is False
        assert "safety hard gate failed" in unsafe.rejection_reasons

        revision = await service.revise_policy(
            active.id,
            PolicyRevisionRequest(
                expected_version=1,
                alpha=0.25,
                epsilon=0.02,
                arms=active.arms,
                source_experiment_id=manifest.id,
            ),
        )
        promoted = await service.activate_policy(
            revision.id,
            PolicyActivationRequest(expected_version=2, reason="holdout passed"),
        )
        assert promoted.status == "active"
        assert (await service.active_policy()).id == revision.id  # type: ignore[union-attr]
        rolled_back = await service.activate_policy(
            active.id,
            PolicyActivationRequest(expected_version=1, reason="canary regression"),
        )
        assert rolled_back.status == "active"
        assert (await service.active_policy()).id == active.id  # type: ignore[union-attr]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_preference_pairs_and_failure_clusters_require_verified_provenance(
    tmp_path: Path,
) -> None:
    store = await SqliteAgentRunStore.open(tmp_path / "checkpoint.db")
    try:
        service = PolicyOptimizationService(store, enabled=True)
        await service.ensure_default_policy()
        chosen_id, _ = await _seed_run(
            store, resource_id="preferred", succeeded=True
        )
        rejected_id, _ = await _seed_run(
            store, resource_id="rejected", succeeded=True
        )
        await service.submit_delayed_outcome(
            chosen_id,
            DelayedLearningOutcome(
                retention=1,
                transfer=1,
                user_feedback=1,
                evidence_reference="review:preferred",
            ),
        )
        await service.submit_delayed_outcome(
            rejected_id,
            DelayedLearningOutcome(
                retention=0.1,
                transfer=0.1,
                user_feedback=-1,
                evidence_reference="review:rejected",
            ),
        )
        pair = await service.create_preference_pair(
            context_fingerprint="a" * 64,
            chosen_run_id=chosen_id,
            rejected_run_id=rejected_id,
            evidence_note="Same task family and rubric; delayed outcomes reviewed.",
        )
        assert pair.margin > 0
        with pytest.raises(ValueError, match="higher mature reward"):
            await service.create_preference_pair(
                context_fingerprint="a" * 64,
                chosen_run_id=rejected_id,
                rejected_run_id=chosen_id,
                evidence_note="Invalid reverse preference.",
            )

        for index, run_id in enumerate((chosen_id, rejected_id), start=1):
            evidence = ReflectionEvidence(
                id=f"evidence-{index}",
                kind="verification",
                event_sequence=3,
                summary="Verifier reported the same failure class.",
                content_sha256=str(index) * 64,
            )
            reflection = RunReflection(
                run_id=run_id,
                outcome="failure",
                problem_category="verification_failure",
                evidence=[evidence],
                root_causes=[
                    ReflectionInsight(
                        statement="  Missing   transfer check ",
                        evidence_ids=[evidence.id],
                    )
                ],
                improvements=[],
                applicability=["daily_learning"],
                strategy_key="b" * 64,
            )
            await store.save_reflection(
                reflection_id=reflection.id,
                run_id=run_id,
                outcome=reflection.outcome,
                strategy_key=reflection.strategy_key,
                reflection_json=reflection.model_dump_json(),
                created_at=reflection.created_at,
            )
        clusters = await service.analyze_failures()
        assert len(clusters) == 1
        assert clusters[0].count == 2
        assert clusters[0].root_causes == ["missing transfer check"]
        assert len(clusters[0].evidence_references) == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_optimization_api_exposes_policy_state_and_guards_admin(
    tmp_path: Path,
) -> None:
    store = await SqliteAgentRunStore.open(tmp_path / "checkpoint.db")
    try:
        service = PolicyOptimizationService(store, enabled=False)
        await service.ensure_default_policy()
        runtime = AgentRuntime(
            checkpointer=object(),  # type: ignore[arg-type]
            run_store=store,
            daily_graph=object(),  # type: ignore[arg-type]
            goal_graph=object(),  # type: ignore[arg-type]
            tools=object(),  # type: ignore[arg-type]
            model=None,
            retention_days=30,
            dynamic_kernel=None,
            optimization=service,
            policy_admin_enabled=False,
        )
        application = create_app(
            Settings(environment="test", data_dir=tmp_path / "data")
        )
        application.state.agent_runtime = runtime
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as client:
            policies = await client.get("/api/v1/agent/optimization/policies")
            export = await client.get("/api/v1/agent/optimization/datasets/sft")
        assert policies.status_code == 200
        assert policies.json()[0]["algorithm"] == "linucb"
        assert export.status_code == 403
    finally:
        await store.close()
