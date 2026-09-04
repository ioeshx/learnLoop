"""Learning goal domain."""

from app.domain.goals.models import GoalStatus, LearningGoal
from app.domain.goals.repository import LearningGoalRepository

__all__ = ["GoalStatus", "LearningGoal", "LearningGoalRepository"]
