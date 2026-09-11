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
    """使用结构化 LLM 生成自适应复习题，并在模型失败时确定性降级。"""

    def __init__(self, model: StructuredModel) -> None:
        """保存结构化模型，同时准备固定题生成器作为安全回退。"""

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
        """把目标难度和解释注入版本化 Prompt，经校验后构造领域练习。

        模型基础设施错误或领域值错误不会阻断复习流程，而是回退到固定选择题；成功输出
        仍通过 Exercise 工厂校验选项和答案键。
        """

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
