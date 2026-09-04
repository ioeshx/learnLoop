"""Exercise persistence contract."""

from typing import Protocol

from app.domain.exercises.models import Exercise, ExerciseAttempt


class ExerciseRepository(Protocol):
    async def add(self, exercise: Exercise) -> None: ...

    async def get(self, exercise_id: str) -> Exercise | None: ...

    async def add_attempt(self, attempt: ExerciseAttempt) -> None: ...

    async def list_attempts(self, exercise_id: str) -> list[ExerciseAttempt]: ...
