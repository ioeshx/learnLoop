"""HTTP request and response contracts for the deterministic learning loop."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.application.models import AttemptResult, DueReview, PlanDetails, SessionDetails
from app.domain.goals import LearningGoal


class RequestModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class CreateGoalRequest(RequestModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=4_000)
    desired_outcome: str = Field(min_length=1, max_length=4_000)
    weekly_minutes: int = Field(gt=0, le=10_080)
    target_date: date | None = None


class GoalResponse(BaseModel):
    id: str
    user_id: str
    title: str
    description: str
    desired_outcome: str
    weekly_minutes: int
    target_date: date | None
    status: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, goal: LearningGoal) -> "GoalResponse":
        return cls(
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


class PlanItemResponse(BaseModel):
    id: str
    knowledge_node_id: str
    title: str
    description: str
    position: int
    estimated_minutes: int
    status: str


class PlanResponse(BaseModel):
    id: str
    goal_id: str
    version: int
    status: str
    created_at: datetime
    items: list[PlanItemResponse]

    @classmethod
    def from_details(cls, details: PlanDetails) -> "PlanResponse":
        return cls(
            id=details.plan.id,
            goal_id=details.plan.goal_id,
            version=details.plan.version,
            status=details.plan.status.value,
            created_at=details.plan.created_at,
            items=[
                PlanItemResponse(
                    id=item.id,
                    knowledge_node_id=item.knowledge_node_id,
                    title=item.title,
                    description=node.description,
                    position=item.position,
                    estimated_minutes=item.estimated_minutes,
                    status=item.status.value,
                )
                for item, node in zip(details.plan.items, details.nodes, strict=True)
            ],
        )


class StartSessionRequest(RequestModel):
    goal_id: str = Field(min_length=1)
    plan_item_id: str = Field(min_length=1)


class ExerciseResponse(BaseModel):
    id: str
    exercise_type: str
    prompt: str
    options: list[str]
    max_score: float


class AttemptResultResponse(BaseModel):
    attempt_id: str
    selected_options: list[str]
    score: float
    is_correct: bool
    expected_answer: list[str]
    mastery_score: float
    due_at: datetime
    attempted_at: datetime

    @classmethod
    def from_result(cls, result: AttemptResult) -> "AttemptResultResponse":
        return cls(
            attempt_id=result.attempt.id,
            selected_options=list(result.attempt.answer),
            score=result.attempt.score,
            is_correct=result.attempt.is_correct,
            expected_answer=list(result.expected_answer),
            mastery_score=result.mastery.score,
            due_at=result.review.due_at,
            attempted_at=result.attempt.attempted_at,
        )


class SessionResponse(BaseModel):
    id: str
    goal_id: str
    plan_id: str
    plan_item_id: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    lesson_title: str
    lesson_content: str
    exercise: ExerciseResponse
    latest_result: AttemptResultResponse | None

    @classmethod
    def from_details(cls, details: SessionDetails) -> "SessionResponse":
        exercise = details.exercise
        return cls(
            id=details.session.id,
            goal_id=details.session.goal_id,
            plan_id=details.plan_id,
            plan_item_id=details.plan_item.id,
            status=details.session.status.value,
            started_at=details.session.started_at,
            completed_at=details.session.completed_at,
            lesson_title=details.knowledge_node.title,
            lesson_content=details.knowledge_node.description,
            exercise=ExerciseResponse(
                id=exercise.id,
                exercise_type=exercise.exercise_type.value,
                prompt=exercise.prompt,
                options=list(exercise.options),
                max_score=exercise.max_score,
            ),
            latest_result=(
                AttemptResultResponse.from_result(details.latest_result)
                if details.latest_result is not None
                else None
            ),
        )


class SubmitAttemptRequest(RequestModel):
    exercise_id: str = Field(min_length=1)
    selected_options: list[str] = Field(min_length=1)


class DueReviewResponse(BaseModel):
    knowledge_node_id: str
    knowledge_node_title: str
    due_at: datetime
    last_review_at: datetime | None
    exercise: ExerciseResponse | None

    @classmethod
    def from_domain(cls, due_review: DueReview) -> "DueReviewResponse":
        exercise = due_review.exercise
        return cls(
            knowledge_node_id=due_review.knowledge_node.id,
            knowledge_node_title=due_review.knowledge_node.title,
            due_at=due_review.schedule.due_at,
            last_review_at=due_review.schedule.last_review_at,
            exercise=(
                ExerciseResponse(
                    id=exercise.id,
                    exercise_type=exercise.exercise_type.value,
                    prompt=exercise.prompt,
                    options=list(exercise.options),
                    max_score=exercise.max_score,
                )
                if exercise is not None
                else None
            ),
        )
