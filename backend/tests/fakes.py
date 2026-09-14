"""Transaction-aware in-memory adapters for application and API tests."""

from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime
from types import TracebackType

from app.domain.exercises import Exercise, ExerciseAttempt
from app.domain.goals import LearningGoal
from app.domain.knowledge import (
    KnowledgeEdge,
    KnowledgeNode,
    validate_knowledge_graph,
)
from app.domain.mastery import MasteryEvent, MasterySnapshot
from app.domain.memory import (
    MemoryAggregate,
    MemoryEvidence,
    MemoryKind,
    MemoryRecord,
    MemoryRevision,
    MemoryStatus,
)
from app.domain.plans import PlanItem, StudyPlan
from app.domain.review import ReviewSchedule
from app.domain.sessions import StudySession
from app.domain.users import User


@dataclass(slots=True)
class FakeState:
    users: dict[str, User] = field(default_factory=dict)
    goals: dict[str, LearningGoal] = field(default_factory=dict)
    nodes: dict[str, KnowledgeNode] = field(default_factory=dict)
    edges: dict[str, KnowledgeEdge] = field(default_factory=dict)
    plans: dict[str, StudyPlan] = field(default_factory=dict)
    exercises: dict[str, Exercise] = field(default_factory=dict)
    attempts: dict[str, ExerciseAttempt] = field(default_factory=dict)
    sessions: dict[str, StudySession] = field(default_factory=dict)
    mastery_events: dict[str, MasteryEvent] = field(default_factory=dict)
    mastery_snapshots: dict[tuple[str, str], MasterySnapshot] = field(
        default_factory=dict
    )
    reviews: dict[tuple[str, str], ReviewSchedule] = field(default_factory=dict)
    memories: dict[str, MemoryRecord] = field(default_factory=dict)
    memory_evidence: dict[str, MemoryEvidence] = field(default_factory=dict)
    memory_revisions: dict[str, MemoryRevision] = field(default_factory=dict)

    def replace_with(self, other: "FakeState") -> None:
        copied = deepcopy(other)
        for field_name in self.__dataclass_fields__:
            setattr(self, field_name, getattr(copied, field_name))


class FakeUserRepository:
    def __init__(self, state: FakeState) -> None:
        self._state = state

    async def add(self, user: User) -> None:
        self._state.users[user.id] = user

    async def get(self, user_id: str) -> User | None:
        return self._state.users.get(user_id)


class FakeGoalRepository:
    def __init__(self, state: FakeState) -> None:
        self._state = state

    async def add(self, goal: LearningGoal) -> None:
        self._state.goals[goal.id] = goal

    async def get(self, goal_id: str) -> LearningGoal | None:
        return self._state.goals.get(goal_id)

    async def update(self, goal: LearningGoal) -> None:
        if goal.id not in self._state.goals:
            raise LookupError(goal.id)
        self._state.goals[goal.id] = goal

    async def list_for_user(self, user_id: str) -> list[LearningGoal]:
        return [goal for goal in self._state.goals.values() if goal.user_id == user_id]


class FakeKnowledgeRepository:
    def __init__(self, state: FakeState) -> None:
        self._state = state

    async def add_node(self, node: KnowledgeNode) -> None:
        self._state.nodes[node.id] = node

    async def add_edge(self, edge: KnowledgeEdge) -> None:
        nodes = await self.list_nodes(edge.goal_id)
        edges = await self.list_edges(edge.goal_id)
        validate_knowledge_graph(nodes, [*edges, edge])
        self._state.edges[edge.id] = edge

    async def get_node(self, node_id: str) -> KnowledgeNode | None:
        return self._state.nodes.get(node_id)

    async def list_nodes(self, goal_id: str) -> list[KnowledgeNode]:
        return [node for node in self._state.nodes.values() if node.goal_id == goal_id]

    async def list_edges(self, goal_id: str) -> list[KnowledgeEdge]:
        return [edge for edge in self._state.edges.values() if edge.goal_id == goal_id]


class FakePlanRepository:
    def __init__(self, state: FakeState) -> None:
        self._state = state

    async def add(self, plan: StudyPlan) -> None:
        self._state.plans[plan.id] = plan

    async def get(self, plan_id: str) -> StudyPlan | None:
        return self._state.plans.get(plan_id)

    async def list_for_goal(self, goal_id: str) -> list[StudyPlan]:
        return sorted(
            (plan for plan in self._state.plans.values() if plan.goal_id == goal_id),
            key=lambda plan: plan.version,
        )

    async def get_item(self, item_id: str) -> PlanItem | None:
        for plan in self._state.plans.values():
            item = plan.get_item(item_id)
            if item is not None:
                return item
        return None

    async def update_item(self, item: PlanItem) -> None:
        plan = self._state.plans.get(item.plan_id)
        if plan is None or plan.get_item(item.id) is None:
            raise LookupError(item.id)
        self._state.plans[plan.id] = replace(
            plan,
            items=tuple(
                item if existing.id == item.id else existing for existing in plan.items
            ),
        )


