"""Use cases for the deterministic learning loop."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from app.application.errors import ConflictError, NotFoundError
from app.application.models import (
    AttemptResult,
    CreateGoalCommand,
    DueReview,
    PlanDetails,
    SessionDetails,
    StartSessionCommand,
    SubmitAttemptCommand,
)
from app.application.ports import UnitOfWork, UnitOfWorkFactory
from app.application.templates import build_fixed_curriculum
from app.domain.common import deterministic_id, utc_now
from app.domain.exercises import Exercise, ExerciseAttempt, grade_multiple_choice
from app.domain.goals import GoalStatus, LearningGoal
from app.domain.knowledge import KnowledgeNode
from app.domain.mastery import (
    MasteryEvent,
    MasteryEventType,
    MasterySnapshot,
    apply_mastery_event,
    mastery_delta,
)
from app.domain.plans import PlanItemStatus, StudyPlan
from app.domain.review import ReviewRating, ReviewScheduler
from app.domain.sessions import StudySession, StudySessionStatus
from app.domain.users import User

DEFAULT_USER_ID = "00000000-0000-4000-8000-000000000001"


@dataclass(frozen=True, slots=True)
class ApplicationDependencies:
    uow_factory: UnitOfWorkFactory
    review_scheduler: ReviewScheduler
    clock: Callable[[], datetime] = utc_now


async def _ensure_default_user(uow: UnitOfWork, now: datetime) -> User:
    user = await uow.users.get(DEFAULT_USER_ID)
    if user is not None:
        return user
    user = User(
        id=DEFAULT_USER_ID,
        display_name="Local learner",
        timezone="Asia/Shanghai",
        created_at=now,
        updated_at=now,
    )
    await uow.users.add(user)
    return user


async def _get_plan_details(uow: UnitOfWork, plan: StudyPlan) -> PlanDetails:
    node_by_id = {
        node.id: node for node in await uow.knowledge.list_nodes(plan.goal_id)
    }
    nodes: list[KnowledgeNode] = []
    for item in plan.items:
        node = node_by_id.get(item.knowledge_node_id)
        if node is None:
            raise NotFoundError("knowledge node", item.knowledge_node_id)
        nodes.append(node)
    return PlanDetails(plan=plan, nodes=tuple(nodes))


async def _get_session_details(
    uow: UnitOfWork, session: StudySession
) -> SessionDetails:
    item = await uow.plans.get_item(session.plan_item_id)
    if item is None:
        raise NotFoundError("plan item", session.plan_item_id)
    node = await uow.knowledge.get_node(item.knowledge_node_id)
    if node is None:
        raise NotFoundError("knowledge node", item.knowledge_node_id)
    exercises = await uow.exercises.list_for_node(node.id)
    if not exercises:
        raise NotFoundError("exercise for knowledge node", node.id)
    exercise = exercises[0]
    attempts = await uow.exercises.list_attempts_for_session(session.id)
    latest_result: AttemptResult | None = None
    if attempts:
        latest_attempt = attempts[-1]
        mastery = await uow.mastery.get_snapshot(DEFAULT_USER_ID, node.id)
        review = await uow.reviews.get(DEFAULT_USER_ID, node.id)
        if mastery is None or review is None:
            raise ConflictError("attempt result is incomplete")
        latest_result = AttemptResult(
            attempt=latest_attempt,
            expected_answer=exercise.answer_key,
            mastery=mastery,
            review=review,
        )
    return SessionDetails(
        session=session,
        plan_id=item.plan_id,
        plan_item=item,
        knowledge_node=node,
        exercise=exercise,
        latest_result=latest_result,
    )


class CreateLearningGoal:
    def __init__(self, dependencies: ApplicationDependencies) -> None:
        self._dependencies = dependencies

    async def execute(self, command: CreateGoalCommand) -> LearningGoal:
        now = self._dependencies.clock()
        goal_id = deterministic_id("learning-goal", command.idempotency_key)
        async with self._dependencies.uow_factory() as uow:
            await _ensure_default_user(uow, now)
            existing = await uow.goals.get(goal_id)
            if existing is not None:
                if (
                    existing.title != command.title.strip()
                    or existing.description != command.description.strip()
                    or existing.desired_outcome != command.desired_outcome.strip()
                    or existing.weekly_minutes != command.weekly_minutes
                    or existing.target_date != command.target_date
                ):
                    raise ConflictError(
                        "idempotency key was already used with different goal data"
                    )
                return existing
            goal = LearningGoal.create(
                user_id=DEFAULT_USER_ID,
                title=command.title,
                description=command.description,
                desired_outcome=command.desired_outcome,
                weekly_minutes=command.weekly_minutes,
                target_date=command.target_date,
                now=now,
                goal_id=goal_id,
            )
            await uow.goals.add(goal)
            await uow.commit()
            return goal


class GetLearningGoal:
    def __init__(self, dependencies: ApplicationDependencies) -> None:
        self._dependencies = dependencies

    async def execute(self, goal_id: str) -> LearningGoal:
        async with self._dependencies.uow_factory() as uow:
            goal = await uow.goals.get(goal_id)
            if goal is None:
                raise NotFoundError("learning goal", goal_id)
            return goal


class CreateStudyPlan:
    def __init__(self, dependencies: ApplicationDependencies) -> None:
        self._dependencies = dependencies

    async def execute(self, goal_id: str) -> PlanDetails:
        now = self._dependencies.clock()
        async with self._dependencies.uow_factory() as uow:
            goal = await uow.goals.get(goal_id)
            if goal is None:
                raise NotFoundError("learning goal", goal_id)
            existing = await uow.plans.list_for_goal(goal_id)
            if existing:
                return await _get_plan_details(uow, existing[-1])

            nodes, edges, plan, exercises = build_fixed_curriculum(goal, now=now)
            for node in nodes:
                await uow.knowledge.add_node(node)
            for edge in edges:
                await uow.knowledge.add_edge(edge)
            await uow.plans.add(plan)
            for exercise in exercises:
                await uow.exercises.add(exercise)
            if goal.status == GoalStatus.DRAFT:
                await uow.goals.update(goal.change_status(GoalStatus.ACTIVE, now=now))
            await uow.commit()
            return PlanDetails(plan=plan, nodes=nodes)


class GetStudyPlan:
    def __init__(self, dependencies: ApplicationDependencies) -> None:
        self._dependencies = dependencies

    async def execute(self, plan_id: str) -> PlanDetails:
        async with self._dependencies.uow_factory() as uow:
            plan = await uow.plans.get(plan_id)
            if plan is None:
                raise NotFoundError("study plan", plan_id)
            return await _get_plan_details(uow, plan)


class StartStudySession:
    def __init__(self, dependencies: ApplicationDependencies) -> None:
        self._dependencies = dependencies

    async def execute(self, command: StartSessionCommand) -> SessionDetails:
        now = self._dependencies.clock()
        session_id = deterministic_id(
            f"study-session:{command.goal_id}", command.idempotency_key
        )
        async with self._dependencies.uow_factory() as uow:
            existing = await uow.sessions.get(session_id)
            if existing is not None:
                if (
                    existing.goal_id != command.goal_id
                    or existing.plan_item_id != command.plan_item_id
                ):
                    raise ConflictError(
                        "idempotency key was already used for another study session"
                    )
                return await _get_session_details(uow, existing)

            goal = await uow.goals.get(command.goal_id)
            if goal is None:
                raise NotFoundError("learning goal", command.goal_id)
            item = await uow.plans.get_item(command.plan_item_id)
            if item is None:
                raise NotFoundError("plan item", command.plan_item_id)
            plan = await uow.plans.get(item.plan_id)
            if plan is None or plan.goal_id != goal.id:
                raise ConflictError("plan item does not belong to the learning goal")
            if item.status == PlanItemStatus.COMPLETED:
                raise ConflictError("completed plan items cannot start a new session")

            exercises = await uow.exercises.list_for_node(item.knowledge_node_id)
            if not exercises:
                raise NotFoundError("exercise for plan item", item.id)
            session = StudySession.create(
                goal_id=goal.id,
                plan_item_id=item.id,
                session_id=session_id,
                now=now,
            )
            await uow.sessions.add(session)
            if item.status != PlanItemStatus.ACTIVE:
                await uow.plans.update_item(item.change_status(PlanItemStatus.ACTIVE))
            await uow.commit()
            return await _get_session_details(uow, session)


class GetStudySession:
    def __init__(self, dependencies: ApplicationDependencies) -> None:
        self._dependencies = dependencies

    async def execute(self, session_id: str) -> SessionDetails:
        async with self._dependencies.uow_factory() as uow:
            session = await uow.sessions.get(session_id)
            if session is None:
                raise NotFoundError("study session", session_id)
            return await _get_session_details(uow, session)


class SubmitExerciseAttempt:
    def __init__(self, dependencies: ApplicationDependencies) -> None:
        self._dependencies = dependencies

    async def execute(self, command: SubmitAttemptCommand) -> AttemptResult:
        now = self._dependencies.clock()
        attempt_id = deterministic_id(
            f"exercise-attempt:{command.session_id}", command.idempotency_key
        )
        async with self._dependencies.uow_factory() as uow:
            session = await uow.sessions.get(command.session_id)
            if session is None:
                raise NotFoundError("study session", command.session_id)
            if session.status != StudySessionStatus.ACTIVE:
                raise ConflictError("completed study sessions cannot accept answers")
            exercise = await uow.exercises.get(command.exercise_id)
            if exercise is None:
                raise NotFoundError("exercise", command.exercise_id)
            item = await uow.plans.get_item(session.plan_item_id)
            if item is None:
                raise NotFoundError("plan item", session.plan_item_id)
            if exercise.knowledge_node_id != item.knowledge_node_id:
                raise ConflictError("exercise does not belong to the study session")

            existing = await uow.exercises.get_attempt(attempt_id)
            if existing is not None:
                if (
                    existing.exercise_id != exercise.id
                    or existing.study_session_id != session.id
                    or existing.answer != command.selected_options
                ):
                    raise ConflictError(
                        "idempotency key was already used with a different answer"
                    )
                return await self._existing_result(uow, existing, exercise)

            grade = grade_multiple_choice(
                exercise,
                list(command.selected_options),
                study_session_id=session.id,
                attempted_at=now,
                attempt_id=attempt_id,
            )
            current_mastery = await uow.mastery.get_snapshot(
                DEFAULT_USER_ID, exercise.knowledge_node_id
            )
            event_type = _mastery_event_type(
                is_correct=grade.attempt.is_correct,
                current=current_mastery,
            )
            event = MasteryEvent.create(
                user_id=DEFAULT_USER_ID,
                knowledge_node_id=exercise.knowledge_node_id,
                event_type=event_type,
                delta=mastery_delta(event_type),
                attempt_id=grade.attempt.id,
                occurred_at=now,
            )
            mastery = apply_mastery_event(current_mastery, event)
            current_review = await uow.reviews.get(
                DEFAULT_USER_ID, exercise.knowledge_node_id
            )
            scheduled = self._dependencies.review_scheduler.review(
                user_id=DEFAULT_USER_ID,
                knowledge_node_id=exercise.knowledge_node_id,
                rating=(
                    ReviewRating.GOOD
                    if grade.attempt.is_correct
                    else ReviewRating.AGAIN
                ),
                reviewed_at=now,
                current=current_review,
            )
            await uow.exercises.add_attempt(grade.attempt)
            await uow.mastery.add_event(event)
            await uow.mastery.save_snapshot(mastery)
            await uow.reviews.save(scheduled.schedule)
            await uow.commit()
            return AttemptResult(
                attempt=grade.attempt,
                expected_answer=grade.expected_answer,
                mastery=mastery,
                review=scheduled.schedule,
            )

    async def _existing_result(
        self, uow: UnitOfWork, attempt: ExerciseAttempt, exercise: Exercise
    ) -> AttemptResult:
        mastery = await uow.mastery.get_snapshot(
            DEFAULT_USER_ID, exercise.knowledge_node_id
        )
        review = await uow.reviews.get(DEFAULT_USER_ID, exercise.knowledge_node_id)
        if mastery is None or review is None:
            raise ConflictError("attempt result is incomplete")
        return AttemptResult(
            attempt=attempt,
            expected_answer=exercise.answer_key,
            mastery=mastery,
            review=review,
        )


class CompleteStudySession:
    def __init__(self, dependencies: ApplicationDependencies) -> None:
        self._dependencies = dependencies

    async def execute(self, session_id: str) -> SessionDetails:
        now = self._dependencies.clock()
        async with self._dependencies.uow_factory() as uow:
            session = await uow.sessions.get(session_id)
            if session is None:
                raise NotFoundError("study session", session_id)
            if session.status == StudySessionStatus.COMPLETED:
                return await _get_session_details(uow, session)
            attempts = await uow.exercises.list_attempts_for_session(session.id)
            if not attempts:
                raise ConflictError("submit an answer before completing the session")
            item = await uow.plans.get_item(session.plan_item_id)
            if item is None:
                raise NotFoundError("plan item", session.plan_item_id)
            completed = session.complete(now=now)
            await uow.sessions.update(completed)
            await uow.plans.update_item(item.change_status(PlanItemStatus.COMPLETED))
            await uow.commit()
            return await _get_session_details(uow, completed)


class GetDueReviews:
    def __init__(self, dependencies: ApplicationDependencies) -> None:
        self._dependencies = dependencies

    async def execute(self, *, due_before: datetime | None = None) -> list[DueReview]:
        cutoff = due_before or self._dependencies.clock()
        async with self._dependencies.uow_factory() as uow:
            schedules = await uow.reviews.list_due(DEFAULT_USER_ID, cutoff)
            results: list[DueReview] = []
            for schedule in schedules:
                node = await uow.knowledge.get_node(schedule.knowledge_node_id)
                if node is None:
                    raise NotFoundError("knowledge node", schedule.knowledge_node_id)
                exercises = await uow.exercises.list_for_node(node.id)
                results.append(
                    DueReview(
                        schedule=schedule,
                        knowledge_node=node,
                        exercise=exercises[0] if exercises else None,
                    )
                )
            return results


def _mastery_event_type(
    *, is_correct: bool, current: MasterySnapshot | None
) -> MasteryEventType:
    if not is_correct:
        return MasteryEventType.INCORRECT
    if current is None or current.attempt_count == 0:
        return MasteryEventType.CORRECT_FIRST_TRY
    return MasteryEventType.CORRECT_AFTER_REMEDIATION
