"""Pure-domain tests for the first deterministic learning workflow."""

from datetime import datetime, timezone

import pytest

from app.domain.exceptions import KnowledgeGraphError
from app.domain.exercises import Exercise, grade_multiple_choice
from app.domain.goals import GoalStatus, LearningGoal
from app.domain.knowledge import (
    KnowledgeEdge,
    KnowledgeNode,
    validate_knowledge_graph,
)
from app.domain.mastery import (
    MasteryEvent,
    MasteryEventType,
    apply_mastery_event,
    mastery_delta,
)
from app.domain.plans import StudyPlan
from app.domain.users import User


NOW = datetime(2026, 1, 10, 8, 30, tzinfo=timezone.utc)


def test_build_plan_grade_answer_and_project_mastery() -> None:
    user = User.create(display_name="Ada", timezone_name="Asia/Shanghai", now=NOW)
    goal = LearningGoal.create(
        user_id=user.id,
        title="Learn graph algorithms",
        desired_outcome="Implement graph algorithms independently",
        weekly_minutes=240,
        now=NOW,
    ).change_status(GoalStatus.ACTIVE, now=NOW)
    basics = KnowledgeNode.create(
        goal_id=goal.id, title="Graph basics", difficulty=1.5, now=NOW
    )
    traversal = KnowledgeNode.create(
        goal_id=goal.id, title="Graph traversal", difficulty=2.5, now=NOW
    )
    prerequisite = KnowledgeEdge.create(
        goal_id=goal.id,
        source_node_id=basics.id,
        target_node_id=traversal.id,
        now=NOW,
    )
    validate_knowledge_graph([basics, traversal], [prerequisite])

    plan = StudyPlan.create(
        goal_id=goal.id,
        item_specs=[
            (basics.id, "Understand graph terminology", 25),
            (traversal.id, "Implement breadth-first search", 40),
        ],
        now=NOW,
    )
    exercise = Exercise.create_multiple_choice(
        knowledge_node_id=traversal.id,
        prompt="Which data structure does BFS normally use?",
        options=["Queue", "Stack", "Heap"],
        answer_key=["Queue"],
        now=NOW,
    )
    grade = grade_multiple_choice(
        exercise,
        [" queue "],
        study_session_id="session-1",
        attempted_at=NOW,
    )
    event_type = MasteryEventType.CORRECT_FIRST_TRY
    event = MasteryEvent.create(
        user_id=user.id,
        knowledge_node_id=traversal.id,
        event_type=event_type,
        delta=mastery_delta(event_type),
        attempt_id=grade.attempt.id,
        occurred_at=NOW,
    )
    snapshot = apply_mastery_event(None, event)

    assert len(plan.items) == 2
    assert grade.attempt.is_correct is True
    assert grade.attempt.score == exercise.max_score
    assert snapshot.score == pytest.approx(0.15)
    assert snapshot.attempt_count == 1
    assert snapshot.correct_count == 1


def test_prerequisite_cycle_is_rejected() -> None:
    first = KnowledgeNode.create(goal_id="goal-1", title="First", now=NOW)
    second = KnowledgeNode.create(goal_id="goal-1", title="Second", now=NOW)
    forward = KnowledgeEdge.create(
        goal_id="goal-1",
        source_node_id=first.id,
        target_node_id=second.id,
        now=NOW,
    )
    backward = KnowledgeEdge.create(
        goal_id="goal-1",
        source_node_id=second.id,
        target_node_id=first.id,
        now=NOW,
    )

    with pytest.raises(KnowledgeGraphError, match="cycle"):
        validate_knowledge_graph([first, second], [forward, backward])


def test_wrong_answer_lowers_but_never_makes_mastery_negative() -> None:
    event_type = MasteryEventType.INCORRECT
    event = MasteryEvent.create(
        user_id="user-1",
        knowledge_node_id="node-1",
        event_type=event_type,
        delta=mastery_delta(event_type),
        occurred_at=NOW,
    )

    snapshot = apply_mastery_event(None, event)

    assert snapshot.score == 0.0
    assert snapshot.attempt_count == 1
    assert snapshot.correct_count == 0