class FakeExerciseRepository:
    def __init__(self, state: FakeState) -> None:
        self._state = state

    async def add(self, exercise: Exercise) -> None:
        self._state.exercises[exercise.id] = exercise

    async def get(self, exercise_id: str) -> Exercise | None:
        return self._state.exercises.get(exercise_id)

    async def list_for_node(self, knowledge_node_id: str) -> list[Exercise]:
        return [
            exercise
            for exercise in self._state.exercises.values()
            if exercise.knowledge_node_id == knowledge_node_id
        ]

    async def add_attempt(self, attempt: ExerciseAttempt) -> None:
        self._state.attempts[attempt.id] = attempt

    async def update_attempt(self, attempt: ExerciseAttempt) -> None:
        if attempt.id not in self._state.attempts:
            raise LookupError(attempt.id)
        self._state.attempts[attempt.id] = attempt

    async def get_attempt(self, attempt_id: str) -> ExerciseAttempt | None:
        return self._state.attempts.get(attempt_id)

    async def list_attempts(self, exercise_id: str) -> list[ExerciseAttempt]:
        return sorted(
            (
                attempt
                for attempt in self._state.attempts.values()
                if attempt.exercise_id == exercise_id
            ),
            key=lambda attempt: attempt.attempted_at,
        )

    async def list_attempts_for_session(
        self, study_session_id: str
    ) -> list[ExerciseAttempt]:
        return sorted(
            (
                attempt
                for attempt in self._state.attempts.values()
                if attempt.study_session_id == study_session_id
            ),
            key=lambda attempt: attempt.attempted_at,
        )


class FakeSessionRepository:
    def __init__(self, state: FakeState) -> None:
        self._state = state

    async def add(self, study_session: StudySession) -> None:
        self._state.sessions[study_session.id] = study_session

    async def get(self, session_id: str) -> StudySession | None:
        return self._state.sessions.get(session_id)

    async def update(self, study_session: StudySession) -> None:
        if study_session.id not in self._state.sessions:
            raise LookupError(study_session.id)
        self._state.sessions[study_session.id] = study_session


class FakeMasteryRepository:
    def __init__(self, state: FakeState) -> None:
        self._state = state

    async def add_event(self, event: MasteryEvent) -> None:
        self._state.mastery_events[event.id] = event

    async def list_events(
        self, user_id: str, knowledge_node_id: str
    ) -> list[MasteryEvent]:
        return sorted(
            (
                event
                for event in self._state.mastery_events.values()
                if event.user_id == user_id
                and event.knowledge_node_id == knowledge_node_id
            ),
            key=lambda event: (event.occurred_at, event.id),
        )

    async def update_event(self, event: MasteryEvent) -> None:
        if event.id not in self._state.mastery_events:
            raise LookupError(event.id)
        self._state.mastery_events[event.id] = event

    async def get_snapshot(
        self, user_id: str, knowledge_node_id: str
    ) -> MasterySnapshot | None:
        return self._state.mastery_snapshots.get((user_id, knowledge_node_id))

    async def save_snapshot(self, snapshot: MasterySnapshot) -> None:
        self._state.mastery_snapshots[
            (snapshot.user_id, snapshot.knowledge_node_id)
        ] = snapshot


class FakeReviewRepository:
    def __init__(self, state: FakeState) -> None:
        self._state = state

    async def get(self, user_id: str, knowledge_node_id: str) -> ReviewSchedule | None:
        return self._state.reviews.get((user_id, knowledge_node_id))

    async def save(self, schedule: ReviewSchedule) -> None:
        self._state.reviews[(schedule.user_id, schedule.knowledge_node_id)] = schedule

    async def list_due(
        self, user_id: str, due_before: datetime
    ) -> list[ReviewSchedule]:
        return sorted(
            (
                schedule
                for schedule in self._state.reviews.values()
                if schedule.user_id == user_id and schedule.due_at <= due_before
            ),
            key=lambda schedule: schedule.due_at,
        )


