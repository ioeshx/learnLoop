"""JSON-serializable state contracts for LearnLoop graphs."""

from operator import add
from typing import Annotated, Literal, TypedDict


class StudySessionState(TypedDict, total=False):
    run_id: str
    session_id: str
    goal_id: str
    plan_id: str
    plan_item_id: str
    knowledge_node_id: str
    knowledge_node_title: str
    knowledge_node_description: str
    knowledge_node_difficulty: float
    target_difficulty: float
    adaptation_reasons: list[str]
    prerequisite_gaps: list[dict[str, object]]
    exercise_id: str
    exercise_prompt: str
    exercise_options: list[str]
    selected_options: list[str]
    learner_answer: str
    evaluation_rubric: str
    reference_answer: str
    source_refs: list[dict[str, object]]
    lesson_title: str
    lesson_content: str
    evaluation: dict[str, object]
    diagnosis: dict[str, object]
    mastery_score: float
    review_due_at: str
    learning_outcome: Literal[
        "mastered", "partially_mastered", "not_mastered", "remediation_exhausted"
    ]
    attempt_number: int
    remediation_count: int
    max_remediations: int
    summary: str
    grade_review: dict[str, object]
    status: Literal[
        "running",
        "awaiting_answer",
        "awaiting_grade_review",
        "remediation",
        "completed",
        "failed",
    ]
    events: Annotated[list[str], add]


class GoalPlanningState(TypedDict, total=False):
    run_id: str
    goal_id: str
    goal: dict[str, object]
    clarification: dict[str, object]
    clarification_answers: dict[str, str]
    knowledge_graph: dict[str, object]
    study_plan: dict[str, object]
    missing_information: list[str]
    diagnostic: dict[str, object]
    approved: bool
    plan_id: str
    status: Literal[
        "running",
        "needs_clarification",
        "awaiting_approval",
        "rejected",
        "completed",
        "failed",
    ]
    events: Annotated[list[str], add]


class ResourceIngestionState(TypedDict, total=False):
    run_id: str
    job_id: str
    document_id: str
    chunk_ids: list[str]
    ambiguities: list[dict[str, object]]
    ambiguity_resolutions: list[dict[str, object]]
    status: Literal[
        "pending", "processing", "awaiting_confirmation", "completed", "failed"
    ]
    error: str
    events: Annotated[list[str], add]


class ReviewSessionState(TypedDict, total=False):
    run_id: str
    session_id: str
    review_ids: list[str]
    current_review_id: str
    selected_options: list[str]
    mastery_score: float
    status: Literal["running", "awaiting_answer", "completed", "failed"]
    events: Annotated[list[str], add]


__all__ = [
    "GoalPlanningState",
    "ResourceIngestionState",
    "ReviewSessionState",
    "StudySessionState",
]
