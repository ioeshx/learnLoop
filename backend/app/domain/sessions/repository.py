"""Study session persistence contract."""

from typing import Protocol

from app.domain.sessions.models import StudySession


class StudySessionRepository(Protocol):
    async def add(self, study_session: StudySession) -> None: ...

    async def get(self, session_id: str) -> StudySession | None: ...

    async def update(self, study_session: StudySession) -> None: ...
