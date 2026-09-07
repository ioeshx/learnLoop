"""Stage-nine adaptive learning and review acceptance tests."""

from datetime import UTC, datetime, timedelta

import pytest

from app.application import (
    ApplicationDependencies,
    CompleteStudySession,
    CorrectAttemptCommand,
    CorrectExerciseAttempt,
    CreateGoalCommand,
    CreateLearningGoal,
    CreateStudyPlan,
    DeferReview,
    GetDueReviews,
    StartReviewSession,
    StartReviewSessionCommand,
    StartSessionCommand,
    StartStudySession,
    SubmitAttemptCommand,
    SubmitExerciseAttempt,
)
from app.domain.mastery import (
    MasteryEventType,
    PrerequisiteMastery,
    recommend_difficulty,
)
from app.domain.plans import PlanItemStatus
from app.domain.sessions import StudySessionKind
from app.infrastructure.review import FsrsReviewScheduler
from tests.fakes import FakeUnitOfWorkFactory

NOW = datetime(2026, 2, 10, 8, 30, tzinfo=UTC)


def dependencies(
    factory: FakeUnitOfWorkFactory, now: datetime = NOW
) -> ApplicationDependencies:
    return ApplicationDependencies(
        uow_factory=factory,
        review_scheduler=FsrsReviewScheduler(),
        clock=lambda: now,
    )


async def create_first_attempt(factory: FakeUnitOfWorkFactory, *, correct: bool):
    deps = dependencies(factory)
    goal = await CreateLearningGoal(deps).execute(
        CreateGoalCommand(
            title="图算法",
            desired_outcome="独立实现 BFS",
            weekly_minutes=180,
            idempotency_key="adaptive-goal",
        )
    )
    plan = await CreateStudyPlan(deps).execute(goal.id)
    item = plan.plan.items[0]
    session = await StartStudySession(deps).execute(
        StartSessionCommand(
            goal_id=goal.id,
            plan_item_id=item.id,
            idempotency_key="adaptive-session",
        )
    )
    answer = (
        session.exercise.answer_key
        if correct
        else (
            next(
                option
                for option in session.exercise.options
                if option not in session.exercise.answer_key
            ),
        )
    )
    result = await SubmitExerciseAttempt(deps).execute(
        SubmitAttemptCommand(
            session_id=session.session.id,
            exercise_id=session.exercise.id,
            selected_options=answer,
            idempotency_key="adaptive-attempt",
        )
    )
    return deps, plan, session, result


def test_recommendation_lowers_difficulty_for_mastery_and_prerequisite_gaps() -> None:
    recommendation = recommend_difficulty(
        base_difficulty=3.0,
        mastery_score=0.3,
        prerequisites=(
            PrerequisiteMastery("node-1", "基础概念", 0.2),
            PrerequisiteMastery("node-2", "已掌握概念", 0.9),
        ),
    )

    assert recommendation.target_difficulty == 2.0
    assert [gap.knowledge_node_id for gap in recommendation.prerequisite_gaps] == [
        "node-1"
    ]
    assert len(recommendation.reasons) == 2


@pytest.mark.asyncio
async def test_due_review_starts_adaptive_session_and_records_review_event() -> None:
    factory = FakeUnitOfWorkFactory()
    _, plan, learning_session, first_result = await create_first_attempt(
        factory, correct=True
    )
    await CompleteStudySession(dependencies(factory)).execute(
        learning_session.session.id
    )
    review_now = first_result.review.due_at + timedelta(minutes=1)
    review_deps = dependencies(factory, review_now)

    due = await GetDueReviews(review_deps).execute()
    review_session = await StartReviewSession(review_deps).execute(
        StartReviewSessionCommand(
            knowledge_node_id=due[0].knowledge_node.id,
            idempotency_key="review-session-1",
        )
    )
    result = await SubmitExerciseAttempt(review_deps).execute(
        SubmitAttemptCommand(
            session_id=review_session.session.id,
            exercise_id=review_session.exercise.id,
            selected_options=review_session.exercise.answer_key,
            idempotency_key="review-attempt-1",
        )
    )
    completed = await CompleteStudySession(review_deps).execute(
        review_session.session.id
    )

    assert due[0].priority_score > 0
    assert due[0].reason
    assert review_session.session.kind == StudySessionKind.REVIEW
    assert review_session.exercise.id != learning_session.exercise.id
    assert result.mastery.score == pytest.approx(0.27)
    assert list(factory.state.mastery_events.values())[-1].event_type == (
        MasteryEventType.REVIEW_CORRECT
    )
    assert completed.plan_item.status == PlanItemStatus.COMPLETED


@pytest.mark.asyncio
async def test_correcting_attempt_rebuilds_mastery_projection() -> None:
    factory = FakeUnitOfWorkFactory()
    deps, _, session, result = await create_first_attempt(factory, correct=False)

    corrected = await CorrectExerciseAttempt(deps).execute(
        CorrectAttemptCommand(
            session_id=session.session.id,
            attempt_id=result.attempt.id,
            selected_options=session.exercise.answer_key,
        )
    )

    assert corrected.attempt.is_correct is True
    assert corrected.mastery.score == pytest.approx(0.15)
    assert corrected.mastery.attempt_count == 1
    assert corrected.mastery.correct_count == 1
    event = next(iter(factory.state.mastery_events.values()))
    assert event.event_type == MasteryEventType.CORRECT_FIRST_TRY


@pytest.mark.asyncio
async def test_missed_review_can_be_deferred_without_resetting_fsrs_card() -> None:
    factory = FakeUnitOfWorkFactory()
    _, _, _, result = await create_first_attempt(factory, correct=True)
    now = result.review.due_at + timedelta(days=3)
    deps = dependencies(factory, now)

    due = await GetDueReviews(deps).execute()
    deferred = await DeferReview(deps).execute(result.review.knowledge_node_id, days=2)

    assert due[0].overdue_days == 3
    assert deferred.due_at == now + timedelta(days=2)
    assert deferred.card_json == result.review.card_json
    assert await GetDueReviews(deps).execute() == []
