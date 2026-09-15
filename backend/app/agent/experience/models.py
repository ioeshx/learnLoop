"""Evidence-bound Reflection and versioned Skill Library contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Self
from uuid import uuid4

from pydantic import Field, model_validator

from app.agent.dynamic.models import AgentContract


class ReflectionOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"


class EvidenceKind(StrEnum):
    OBSERVATION = "observation"
    VERIFICATION = "verification"
    TERMINAL = "terminal"


class SkillStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"
    DISABLED = "disabled"


class SkillRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ReflectionEvidence(AgentContract):
    """Immutable pointer to a public Observation, Verifier, or terminal signal."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    kind: EvidenceKind
    event_sequence: int = Field(ge=1)
    observation_id: str | None = None
    summary: str = Field(min_length=1, max_length=2_000)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReflectionInsight(AgentContract):
    statement: str = Field(min_length=1, max_length=2_000)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)


class RunReflection(AgentContract):
    """Candidate experience whose every conclusion is grounded in Run evidence.

    Reflection is deliberately stored outside Agent Memory. It describes execution
    behavior, not domain truth or a durable learner fact, so it can never overwrite
    Semantic/Personal Memory through the Stage 13 consolidation path.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    outcome: ReflectionOutcome
    problem_category: str = Field(min_length=1, max_length=120)
    evidence: list[ReflectionEvidence] = Field(min_length=1, max_length=100)
    root_causes: list[ReflectionInsight] = Field(default_factory=list, max_length=20)
    improvements: list[ReflectionInsight] = Field(default_factory=list, max_length=20)
    applicability: list[str] = Field(min_length=1, max_length=20)
    strategy_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_evidence_graph(self) -> Self:
        known = {item.id for item in self.evidence}
        referenced = {
            evidence_id
            for insight in [*self.root_causes, *self.improvements]
            for evidence_id in insight.evidence_ids
        }
        if not referenced.issubset(known):
            raise ValueError("Reflection insight references unknown evidence")
        return self


class SkillApplicability(AgentContract):
    graph_kind: str
    objective_keywords: list[str] = Field(min_length=1, max_length=20)
    required_tools: list[str] = Field(default_factory=list, max_length=20)


class SkillStep(AgentContract):
    order: int = Field(ge=1, le=20)
    instruction: str = Field(min_length=1, max_length=1_000)
    allowed_tools: list[str] = Field(default_factory=list, max_length=12)
    verifier: str = Field(min_length=1, max_length=500)


class SkillRecord(AgentContract):
    """One immutable Skill version plus mutable lifecycle and aggregate telemetry."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    family_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2_000)
    version: int = Field(default=1, ge=1)
    status: SkillStatus = SkillStatus.CANDIDATE
    risk: SkillRisk = SkillRisk.LOW
    applicability: SkillApplicability
    prerequisites: list[str] = Field(default_factory=list, max_length=20)
    steps: list[SkillStep] = Field(min_length=1, max_length=20)
    source_run_ids: list[str] = Field(min_length=2, max_length=100)
    source_reflections: list[RunReflection] = Field(min_length=2, max_length=20)
    success_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    average_tool_calls: float = Field(default=0, ge=0)
    average_tokens: float = Field(default=0, ge=0)
    valid_until: datetime | None = None
    review_note: str | None = Field(default=None, max_length=2_000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_provenance_snapshot(self) -> Self:
        snapshotted_runs = {item.run_id for item in self.source_reflections}
        if not snapshotted_runs.issubset(set(self.source_run_ids)):
            raise ValueError("Skill provenance references an unknown source Run")
        return self

    @property
    def success_rate(self) -> float | None:
        total = self.success_count + self.failure_count
        return self.success_count / total if total else None


class SkillRecall(AgentContract):
    skill: SkillRecord
    matched_keywords: list[str]
    score: float = Field(ge=0, le=1)


class SkillUsage(AgentContract):
    run_id: str
    skill_id: str
    skill_version: int = Field(ge=1)
    status: str
    succeeded: bool | None = None
    tool_calls: int = Field(default=0, ge=0)
    tokens: int = Field(default=0, ge=0)
    failure_type: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None


class SkillReviewRequest(AgentContract):
    decision: str = Field(pattern=r"^(publish|reject)$")
    expected_version: int = Field(ge=1)
    note: str = Field(min_length=1, max_length=2_000)


class SkillStatusRequest(AgentContract):
    status: SkillStatus
    expected_version: int = Field(ge=1)
    note: str = Field(min_length=1, max_length=2_000)


class SkillRevisionRequest(AgentContract):
    """Human-authored candidate revision; the previous version stays recoverable."""

    expected_version: int = Field(ge=1)
    description: str = Field(min_length=1, max_length=2_000)
    applicability: SkillApplicability
    prerequisites: list[str] = Field(default_factory=list, max_length=20)
    steps: list[SkillStep] = Field(min_length=1, max_length=20)
    risk: SkillRisk
    note: str = Field(min_length=1, max_length=2_000)
