"""Framework-independent inputs and results for learning use cases."""

from dataclasses import dataclass
from datetime import date

from app.domain.exercises.models import Exercise, ExerciseAttempt
from app.domain.knowledge.models import KnowledgeNode
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
class SubmitAttemptCommand:
    session_id: str
    exercise_id: str
    selected_options: tuple[str, ...]
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class GradeAnswerCommand:
    session_id: str
    exercise_id: str
    selected_options: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResourceSnippet:
    resource_id: str
    title: str
    excerpt: str


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
    session: StudySession
    plan_id: str
    plan_item: PlanItem
    knowledge_node: KnowledgeNode
    exercise: Exercise
    latest_result: AttemptResult | None


@dataclass(frozen=True, slots=True)
class DueReview:
    schedule: ReviewSchedule
    knowledge_node: KnowledgeNode
    exercise: Exercise | None
