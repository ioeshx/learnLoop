"""Validation tests for model-generated artifact contracts."""

import pytest
from pydantic import ValidationError

from app.agent.schemas import (
    ExerciseProposal,
    KnowledgeEdgeProposal,
    KnowledgeGraphProposal,
    KnowledgeNodeProposal,
    StudyPlanItemProposal,
    StudyPlanProposal,
)


def test_knowledge_graph_rejects_unknown_node_reference() -> None:
    with pytest.raises(ValidationError, match="unknown node key"):
        KnowledgeGraphProposal(
            nodes=[
                KnowledgeNodeProposal(
                    key="basics",
                    title="基础",
                    description="基础概念",
                    difficulty=1,
                )
            ],
            edges=[KnowledgeEdgeProposal(source_key="basics", target_key="missing")],
        )


def test_study_plan_rejects_duplicate_knowledge_nodes() -> None:
    item = StudyPlanItemProposal(
        knowledge_node_key="basics",
        title="基础",
        estimated_minutes=30,
        learning_objectives=["解释基本概念"],
    )
    with pytest.raises(ValidationError, match="only once"):
        StudyPlanProposal(title="计划", rationale="循序渐进", items=[item, item])


def test_exercise_rejects_answer_outside_options() -> None:
    with pytest.raises(ValidationError, match="present in options"):
        ExerciseProposal(
            prompt="正确答案是什么？",
            options=["A", "B"],
            correct_options=["C"],
            explanation="A 才是正确答案。",
            difficulty=1,
        )
