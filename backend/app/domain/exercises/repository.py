"""Exercise persistence contract."""

from typing import Protocol

from app.domain.exercises.models import Exercise, ExerciseAttempt


class ExerciseRepository(Protocol):
    async def add(self, exercise: Exercise) -> None: ...

    async def get(self, exercise_id: str) -> Exercise | None: ...

    async def list_for_node(self, knowledge_node_id: str) -> list[Exercise]: ...

    async def add_attempt(self, attempt: ExerciseAttempt) -> None: ...

    async def update_attempt(self, attempt: ExerciseAttempt) -> None:
        """用相同 ID 的新评分结果替换已存在作答，供用户纠正流程使用。"""
        ...

    async def get_attempt(self, attempt_id: str) -> ExerciseAttempt | None: ...

    async def list_attempts(self, exercise_id: str) -> list[ExerciseAttempt]: ...

    async def list_attempts_for_session(
        self, study_session_id: str
    ) -> list[ExerciseAttempt]: ...
