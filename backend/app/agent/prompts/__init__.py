"""Prompt catalog exposed to model-backed application services."""

from app.agent.prompts.answer_evaluation import ANSWER_EVALUATION_PROMPT
from app.agent.prompts.dynamic_agent import (
    DECISION_PROMPT,
    PLANNER_PROMPT,
    REPLAN_PROMPT,
)
from app.agent.prompts.exercise import EXERCISE_PROMPT
from app.agent.prompts.goal_clarification import GOAL_CLARIFICATION_PROMPT
from app.agent.prompts.knowledge_graph import KNOWLEDGE_GRAPH_PROMPT
from app.agent.prompts.lesson import LESSON_PROMPT
from app.agent.prompts.misconception_diagnosis import (
    MISCONCEPTION_DIAGNOSIS_PROMPT,
)
from app.agent.prompts.models import PromptInput, PromptTemplate, RenderedPrompt
from app.agent.prompts.research import RESEARCH_SYNTHESIS_PROMPT
from app.agent.prompts.study_plan import STUDY_PLAN_PROMPT

PROMPT_CATALOG = (
    PLANNER_PROMPT,
    DECISION_PROMPT,
    REPLAN_PROMPT,
    GOAL_CLARIFICATION_PROMPT,
    KNOWLEDGE_GRAPH_PROMPT,
    STUDY_PLAN_PROMPT,
    LESSON_PROMPT,
    EXERCISE_PROMPT,
    ANSWER_EVALUATION_PROMPT,
    MISCONCEPTION_DIAGNOSIS_PROMPT,
    RESEARCH_SYNTHESIS_PROMPT,
)

__all__ = [
    "ANSWER_EVALUATION_PROMPT",
    "EXERCISE_PROMPT",
    "GOAL_CLARIFICATION_PROMPT",
    "KNOWLEDGE_GRAPH_PROMPT",
    "LESSON_PROMPT",
    "MISCONCEPTION_DIAGNOSIS_PROMPT",
    "PROMPT_CATALOG",
    "RESEARCH_SYNTHESIS_PROMPT",
    "STUDY_PLAN_PROMPT",
    "PromptInput",
    "PromptTemplate",
    "RenderedPrompt",
]
