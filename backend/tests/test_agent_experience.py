"""Stage 16 Reflection provenance and Skill governance tests."""

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
from app.agent.execution import SqliteAgentRunStore, open_agent_runtime
from app.agent.experience import (
    ReflectionSkillService,
    SkillReviewRequest,
    SkillRevisionRequest,
    SkillStatus,
    SkillStatusRequest,
)
from app.application import ApplicationDependencies
from app.config import Settings
from app.infrastructure.review import FsrsReviewScheduler
from app.main import create_app
from tests.fakes import FakeUnitOfWorkFactory


def _plan(
    *,
    objective: str = "完成学习 Session：图遍历",
    completed: bool = True,
    skill_id: str | None = None,
    skill_version: int | None = None,
) -> AgentPlan:
    status = StepStatus.COMPLETED if completed else StepStatus.PENDING
    return AgentPlan(
        objective=objective,
        applied_skill_id=skill_id,
        applied_skill_version=skill_version,
        steps=[
            PlanStep(
                id="read",
                objective="读取当前 Session",
                success_criteria=["Session state is available"],
                allowed_tools=["session.get_state"],
                status=status,
                evidence_ids=["observation-1"] if completed else [],
            ),
            PlanStep(
                id="teach",
                objective="基于状态完成讲解",
                dependencies=["read"],
                success_criteria=["Explanation is presented"],
                status=status,
                evidence_ids=["observation-1"] if completed else [],
            ),
        ],
    )


async def _seed_verified_run(
    store: SqliteAgentRunStore,
    *,
    resource_id: str,
    succeeded: bool,
    include_verifier: bool = True,
) -> str:
    run, _ = await store.create_or_get(
        "daily_learning", resource_id, engine_version="dynamic_v2"
    )
    await store.set_status(run.run_id, "running")
    state = DynamicAgentState(
        run_id=run.run_id,
        user_id="local-user",
        session_id=resource_id,
        plan=_plan(completed=succeeded),
        budget=RunBudget(),
        usage=BudgetUsage(tool_calls=1, total_tokens=300),
    )
    await store.save_dynamic_state(run.run_id, state.model_dump_json())
    await store.append_event(
        run.run_id,
        "observation_recorded",
        node="read",
        data={
            "id": "observation-1",
            "succeeded": succeeded,
            "summary": "Session state loaded" if succeeded else "Session lookup failed",
        },
    )
    if include_verifier:
        await store.append_event(
            run.run_id,
            "verification_completed",
            node="read",
            data={
                "status": "passed" if succeeded else "failed",
                "explanation": "Evidence satisfies criterion"
                if succeeded
                else "Missing evidence",
                "evidence_ids": ["observation-1"] if succeeded else [],
            },
        )
    if succeeded:
        await store.set_status(run.run_id, "completed", terminal_reason="completed")
        await store.append_event(
            run.run_id,
            "run_completed",
            data={"terminal_reason": "completed", "summary": "done"},
        )
    else:
        await store.set_status(
            run.run_id, "failed", terminal_reason="verification_failed"
        )
        await store.append_event(
            run.run_id,
            "run_failed",
            data={
                "terminal_reason": "verification_failed",
                "message": "Missing evidence",
            },
        )
    return run.run_id


