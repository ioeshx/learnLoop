"""Exercise entities."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.domain.common import new_id, require_aware_utc, require_text, utc_now


class ExerciseType(StrEnum):
    MULTIPLE_CHOICE = "multiple_choice"
    SHORT_ANSWER = "short_answer"
    CODE = "code"


@dataclass(frozen=True, slots=True)
class Exercise:
    id: str
    knowledge_node_id: str
    exercise_type: ExerciseType
    prompt: str
    options: tuple[str, ...]
    answer_key: tuple[str, ...]
    max_score: float
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "prompt", require_text(self.prompt, "prompt"))
        if self.max_score <= 0:
            raise ValueError("max_score must be positive")
        require_aware_utc(self.created_at, "created_at")
        if self.exercise_type == ExerciseType.MULTIPLE_CHOICE:
            if len(self.options) < 2:
                raise ValueError("multiple-choice exercises need at least two options")
            if not self.answer_key:
                raise ValueError("multiple-choice exercises need an answer key")
            unknown = set(self.answer_key) - set(self.options)
            if unknown:
                raise ValueError("answer key contains an unknown option")

    @classmethod
    def create_multiple_choice(
        cls,
        *,
        knowledge_node_id: str,
        prompt: str,
        options: list[str],
        answer_key: list[str],
        max_score: float = 1.0,
        now: datetime | None = None,
    ) -> "Exercise":
        return cls(
            id=new_id(),
            knowledge_node_id=require_text(knowledge_node_id, "knowledge_node_id"),
            exercise_type=ExerciseType.MULTIPLE_CHOICE,
            prompt=prompt,
            options=tuple(require_text(option, "option") for option in options),
            answer_key=tuple(require_text(answer, "answer") for answer in answer_key),
            max_score=max_score,
            created_at=require_aware_utc(now or utc_now(), "now"),
        )


@dataclass(frozen=True, slots=True)
class ExerciseAttempt:
    id: str
    exercise_id: str
    study_session_id: str
    answer: tuple[str, ...]
    score: float
    is_correct: bool
    attempted_at: datetime

    def __post_init__(self) -> None:
        if self.score < 0:
            raise ValueError("score must not be negative")
        require_aware_utc(self.attempted_at, "attempted_at")

    @classmethod
    def create(
        cls,
        *,
        exercise_id: str,
        study_session_id: str,
        answer: tuple[str, ...],
        score: float,
        is_correct: bool,
        attempted_at: datetime | None = None,
        attempt_id: str | None = None,
    ) -> "ExerciseAttempt":
        return cls(
            id=attempt_id or new_id(),
            exercise_id=exercise_id,
            study_session_id=study_session_id,
            answer=answer,
            score=score,
            is_correct=is_correct,
            attempted_at=require_aware_utc(attempted_at or utc_now(), "attempted_at"),
        )
