"""LLM-backed generation of an adaptive, objectively gradable review item."""

from datetime import datetime

from app.agent.prompts import EXERCISE_PROMPT
from app.agent.schemas import ExerciseProposal
from app.application.review_exercises import FixedReviewExerciseGenerator
from app.domain.exercises import Exercise
from app.domain.goals import LearningGoal
from app.domain.knowledge import KnowledgeNode
from app.domain.mastery import AdaptiveRecommendation
from app.infrastructure.llm.errors import ModelError
from app.infrastructure.llm.structured import StructuredModel


class LlmReviewExerciseGenerator:
    def __init__(self, model: StructuredModel) -> None:
        self._model = model
        self._fallback = FixedReviewExerciseGenerator()

    async def generate(
        self,
        goal: LearningGoal,
        node: KnowledgeNode,
        recommendation: AdaptiveRecommendation,
        *,
        now: datetime,
    ) -> Exercise:
        try:
            proposal = (
                await self._model.generate(
                    EXERCISE_PROMPT,
                    {
                        "goal": goal.desired_outcome,
                        "lesson_title": f"复习：{node.title}",
                        "lesson_summary": (
                            f"{node.description}\n自适应依据："
                            f"{'；'.join(recommendation.reasons)}"
                        ),
                        "difficulty": recommendation.target_difficulty,
                    },
                    ExerciseProposal,
                )
            ).value
        except (ModelError, ValueError):
            return await self._fallback.generate(goal, node, recommendation, now=now)
        return Exercise.create_multiple_choice(
            knowledge_node_id=node.id,
            prompt=proposal.prompt,
            options=proposal.options,
            answer_key=proposal.correct_options,
            now=now,
        )
