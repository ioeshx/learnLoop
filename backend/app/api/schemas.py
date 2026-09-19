"""HTTP request and response contracts for the deterministic learning loop."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field
from pydantic.types import JsonValue

from app.agent.delegation import DelegationRecord
from app.agent.execution import AgentEvent, AgentRun
from app.agent.execution.models import ModelCallTrace, ToolCallTrace
from app.agent.experience import RunReflection, SkillUsage
from app.agent.optimization import BanditDecision, RewardRecord
from app.agent.policy import PolicyDecision
from app.agent.team import TeamArtifact, TeamTask
from app.application.models import (
    AttemptResult,
    DueReview,
    LearningInsights,
    PlanDetails,
    SessionDetails,
)
from app.domain.goals import LearningGoal
from app.domain.mastery import AdaptiveRecommendation
from app.domain.resources import LearningResource, ResourceCitation
from app.domain.review import ReviewSchedule
from app.infrastructure.llm.gateway import ModelRouteRecord
from app.workers import BackgroundJob


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


class KnowledgeNodeInsightResponse(BaseModel):
    id: str
    title: str
    difficulty: float
    mastery_score: float
    status: str


class KnowledgeEdgeInsightResponse(BaseModel):
    source_node_id: str
    target_node_id: str
    relation: str


class MasteryTrendPointResponse(BaseModel):
    knowledge_node_id: str
    knowledge_node_title: str
    score: float
    event_type: str
    occurred_at: datetime


class WeeklyLearningSummaryResponse(BaseModel):
    attempts: int
    correct_attempts: int
    completed_plan_items: int
    average_mastery: float


class LearningInsightsResponse(BaseModel):
    goal_id: str
    nodes: list[KnowledgeNodeInsightResponse]
    edges: list[KnowledgeEdgeInsightResponse]
    mastery_trend: list[MasteryTrendPointResponse]
    weekly: WeeklyLearningSummaryResponse

    @classmethod
    def from_application(cls, insights: LearningInsights) -> "LearningInsightsResponse":
        return cls(
            goal_id=insights.goal_id,
            nodes=[
                KnowledgeNodeInsightResponse(
                    id=node.id,
                    title=node.title,
                    difficulty=node.difficulty,
                    mastery_score=node.mastery_score,
                    status=node.status,
                )
                for node in insights.nodes
            ],
            edges=[
                KnowledgeEdgeInsightResponse(
                    source_node_id=edge.source_node_id,
                    target_node_id=edge.target_node_id,
                    relation=edge.relation,
                )
                for edge in insights.edges
            ],
            mastery_trend=[
                MasteryTrendPointResponse(
                    knowledge_node_id=point.knowledge_node_id,
                    knowledge_node_title=point.knowledge_node_title,
                    score=point.score,
                    event_type=point.event_type,
                    occurred_at=point.occurred_at,
                )
                for point in insights.mastery_trend
            ],
            weekly=WeeklyLearningSummaryResponse(
                attempts=insights.weekly.attempts,
                correct_attempts=insights.weekly.correct_attempts,
                completed_plan_items=insights.weekly.completed_plan_items,
                average_mastery=insights.weekly.average_mastery,
            ),
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
    kind: str
    started_at: datetime
    completed_at: datetime | None
    lesson_title: str
    lesson_content: str
    exercise: ExerciseResponse
    latest_result: AttemptResultResponse | None
    adaptation: "AdaptiveRecommendationResponse"

    @classmethod
    def from_details(cls, details: SessionDetails) -> "SessionResponse":
        exercise = details.exercise
        return cls(
            id=details.session.id,
            goal_id=details.session.goal_id,
            plan_id=details.plan_id,
            plan_item_id=details.plan_item.id,
            status=details.session.status.value,
            kind=details.session.kind.value,
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
            adaptation=AdaptiveRecommendationResponse.from_domain(details.adaptation),
        )


class SubmitAttemptRequest(RequestModel):
    exercise_id: str = Field(min_length=1)
    selected_options: list[str] = Field(min_length=1)


class CorrectAttemptRequest(RequestModel):
    """作答纠正请求，只接受用于重新评分的新选项集合。"""

    selected_options: list[str] = Field(min_length=1)


class StartReviewSessionRequest(RequestModel):
    """复习会话创建请求，标识需要复习的知识点。"""

    knowledge_node_id: str = Field(min_length=1)


class DeferReviewRequest(RequestModel):
    """复习延期请求，将延期范围约束为 1 至 7 天。"""

    days: int = Field(default=1, ge=1, le=7)


class PrerequisiteGapResponse(BaseModel):
    """前置知识缺口响应，包含知识点身份、标题和当前掌握度。"""

    knowledge_node_id: str
    title: str
    score: float


class AdaptiveRecommendationResponse(BaseModel):
    """可解释自适应决策响应，供前端展示目标难度及其计算依据。"""

    mastery_score: float
    base_difficulty: float
    target_difficulty: float
    prerequisite_gaps: list[PrerequisiteGapResponse]
    reasons: list[str]

    @classmethod
    def from_domain(
        cls, recommendation: AdaptiveRecommendation
    ) -> "AdaptiveRecommendationResponse":
        """把不可变领域建议及嵌套前置缺口转换为 HTTP 响应模型。"""

        return cls(
            mastery_score=recommendation.mastery_score,
            base_difficulty=recommendation.base_difficulty,
            target_difficulty=recommendation.target_difficulty,
            prerequisite_gaps=[
                PrerequisiteGapResponse(
                    knowledge_node_id=gap.knowledge_node_id,
                    title=gap.title,
                    score=gap.score,
                )
                for gap in recommendation.prerequisite_gaps
            ],
            reasons=list(recommendation.reasons),
        )


class ReviewScheduleResponse(BaseModel):
    """复习日程响应，用于延期等只更新计划时间的操作。"""

    knowledge_node_id: str
    due_at: datetime
    last_review_at: datetime | None

    @classmethod
    def from_domain(cls, schedule: ReviewSchedule) -> "ReviewScheduleResponse":
        """从领域日程提取知识点、下次到期和上次复习时间。"""

        return cls(
            knowledge_node_id=schedule.knowledge_node_id,
            due_at=schedule.due_at,
            last_review_at=schedule.last_review_at,
        )


class DueReviewResponse(BaseModel):
    knowledge_node_id: str
    knowledge_node_title: str
    due_at: datetime
    last_review_at: datetime | None
    exercise: ExerciseResponse | None
    priority_score: float
    overdue_days: int
    reason: str

    @classmethod
    def from_domain(cls, due_review: DueReview) -> "DueReviewResponse":
        exercise = due_review.exercise
        return cls(
            knowledge_node_id=due_review.knowledge_node.id,
            knowledge_node_title=due_review.knowledge_node.title,
            due_at=due_review.schedule.due_at,
            last_review_at=due_review.schedule.last_review_at,
            priority_score=due_review.priority_score,
            overdue_days=due_review.overdue_days,
            reason=due_review.reason,
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
    engine_version: str
    parent_run_id: str | None
    attempt_no: int
    status: str
    terminal_reason: str | None
    cancel_requested: bool
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_execution(cls, run: AgentRun) -> "AgentRunResponse":
        return cls(
            run_id=run.run_id,
            thread_id=run.thread_id,
            graph=run.graph_kind,
            resource_id=run.resource_id,
            engine_version=run.engine_version,
            parent_run_id=run.parent_run_id,
            attempt_no=run.attempt_no,
            status=run.status,
            terminal_reason=run.terminal_reason,
            cancel_requested=run.cancel_requested,
            version=run.version,
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


class ToolCallTraceResponse(BaseModel):
    call_id: str
    tool_name: str
    arguments: dict[str, object]
    result_summary: dict[str, object] | None
    status: str
    duration_ms: float | None
    error: str | None
    started_at: datetime
    completed_at: datetime | None

    @classmethod
    def from_execution(cls, call: ToolCallTrace) -> "ToolCallTraceResponse":
        return cls(
            call_id=call.call_id,
            tool_name=call.tool_name,
            arguments=call.arguments,
            result_summary=call.result_summary,
            status=call.status,
            duration_ms=call.duration_ms,
            error=call.error,
            started_at=call.started_at,
            completed_at=call.completed_at,
        )


class ModelCallTraceResponse(BaseModel):
    call_id: str
    prompt_name: str
    prompt_version: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    duration_ms: float
    attempts: int
    repaired: bool
    error: str | None
    created_at: datetime

    @classmethod
    def from_execution(cls, call: ModelCallTrace) -> "ModelCallTraceResponse":
        return cls(
            call_id=call.call_id,
            prompt_name=call.prompt_name,
            prompt_version=call.prompt_version,
            model=call.model,
            input_tokens=call.input_tokens,
            output_tokens=call.output_tokens,
            total_tokens=call.total_tokens,
            duration_ms=call.duration_ms,
            attempts=call.attempts,
            repaired=call.repaired,
            error=call.error,
            created_at=call.created_at,
        )


class AgentTraceResponse(BaseModel):
    run: AgentRunResponse
    events: list[AgentEventResponse]
    tool_calls: list[ToolCallTraceResponse]
    model_calls: list[ModelCallTraceResponse]
    total_tokens: int
    total_model_duration_ms: float
    total_tool_duration_ms: float
    dynamic_state: dict[str, object] | None = None
    plan_versions: list[dict[str, object]] = Field(default_factory=list)
    context_snapshots: list[dict[str, object]] = Field(default_factory=list)
    delegations: list[DelegationRecord] = Field(default_factory=list)
    child_runs: list[AgentRunResponse] = Field(default_factory=list)
    reflections: list[RunReflection] = Field(default_factory=list)
    skill_usage: SkillUsage | None = None
    reward: RewardRecord | None = None
    policy_decisions: list[BanditDecision] = Field(default_factory=list)
    authorization_decisions: list[PolicyDecision] = Field(default_factory=list)
    model_routes: list[ModelRouteRecord] = Field(default_factory=list)
    team_tasks: list[TeamTask] = Field(default_factory=list)
    team_artifacts: list[TeamArtifact] = Field(default_factory=list)


class ResumeAgentRunRequest(RequestModel):
    value: JsonValue
    interrupt_id: str | None = None


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


class BackgroundJobResponse(BaseModel):
    """后台任务对外响应模型，隐藏内部租约所有者并暴露可观察的执行状态。"""

    id: str
    job_type: str
    payload: dict[str, JsonValue]
    result: dict[str, JsonValue] | None
    status: str
    progress: int
    progress_message: str | None
    attempts: int
    max_attempts: int
    available_at: datetime
    cancel_requested: bool
    last_error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime

    @classmethod
    def from_domain(cls, job: BackgroundJob) -> "BackgroundJobResponse":
        """把内部任务快照映射为可 JSON 序列化且不泄露租约细节的响应。"""

        return cls(
            id=job.id,
            job_type=job.job_type.value,
            payload=job.payload,
            result=job.result,
            status=job.status.value,
            progress=job.progress,
            progress_message=job.progress_message,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            available_at=job.available_at,
            cancel_requested=job.cancel_requested,
            last_error=job.last_error,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            updated_at=job.updated_at,
        )


class ResourceImportResponse(BaseModel):
    """资料导入受理结果，同时返回资料元数据和负责处理它的后台任务。"""

    resource: ResourceResponse
    job: BackgroundJobResponse


class WeeklyReportJobRequest(RequestModel):
    """周报任务输入，可限定目标并约束统计窗口为 1 至 90 天。"""

    goal_id: str | None = Field(default=None, min_length=1)
    days: int = Field(default=7, ge=1, le=90)


class DueReviewJobRequest(RequestModel):
    """到期复习生成任务输入；空截止时间表示由服务使用当前时间。"""

    due_before: datetime | None = None


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
