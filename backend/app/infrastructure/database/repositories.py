"""SQLAlchemy implementations of domain repository contracts."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.exercises.models import Exercise, ExerciseAttempt, ExerciseType
from app.domain.goals.models import GoalStatus, LearningGoal
from app.domain.knowledge.graph import validate_knowledge_graph
from app.domain.knowledge.models import KnowledgeEdge, KnowledgeNode, RelationType
from app.domain.mastery.models import MasteryEvent, MasterySnapshot
from app.domain.plans.models import (
    PlanItem,
    PlanItemStatus,
    StudyPlan,
    StudyPlanStatus,
)
from app.domain.review.models import ReviewSchedule
from app.domain.sessions.models import StudySession, StudySessionStatus
from app.domain.users.models import User
from app.infrastructure.database.models import (
    ExerciseAttemptModel,
    ExerciseModel,
    KnowledgeEdgeModel,
    KnowledgeNodeModel,
    LearningGoalModel,
    MasteryEventModel,
    MasterySnapshotModel,
    PlanItemModel,
    ReviewScheduleModel,
    StudyPlanModel,
    StudySessionModel,
    UserModel,
)


class SqlAlchemyUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, user: User) -> None:
        self._session.add(
            UserModel(
                id=user.id,
                display_name=user.display_name,
                timezone=user.timezone,
                created_at=user.created_at,
                updated_at=user.updated_at,
            )
        )

    async def get(self, user_id: str) -> User | None:
        model = await self._session.get(UserModel, user_id)
        return _user_from_model(model) if model is not None else None


class SqlAlchemyLearningGoalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, goal: LearningGoal) -> None:
        self._session.add(_goal_to_model(goal))

    async def get(self, goal_id: str) -> LearningGoal | None:
        model = await self._session.get(LearningGoalModel, goal_id)
        return _goal_from_model(model) if model is not None else None

    async def update(self, goal: LearningGoal) -> None:
        model = await self._session.get(LearningGoalModel, goal.id)
        if model is None:
            raise LookupError(f"learning goal {goal.id} was not found")
        model.title = goal.title
        model.description = goal.description
        model.desired_outcome = goal.desired_outcome
        model.weekly_minutes = goal.weekly_minutes
        model.target_date = goal.target_date
        model.status = goal.status.value
        model.updated_at = goal.updated_at

    async def list_for_user(self, user_id: str) -> list[LearningGoal]:
        result = await self._session.scalars(
            select(LearningGoalModel)
            .where(LearningGoalModel.user_id == user_id)
            .order_by(LearningGoalModel.created_at)
        )
        return [_goal_from_model(model) for model in result]


class SqlAlchemyKnowledgeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_node(self, node: KnowledgeNode) -> None:
        self._session.add(
            KnowledgeNodeModel(
                id=node.id,
                goal_id=node.goal_id,
                title=node.title,
                description=node.description,
                difficulty=node.difficulty,
                created_at=node.created_at,
            )
        )

    async def add_edge(self, edge: KnowledgeEdge) -> None:
        nodes = await self.list_nodes(edge.goal_id)
        edges = await self.list_edges(edge.goal_id)
        validate_knowledge_graph(nodes, [*edges, edge])
        self._session.add(
            KnowledgeEdgeModel(
                id=edge.id,
                goal_id=edge.goal_id,
                source_node_id=edge.source_node_id,
                target_node_id=edge.target_node_id,
                relation=edge.relation.value,
                created_at=edge.created_at,
            )
        )

    async def get_node(self, node_id: str) -> KnowledgeNode | None:
        model = await self._session.get(KnowledgeNodeModel, node_id)
        return _knowledge_node_from_model(model) if model is not None else None

    async def list_nodes(self, goal_id: str) -> list[KnowledgeNode]:
        result = await self._session.scalars(
            select(KnowledgeNodeModel)
            .where(KnowledgeNodeModel.goal_id == goal_id)
            .order_by(KnowledgeNodeModel.created_at)
        )
        return [_knowledge_node_from_model(model) for model in result]

    async def list_edges(self, goal_id: str) -> list[KnowledgeEdge]:
        result = await self._session.scalars(
            select(KnowledgeEdgeModel)
            .where(KnowledgeEdgeModel.goal_id == goal_id)
            .order_by(KnowledgeEdgeModel.created_at)
        )
        return [_knowledge_edge_from_model(model) for model in result]


class SqlAlchemyStudyPlanRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, plan: StudyPlan) -> None:
        self._session.add(
            StudyPlanModel(
                id=plan.id,
                goal_id=plan.goal_id,
                version=plan.version,
                status=plan.status.value,
                created_at=plan.created_at,
            )
        )
        self._session.add_all(
            [
                PlanItemModel(
                    id=item.id,
                    plan_id=item.plan_id,
                    knowledge_node_id=item.knowledge_node_id,
                    title=item.title,
                    position=item.position,
                    estimated_minutes=item.estimated_minutes,
                    status=item.status.value,
                )
                for item in plan.items
            ]
        )

    async def get(self, plan_id: str) -> StudyPlan | None:
        model = await self._session.get(StudyPlanModel, plan_id)
        if model is None:
            return None
        return await self._plan_from_model(model)

    async def list_for_goal(self, goal_id: str) -> list[StudyPlan]:
        result = await self._session.scalars(
            select(StudyPlanModel)
            .where(StudyPlanModel.goal_id == goal_id)
            .order_by(StudyPlanModel.version)
        )
        return [await self._plan_from_model(model) for model in result]

    async def get_item(self, item_id: str) -> PlanItem | None:
        model = await self._session.get(PlanItemModel, item_id)
        return _plan_item_from_model(model) if model is not None else None

    async def update_item(self, item: PlanItem) -> None:
        model = await self._session.get(PlanItemModel, item.id)
        if model is None:
            raise LookupError(f"plan item {item.id} was not found")
        model.title = item.title
        model.position = item.position
        model.estimated_minutes = item.estimated_minutes
        model.status = item.status.value

    async def _plan_from_model(self, model: StudyPlanModel) -> StudyPlan:
        item_result = await self._session.scalars(
            select(PlanItemModel)
            .where(PlanItemModel.plan_id == model.id)
            .order_by(PlanItemModel.position)
        )
        items = tuple(
            PlanItem(
                id=item.id,
                plan_id=item.plan_id,
                knowledge_node_id=item.knowledge_node_id,
                title=item.title,
                position=item.position,
                estimated_minutes=item.estimated_minutes,
                status=PlanItemStatus(item.status),
            )
            for item in item_result
        )
        return StudyPlan(
            id=model.id,
            goal_id=model.goal_id,
            version=model.version,
            status=StudyPlanStatus(model.status),
            created_at=model.created_at,
            items=items,
        )


class SqlAlchemyExerciseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, exercise: Exercise) -> None:
        self._session.add(
            ExerciseModel(
                id=exercise.id,
                knowledge_node_id=exercise.knowledge_node_id,
                exercise_type=exercise.exercise_type.value,
                prompt=exercise.prompt,
                options=list(exercise.options),
                answer_key=list(exercise.answer_key),
                max_score=exercise.max_score,
                created_at=exercise.created_at,
            )
        )

    async def get(self, exercise_id: str) -> Exercise | None:
        model = await self._session.get(ExerciseModel, exercise_id)
        return _exercise_from_model(model) if model is not None else None

    async def list_for_node(self, knowledge_node_id: str) -> list[Exercise]:
        result = await self._session.scalars(
            select(ExerciseModel)
            .where(ExerciseModel.knowledge_node_id == knowledge_node_id)
            .order_by(ExerciseModel.created_at)
        )
        return [_exercise_from_model(model) for model in result]

    async def add_attempt(self, attempt: ExerciseAttempt) -> None:
        self._session.add(
            ExerciseAttemptModel(
                id=attempt.id,
                exercise_id=attempt.exercise_id,
                study_session_id=attempt.study_session_id,
                answer=list(attempt.answer),
                score=attempt.score,
                is_correct=attempt.is_correct,
                attempted_at=attempt.attempted_at,
            )
        )

    async def get_attempt(self, attempt_id: str) -> ExerciseAttempt | None:
        model = await self._session.get(ExerciseAttemptModel, attempt_id)
        return _attempt_from_model(model) if model is not None else None

    async def list_attempts(self, exercise_id: str) -> list[ExerciseAttempt]:
        result = await self._session.scalars(
            select(ExerciseAttemptModel)
            .where(ExerciseAttemptModel.exercise_id == exercise_id)
            .order_by(ExerciseAttemptModel.attempted_at)
        )
        return [_attempt_from_model(model) for model in result]

    async def list_attempts_for_session(
        self, study_session_id: str
    ) -> list[ExerciseAttempt]:
        result = await self._session.scalars(
            select(ExerciseAttemptModel)
            .where(ExerciseAttemptModel.study_session_id == study_session_id)
            .order_by(ExerciseAttemptModel.attempted_at)
        )
        return [_attempt_from_model(model) for model in result]


class SqlAlchemyStudySessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, study_session: StudySession) -> None:
        self._session.add(_study_session_to_model(study_session))

    async def get(self, session_id: str) -> StudySession | None:
        model = await self._session.get(StudySessionModel, session_id)
        return _study_session_from_model(model) if model is not None else None

    async def update(self, study_session: StudySession) -> None:
        model = await self._session.get(StudySessionModel, study_session.id)
        if model is None:
            raise LookupError(f"study session {study_session.id} was not found")
        model.status = study_session.status.value
        model.completed_at = study_session.completed_at


class SqlAlchemyMasteryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_event(self, event: MasteryEvent) -> None:
        self._session.add(
            MasteryEventModel(
                id=event.id,
                user_id=event.user_id,
                knowledge_node_id=event.knowledge_node_id,
                attempt_id=event.attempt_id,
                event_type=event.event_type.value,
                delta=event.delta,
                occurred_at=event.occurred_at,
            )
        )

    async def get_snapshot(
        self, user_id: str, knowledge_node_id: str
    ) -> MasterySnapshot | None:
        model = await self._session.get(
            MasterySnapshotModel,
            {"user_id": user_id, "knowledge_node_id": knowledge_node_id},
        )
        return _mastery_snapshot_from_model(model) if model is not None else None

    async def save_snapshot(self, snapshot: MasterySnapshot) -> None:
        await self._session.merge(
            MasterySnapshotModel(
                user_id=snapshot.user_id,
                knowledge_node_id=snapshot.knowledge_node_id,
                score=snapshot.score,
                attempt_count=snapshot.attempt_count,
                correct_count=snapshot.correct_count,
                updated_at=snapshot.updated_at,
            )
        )


class SqlAlchemyReviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: str, knowledge_node_id: str) -> ReviewSchedule | None:
        model = await self._session.get(
            ReviewScheduleModel,
            {"user_id": user_id, "knowledge_node_id": knowledge_node_id},
        )
        return _review_schedule_from_model(model) if model is not None else None

    async def save(self, schedule: ReviewSchedule) -> None:
        await self._session.merge(
            ReviewScheduleModel(
                user_id=schedule.user_id,
                knowledge_node_id=schedule.knowledge_node_id,
                card_json=schedule.card_json,
                due_at=schedule.due_at,
                last_review_at=schedule.last_review_at,
            )
        )

    async def list_due(
        self, user_id: str, due_before: datetime
    ) -> list[ReviewSchedule]:
        result = await self._session.scalars(
            select(ReviewScheduleModel)
            .where(
                ReviewScheduleModel.user_id == user_id,
                ReviewScheduleModel.due_at <= due_before,
            )
            .order_by(ReviewScheduleModel.due_at)
        )
        return [_review_schedule_from_model(model) for model in result]


def _user_from_model(model: UserModel) -> User:
    return User(
        id=model.id,
        display_name=model.display_name,
        timezone=model.timezone,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _goal_to_model(goal: LearningGoal) -> LearningGoalModel:
    return LearningGoalModel(
        id=goal.id,
        user_id=goal.user_id,
        title=goal.title,
        description=goal.description,
        desired_outcome=goal.desired_outcome,
        weekly_minutes=goal.weekly_minutes,
        target_date=goal.target_date,
        status=goal.status.value,
        created_at=goal.created_at,
        updated_at=goal.updated_at,
    )


def _goal_from_model(model: LearningGoalModel) -> LearningGoal:
    return LearningGoal(
        id=model.id,
        user_id=model.user_id,
        title=model.title,
        description=model.description,
        desired_outcome=model.desired_outcome,
        weekly_minutes=model.weekly_minutes,
        target_date=model.target_date,
        status=GoalStatus(model.status),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _knowledge_node_from_model(model: KnowledgeNodeModel) -> KnowledgeNode:
    return KnowledgeNode(
        id=model.id,
        goal_id=model.goal_id,
        title=model.title,
        description=model.description,
        difficulty=model.difficulty,
        created_at=model.created_at,
    )


def _knowledge_edge_from_model(model: KnowledgeEdgeModel) -> KnowledgeEdge:
    return KnowledgeEdge(
        id=model.id,
        goal_id=model.goal_id,
        source_node_id=model.source_node_id,
        target_node_id=model.target_node_id,
        relation=RelationType(model.relation),
        created_at=model.created_at,
    )


def _exercise_from_model(model: ExerciseModel) -> Exercise:
    return Exercise(
        id=model.id,
        knowledge_node_id=model.knowledge_node_id,
        exercise_type=ExerciseType(model.exercise_type),
        prompt=model.prompt,
        options=tuple(model.options),
        answer_key=tuple(model.answer_key),
        max_score=model.max_score,
        created_at=model.created_at,
    )


def _plan_item_from_model(model: PlanItemModel) -> PlanItem:
    return PlanItem(
        id=model.id,
        plan_id=model.plan_id,
        knowledge_node_id=model.knowledge_node_id,
        title=model.title,
        position=model.position,
        estimated_minutes=model.estimated_minutes,
        status=PlanItemStatus(model.status),
    )


def _study_session_to_model(study_session: StudySession) -> StudySessionModel:
    return StudySessionModel(
        id=study_session.id,
        goal_id=study_session.goal_id,
        plan_item_id=study_session.plan_item_id,
        status=study_session.status.value,
        started_at=study_session.started_at,
        completed_at=study_session.completed_at,
    )


def _study_session_from_model(model: StudySessionModel) -> StudySession:
    if model.plan_item_id is None:
        raise ValueError(f"study session {model.id} has no plan item")
    return StudySession(
        id=model.id,
        goal_id=model.goal_id,
        plan_item_id=model.plan_item_id,
        status=StudySessionStatus(model.status),
        started_at=model.started_at,
        completed_at=model.completed_at,
    )


def _attempt_from_model(model: ExerciseAttemptModel) -> ExerciseAttempt:
    return ExerciseAttempt(
        id=model.id,
        exercise_id=model.exercise_id,
        study_session_id=model.study_session_id,
        answer=tuple(model.answer),
        score=model.score,
        is_correct=model.is_correct,
        attempted_at=model.attempted_at,
    )


def _mastery_snapshot_from_model(model: MasterySnapshotModel) -> MasterySnapshot:
    return MasterySnapshot(
        user_id=model.user_id,
        knowledge_node_id=model.knowledge_node_id,
        score=model.score,
        attempt_count=model.attempt_count,
        correct_count=model.correct_count,
        updated_at=model.updated_at,
    )


def _review_schedule_from_model(model: ReviewScheduleModel) -> ReviewSchedule:
    return ReviewSchedule(
        user_id=model.user_id,
        knowledge_node_id=model.knowledge_node_id,
        card_json=model.card_json,
        due_at=model.due_at,
        last_review_at=model.last_review_at,
    )
