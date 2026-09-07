"""HTTP request and response contracts for the deterministic learning loop."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field
from pydantic.types import JsonValue

from app.agent.execution import AgentEvent, AgentRun
from app.application.models import AttemptResult, DueReview, PlanDetails, SessionDetails
from app.domain.goals import LearningGoal
from app.domain.resources import LearningResource, ResourceCitation


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
            lesson_content=(
                details.knowledge_node.lesson_content
                or details.knowledge_node.description
            ),
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


class AgentRunResponse(BaseModel):
    run_id: str
    thread_id: str
    graph: str
    resource_id: str
    status: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_execution(cls, run: AgentRun) -> "AgentRunResponse":
        return cls(
            run_id=run.run_id,
            thread_id=run.thread_id,
            graph=run.graph_kind,
            resource_id=run.resource_id,
            status=run.status,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )


class AgentEventResponse(BaseModel):
    run_id: str
    sequence: int
    event: str
    node: str | None
    timestamp: datetime
    data: dict[str, object]

    @classmethod
    def from_execution(cls, event: AgentEvent) -> "AgentEventResponse":
        return cls.model_validate(event.as_dict())


class ResumeAgentRunRequest(RequestModel):
    value: JsonValue


class IngestUrlRequest(RequestModel):
    url: str = Field(min_length=1, max_length=2_000)
    goal_id: str = Field(min_length=1)
    knowledge_node_id: str | None = None
    title: str | None = Field(default=None, max_length=500)


class ResourceResponse(BaseModel):
    id: str
    goal_id: str
    knowledge_node_id: str | None
    title: str
    source_type: str
    source_uri: str | None
    original_filename: str | None
    media_type: str
    sha256: str
    size_bytes: int
    status: str
    error: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, resource: LearningResource) -> "ResourceResponse":
        return cls(
            id=resource.id,
            goal_id=resource.goal_id,
            knowledge_node_id=resource.knowledge_node_id,
            title=resource.title,
            source_type=resource.source_type.value,
            source_uri=resource.source_uri,
            original_filename=resource.original_filename,
            media_type=resource.media_type,
            sha256=resource.sha256,
            size_bytes=resource.size_bytes,
            status=resource.status.value,
            error=resource.error,
            created_at=resource.created_at,
            updated_at=resource.updated_at,
        )


class CitationResponse(BaseModel):
    resource_id: str
    chunk_id: str
    title: str
    excerpt: str
    score: float
    page_number: int | None
    section: str | None
    locator: str | None
    source_uri: str | None

    @classmethod
    def from_domain(cls, citation: ResourceCitation) -> "CitationResponse":
        return cls(
            resource_id=citation.resource_id,
            chunk_id=citation.chunk_id,
            title=citation.title,
            excerpt=citation.excerpt,
            score=citation.score,
            page_number=citation.page_number,
            section=citation.section,
            locator=citation.locator,
            source_uri=citation.source_uri,
        )
