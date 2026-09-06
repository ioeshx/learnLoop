"""Prompt for sequencing known knowledge nodes into a study plan."""

from typing import Any

from pydantic import Field

from app.agent.prompts.models import PromptInput, PromptTemplate
from app.agent.schemas import StudyPlanProposal


class StudyPlanInput(PromptInput):
    goal: str = Field(min_length=1)
    weekly_minutes: int = Field(gt=0)
    knowledge_nodes: list[dict[str, Any]] = Field(min_length=1)
    prerequisite_edges: list[dict[str, Any]] = Field(default_factory=list)


STUDY_PLAN_PROMPT = PromptTemplate(
    name="study_plan",
    version="1.0.0",
    use_case="Sequence validated knowledge nodes into executable sessions.",
    input_schema=StudyPlanInput,
    output_schema=StudyPlanProposal,
    system_template="""
你是学习计划设计师。只能使用给定知识点 key，并遵守前置依赖顺序。
每个知识点恰好安排一次，每项给出可观察的学习目标和合理时长。
仅输出符合指定 Schema 的 JSON。
""",
    user_template="""
目标：{goal}
每周分钟数：{weekly_minutes}
知识点：{knowledge_nodes}
依赖：{prerequisite_edges}
""",
    test_input={
        "goal": "独立实现 BFS 和 DFS",
        "weekly_minutes": 180,
        "knowledge_nodes": [{"key": "graph-basics", "title": "图基础"}],
        "prerequisite_edges": [],
    },
)
