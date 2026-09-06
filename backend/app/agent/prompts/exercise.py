"""Prompt for a deterministically gradable exercise."""

from pydantic import Field

from app.agent.prompts.models import PromptInput, PromptTemplate
from app.agent.schemas import ExerciseProposal


class ExerciseInput(PromptInput):
    goal: str = Field(min_length=1)
    lesson_title: str = Field(min_length=1)
    lesson_summary: str = Field(min_length=1)
    difficulty: float = Field(ge=1.0, le=5.0)


EXERCISE_PROMPT = PromptTemplate(
    name="exercise",
    version="1.0.0",
    use_case="Generate one objective exercise that can be graded without an LLM.",
    input_schema=ExerciseInput,
    output_schema=ExerciseProposal,
    system_template="""
你是学习测评设计师。只生成一道与讲解直接相关的选择题。
选项必须互异、答案必须包含在选项中，不使用“以上都是”等模糊表述。
仅输出符合指定 Schema 的 JSON。
""",
    user_template="""
总目标：{goal}
课程标题：{lesson_title}
课程总结：{lesson_summary}
难度：{difficulty}
""",
    test_input={
        "goal": "独立实现 BFS 和 DFS",
        "lesson_title": "图的表示",
        "lesson_summary": "邻接表适合稀疏图",
        "difficulty": 2.0,
    },
)
