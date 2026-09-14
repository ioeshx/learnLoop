"""Application-level ports used by learning use cases."""

from collections.abc import Callable
from types import TracebackType
from typing import Protocol, Self

from app.domain.exercises.repository import ExerciseRepository
from app.domain.goals.repository import LearningGoalRepository
from app.domain.knowledge.repository import KnowledgeRepository
from app.domain.mastery.repository import MasteryRepository
from app.domain.memory.repository import MemoryRepository
from app.domain.plans.repository import StudyPlanRepository
from app.domain.resources import ResourceCitation
from app.domain.review.repository import ReviewRepository
from app.domain.sessions.repository import StudySessionRepository
from app.domain.users.repository import UserRepository


class ResourceSearch(Protocol):
    async def search_for_knowledge_node(
        self, knowledge_node_id: str, *, limit: int = 5
    ) -> list[ResourceCitation]: ...


class UnitOfWork(Protocol):
    @property
    def users(self) -> UserRepository: ...

    @property
    def goals(self) -> LearningGoalRepository: ...

    @property
    def knowledge(self) -> KnowledgeRepository: ...

    @property
    def plans(self) -> StudyPlanRepository: ...

    @property
    def exercises(self) -> ExerciseRepository: ...

    @property
    def sessions(self) -> StudySessionRepository: ...

    @property
    def mastery(self) -> MasteryRepository: ...

    @property
    def reviews(self) -> ReviewRepository: ...

    @property
    def memories(self) -> MemoryRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


UnitOfWorkFactory = Callable[[], UnitOfWork]
