"""Application use-case tests for the complete no-LLM learning loop."""

from datetime import UTC, datetime

import pytest

from app.application import (
    ApplicationDependencies,
    CompleteStudySession,
    CreateGoalCommand,
    CreateLearningGoal,
    CreateStudyPlan,
    GetDueReviews,
    GetStudySession,
    StartSessionCommand,
    StartStudySession,
    SubmitAttemptCommand,
    SubmitExerciseAttempt,
)
from app.application.errors import ConflictError
from app.domain.plans import PlanItemStatus
from app.domain.sessions import StudySessionStatus
from app.infrastructure.review import FsrsReviewScheduler
from tests.fakes import FakeUnitOfWorkFactory

NOW = datetime(2026, 2, 10, 8, 30, tzinfo=UTC)


def build_dependencies(factory: FakeUnitOfWorkFactory) -> ApplicationDependencies:
    return ApplicationDependencies(
        uow_factory=factory,
        review_scheduler=FsrsReviewScheduler(),
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_complete_learning_loop_is_persistent_and_idempotent() -> None:
    factory = FakeUnitOfWorkFactory()
    dependencies = build_dependencies(factory)
    create_goal = CreateLearningGoal(dependencies)
    goal_command = CreateGoalCommand(
        title="Learn graph algorithms",
        desired_outcome="Implement BFS independently",
        weekly_minutes=180,
        idempotency_key="goal-request-1",
    )

    goal = await create_goal.execute(goal_command)
    repeated_goal = await create_goal.execute(goal_command)
    plan = await CreateStudyPlan(dependencies).execute(goal.id)
    repeated_plan = await CreateStudyPlan(dependencies).execute(goal.id)
    item = plan.plan.items[0]
    start_command = StartSessionCommand(
        goal_id=goal.id,
        plan_item_id=item.id,
        idempotency_key="session-request-1",
    )
    session = await StartStudySession(dependencies).execute(start_command)
    repeated_session = await StartStudySession(dependencies).execute(start_command)
    answer = session.exercise.answer_key
    attempt_command = SubmitAttemptCommand(
        session_id=session.session.id,
        exercise_id=session.exercise.id,
        selected_options=answer,
        idempotency_key="attempt-request-1",
    )
    result = await SubmitExerciseAttempt(dependencies).execute(attempt_command)
    repeated_result = await SubmitExerciseAttempt(dependencies).execute(attempt_command)
    restored_session = await GetStudySession(dependencies).execute(session.session.id)
    completed = await CompleteStudySession(dependencies).execute(session.session.id)
    repeated_completed = await CompleteStudySession(dependencies).execute(
        session.session.id
    )
    due_reviews = await GetDueReviews(dependencies).execute(
        due_before=result.review.due_at
    )

    assert repeated_goal.id == goal.id
    assert repeated_plan.plan.id == plan.plan.id
    assert repeated_session.session.id == session.session.id
    assert repeated_result.attempt.id == result.attempt.id
    assert result.attempt.is_correct is True
    assert result.mastery.score == pytest.approx(0.15)
    assert result.mastery.attempt_count == 1
    assert restored_session.latest_result == result
    assert completed.session.status == StudySessionStatus.COMPLETED
    assert repeated_completed.session == completed.session
    assert completed.plan_item.status == PlanItemStatus.COMPLETED
    assert len(factory.state.attempts) == 1
    assert len(factory.state.mastery_events) == 1
    assert [review.knowledge_node.id for review in due_reviews] == [
        item.knowledge_node_id
    ]


@pytest.mark.asyncio
async def test_session_cannot_complete_before_an_answer() -> None:
    factory = FakeUnitOfWorkFactory()
    dependencies = build_dependencies(factory)
    goal = await CreateLearningGoal(dependencies).execute(
        CreateGoalCommand(
            title="Learn SQL",
            desired_outcome="Write useful queries",
            weekly_minutes=120,
            idempotency_key="goal-request-2",
        )
    )
    plan = await CreateStudyPlan(dependencies).execute(goal.id)
    session = await StartStudySession(dependencies).execute(
        StartSessionCommand(
            goal_id=goal.id,
            plan_item_id=plan.plan.items[0].id,
            idempotency_key="session-request-2",
        )
    )

    with pytest.raises(ConflictError, match="submit an answer"):
        await CompleteStudySession(dependencies).execute(session.session.id)

    persisted_session = factory.state.sessions[session.session.id]
    assert persisted_session.status == StudySessionStatus.ACTIVE
