"""Validated contracts for every model-generated LearnLoop artifact."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentOutput(BaseModel):
    """Strict base class shared by model-generated output."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class GoalClarification(AgentOutput):
    refined_goal: str = Field(min_length=1, max_length=500)
    desired_outcome: str = Field(min_length=1, max_length=1_000)
    assumptions: list[str] = Field(default_factory=list, max_length=8)
    clarification_questions: list[str] = Field(default_factory=list, max_length=5)


class KnowledgeNodeProposal(AgentOutput):
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=2_000)
    difficulty: float = Field(ge=1.0, le=5.0)


class KnowledgeEdgeProposal(AgentOutput):
    source_key: str = Field(min_length=1, max_length=64)
    target_key: str = Field(min_length=1, max_length=64)
    relation: Literal["prerequisite", "part_of", "related"] = "prerequisite"


class KnowledgeGraphProposal(AgentOutput):
    nodes: list[KnowledgeNodeProposal] = Field(min_length=1, max_length=20)
    edges: list[KnowledgeEdgeProposal] = Field(default_factory=list, max_length=60)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        keys = [node.key for node in self.nodes]
        if len(keys) != len(set(keys)):
            raise ValueError("knowledge node keys must be unique")
        known = set(keys)
        edge_signatures: set[tuple[str, str, str]] = set()
        for edge in self.edges:
            if edge.source_key not in known or edge.target_key not in known:
                raise ValueError("knowledge edge references an unknown node key")
            if edge.source_key == edge.target_key:
                raise ValueError("knowledge edge cannot point to itself")
            signature = (edge.source_key, edge.target_key, edge.relation)
            if signature in edge_signatures:
                raise ValueError("knowledge edges must be unique")
            edge_signatures.add(signature)
        return self


class StudyPlanItemProposal(AgentOutput):
    knowledge_node_key: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=300)
    estimated_minutes: int = Field(gt=0, le=480)
    learning_objectives: list[str] = Field(min_length=1, max_length=5)


class StudyPlanProposal(AgentOutput):
    title: str = Field(min_length=1, max_length=300)
    rationale: str = Field(min_length=1, max_length=2_000)
    items: list[StudyPlanItemProposal] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_unique_items(self) -> Self:
        keys = [item.knowledge_node_key for item in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("each knowledge node can appear only once in a study plan")
        return self


class LessonContent(AgentOutput):
    title: str = Field(min_length=1, max_length=300)
    explanation: str = Field(min_length=1, max_length=8_000)
    examples: list[str] = Field(min_length=1, max_length=5)
    checkpoints: list[str] = Field(min_length=1, max_length=5)
    summary: str = Field(min_length=1, max_length=2_000)


class ExerciseProposal(AgentOutput):
    exercise_type: Literal["multiple_choice"] = "multiple_choice"
    prompt: str = Field(min_length=1, max_length=2_000)
    options: list[str] = Field(min_length=2, max_length=6)
    correct_options: list[str] = Field(min_length=1, max_length=3)
    explanation: str = Field(min_length=1, max_length=2_000)
    difficulty: float = Field(ge=1.0, le=5.0)

    @model_validator(mode="after")
    def validate_options(self) -> Self:
        if len(self.options) != len(set(self.options)):
            raise ValueError("exercise options must be unique")
        if not set(self.correct_options).issubset(self.options):
            raise ValueError("correct options must be present in options")
        return self


class AnswerEvaluation(AgentOutput):
    score_ratio: float = Field(ge=0.0, le=1.0)
    is_correct: bool
    feedback: str = Field(min_length=1, max_length=2_000)
    strengths: list[str] = Field(default_factory=list, max_length=5)
    improvements: list[str] = Field(default_factory=list, max_length=5)


class MisconceptionDiagnosis(AgentOutput):
    misconception: str = Field(min_length=1, max_length=1_000)
    evidence: list[str] = Field(min_length=1, max_length=5)
    remediation_steps: list[str] = Field(min_length=1, max_length=5)
    prerequisite_node_keys: list[str] = Field(default_factory=list, max_length=5)


class StudySummary(AgentOutput):
    covered_concepts: list[str] = Field(min_length=1, max_length=20)
    accomplishments: list[str] = Field(min_length=1, max_length=10)
    remaining_gaps: list[str] = Field(default_factory=list, max_length=10)
    next_actions: list[str] = Field(min_length=1, max_length=10)
