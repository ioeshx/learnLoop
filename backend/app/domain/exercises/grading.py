"""Deterministic objective exercise grading."""

from dataclasses import dataclass
from datetime import datetime

from app.domain.exceptions import UnsupportedExerciseError
from app.domain.exercises.models import Exercise, ExerciseAttempt, ExerciseType


@dataclass(frozen=True, slots=True)
class ObjectiveGrade:
    attempt: ExerciseAttempt
    expected_answer: tuple[str, ...]


def _normalize(values: tuple[str, ...] | list[str]) -> frozenset[str]:
    return frozenset(value.strip().casefold() for value in values if value.strip())


def grade_multiple_choice(
    exercise: Exercise,
    selected_options: list[str],
    *,
    study_session_id: str,
    attempted_at: datetime | None = None,
) -> ObjectiveGrade:
    if exercise.exercise_type != ExerciseType.MULTIPLE_CHOICE:
        raise UnsupportedExerciseError("only multiple-choice exercises are supported")

    normalized_answer = _normalize(selected_options)
    normalized_key = _normalize(exercise.answer_key)
    is_correct = normalized_answer == normalized_key
    attempt = ExerciseAttempt.create(
        exercise_id=exercise.id,
        study_session_id=study_session_id,
        answer=tuple(selected_options),
        score=exercise.max_score if is_correct else 0.0,
        is_correct=is_correct,
        attempted_at=attempted_at,
    )
    return ObjectiveGrade(attempt=attempt, expected_answer=exercise.answer_key)
