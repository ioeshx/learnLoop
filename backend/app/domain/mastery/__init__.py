"""Mastery event and projection domain."""

from app.domain.mastery.adaptive import (
    AdaptiveRecommendation,
    PrerequisiteMastery,
    recommend_difficulty,
)
from app.domain.mastery.models import MasteryEvent, MasteryEventType, MasterySnapshot
from app.domain.mastery.repository import MasteryRepository
from app.domain.mastery.service import (
    apply_mastery_event,
    mastery_delta,
    project_mastery,
)

__all__ = [
    "MasteryEvent",
    "MasteryEventType",
    "MasteryRepository",
    "MasterySnapshot",
    "apply_mastery_event",
    "mastery_delta",
    "AdaptiveRecommendation",
    "PrerequisiteMastery",
    "project_mastery",
    "recommend_difficulty",
]
