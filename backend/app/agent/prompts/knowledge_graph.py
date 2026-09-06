"""Prompt for proposing a bounded acyclic learning graph."""

from pydantic import Field

from app.agent.prompts.models import PromptInput, PromptTemplate
from app.agent.schemas import KnowledgeGraphProposal


class KnowledgeGraphInput(PromptInput):
    refined_goal: str = Field(min_length=1)
    desired_outcome: str = Field(min_length=1)
    weekly_minutes: int = Field(gt=0)


KNOWLEDGE_GRAPH_PROMPT = PromptTemplate(
    name="knowledge_graph",
    version="1.0.0",
    use_case="Propose knowledge nodes and explicit prerequisite relations.",
    input_schema=KnowledgeGraphInput,
    output_schema=KnowledgeGraphProposal,
    system_template="""
你是课程知识架构师。生成 3 到 8 个边界清晰的知识点及其依赖关系。
key 使用简短的小写英文标识；prerequisite 边必须构成有向无环图。
难度使用 1 到 5。仅输出符合指定 Schema 的 JSON。
""",
    user_template="""
已澄清目标：{refined_goal}
期望能力：{desired_outcome}
每周可投入分钟数：{weekly_minutes}
""",
    test_input={
        "refined_goal": "理解图遍历基础",
        "desired_outcome": "独立实现 BFS 和 DFS",
        "weekly_minutes": 180,
    },
)
