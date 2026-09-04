"""SQLite configuration, repository, and transaction integration tests."""

from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.domain.exceptions import KnowledgeGraphError
from app.domain.exercises import Exercise, grade_multiple_choice
from app.domain.goals import LearningGoal
from app.domain.knowledge import KnowledgeEdge, KnowledgeNode
from app.domain.mastery import (
    MasteryEvent,
    MasteryEventType,
    apply_mastery_event,
    mastery_delta,
)
from app.domain.plans import StudyPlan
from app.domain.review import ReviewSchedule
from app.domain.users import User
from app.infrastructure.database import Database, SqlAlchemyUnitOfWork, create_database
from app.infrastructure.database.base import Base
from app.infrastructure.database.models import StudySessionModel


NOW = datetime(2026, 1, 10, 8, 30, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def database(test_settings: Settings) -> AsyncIterator[Database]:
    created = create_database(test_settings)
    async with created.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield created
    finally:
        await created.dispose()


@pytest.mark.asyncio
async def test_sqlite_connection_invariants(database: Database) -> None:
    async with database.engine.connect() as connection:
        foreign_keys = await connection.scalar(text("PRAGMA foreign_keys"))
        journal_mode = await connection.scalar(text("PRAGMA journal_mode"))
        busy_timeout = await connection.scalar(text("PRAGMA busy_timeout"))

    assert foreign_keys == 1
    assert str(journal_mode).lower() == "wal"
    assert busy_timeout == 5000


@pytest.mark.asyncio
async def test_sqlite_enforces_foreign_keys(database: Database) -> None:
    orphan = LearningGoal.create(
        user_id="missing-user",
        title="Orphan goal",
        desired_outcome="This must not persist",
        weekly_minutes=60,
        now=NOW,
    )

    async with SqlAlchemyUnitOfWork(database.session_factory) as uow:
        await uow.goals.add(orphan)
        with pytest.raises(IntegrityError):
            await uow.commit()


@pytest.mark.asyncio
async def test_repositories_persist_a_complete_plan(database: Database) -> None:
    user = User.create(display_name="Ada", timezone_name="UTC", now=NOW)
    goal = LearningGoal.create(
        user_id=user.id,
        title="Learn graphs",
        desired_outcome="Implement BFS",
        weekly_minutes=180,
        now=NOW,
    )
    basics = KnowledgeNode.create(goal_id=goal.id, title="Basics", now=NOW)
    traversal = KnowledgeNode.create(goal_id=goal.id, title="Traversal", now=NOW)
    edge = KnowledgeEdge.create(
        goal_id=goal.id,
        source_node_id=basics.id,
        target_node_id=traversal.id,
        now=NOW,
    )
    plan = StudyPlan.create(
        goal_id=goal.id,
        item_specs=[
            (basics.id, "Learn terminology", 20),
            (traversal.id, "Write BFS", 35),
        ],
        now=NOW,
    )

    async with SqlAlchemyUnitOfWork(database.session_factory) as uow:
        await uow.users.add(user)
        await uow.goals.add(goal)
        await uow.knowledge.add_node(basics)
        await uow.knowledge.add_node(traversal)
        await uow.knowledge.add_edge(edge)
        await uow.plans.add(plan)
        await uow.commit()

    async with SqlAlchemyUnitOfWork(database.session_factory) as uow:
        saved_goal = await uow.goals.get(goal.id)
        saved_nodes = await uow.knowledge.list_nodes(goal.id)
        saved_plan = await uow.plans.get(plan.id)

    assert saved_goal == goal
    assert {node.id for node in saved_nodes} == {basics.id, traversal.id}
    assert saved_plan == plan


@pytest.mark.asyncio
async def test_repository_rejects_a_prerequisite_cycle(database: Database) -> None:
    user = User.create(display_name="Ada", timezone_name="UTC", now=NOW)
    goal = LearningGoal.create(
        user_id=user.id,
        title="Learn graphs",
        desired_outcome="Implement BFS",
        weekly_minutes=180,
        now=NOW,
    )
    first = KnowledgeNode.create(goal_id=goal.id, title="First", now=NOW)
    second = KnowledgeNode.create(goal_id=goal.id, title="Second", now=NOW)
    forward = KnowledgeEdge.create(
        goal_id=goal.id,
        source_node_id=first.id,
        target_node_id=second.id,
        now=NOW,
    )
    backward = KnowledgeEdge.create(
        goal_id=goal.id,
        source_node_id=second.id,
        target_node_id=first.id,
        now=NOW,
    )

    async with SqlAlchemyUnitOfWork(database.session_factory) as uow:
        await uow.users.add(user)
        await uow.goals.add(goal)
        await uow.knowledge.add_node(first)
        await uow.knowledge.add_node(second)
        await uow.knowledge.add_edge(forward)
        with pytest.raises(KnowledgeGraphError, match="cycle"):
            await uow.knowledge.add_edge(backward)


@pytest.mark.asyncio
async def test_uncommitted_unit_of_work_is_rolled_back(database: Database) -> None:
    user = User.create(display_name="Ada", timezone_name="UTC", now=NOW)

    async with SqlAlchemyUnitOfWork(database.session_factory) as uow:
        await uow.users.add(user)

    async with SqlAlchemyUnitOfWork(database.session_factory) as uow:
        assert await uow.users.get(user.id) is None


@pytest.mark.asyncio
async def test_attempt_mastery_and_review_round_trip(database: Database) -> None:
    user = User.create(display_name="Ada", timezone_name="UTC", now=NOW)
    goal = LearningGoal.create(
        user_id=user.id,
        title="Learn graphs",
        desired_outcome="Implement BFS",
        weekly_minutes=180,
        now=NOW,
    )
    node = KnowledgeNode.create(goal_id=goal.id, title="Traversal", now=NOW)
    exercise = Exercise.create_multiple_choice(
        knowledge_node_id=node.id,
        prompt="Which structure does BFS use?",
        options=["Queue", "Stack"],
        answer_key=["Queue"],
        now=NOW,
    )
    grade = grade_multiple_choice(
        exercise, ["Queue"], study_session_id="session-1", attempted_at=NOW
    )
    event_type = MasteryEventType.CORRECT_FIRST_TRY
    event = MasteryEvent.create(
        user_id=user.id,
        knowledge_node_id=node.id,
        event_type=event_type,
        delta=mastery_delta(event_type),
        attempt_id=grade.attempt.id,
        occurred_at=NOW,
    )
    snapshot = apply_mastery_event(None, event)
    schedule = ReviewSchedule(
        user_id=user.id,
        knowledge_node_id=node.id,
        card_json='{"card_id": 1}',
        due_at=NOW,
        last_review_at=None,
    )

    async with SqlAlchemyUnitOfWork(database.session_factory) as uow:
        await uow.users.add(user)
        await uow.goals.add(goal)
        await uow.knowledge.add_node(node)
        await uow.exercises.add(exercise)
        assert uow.session is not None
        uow.session.add(
            StudySessionModel(
                id="session-1",
                goal_id=goal.id,
                plan_item_id=None,
                status="completed",
                started_at=NOW,
                completed_at=NOW,
            )
        )
        await uow.exercises.add_attempt(grade.attempt)
        await uow.mastery.add_event(event)
        await uow.mastery.save_snapshot(snapshot)
        await uow.reviews.save(schedule)
        await uow.commit()

    async with SqlAlchemyUnitOfWork(database.session_factory) as uow:
        saved_exercise = await uow.exercises.get(exercise.id)
        saved_attempts = await uow.exercises.list_attempts(exercise.id)
        saved_snapshot = await uow.mastery.get_snapshot(user.id, node.id)
        due_reviews = await uow.reviews.list_due(user.id, NOW)

    assert saved_exercise == exercise
    assert saved_attempts == [grade.attempt]
    assert saved_snapshot == snapshot
    assert due_reviews == [schedule]
