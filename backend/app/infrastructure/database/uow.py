"""Explicit async unit-of-work transaction boundary."""

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.database.repositories import (
    SqlAlchemyExerciseRepository,
    SqlAlchemyKnowledgeRepository,
    SqlAlchemyLearningGoalRepository,
    SqlAlchemyMasteryRepository,
    SqlAlchemyReviewRepository,
    SqlAlchemyStudyPlanRepository,
    SqlAlchemyUserRepository,
)


class SqlAlchemyUnitOfWork:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self._session_factory = session_factory
        self.session: AsyncSession | None = None
        self.users: SqlAlchemyUserRepository
        self.goals: SqlAlchemyLearningGoalRepository
        self.knowledge: SqlAlchemyKnowledgeRepository
        self.plans: SqlAlchemyStudyPlanRepository
        self.exercises: SqlAlchemyExerciseRepository
        self.mastery: SqlAlchemyMasteryRepository
        self.reviews: SqlAlchemyReviewRepository

    async def __aenter__(self) -> "SqlAlchemyUnitOfWork":
        self.session = self._session_factory()
        self.users = SqlAlchemyUserRepository(self.session)
        self.goals = SqlAlchemyLearningGoalRepository(self.session)
        self.knowledge = SqlAlchemyKnowledgeRepository(self.session)
        self.plans = SqlAlchemyStudyPlanRepository(self.session)
        self.exercises = SqlAlchemyExerciseRepository(self.session)
        self.mastery = SqlAlchemyMasteryRepository(self.session)
        self.reviews = SqlAlchemyReviewRepository(self.session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        if self.session is None:
            return
        # Closing rolls an open transaction back too, but doing it explicitly
        # makes the transaction boundary predictable and easy to test.
        await self.session.rollback()
        await self.session.close()
        self.session = None

    async def commit(self) -> None:
        if self.session is None:
            raise RuntimeError("unit of work has not been entered")
        await self.session.commit()

    async def rollback(self) -> None:
        if self.session is None:
            raise RuntimeError("unit of work has not been entered")
        await self.session.rollback()
