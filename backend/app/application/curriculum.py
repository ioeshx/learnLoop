"""Application port for producing a domain-valid learning curriculum."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.exercises import Exercise
from app.domain.goals import LearningGoal
from app.domain.knowledge import KnowledgeEdge, KnowledgeNode
from app.domain.plans import StudyPlan


@dataclass(frozen=True, slots=True)
class Curriculum:
    nodes: tuple[KnowledgeNode, ...]
    edges: tuple[KnowledgeEdge, ...]
    plan: StudyPlan
    exercises: tuple[Exercise, ...]


class CurriculumGenerator(Protocol):
    async def generate(self, goal: LearningGoal, *, now: datetime) -> Curriculum: ...
