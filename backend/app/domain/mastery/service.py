"""Deterministic mastery projection rules."""

from app.domain.mastery.models import MasteryEvent, MasteryEventType, MasterySnapshot


_DEFAULT_DELTAS: dict[MasteryEventType, float] = {
    MasteryEventType.CORRECT_FIRST_TRY: 0.15,
    MasteryEventType.CORRECT_AFTER_REMEDIATION: 0.08,
    MasteryEventType.INCORRECT: -0.10,
    MasteryEventType.REVIEW_CORRECT: 0.12,
    MasteryEventType.USER_CORRECTION: 0.0,
}

_CORRECT_EVENTS = {
    MasteryEventType.CORRECT_FIRST_TRY,
    MasteryEventType.CORRECT_AFTER_REMEDIATION,
    MasteryEventType.REVIEW_CORRECT,
}


def mastery_delta(event_type: MasteryEventType) -> float:
    return _DEFAULT_DELTAS[event_type]


def apply_mastery_event(
    current: MasterySnapshot | None, event: MasteryEvent
) -> MasterySnapshot:
    if current is not None and (
        current.user_id != event.user_id
        or current.knowledge_node_id != event.knowledge_node_id
    ):
        raise ValueError("mastery event does not belong to the snapshot")

    score = current.score if current is not None else 0.0
    attempt_count = current.attempt_count if current is not None else 0
    correct_count = current.correct_count if current is not None else 0

    if event.event_type != MasteryEventType.USER_CORRECTION:
        attempt_count += 1
        if event.event_type in _CORRECT_EVENTS:
            correct_count += 1

    return MasterySnapshot(
        user_id=event.user_id,
        knowledge_node_id=event.knowledge_node_id,
        score=min(1.0, max(0.0, score + event.delta)),
        attempt_count=attempt_count,
        correct_count=correct_count,
        updated_at=event.occurred_at,
    )
