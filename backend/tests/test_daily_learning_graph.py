"""End-to-end tests for the bounded LangGraph daily-learning workflow."""

from datetime import UTC, datetime
from typing import cast

import pytest

from app.agent.graphs import DailyLearningContext, build_daily_learning_graph
from app.agent.states import StudySessionState
from app.agent.tools import LearningTools
from app.application import (
    ApplicationDependencies,
    CreateGoalCommand,
    CreateLearningGoal,
    CreateStudyPlan,
    StartSessionCommand,
    StartStudySession,
)
from app.domain.sessions import StudySessionStatus
from app.infrastructure.review import FsrsReviewScheduler
from tests.fakes import FakeUnitOfWorkFactory

NOW = datetime(2026, 3, 1, 9, tzinfo=UTC)


async def _learning_run() -> tuple[
    FakeUnitOfWorkFactory,
    DailyLearningContext,
    str,
    str,
    str,
]:
    factory = FakeUnitOfWorkFactory()
    dependencies = ApplicationDependencies(
        uow_factory=factory,
        review_scheduler=FsrsReviewScheduler(),
        clock=lambda: NOW,
    )
    goal = await CreateLearningGoal(dependencies).execute(
        CreateGoalCommand(
            title="Learn graph traversal",
            desired_outcome="Implement BFS independently",
            weekly_minutes=180,
            idempotency_key="daily-graph-goal",
        )
    )
    plan = await CreateStudyPlan(dependencies).execute(goal.id)
    session = await StartStudySession(dependencies).execute(
        StartSessionCommand(
            goal_id=goal.id,
            plan_item_id=plan.plan.items[0].id,
            idempotency_key="daily-graph-session",
        )
    )
    return (
        factory,
        DailyLearningContext(tools=LearningTools(dependencies)),
        session.session.id,
        session.exercise.answer_key[0],
        next(
            option
            for option in session.exercise.options
            if option not in session.exercise.answer_key
        ),
    )


@pytest.mark.asyncio
async def test_daily_graph_waits_then_completes_correct_answer() -> None:
    factory, context, session_id, correct_answer, _ = await _learning_run()
    graph = build_daily_learning_graph()

    awaiting = cast(
        StudySessionState,
        await graph.ainvoke(
            {"run_id": "daily-run-correct", "session_id": session_id},
            context=context,
        ),
    )

    assert awaiting["status"] == "awaiting_answer"
    assert awaiting["events"] == [
        "load_context",
        "select_concepts",
        "retrieve_sources",
        "generate_lesson",
        "generate_exercise",
        "wait_for_answer",
    ]
    assert factory.state.attempts == {}

    completed = cast(
        StudySessionState,
        await graph.ainvoke(
            {**awaiting, "selected_options": [correct_answer]},
            context=context,
        ),
    )

    assert completed["status"] == "completed"
    assert completed["learning_outcome"] == "mastered"
    assert completed["mastery_score"] == pytest.approx(0.15)
    assert completed["attempt_number"] == 1
    assert completed["review_due_at"]
    assert len(factory.state.attempts) == 1
    assert (
        factory.state.sessions[session_id].status
        == StudySessionStatus.COMPLETED
    )


@pytest.mark.asyncio
async def test_daily_graph_bounds_remediation_to_two_retries() -> None:
    factory, context, session_id, _, wrong_answer = await _learning_run()
    graph = build_daily_learning_graph()
    state: StudySessionState = {
        "run_id": "daily-run-wrong",
        "session_id": session_id,
        "selected_options": [wrong_answer],
    }

    first = cast(
        StudySessionState, await graph.ainvoke(state, context=context)
    )
    assert first["status"] == "awaiting_answer"
    assert first["learning_outcome"] == "partially_mastered"
    assert first["remediation_count"] == 1
    assert first["selected_options"] == []
    assert "generate_supplemental" in first["events"]

    second = cast(
        StudySessionState,
        await graph.ainvoke(
            {**first, "selected_options": [wrong_answer]}, context=context
        ),
    )
    assert second["status"] == "awaiting_answer"
    assert second["learning_outcome"] == "not_mastered"
    assert second["remediation_count"] == 2
    assert "generate_prerequisite_remediation" in second["events"]

    completed = cast(
        StudySessionState,
        await graph.ainvoke(
            {**second, "selected_options": [wrong_answer]}, context=context
        ),
    )
    assert completed["status"] == "completed"
    assert completed["learning_outcome"] == "remediation_exhausted"
    assert completed["remediation_count"] == 2
    assert completed["attempt_number"] == 3
    assert len(factory.state.attempts) == 3
