"""Generate a curriculum with an LLM, then enforce deterministic domain rules."""

from dataclasses import replace
from datetime import datetime

from app.agent.prompts import (
    EXERCISE_PROMPT,
    GOAL_CLARIFICATION_PROMPT,
    KNOWLEDGE_GRAPH_PROMPT,
    LESSON_PROMPT,
    STUDY_PLAN_PROMPT,
)
from app.agent.schemas import (
    ExerciseProposal,
    GoalClarification,
    KnowledgeGraphProposal,
    LessonContent,
    StudyPlanProposal,
)
from app.application.curriculum import Curriculum
from app.application.errors import CurriculumGenerationError
from app.domain.exceptions import DomainError
from app.domain.exercises import Exercise
from app.domain.goals import LearningGoal
from app.domain.knowledge import (
    KnowledgeEdge,
    KnowledgeNode,
    RelationType,
    validate_knowledge_graph,
)
from app.domain.plans import StudyPlan
from app.infrastructure.llm.errors import ModelError
from app.infrastructure.llm.structured import StructuredModel


class LlmCurriculumGenerator:
    def __init__(self, model: StructuredModel) -> None:
        self._model = model

    async def generate(self, goal: LearningGoal, *, now: datetime) -> Curriculum:
        try:
            return await self._generate_validated(goal, now=now)
        except (ModelError, DomainError, ValueError, KeyError) as error:
            raise CurriculumGenerationError(
                "The model could not produce a domain-valid curriculum. Please retry."
            ) from error

    async def _generate_validated(
        self, goal: LearningGoal, *, now: datetime
    ) -> Curriculum:
        clarification = (
            await self._model.generate(
                GOAL_CLARIFICATION_PROMPT,
                {
                    "goal_title": goal.title,
                    "goal_description": goal.description,
                    "desired_outcome": goal.desired_outcome,
                    "weekly_minutes": goal.weekly_minutes,
                },
                GoalClarification,
            )
        ).value
        graph = (
            await self._model.generate(
                KNOWLEDGE_GRAPH_PROMPT,
                {
                    "refined_goal": clarification.refined_goal,
                    "desired_outcome": clarification.desired_outcome,
                    "weekly_minutes": goal.weekly_minutes,
                },
                KnowledgeGraphProposal,
            )
        ).value
        nodes = tuple(
            KnowledgeNode.create(
                goal_id=goal.id,
                title=proposal.title,
                description=proposal.description,
                difficulty=proposal.difficulty,
                now=now,
            )
            for proposal in graph.nodes
        )
        node_by_key = dict(
            zip((proposal.key for proposal in graph.nodes), nodes, strict=True)
        )
        edges = tuple(
            KnowledgeEdge.create(
                goal_id=goal.id,
                source_node_id=node_by_key[proposal.source_key].id,
                target_node_id=node_by_key[proposal.target_key].id,
                relation=RelationType(proposal.relation),
                now=now,
            )
            for proposal in graph.edges
        )
        validate_knowledge_graph(nodes, edges)

        plan_proposal = (
            await self._model.generate(
                STUDY_PLAN_PROMPT,
                {
                    "goal": clarification.desired_outcome,
                    "weekly_minutes": goal.weekly_minutes,
                    "knowledge_nodes": [
                        proposal.model_dump(mode="json") for proposal in graph.nodes
                    ],
                    "prerequisite_edges": [
                        proposal.model_dump(mode="json") for proposal in graph.edges
                    ],
                },
                StudyPlanProposal,
            )
        ).value
        _validate_plan(plan_proposal, graph)

        lesson_by_key: dict[str, LessonContent] = {}
        exercise_by_key: dict[str, ExerciseProposal] = {}
        for item in plan_proposal.items:
            node = node_by_key[item.knowledge_node_key]
            lesson = (
                await self._model.generate(
                    LESSON_PROMPT,
                    {
                        "goal": clarification.desired_outcome,
                        "knowledge_node_title": node.title,
                        "knowledge_node_description": node.description,
                        "difficulty": node.difficulty,
                    },
                    LessonContent,
                )
            ).value
            exercise = (
                await self._model.generate(
                    EXERCISE_PROMPT,
                    {
                        "goal": clarification.desired_outcome,
                        "lesson_title": lesson.title,
                        "lesson_summary": lesson.summary,
                        "difficulty": node.difficulty,
                    },
                    ExerciseProposal,
                )
            ).value
            lesson_by_key[item.knowledge_node_key] = lesson
            exercise_by_key[item.knowledge_node_key] = exercise

        nodes_with_lessons = tuple(
            replace(
                node,
                lesson_content=_render_lesson(lesson_by_key[proposal.key]),
            )
            for proposal, node in zip(graph.nodes, nodes, strict=True)
        )
        plan = StudyPlan.create(
            goal_id=goal.id,
            item_specs=[
                (
                    node_by_key[item.knowledge_node_key].id,
                    item.title,
                    item.estimated_minutes,
                )
                for item in plan_proposal.items
            ],
            now=now,
        )
        exercises = tuple(
            Exercise.create_multiple_choice(
                knowledge_node_id=node_by_key[item.knowledge_node_key].id,
                prompt=exercise_by_key[item.knowledge_node_key].prompt,
                options=exercise_by_key[item.knowledge_node_key].options,
                answer_key=exercise_by_key[item.knowledge_node_key].correct_options,
                now=now,
            )
            for item in plan_proposal.items
        )
        return Curriculum(
            nodes=nodes_with_lessons,
            edges=edges,
            plan=plan,
            exercises=exercises,
        )


def _validate_plan(plan: StudyPlanProposal, graph: KnowledgeGraphProposal) -> None:
    node_keys = {node.key for node in graph.nodes}
    plan_keys = {item.knowledge_node_key for item in plan.items}
    if plan_keys != node_keys:
        raise ValueError("study plan must contain every proposed knowledge node once")

    position = {item.knowledge_node_key: index for index, item in enumerate(plan.items)}
    for edge in graph.edges:
        if (
            edge.relation == RelationType.PREREQUISITE.value
            and position[edge.source_key] >= position[edge.target_key]
        ):
            raise ValueError("study plan violates a prerequisite ordering")


def _render_lesson(lesson: LessonContent) -> str:
    examples = "\n".join(f"- {example}" for example in lesson.examples)
    checkpoints = "\n".join(f"- {checkpoint}" for checkpoint in lesson.checkpoints)
    return (
        f"{lesson.explanation}\n\n"
        f"示例\n{examples}\n\n"
        f"自检\n{checkpoints}\n\n"
        f"总结\n{lesson.summary}"
    )