@pytest.mark.asyncio
async def test_reflection_requires_verifier_and_all_insights_are_grounded(
    tmp_path: Path,
) -> None:
    store = await SqliteAgentRunStore.open(tmp_path / "checkpoints.db")
    try:
        service = ReflectionSkillService(store)
        unverified = await _seed_verified_run(
            store,
            resource_id="unverified",
            succeeded=False,
            include_verifier=False,
        )
        assert await service.process_run(unverified) == []
        assert await service.list_reflections(run_id=unverified) == []

        failed = await _seed_verified_run(store, resource_id="failed", succeeded=False)
        events = await service.process_run(failed)
        assert [event.event for event in events] == ["reflection_created"]
        reflection = (await service.list_reflections(run_id=failed))[0]
        evidence_ids = {item.id for item in reflection.evidence}
        assert reflection.outcome == "failure"
        assert reflection.root_causes
        assert all(
            set(insight.evidence_ids).issubset(evidence_ids)
            for insight in [*reflection.root_causes, *reflection.improvements]
        )
        recalled_failures = await service.recall_failures(
            graph_kind="daily_learning",
            objective="完成学习 Session：图遍历",
        )
        assert [item.id for item in recalled_failures] == [reflection.id]
        assert (
            await service.recall_failures(
                graph_kind="goal_planning",
                objective="完成学习 Session：图遍历",
            )
            == []
        )
        # Idempotency avoids generating a second Reflection or duplicate Trace event.
        assert await service.process_run(failed) == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_repeated_success_requires_review_then_scope_safe_recall(
    tmp_path: Path,
) -> None:
    store = await SqliteAgentRunStore.open(tmp_path / "checkpoints.db")
    try:
        service = ReflectionSkillService(store, minimum_source_runs=2)
        first = await _seed_verified_run(store, resource_id="success-1", succeeded=True)
        second = await _seed_verified_run(
            store, resource_id="success-2", succeeded=True
        )
        await service.process_run(first)
        assert await service.list_skills() == []
        emitted = await service.process_run(second)
        assert [event.event for event in emitted] == [
            "reflection_created",
            "skill_candidate_created",
        ]
        candidate = (await service.list_skills())[0]
        assert candidate.status == SkillStatus.CANDIDATE
        assert set(candidate.source_run_ids) == {first, second}
        assert (
            await service.recall(
                graph_kind="daily_learning",
                objective="完成学习 Session：图遍历",
                allowed_tools={"session.get_state"},
            )
            == []
        )

        active = await service.review(
            candidate.id,
            SkillReviewRequest(
                decision="publish",
                expected_version=1,
                note="Two traces passed deterministic verification.",
            ),
        )
        recalled = await service.recall(
            graph_kind="daily_learning",
            objective="完成学习 Session：图遍历",
            allowed_tools={"session.get_state"},
        )
        assert active.status == SkillStatus.ACTIVE
        assert recalled[0].skill.id == active.id
        assert (
            await service.recall(
                graph_kind="goal_planning",
                objective="完成学习 Session：图遍历",
                allowed_tools={"session.get_state"},
            )
            == []
        )
        assert (
            await service.recall(
                graph_kind="daily_learning",
                objective="完成学习 Session：图遍历",
                allowed_tools=set(),
            )
            == []
        )
        with pytest.raises(ValueError, match="not recalled"):
            await service.validate_and_start_usage(
                "forged-run",
                _plan(skill_id=active.id, skill_version=active.version),
                [],
            )

        revision = await service.revise(
            active.id,
            SkillRevisionRequest(
                expected_version=active.version,
                description="Refined procedure with the same bounded capabilities.",
                applicability=active.applicability,
                prerequisites=active.prerequisites,
                steps=active.steps,
                risk=active.risk,
                note="Create a canary version.",
            ),
        )
        assert revision.version == 2
        assert (await service.get_skill(active.id)).status == SkillStatus.ACTIVE  # type: ignore[union-attr]
        promoted = await service.review(
            revision.id,
            SkillReviewRequest(
                decision="publish",
                expected_version=2,
                note="Canary passed.",
            ),
        )
        assert promoted.status == SkillStatus.ACTIVE
        assert (await service.get_skill(active.id)).status == SkillStatus.DISABLED  # type: ignore[union-attr]
        rolled_back = await service.set_status(
            active.id,
            SkillStatusRequest(
                status=SkillStatus.ACTIVE,
                expected_version=1,
                note="Rollback after regression.",
            ),
        )
        assert rolled_back.status == SkillStatus.ACTIVE
        assert (await service.get_skill(revision.id)).status == SkillStatus.DISABLED  # type: ignore[union-attr]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_low_success_skill_is_automatically_quarantined(tmp_path: Path) -> None:
    store = await SqliteAgentRunStore.open(tmp_path / "checkpoints.db")
    try:
        service = ReflectionSkillService(
            store,
            quarantine_min_uses=3,
            quarantine_success_rate=0.5,
        )
        for index in range(2):
            run_id = await _seed_verified_run(
                store, resource_id=f"source-{index}", succeeded=True
            )
            await service.process_run(run_id)
        candidate = (await service.list_skills())[0]
        active = await service.review(
            candidate.id,
            SkillReviewRequest(
                decision="publish", expected_version=1, note="Publish for canary."
            ),
        )
        recalls = await service.recall(
            graph_kind="daily_learning",
            objective="完成学习 Session：图遍历",
            allowed_tools={"session.get_state"},
        )
        for index in range(3):
            run, _ = await store.create_or_get(
                "daily_learning", f"failed-use-{index}", engine_version="dynamic_v2"
            )
            await store.set_status(run.run_id, "running")
            state = DynamicAgentState(
                run_id=run.run_id,
                plan=_plan(
                    completed=False,
                    skill_id=active.id,
                    skill_version=active.version,
                ),
                budget=RunBudget(),
                usage=BudgetUsage(tool_calls=2, total_tokens=500),
            )
            await store.save_dynamic_state(run.run_id, state.model_dump_json())
            await service.validate_and_start_usage(run.run_id, state.plan, recalls)
            await store.set_status(
                run.run_id, "failed", terminal_reason="verification_failed"
            )
            events = await service.complete_usage(run.run_id)
        assert events[-1].event == "skill_quarantined"
        quarantined = await service.get_skill(active.id)
        assert quarantined is not None
        assert quarantined.status == SkillStatus.QUARANTINED
        assert quarantined.failure_count == 3
        assert (
            await service.recall(
                graph_kind="daily_learning",
                objective="完成学习 Session：图遍历",
                allowed_tools={"session.get_state"},
            )
            == []
        )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_skill_management_api_lists_and_publishes_candidate(
    tmp_path: Path,
) -> None:
    dependencies = ApplicationDependencies(
        uow_factory=FakeUnitOfWorkFactory(),
        review_scheduler=FsrsReviewScheduler(),
    )
    settings = Settings(environment="test", data_dir=tmp_path / "data")
    async with open_agent_runtime(settings, dependencies, None) as runtime:
        assert runtime.experience is not None
        for index in range(2):
            run_id = await _seed_verified_run(
                runtime.run_store, resource_id=f"api-source-{index}", succeeded=True
            )
            await runtime.experience.process_run(run_id)
        app = create_app(settings)
        app.state.application_dependencies = dependencies
        app.state.agent_runtime = runtime
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            listed = await client.get("/api/v1/agent/skills")
            assert listed.status_code == 200
            candidate = listed.json()[0]
            reviewed = await client.post(
                f"/api/v1/agent/skills/{candidate['id']}/review",
                json={
                    "decision": "publish",
                    "expected_version": candidate["version"],
                    "note": "Reviewed against source traces.",
                },
            )
            assert reviewed.status_code == 200
            assert reviewed.json()["status"] == "active"
