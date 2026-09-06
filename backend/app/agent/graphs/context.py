"""Runtime-only dependencies injected into compiled LearnLoop graphs."""

from dataclasses import dataclass

from app.agent.tools import LearningTools
from app.infrastructure.llm import StructuredModel


@dataclass(frozen=True, slots=True)
class DailyLearningContext:
    """Dependencies excluded from serializable daily-learning state."""

    tools: LearningTools
    model: StructuredModel | None = None
    max_remediations: int = 2

    def __post_init__(self) -> None:
        if self.max_remediations < 0:
            raise ValueError("max_remediations cannot be negative")


@dataclass(frozen=True, slots=True)
class GoalPlanningContext:
    """Dependencies excluded from serializable goal-planning state."""

    tools: LearningTools
    model: StructuredModel
