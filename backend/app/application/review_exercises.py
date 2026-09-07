"""Ports and deterministic fallback for adaptive review exercise generation."""

from datetime import datetime
from typing import Protocol

from app.domain.exercises import Exercise
from app.domain.goals import LearningGoal
from app.domain.knowledge import KnowledgeNode
from app.domain.mastery import AdaptiveRecommendation


class ReviewExerciseGenerator(Protocol):
    async def generate(
        self,
        goal: LearningGoal,
        node: KnowledgeNode,
        recommendation: AdaptiveRecommendation,
        *,
        now: datetime,
    ) -> Exercise: ...


class FixedReviewExerciseGenerator:
    async def generate(
        self,
        goal: LearningGoal,
        node: KnowledgeNode,
        recommendation: AdaptiveRecommendation,
        *,
        now: datetime,
    ) -> Exercise:
        del goal
        reason = recommendation.reasons[0]
        return Exercise.create_multiple_choice(
            knowledge_node_id=node.id,
            prompt=f"复习“{node.title}”：以下哪项最符合本知识点？（{reason}）",
            options=[
                node.description,
                "只记忆结论，不验证适用条件",
                "跳过基础概念，直接增加题目难度",
            ],
            answer_key=[node.description],
            now=now,
        )
