"""Application use cases and orchestration."""

from app.application.curriculum import Curriculum, CurriculumGenerator
from app.application.errors import CurriculumGenerationError
from app.application.models import (
    AttemptResult,
    CreateGoalCommand,
    DueReview,
    GradeAnswerCommand,
    PlanDetails,
    ResourceSnippet,
    SessionDetails,
    StartSessionCommand,
    SubmitAttemptCommand,
)
from app.application.services import (
    ApplicationDependencies,
    CompleteStudySession,
    CreateLearningGoal,
    CreateStudyPlan,
    GetDueReviews,
    GetLearningGoal,
    GetMasteryState,
    GetStudyPlan,
    GetStudySession,
    GradeObjectiveAnswer,
    SearchLearningResources,
    StartStudySession,
    SubmitExerciseAttempt,
)
from app.application.templates import FixedCurriculumGenerator

__all__ = [
    "ApplicationDependencies",
    "AttemptResult",
    "CompleteStudySession",
    "CreateGoalCommand",
    "CreateLearningGoal",
    "CreateStudyPlan",
    "Curriculum",
    "CurriculumGenerationError",
    "CurriculumGenerator",
    "DueReview",
    "FixedCurriculumGenerator",
    "GetDueReviews",
    "GetLearningGoal",
    "GetMasteryState",
    "GetStudyPlan",
    "GetStudySession",
    "GradeAnswerCommand",
    "GradeObjectiveAnswer",
    "PlanDetails",
    "ResourceSnippet",
    "SearchLearningResources",
    "SessionDetails",
    "StartSessionCommand",
    "StartStudySession",
    "SubmitAttemptCommand",
    "SubmitExerciseAttempt",
]
