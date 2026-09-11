"""Ports and deterministic fallback for adaptive review exercise generation."""

from datetime import datetime
from typing import Protocol

from app.domain.exercises import Exercise
from app.domain.goals import LearningGoal
from app.domain.knowledge import KnowledgeNode
from app.domain.mastery import AdaptiveRecommendation


class ReviewExerciseGenerator(Protocol):
    """自适应复习题生成端口，允许固定规则和 LLM 实现互换。"""

    async def generate(
        self,
        goal: LearningGoal,
        node: KnowledgeNode,
        recommendation: AdaptiveRecommendation,
        *,
        now: datetime,
    ) -> Exercise:
        """依据目标、知识点和难度建议生成可客观评分的复习题。"""
        ...


class FixedReviewExerciseGenerator:
    """无需模型的确定性复习题生成器，作为离线模式和模型失败回退。"""

    async def generate(
        self,
        goal: LearningGoal,
        node: KnowledgeNode,
        recommendation: AdaptiveRecommendation,
        *,
        now: datetime,
    ) -> Exercise:
        """把知识点描述作为正确项构造选择题，并在题干中展示自适应原因。"""

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
