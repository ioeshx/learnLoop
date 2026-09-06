"""Thin graph tools around application services and domain validation."""

from dataclasses import dataclass
from datetime import datetime

from app.agent.schemas import KnowledgeGraphProposal, StudyPlanProposal
from app.application import (
    ApplicationDependencies,
    CompleteStudySession,
    GetDueReviews,
    GetLearningGoal,
    GetMasteryState,
    GetStudySession,
    GradeAnswerCommand,
    GradeObjectiveAnswer,
    PersistPlanProposalCommand,
    PersistStudyPlanProposal,
    ProposedKnowledgeEdge,
    ProposedKnowledgeNode,
    ProposedPlanItem,
    SearchLearningResources,
    SubmitAttemptCommand,
    SubmitExerciseAttempt,
)
from app.domain.knowledge import (
    KnowledgeEdge,
    KnowledgeNode,
    RelationType,
    validate_knowledge_graph,
)


@dataclass(frozen=True, slots=True)
class LearningTools:
    dependencies: ApplicationDependencies

    async def get_learning_goal(self, goal_id: str) -> dict[str, object]:
        goal = await GetLearningGoal(self.dependencies).execute(goal_id)
        return {
            "id": goal.id,
            "title": goal.title,
            "description": goal.description,
            "desired_outcome": goal.desired_outcome,
            "weekly_minutes": goal.weekly_minutes,
            "status": goal.status.value,
        }

    async def get_study_session(self, session_id: str) -> dict[str, object]:
        details = await GetStudySession(self.dependencies).execute(session_id)
        return {
            "session_id": details.session.id,
            "goal_id": details.session.goal_id,
            "plan_id": details.plan_id,
            "plan_item_id": details.plan_item.id,
            "knowledge_node_id": details.knowledge_node.id,
            "knowledge_node_title": details.knowledge_node.title,
            "knowledge_node_description": details.knowledge_node.description,
            "knowledge_node_difficulty": details.knowledge_node.difficulty,
            "lesson_title": details.knowledge_node.title,
            "lesson_content": (
                details.knowledge_node.lesson_content
                or details.knowledge_node.description
            ),
            "exercise_id": details.exercise.id,
            "exercise_prompt": details.exercise.prompt,
            "exercise_options": list(details.exercise.options),
        }

    async def get_mastery_state(
        self, knowledge_node_id: str
    ) -> dict[str, object]:
        snapshot = await GetMasteryState(self.dependencies).execute(
            knowledge_node_id
        )
        if snapshot is None:
            return {"score": 0.0, "attempt_count": 0, "correct_count": 0}
        return {
            "score": snapshot.score,
            "attempt_count": snapshot.attempt_count,
            "correct_count": snapshot.correct_count,
            "updated_at": snapshot.updated_at.isoformat(),
        }

    async def get_due_reviews(
        self, *, due_before: datetime | None = None
    ) -> list[dict[str, object]]:
        reviews = await GetDueReviews(self.dependencies).execute(
            due_before=due_before
        )
        return [
            {
                "knowledge_node_id": review.knowledge_node.id,
                "title": review.knowledge_node.title,
                "due_at": review.schedule.due_at.isoformat(),
            }
            for review in reviews
        ]

    async def search_learning_resources(
        self, knowledge_node_id: str
    ) -> list[dict[str, str]]:
        resources = await SearchLearningResources(self.dependencies).execute(
            knowledge_node_id
        )
        return [
            {
                "resource_id": resource.resource_id,
                "title": resource.title,
                "excerpt": resource.excerpt,
            }
            for resource in resources
        ]

    def validate_knowledge_graph(
        self, goal_id: str, proposal_data: dict[str, object]
    ) -> KnowledgeGraphProposal:
        proposal = KnowledgeGraphProposal.model_validate(proposal_data)
        now = self.dependencies.clock()
        nodes = tuple(
            KnowledgeNode.create(
                goal_id=goal_id,
                title=node.title,
                description=node.description,
                difficulty=node.difficulty,
                now=now,
            )
            for node in proposal.nodes
        )
        node_by_key = dict(
            zip((node.key for node in proposal.nodes), nodes, strict=True)
        )
        edges = tuple(
            KnowledgeEdge.create(
                goal_id=goal_id,
                source_node_id=node_by_key[edge.source_key].id,
                target_node_id=node_by_key[edge.target_key].id,
                relation=RelationType(edge.relation),
                now=now,
            )
            for edge in proposal.edges
        )
        validate_knowledge_graph(nodes, edges)
        return proposal

    def validate_study_plan(
        self,
        proposal_data: dict[str, object],
        graph_data: dict[str, object],
    ) -> StudyPlanProposal:
        graph = KnowledgeGraphProposal.model_validate(graph_data)
        proposal = StudyPlanProposal.model_validate(proposal_data)
        node_keys = {node.key for node in graph.nodes}
        plan_keys = {item.knowledge_node_key for item in proposal.items}
        if plan_keys != node_keys or len(proposal.items) != len(graph.nodes):
            raise ValueError("study plan must contain every proposed node once")
        position = {
            item.knowledge_node_key: index
            for index, item in enumerate(proposal.items)
        }
        for edge in graph.edges:
            if (
                edge.relation == RelationType.PREREQUISITE.value
                and position[edge.source_key] >= position[edge.target_key]
            ):
                raise ValueError("study plan violates prerequisite ordering")
        return proposal

    async def save_plan_proposal(
        self,
        goal_id: str,
        graph_data: dict[str, object],
        plan_data: dict[str, object],
    ) -> dict[str, object]:
        graph = self.validate_knowledge_graph(goal_id, graph_data)
        plan = self.validate_study_plan(plan_data, graph_data)
        details = await PersistStudyPlanProposal(self.dependencies).execute(
            PersistPlanProposalCommand(
                goal_id=goal_id,
                nodes=tuple(
                    ProposedKnowledgeNode(
                        key=node.key,
                        title=node.title,
                        description=node.description,
                        difficulty=node.difficulty,
                    )
                    for node in graph.nodes
                ),
                edges=tuple(
                    ProposedKnowledgeEdge(
                        source_key=edge.source_key,
                        target_key=edge.target_key,
                        relation=edge.relation,
                    )
                    for edge in graph.edges
                ),
                items=tuple(
                    ProposedPlanItem(
                        knowledge_node_key=item.knowledge_node_key,
                        title=item.title,
                        estimated_minutes=item.estimated_minutes,
                    )
                    for item in plan.items
                ),
            )
        )
        return {
            "plan_id": details.plan.id,
            "item_ids": [item.id for item in details.plan.items],
        }

    async def create_exercise(self, session_id: str) -> dict[str, object]:
        """Return the persisted exercise created with the session curriculum."""
        session = await self.get_study_session(session_id)
        return {
            "exercise_id": session["exercise_id"],
            "prompt": session["exercise_prompt"],
            "options": session["exercise_options"],
        }

    async def grade_objective_answer(
        self,
        *,
        session_id: str,
        exercise_id: str,
        selected_options: list[str],
    ) -> dict[str, object]:
        grade = await GradeObjectiveAnswer(self.dependencies).execute(
            GradeAnswerCommand(
                session_id=session_id,
                exercise_id=exercise_id,
                selected_options=tuple(selected_options),
            )
        )
        return {
            "score": grade.attempt.score,
            "is_correct": grade.attempt.is_correct,
            "expected_answer": list(grade.expected_answer),
        }

    async def update_mastery(
        self,
        *,
        session_id: str,
        exercise_id: str,
        selected_options: list[str],
        idempotency_key: str,
    ) -> dict[str, object]:
        result = await SubmitExerciseAttempt(self.dependencies).execute(
            SubmitAttemptCommand(
                session_id=session_id,
                exercise_id=exercise_id,
                selected_options=tuple(selected_options),
                idempotency_key=idempotency_key,
            )
        )
        return {
            "attempt_id": result.attempt.id,
            "is_correct": result.attempt.is_correct,
            "score": result.attempt.score,
            "mastery_score": result.mastery.score,
            "due_at": result.review.due_at.isoformat(),
        }

    def schedule_review(self, mastery_update: dict[str, object]) -> str:
        """Expose the schedule atomically produced by update_mastery."""
        due_at = mastery_update.get("due_at")
        if not isinstance(due_at, str):
            raise ValueError("mastery update does not contain a review due date")
        return due_at

    async def complete_study_session(self, session_id: str) -> None:
        await CompleteStudySession(self.dependencies).execute(session_id)
