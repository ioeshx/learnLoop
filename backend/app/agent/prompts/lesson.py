"""Prompt for generating a concise lesson for one validated node."""

from pydantic import BaseModel, Field

from app.agent.prompts.models import PromptInput, PromptTemplate
from app.agent.schemas import LessonContent


class LessonSource(BaseModel):
    title: str
    excerpt: str
    page_number: int | None = None
    section: str | None = None


class LessonInput(PromptInput):
    goal: str = Field(min_length=1)
    knowledge_node_title: str = Field(min_length=1)
    knowledge_node_description: str = Field(min_length=1)
    difficulty: float = Field(ge=1.0, le=5.0)
    sources: list[LessonSource] = Field(default_factory=list, max_length=5)


LESSON_PROMPT = PromptTemplate(
    name="lesson",
    version="1.1.0",
    use_case="Generate a self-contained explanation for one knowledge node.",
    input_schema=LessonInput,
    output_schema=LessonContent,
    system_template="""
你是耐心、准确的个人导师。围绕一个知识点生成短讲解、例子、自检问题和总结。
仅使用“检索资料”中真实提供的内容；资料为空时不要声称有引用。不要编造页码、章节或
资料内容。仅输出符合指定 Schema 的 JSON。
""",
    user_template="""
总目标：{goal}
知识点：{knowledge_node_title}
定义：{knowledge_node_description}
难度：{difficulty}
检索资料：{sources}
""",
    test_input={
        "goal": "独立实现 BFS 和 DFS",
        "knowledge_node_title": "图的表示",
        "knowledge_node_description": "理解邻接表和邻接矩阵",
        "difficulty": 2.0,
        "sources": [],
    },
)
