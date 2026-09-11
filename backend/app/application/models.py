"""Framework-independent inputs and results for learning use cases."""

from dataclasses import dataclass
from datetime import date, datetime

from app.domain.exercises.models import Exercise, ExerciseAttempt
from app.domain.knowledge.models import KnowledgeNode
from app.domain.mastery import AdaptiveRecommendation
from app.domain.mastery.models import MasterySnapshot
from app.domain.plans.models import PlanItem, StudyPlan
from app.domain.review.models import ReviewSchedule
from app.domain.sessions.models import StudySession


@dataclass(frozen=True, slots=True)
class CreateGoalCommand:
    title: str
    desired_outcome: str
    weekly_minutes: int
    idempotency_key: str
    description: str = ""
    target_date: date | None = None


@dataclass(frozen=True, slots=True)
class StartSessionCommand:
    goal_id: str
    plan_item_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class StartReviewSessionCommand:
    """启动复习会话的应用命令，携带知识点和防止重复创建的幂等键。"""

    knowledge_node_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class SubmitAttemptCommand:
    session_id: str
    exercise_id: str
    selected_options: tuple[str, ...]
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class CorrectAttemptCommand:
    """纠正已持久化作答的命令，用新选项触发评分和掌握度重放。"""

    session_id: str
    attempt_id: str
    selected_options: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GradeAnswerCommand:
    session_id: str
    exercise_id: str
    selected_options: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResourceSnippet:
    resource_id: str
    chunk_id: str
    title: str
    excerpt: str
    score: float
    page_number: int | None = None
    section: str | None = None
    source_uri: str | None = None


@dataclass(frozen=True, slots=True)
class ProposedKnowledgeNode:
    key: str
    title: str
    description: str
    difficulty: float


@dataclass(frozen=True, slots=True)
class ProposedKnowledgeEdge:
    source_key: str
    target_key: str
    relation: str


@dataclass(frozen=True, slots=True)
class ProposedPlanItem:
    knowledge_node_key: str
    title: str
    estimated_minutes: int


@dataclass(frozen=True, slots=True)
class PersistPlanProposalCommand:
    goal_id: str
    nodes: tuple[ProposedKnowledgeNode, ...]
    edges: tuple[ProposedKnowledgeEdge, ...]
    items: tuple[ProposedPlanItem, ...]


@dataclass(frozen=True, slots=True)
class PlanDetails:
    plan: StudyPlan
    nodes: tuple[KnowledgeNode, ...]


@dataclass(frozen=True, slots=True)
class AttemptResult:
    attempt: ExerciseAttempt
    expected_answer: tuple[str, ...]
    mastery: MasterySnapshot
    review: ReviewSchedule


@dataclass(frozen=True, slots=True)
class SessionDetails:
    """学习或复习会话的完整应用层视图。

    该数据类聚合会话、计划项、知识点、当前练习、最近作答和自适应建议，避免 API 与
    Agent 分别拼装领域对象；阶段九加入的 `adaptation` 用于解释当前练习难度。
    """

    session: StudySession
    plan_id: str
    plan_item: PlanItem
    knowledge_node: KnowledgeNode
    exercise: Exercise
    latest_result: AttemptResult | None
    adaptation: AdaptiveRecommendation


@dataclass(frozen=True, slots=True)
class DueReview:
    """到期复习队列中的展示项。

    该数据类将复习日程与知识点、可用练习、逾期天数、优先级分数和解释文本组合起来，
    使 API 和后台任务共享同一排序结果。
    """

    schedule: ReviewSchedule
    knowledge_node: KnowledgeNode
    exercise: Exercise | None
    priority_score: float
    overdue_days: int
    reason: str


@dataclass(frozen=True, slots=True)
class KnowledgeNodeInsight:
    id: str
    title: str
    difficulty: float
    mastery_score: float
    status: str


@dataclass(frozen=True, slots=True)
class KnowledgeEdgeInsight:
    source_node_id: str
    target_node_id: str
    relation: str


@dataclass(frozen=True, slots=True)
class MasteryTrendPoint:
    knowledge_node_id: str
    knowledge_node_title: str
    score: float
    event_type: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class WeeklyLearningSummary:
    attempts: int
    correct_attempts: int
    completed_plan_items: int
    average_mastery: float


@dataclass(frozen=True, slots=True)
class LearningInsights:
    goal_id: str
    nodes: tuple[KnowledgeNodeInsight, ...]
    edges: tuple[KnowledgeEdgeInsight, ...]
    mastery_trend: tuple[MasteryTrendPoint, ...]
    weekly: WeeklyLearningSummary