class FakeMemoryRepository:
    def __init__(self, state: FakeState) -> None:
        self._state = state

    async def add(
        self,
        record: MemoryRecord,
        evidence: MemoryEvidence,
        revision: MemoryRevision,
    ) -> None:
        self._state.memories[record.id] = record
        self._state.memory_evidence[evidence.id] = evidence
        self._state.memory_revisions[revision.id] = revision

    async def get(self, memory_id: str) -> MemoryAggregate | None:
        record = self._state.memories.get(memory_id)
        if record is None:
            return None
        return self._aggregate(record)

    async def list_for_user(
        self,
        user_id: str,
        *,
        status: MemoryStatus | None = None,
        kind: MemoryKind | None = None,
        limit: int = 100,
    ) -> list[MemoryAggregate]:
        records = [
            record
            for record in self._state.memories.values()
            if record.user_id == user_id
            and (status is None or record.status == status)
            and (kind is None or record.kind == kind)
        ]
        records.sort(key=lambda item: (item.updated_at, item.id), reverse=True)
        return [self._aggregate(record) for record in records[:limit]]

    async def find_by_fingerprint(
        self, user_id: str, fingerprint: str
    ) -> MemoryAggregate | None:
        matches = [
            item
            for item in self._state.memories.values()
            if item.user_id == user_id and item.fingerprint == fingerprint
        ]
        if not matches:
            return None
        return self._aggregate(max(matches, key=lambda item: item.updated_at))

    async def find_active_by_key(
        self, user_id: str, memory_key: str
    ) -> MemoryAggregate | None:
        matches = [
            item
            for item in self._state.memories.values()
            if item.user_id == user_id
            and item.memory_key == memory_key
            and item.status == MemoryStatus.ACTIVE
        ]
        if not matches:
            return None
        return self._aggregate(max(matches, key=lambda item: item.valid_from))

    async def update(self, record: MemoryRecord) -> None:
        if record.id not in self._state.memories:
            raise LookupError(record.id)
        self._state.memories[record.id] = record

    async def add_evidence(self, evidence: MemoryEvidence) -> None:
        self._state.memory_evidence[evidence.id] = evidence

    async def add_revision(self, revision: MemoryRevision) -> None:
        self._state.memory_revisions[revision.id] = revision

    async def delete(self, memory_id: str) -> bool:
        if self._state.memories.pop(memory_id, None) is None:
            return False
        self._state.memory_evidence = {
            key: value
            for key, value in self._state.memory_evidence.items()
            if value.memory_id != memory_id
        }
        self._state.memory_revisions = {
            key: value
            for key, value in self._state.memory_revisions.items()
            if value.memory_id != memory_id
        }
        return True

    async def expire_due(self, user_id: str, now: datetime) -> int:
        count = 0
        for memory_id, record in tuple(self._state.memories.items()):
            if (
                record.user_id == user_id
                and record.status == MemoryStatus.ACTIVE
                and record.expires_at is not None
                and record.expires_at <= now
            ):
                self._state.memories[memory_id] = record.transition(
                    MemoryStatus.EXPIRED, now=now
                )
                count += 1
        return count

    def _aggregate(self, record: MemoryRecord) -> MemoryAggregate:
        evidence = tuple(
            sorted(
                (
                    item
                    for item in self._state.memory_evidence.values()
                    if item.memory_id == record.id
                ),
                key=lambda item: (item.observed_at, item.id),
            )
        )
        revisions = tuple(
            sorted(
                (
                    item
                    for item in self._state.memory_revisions.values()
                    if item.memory_id == record.id
                ),
                key=lambda item: item.revision,
            )
        )
        return MemoryAggregate(record=record, evidence=evidence, revisions=revisions)


class FakeUnitOfWork:
    def __init__(self, state: FakeState) -> None:
        self._shared_state = state
        self._working_state: FakeState | None = None
        self.committed = False
        self.users: FakeUserRepository
        self.goals: FakeGoalRepository
        self.knowledge: FakeKnowledgeRepository
        self.plans: FakePlanRepository
        self.exercises: FakeExerciseRepository
        self.sessions: FakeSessionRepository
        self.mastery: FakeMasteryRepository
        self.reviews: FakeReviewRepository
        self.memories: FakeMemoryRepository

    async def __aenter__(self) -> "FakeUnitOfWork":
        state = deepcopy(self._shared_state)
        self._working_state = state
        self.users = FakeUserRepository(state)
        self.goals = FakeGoalRepository(state)
        self.knowledge = FakeKnowledgeRepository(state)
        self.plans = FakePlanRepository(state)
        self.exercises = FakeExerciseRepository(state)
        self.sessions = FakeSessionRepository(state)
        self.mastery = FakeMasteryRepository(state)
        self.reviews = FakeReviewRepository(state)
        self.memories = FakeMemoryRepository(state)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self._working_state = None

    async def commit(self) -> None:
        if self._working_state is None:
            raise RuntimeError("unit of work has not been entered")
        self._shared_state.replace_with(self._working_state)
        self.committed = True

    async def rollback(self) -> None:
        if self._working_state is None:
            raise RuntimeError("unit of work has not been entered")
        self._working_state = deepcopy(self._shared_state)


class FakeUnitOfWorkFactory:
    def __init__(self, state: FakeState | None = None) -> None:
        self.state = state or FakeState()
        self.instances: list[FakeUnitOfWork] = []

    def __call__(self) -> FakeUnitOfWork:
        unit_of_work = FakeUnitOfWork(self.state)
        self.instances.append(unit_of_work)
        return unit_of_work
