"""Prompt for turning a broad learning request into a testable goal."""

from pydantic import Field

from app.agent.prompts.models import PromptInput, PromptTemplate
from app.agent.schemas import GoalClarification


class GoalClarificationInput(PromptInput):
    goal_title: str = Field(min_length=1)
    goal_description: str = ""
    desired_outcome: str = Field(min_length=1)
    weekly_minutes: int = Field(gt=0)


GOAL_CLARIFICATION_PROMPT = PromptTemplate(
    name="goal_clarification",
    version="1.0.0",
    use_case="Clarify a learner goal without inventing personal background.",
    input_schema=GoalClarificationInput,
    output_schema=GoalClarification,
    system_template="""
你是 LearnLoop 的学习目标分析器。将宽泛目标改写为可验证、可执行的学习目标。
只能基于用户提供的信息；缺失信息写成假设或澄清问题。仅输出符合指定 Schema 的 JSON。
""",
    user_template="""
学习主题：{goal_title}
补充说明：{goal_description}
期望结果：{desired_outcome}
每周可投入分钟数：{weekly_minutes}
""",
    test_input={
        "goal_title": "图算法",
        "goal_description": "会 Python",
        "desired_outcome": "独立实现 BFS 和 DFS",
        "weekly_minutes": 180,
    },
)
