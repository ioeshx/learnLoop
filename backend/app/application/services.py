"""Use cases for the deterministic learning loop."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from app.application.curriculum import CurriculumGenerator
from app.application.errors import ConflictError, NotFoundError
from app.application.models import (
    AttemptResult,
    CorrectAttemptCommand,
    CreateGoalCommand,
    DueReview,
    GradeAnswerCommand,
    KnowledgeEdgeInsight,
    KnowledgeNodeInsight,
    LearningInsights,
    MasteryTrendPoint,
    PersistPlanProposalCommand,
    PlanDetails,
    ResourceSnippet,
    SessionDetails,
    StartReviewSessionCommand,
    StartSessionCommand,
    SubmitAttemptCommand,
    WeeklyLearningSummary,
)
from app.application.ports import ResourceSearch, UnitOfWork, UnitOfWorkFactory
from app.application.review_exercises import (
    FixedReviewExerciseGenerator,
    ReviewExerciseGenerator,
)
from app.application.templates import FixedCurriculumGenerator
from app.domain.common import deterministic_id, utc_now
from app.domain.exercises import (
    Exercise,
    ExerciseAttempt,
    ObjectiveGrade,
    grade_multiple_choice,
)
from app.domain.goals import GoalStatus, LearningGoal
from app.domain.knowledge import (
    KnowledgeEdge,
    KnowledgeNode,
    RelationType,
    validate_knowledge_graph,
)
from app.domain.mastery import (
    AdaptiveRecommendation,
    MasteryEvent,
    MasteryEventType,
    MasterySnapshot,
    PrerequisiteMastery,
    apply_mastery_event,
    mastery_delta,
    project_mastery,
    recommend_difficulty,
)
from app.domain.plans import PlanItemStatus, StudyPlan
from app.domain.review import ReviewRating, ReviewSchedule, ReviewScheduler
from app.domain.sessions import StudySession, StudySessionKind, StudySessionStatus
from app.domain.users import User

DEFAULT_USER_ID = "00000000-0000-4000-8000-000000000001"


@dataclass(frozen=True, slots=True)
class ApplicationDependencies:
    uow_factory: UnitOfWorkFactory
    review_scheduler: ReviewScheduler
    clock: Callable[[], datetime] = utc_now
    curriculum_generator: CurriculumGenerator | None = None
    resource_search: ResourceSearch | None = None
    review_exercise_generator: ReviewExerciseGenerator | None = None


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
    exercise = (
        await uow.exercises.get(session.exercise_id)
        if session.exercise_id is not None
        else None
    )
    if exercise is None:
        exercises = await uow.exercises.list_for_node(node.id)
        if not exercises:
            raise NotFoundError("exercise for knowledge node", node.id)
        exercise = exercises[0]
    attempts = await uow.exercises.list_attempts_for_session(session.id)
    latest_result: AttemptResult | None = None
    if attempts:
        latest_attempt = attempts[-1]
        attempted_exercise = await uow.exercises.get(latest_attempt.exercise_id)
        if attempted_exercise is None:
            raise ConflictError("attempt exercise is missing")
        mastery = await uow.mastery.get_snapshot(DEFAULT_USER_ID, node.id)
        review = await uow.reviews.get(DEFAULT_USER_ID, node.id)
        if mastery is None or review is None:
            raise ConflictError("attempt result is incomplete")
        latest_result = AttemptResult(
            attempt=latest_attempt,
            expected_answer=attempted_exercise.answer_key,
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
        adaptation=await _get_adaptive_recommendation(uow, node),
    )


async def _get_adaptive_recommendation(
    uow: UnitOfWork, node: KnowledgeNode
) -> AdaptiveRecommendation:
    snapshot = await uow.mastery.get_snapshot(DEFAULT_USER_ID, node.id)
    node_by_id = {
        candidate.id: candidate
        for candidate in await uow.knowledge.list_nodes(node.goal_id)
    }
    prerequisites: list[PrerequisiteMastery] = []
    for edge in await uow.knowledge.list_edges(node.goal_id):
        if edge.relation != RelationType.PREREQUISITE or edge.target_node_id != node.id:
            continue
        prerequisite = node_by_id.get(edge.source_node_id)
        if prerequisite is None:
            continue
        prerequisite_snapshot = await uow.mastery.get_snapshot(
            DEFAULT_USER_ID, prerequisite.id
        )
        prerequisites.append(
            PrerequisiteMastery(
                knowledge_node_id=prerequisite.id,
                title=prerequisite.title,
                score=(prerequisite_snapshot.score if prerequisite_snapshot else 0.0),
            )
        )
    return recommend_difficulty(
        base_difficulty=node.difficulty,
        mastery_score=snapshot.score if snapshot is not None else 0.0,
        prerequisites=tuple(prerequisites),
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


class ListLearningGoals:
    def __init__(self, dependencies: ApplicationDependencies) -> None:
        self._dependencies = dependencies

    async def execute(self) -> list[LearningGoal]:
        async with self._dependencies.uow_factory() as uow:
            return await uow.goals.list_for_user(DEFAULT_USER_ID)


class ExportLearningData:
    """Create a portable JSON backup without exposing model prompts or secrets."""

    def __init__(self, dependencies: ApplicationDependencies) -> None:
        self._dependencies = dependencies

    async def execute(self) -> dict[str, object]:
        async with self._dependencies.uow_factory() as uow:
            goals = await uow.goals.list_for_user(DEFAULT_USER_ID)
            exported_goals: list[dict[str, object]] = []
            for goal in goals:
                nodes = await uow.knowledge.list_nodes(goal.id)
                edges = await uow.knowledge.list_edges(goal.id)
                plans = await uow.plans.list_for_goal(goal.id)
                node_data: list[dict[str, object]] = []
                for node in nodes:
                    exercises = await uow.exercises.list_for_node(node.id)
                    exercise_data: list[dict[str, object]] = []
                    for exercise in exercises:
                        attempts = await uow.exercises.list_attempts(exercise.id)
                        exercise_data.append(
                            {
                                "id": exercise.id,
                                "type": exercise.exercise_type.value,
                                "prompt": exercise.prompt,
                                "options": list(exercise.options),
                                "answer_key": list(exercise.answer_key),
                                "attempts": [
                                    {
                                        "id": attempt.id,
                                        "answer": list(attempt.answer),
                                        "score": attempt.score,
                                        "is_correct": attempt.is_correct,
                                        "attempted_at": (
                                            attempt.attempted_at.isoformat()
                                        ),
                                    }
                                    for attempt in attempts
                                ],
                            }
                        )
                    snapshot = await uow.mastery.get_snapshot(DEFAULT_USER_ID, node.id)
                    events = await uow.mastery.list_events(DEFAULT_USER_ID, node.id)
                    review = await uow.reviews.get(DEFAULT_USER_ID, node.id)
                    node_data.append(
                        {
                            "id": node.id,
                            "title": node.title,
                            "description": node.description,
                            "lesson_content": node.lesson_content,
                            "difficulty": node.difficulty,
                            "exercises": exercise_data,
                            "mastery": (
                                {
                                    "score": snapshot.score,
                                    "attempt_count": snapshot.attempt_count,
                                    "correct_count": snapshot.correct_count,
                                    "updated_at": snapshot.updated_at.isoformat(),
                                }
                                if snapshot
                                else None
                            ),
                            "mastery_events": [
                                {
                                    "id": event.id,
                                    "attempt_id": event.attempt_id,
                                    "type": event.event_type.value,
                                    "delta": event.delta,
                                    "occurred_at": event.occurred_at.isoformat(),
                                }
                                for event in events
                            ],
                            "review": (
                                {
                                    "due_at": review.due_at.isoformat(),
                                    "last_review_at": (
                                        review.last_review_at.isoformat()
                                        if review.last_review_at
                                        else None
                                    ),
                                    "card_json": review.card_json,
                                }
                                if review
                                else None
                            ),
                        }
                    )
                exported_goals.append(
                    {
                        "id": goal.id,
                        "title": goal.title,
                        "description": goal.description,
                        "desired_outcome": goal.desired_outcome,
                        "weekly_minutes": goal.weekly_minutes,
                        "target_date": (
                            goal.target_date.isoformat() if goal.target_date else None
                        ),
                        "status": goal.status.value,
                        "nodes": node_data,
                        "edges": [
                            {
                                "source_node_id": edge.source_node_id,
                                "target_node_id": edge.target_node_id,
                                "relation": edge.relation.value,
                            }
                            for edge in edges
                        ],
                        "plans": [
                            {
                                "id": plan.id,
                                "version": plan.version,
                                "status": plan.status.value,
                                "items": [
                                    {
                                        "id": item.id,
                                        "knowledge_node_id": item.knowledge_node_id,
                                        "title": item.title,
                                        "position": item.position,
                                        "estimated_minutes": item.estimated_minutes,
                                        "status": item.status.value,
                                    }
                                    for item in plan.items
                                ],
                            }
                            for plan in plans
                        ],
                    }
                )
        return {
            "schema": "learnloop.learning-data",
            "version": "1.0.0",
            "exported_at": self._dependencies.clock().isoformat(),
            "goals": exported_goals,
        }


class GetLearningInsights:
    def __init__(self, dependencies: ApplicationDependencies) -> None:
        self._dependencies = dependencies

    async def execute(self, goal_id: str) -> LearningInsights:
        now = self._dependencies.clock().astimezone(UTC)
        week_start = now - timedelta(days=7)
        async with self._dependencies.uow_factory() as uow:
            goal = await uow.goals.get(goal_id)
            if goal is None:
                raise NotFoundError("learning goal", goal_id)
            nodes = await uow.knowledge.list_nodes(goal_id)
            edges = await uow.knowledge.list_edges(goal_id)
            plans = await uow.plans.list_for_goal(goal_id)
            latest_plan = plans[-1] if plans else None
            status_by_node = {
                item.knowledge_node_id: item.status.value
                for item in (latest_plan.items if latest_plan else ())
            }
            node_insights: list[KnowledgeNodeInsight] = []
            trend: list[MasteryTrendPoint] = []
            weekly_attempts = 0
            weekly_correct = 0
            mastery_scores: list[float] = []
            for node in nodes:
                snapshot = await uow.mastery.get_snapshot(DEFAULT_USER_ID, node.id)
                score = snapshot.score if snapshot is not None else 0.0
                mastery_scores.append(score)
                node_insights.append(
                    KnowledgeNodeInsight(
                        id=node.id,
                        title=node.title,
                        difficulty=node.difficulty,
                        mastery_score=score,
                        status=status_by_node.get(node.id, "unplanned"),
                    )
                )
                projection: MasterySnapshot | None = None
                for event in await uow.mastery.list_events(DEFAULT_USER_ID, node.id):
                    projection = apply_mastery_event(projection, event)
                    trend.append(
                        MasteryTrendPoint(
                            knowledge_node_id=node.id,
                            knowledge_node_title=node.title,
                            score=projection.score,
                            event_type=event.event_type.value,
                            occurred_at=event.occurred_at,
                        )
                    )
                for exercise in await uow.exercises.list_for_node(node.id):
                    for attempt in await uow.exercises.list_attempts(exercise.id):
                        if week_start <= attempt.attempted_at.astimezone(UTC) <= now:
                            weekly_attempts += 1
                            weekly_correct += int(attempt.is_correct)
            completed_items = sum(
                item.status == PlanItemStatus.COMPLETED
                for item in (latest_plan.items if latest_plan else ())
            )
            return LearningInsights(
                goal_id=goal_id,
                nodes=tuple(node_insights),
                edges=tuple(
                    KnowledgeEdgeInsight(
                        source_node_id=edge.source_node_id,
                        target_node_id=edge.target_node_id,
                        relation=edge.relation.value,
                    )
                    for edge in edges
                ),
                mastery_trend=tuple(
                    sorted(trend, key=lambda point: point.occurred_at)
                ),
                weekly=WeeklyLearningSummary(
                    attempts=weekly_attempts,
                    correct_attempts=weekly_correct,
                    completed_plan_items=completed_items,
                    average_mastery=(
                        sum(mastery_scores) / len(mastery_scores)
                        if mastery_scores
                        else 0.0
                    ),
                ),
            )


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

        generator = (
            self._dependencies.curriculum_generator or FixedCurriculumGenerator()
        )
        curriculum = await generator.generate(goal, now=now)

        async with self._dependencies.uow_factory() as uow:
            persisted_goal = await uow.goals.get(goal_id)
            if persisted_goal is None:
                raise NotFoundError("learning goal", goal_id)
            existing = await uow.plans.list_for_goal(goal_id)
            if existing:
                return await _get_plan_details(uow, existing[-1])

            for node in curriculum.nodes:
                await uow.knowledge.add_node(node)
            for edge in curriculum.edges:
                await uow.knowledge.add_edge(edge)
            await uow.plans.add(curriculum.plan)
            for exercise in curriculum.exercises:
                await uow.exercises.add(exercise)
            if persisted_goal.status == GoalStatus.DRAFT:
                await uow.goals.update(
                    persisted_goal.change_status(GoalStatus.ACTIVE, now=now)
                )
            await uow.commit()
            return await _get_plan_details(uow, curriculum.plan)


class PersistStudyPlanProposal:
    """Validate and atomically persist an approved Agent plan proposal."""

    def __init__(self, dependencies: ApplicationDependencies) -> None:
        self._dependencies = dependencies

    async def execute(self, command: PersistPlanProposalCommand) -> PlanDetails:
        now = self._dependencies.clock()
        async with self._dependencies.uow_factory() as uow:
            goal = await uow.goals.get(command.goal_id)
            if goal is None:
                raise NotFoundError("learning goal", command.goal_id)
            existing = await uow.plans.list_for_goal(command.goal_id)
            if existing:
                return await _get_plan_details(uow, existing[-1])

        node_keys = [node.key for node in command.nodes]
        if not node_keys or len(node_keys) != len(set(node_keys)):
            raise ValueError(
                "proposed knowledge node keys must be non-empty and unique"
            )
        node_by_key = {
            proposal.key: KnowledgeNode.create(
                goal_id=command.goal_id,
                title=proposal.title,
                description=proposal.description,
                difficulty=proposal.difficulty,
                now=now,
            )
            for proposal in command.nodes
        }
        try:
            edges = tuple(
                KnowledgeEdge.create(
                    goal_id=command.goal_id,
                    source_node_id=node_by_key[proposal_edge.source_key].id,
                    target_node_id=node_by_key[proposal_edge.target_key].id,
                    relation=RelationType(proposal_edge.relation),
                    now=now,
                )
                for proposal_edge in command.edges
            )
        except KeyError as error:
            raise ValueError("proposed edge references an unknown node") from error
        nodes = tuple(node_by_key.values())
        validate_knowledge_graph(nodes, edges)
        item_keys = [item.knowledge_node_key for item in command.items]
        if set(item_keys) != set(node_keys) or len(item_keys) != len(node_keys):
            raise ValueError("study plan must contain every proposed node once")
        position = {key: index for index, key in enumerate(item_keys)}
        for proposal_edge in command.edges:
            if (
                proposal_edge.relation == RelationType.PREREQUISITE.value
                and position[proposal_edge.source_key]
                >= position[proposal_edge.target_key]
            ):
                raise ValueError("study plan violates prerequisite ordering")
        plan = StudyPlan.create(
            goal_id=command.goal_id,
            item_specs=[
                (
                    node_by_key[item.knowledge_node_key].id,
                    item.title,
                    item.estimated_minutes,
                )
                for item in command.items
            ],
            now=now,
        )
        exercises = tuple(
            Exercise.create_multiple_choice(
                knowledge_node_id=node.id,
                prompt=f"以下哪项最符合“{node.title}”的学习重点？",
                options=[
                    node.description,
                    "跳过概念验证，直接记忆结论",
                    "只记录学习时长，不检查理解",
                ],
                answer_key=[node.description],
                now=now,
            )
            for node in nodes
        )

        async with self._dependencies.uow_factory() as uow:
            persisted_goal = await uow.goals.get(command.goal_id)
            if persisted_goal is None:
                raise NotFoundError("learning goal", command.goal_id)
            existing = await uow.plans.list_for_goal(command.goal_id)
            if existing:
                return await _get_plan_details(uow, existing[-1])
            for node in nodes:
                await uow.knowledge.add_node(node)
            for domain_edge in edges:
                await uow.knowledge.add_edge(domain_edge)
            await uow.plans.add(plan)
            for exercise in exercises:
                await uow.exercises.add(exercise)
            if persisted_goal.status == GoalStatus.DRAFT:
                await uow.goals.update(
                    persisted_goal.change_status(GoalStatus.ACTIVE, now=now)
                )
            await uow.commit()
            return await _get_plan_details(uow, plan)


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
                exercise_id=exercises[0].id,
            )
            await uow.sessions.add(session)
            if item.status != PlanItemStatus.ACTIVE:
                await uow.plans.update_item(item.change_status(PlanItemStatus.ACTIVE))
            await uow.commit()
            return await _get_session_details(uow, session)


class StartReviewSession:
    """Create an adaptive review exercise and bind it to a review session."""

    def __init__(self, dependencies: ApplicationDependencies) -> None:
        self._dependencies = dependencies

    async def execute(self, command: StartReviewSessionCommand) -> SessionDetails:
        now = self._dependencies.clock()
        session_id = deterministic_id(
            f"review-session:{command.knowledge_node_id}", command.idempotency_key
        )
        async with self._dependencies.uow_factory() as uow:
            existing = await uow.sessions.get(session_id)
            if existing is not None:
                if existing.kind != StudySessionKind.REVIEW:
                    raise ConflictError("idempotency key belongs to another session")
                return await _get_session_details(uow, existing)
            node = await uow.knowledge.get_node(command.knowledge_node_id)
            if node is None:
                raise NotFoundError("knowledge node", command.knowledge_node_id)
            goal = await uow.goals.get(node.goal_id)
            if goal is None:
                raise NotFoundError("learning goal", node.goal_id)
            schedule = await uow.reviews.get(DEFAULT_USER_ID, node.id)
            if schedule is None:
                raise ConflictError("knowledge node has no review schedule")
            if schedule.due_at > now:
                raise ConflictError("review is not due yet")
            plans = await uow.plans.list_for_goal(node.goal_id)
            item = next(
                (
                    candidate
                    for plan in reversed(plans)
                    for candidate in plan.items
                    if candidate.knowledge_node_id == node.id
                ),
                None,
            )
            if item is None:
                raise NotFoundError("plan item for knowledge node", node.id)
            recommendation = await _get_adaptive_recommendation(uow, node)

        generator = (
            self._dependencies.review_exercise_generator
            or FixedReviewExerciseGenerator()
        )
        exercise = await generator.generate(goal, node, recommendation, now=now)

        async with self._dependencies.uow_factory() as uow:
            existing = await uow.sessions.get(session_id)
            if existing is not None:
                return await _get_session_details(uow, existing)
            await uow.exercises.add(exercise)
            session = StudySession.create(
                goal_id=node.goal_id,
                plan_item_id=item.id,
                session_id=session_id,
                now=now,
                kind=StudySessionKind.REVIEW,
                exercise_id=exercise.id,
            )
            await uow.sessions.add(session)
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


class GetMasteryState:
    def __init__(self, dependencies: ApplicationDependencies) -> None:
        self._dependencies = dependencies

    async def execute(self, knowledge_node_id: str) -> MasterySnapshot | None:
        async with self._dependencies.uow_factory() as uow:
            node = await uow.knowledge.get_node(knowledge_node_id)
            if node is None:
                raise NotFoundError("knowledge node", knowledge_node_id)
            return await uow.mastery.get_snapshot(DEFAULT_USER_ID, knowledge_node_id)


class GetAdaptiveRecommendation:
    def __init__(self, dependencies: ApplicationDependencies) -> None:
        self._dependencies = dependencies

    async def execute(self, knowledge_node_id: str) -> AdaptiveRecommendation:
        async with self._dependencies.uow_factory() as uow:
            node = await uow.knowledge.get_node(knowledge_node_id)
            if node is None:
                raise NotFoundError("knowledge node", knowledge_node_id)
            return await _get_adaptive_recommendation(uow, node)


class CreateRemediationExercise:
    """Generate a simpler exercise after a diagnosed wrong answer."""

    def __init__(self, dependencies: ApplicationDependencies) -> None:
        self._dependencies = dependencies

    async def execute(
        self,
        session_id: str,
        *,
        remediation_count: int,
        idempotency_key: str,
    ) -> SessionDetails:
        if not 1 <= remediation_count <= 2:
            raise ValueError("remediation count must be 1 or 2")
        now = self._dependencies.clock()
        exercise_id = deterministic_id(
            f"remediation-exercise:{session_id}", idempotency_key
        )
        async with self._dependencies.uow_factory() as uow:
            session = await uow.sessions.get(session_id)
            if session is None:
                raise NotFoundError("study session", session_id)
            existing = await uow.exercises.get(exercise_id)
            if existing is not None:
                if session.exercise_id != existing.id:
                    session = replace(session, exercise_id=existing.id)
                    await uow.sessions.update(session)
                    await uow.commit()
                return await _get_session_details(uow, session)
            item = await uow.plans.get_item(session.plan_item_id)
            if item is None:
                raise NotFoundError("plan item", session.plan_item_id)
            node = await uow.knowledge.get_node(item.knowledge_node_id)
            if node is None:
                raise NotFoundError("knowledge node", item.knowledge_node_id)
            goal = await uow.goals.get(node.goal_id)
            if goal is None:
                raise NotFoundError("learning goal", node.goal_id)
            recommendation = await _get_adaptive_recommendation(uow, node)
            simpler = replace(
                recommendation,
                target_difficulty=max(
                    1.0,
                    recommendation.target_difficulty - 0.5 * remediation_count,
                ),
                reasons=(
                    *recommendation.reasons,
                    f"第 {remediation_count} 次补救，练习再降低半级",
                ),
            )

        generator = (
            self._dependencies.review_exercise_generator
            or FixedReviewExerciseGenerator()
        )
        generated = await generator.generate(goal, node, simpler, now=now)
        exercise = replace(generated, id=exercise_id)

        async with self._dependencies.uow_factory() as uow:
            session = await uow.sessions.get(session_id)
            if session is None:
                raise NotFoundError("study session", session_id)
            existing = await uow.exercises.get(exercise_id)
            if existing is None:
                await uow.exercises.add(exercise)
            updated = replace(session, exercise_id=exercise_id)
            await uow.sessions.update(updated)
            await uow.commit()
            return await _get_session_details(uow, updated)


class SearchLearningResources:
    """Retrieve grounded personal-resource citations for a knowledge node."""

    def __init__(self, dependencies: ApplicationDependencies) -> None:
        self._dependencies = dependencies

    async def execute(self, knowledge_node_id: str) -> tuple[ResourceSnippet, ...]:
        async with self._dependencies.uow_factory() as uow:
            node = await uow.knowledge.get_node(knowledge_node_id)
            if node is None:
                raise NotFoundError("knowledge node", knowledge_node_id)
        if self._dependencies.resource_search is None:
            return ()
        citations = await self._dependencies.resource_search.search_for_knowledge_node(
            knowledge_node_id
        )
        return tuple(
            ResourceSnippet(
                resource_id=citation.resource_id,
                chunk_id=citation.chunk_id,
                title=citation.title,
                excerpt=citation.excerpt,
                score=citation.score,
                page_number=citation.page_number,
                section=citation.section,
                source_uri=citation.source_uri,
            )
            for citation in citations
        )


class GradeObjectiveAnswer:
    def __init__(self, dependencies: ApplicationDependencies) -> None:
        self._dependencies = dependencies

    async def execute(self, command: GradeAnswerCommand) -> ObjectiveGrade:
        now = self._dependencies.clock()
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
            return grade_multiple_choice(
                exercise,
                list(command.selected_options),
                study_session_id=session.id,
                attempted_at=now,
            )


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
                return await _existing_attempt_result(uow, existing, exercise)

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
                is_review=session.kind == StudySessionKind.REVIEW,
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


class CorrectExerciseAttempt:
    """Replace a persisted answer and rebuild its mastery projection."""

    def __init__(self, dependencies: ApplicationDependencies) -> None:
        self._dependencies = dependencies

    async def execute(self, command: CorrectAttemptCommand) -> AttemptResult:
        async with self._dependencies.uow_factory() as uow:
            session = await uow.sessions.get(command.session_id)
            if session is None:
                raise NotFoundError("study session", command.session_id)
            attempt = await uow.exercises.get_attempt(command.attempt_id)
            if attempt is None or attempt.study_session_id != session.id:
                raise NotFoundError("exercise attempt", command.attempt_id)
            exercise = await uow.exercises.get(attempt.exercise_id)
            if exercise is None:
                raise NotFoundError("exercise", attempt.exercise_id)
            corrected_grade = grade_multiple_choice(
                exercise,
                list(command.selected_options),
                study_session_id=session.id,
                attempted_at=attempt.attempted_at,
                attempt_id=attempt.id,
            )
            events = await uow.mastery.list_events(
                DEFAULT_USER_ID, exercise.knowledge_node_id
            )
            event = next(
                (
                    candidate
                    for candidate in events
                    if candidate.attempt_id == attempt.id
                ),
                None,
            )
            if event is None:
                raise ConflictError("attempt has no mastery event")
            prior_events = [
                candidate for candidate in events if candidate.id != event.id
            ]
            event_type = _mastery_event_type(
                is_correct=corrected_grade.attempt.is_correct,
                current=project_mastery(
                    [
                        candidate
                        for candidate in prior_events
                        if candidate.occurred_at <= event.occurred_at
                    ]
                ),
                is_review=session.kind == StudySessionKind.REVIEW,
            )
            corrected_event = replace(
                event,
                event_type=event_type,
                delta=mastery_delta(event_type),
            )
            projection = project_mastery([*prior_events, corrected_event])
            if projection is None:
                raise ConflictError("mastery projection cannot be empty")
            review = await uow.reviews.get(DEFAULT_USER_ID, exercise.knowledge_node_id)
            if review is None:
                raise ConflictError("attempt has no review schedule")
            await uow.exercises.update_attempt(corrected_grade.attempt)
            await uow.mastery.update_event(corrected_event)
            await uow.mastery.save_snapshot(projection)
            await uow.commit()
            return AttemptResult(
                attempt=corrected_grade.attempt,
                expected_answer=corrected_grade.expected_answer,
                mastery=projection,
                review=review,
            )


async def _existing_attempt_result(
    uow: UnitOfWork, attempt: ExerciseAttempt, exercise: Exercise
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
            if session.kind == StudySessionKind.LEARNING:
                await uow.plans.update_item(
                    item.change_status(PlanItemStatus.COMPLETED)
                )
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
                mastery = await uow.mastery.get_snapshot(DEFAULT_USER_ID, node.id)
                overdue_days = max(
                    0, int((cutoff - schedule.due_at).total_seconds() // 86_400)
                )
                mastery_score = mastery.score if mastery is not None else 0.0
                priority = round(
                    overdue_days * 10
                    + (1.0 - mastery_score) * 100
                    + node.difficulty * 5,
                    2,
                )
                results.append(
                    DueReview(
                        schedule=schedule,
                        knowledge_node=node,
                        exercise=exercises[0] if exercises else None,
                        priority_score=priority,
                        overdue_days=overdue_days,
                        reason=(
                            f"逾期 {overdue_days} 天；掌握度 {mastery_score:.0%}；"
                            f"知识点难度 {node.difficulty:.1f}"
                        ),
                    )
                )
            return sorted(results, key=lambda item: item.priority_score, reverse=True)


class DeferReview:
    def __init__(self, dependencies: ApplicationDependencies) -> None:
        self._dependencies = dependencies

    async def execute(
        self, knowledge_node_id: str, *, days: int = 1
    ) -> ReviewSchedule:
        if not 1 <= days <= 7:
            raise ValueError("review may be deferred by 1 to 7 days")
        now = self._dependencies.clock()
        async with self._dependencies.uow_factory() as uow:
            schedule = await uow.reviews.get(DEFAULT_USER_ID, knowledge_node_id)
            if schedule is None:
                raise NotFoundError("review schedule", knowledge_node_id)
            deferred = replace(schedule, due_at=now + timedelta(days=days))
            await uow.reviews.save(deferred)
            await uow.commit()
            return deferred


def _mastery_event_type(
    *, is_correct: bool, current: MasterySnapshot | None, is_review: bool = False
) -> MasteryEventType:
    if not is_correct:
        return MasteryEventType.INCORRECT
    if is_review:
        return MasteryEventType.REVIEW_CORRECT
    if current is None or current.attempt_count == 0:
        return MasteryEventType.CORRECT_FIRST_TRY
    return MasteryEventType.CORRECT_AFTER_REMEDIATION
