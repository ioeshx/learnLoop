"""Typed contracts for Stage 17 policy optimization and Agentic RL research."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Self
from uuid import uuid4

from pydantic import Field, model_validator

from app.agent.dynamic.models import AgentContract


class PolicyStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    DISABLED = "disabled"
    QUARANTINED = "quarantined"


class RewardStatus(StrEnum):
    PROVISIONAL = "provisional"
    MATURE = "mature"
    INELIGIBLE = "ineligible"


class ReviewDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class ExperimentStatus(StrEnum):
    DRAFT = "draft"
    COMPLETED = "completed"
    PROMOTABLE = "promotable"
    REJECTED = "rejected"


class FailureCluster(AgentContract):
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    problem_category: str
    count: int = Field(ge=1)
    run_ids: list[str] = Field(min_length=1)
    root_causes: list[str]
    evidence_references: list[str]


class TeachingArm(AgentContract):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    instruction: str = Field(min_length=1, max_length=1_000)
    required_tools: list[str] = Field(default_factory=list, max_length=12)
    prohibited_actions: list[str] = Field(default_factory=list, max_length=12)


class PolicyVersion(AgentContract):
    """Immutable policy configuration; learned sufficient statistics live separately."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    family: str = Field(default="teaching_strategy", min_length=1, max_length=100)
    version: int = Field(default=1, ge=1)
    status: PolicyStatus = PolicyStatus.CANDIDATE
    algorithm: str = Field(default="linucb", pattern=r"^linucb$")
    feature_schema_version: str = "teaching-context-1.0.0"
    reward_version: str = "learning-reward-1.0.0"
    alpha: float = Field(default=0.5, ge=0, le=5)
    epsilon: float = Field(default=0.05, ge=0, le=0.5)
    arms: list[TeachingArm] = Field(min_length=2, max_length=12)
    source_experiment_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_unique_arms(self) -> Self:
        ids = [item.id for item in self.arms]
        if len(ids) != len(set(ids)):
            raise ValueError("Policy arm ids must be unique")
        return self


class PolicyRevisionRequest(AgentContract):
    expected_version: int = Field(ge=1)
    alpha: float = Field(ge=0, le=5)
    epsilon: float = Field(ge=0, le=0.5)
    arms: list[TeachingArm] = Field(min_length=2, max_length=12)
    source_experiment_id: str


class PolicyActivationRequest(AgentContract):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2_000)


class TeachingContext(AgentContract):
    """Small auditable feature vector used by the online Contextual Bandit."""

    progress: float = Field(ge=0, le=1)
    consecutive_failures: float = Field(ge=0, le=1)
    retrieval_available: float = Field(ge=0, le=1)
    write_step: float = Field(ge=0, le=1)
    intercept: float = Field(default=1, ge=1, le=1)

    def vector(self) -> list[float]:
        return [
            self.progress,
            self.consecutive_failures,
            self.retrieval_available,
            self.write_step,
            self.intercept,
        ]


class BanditDecision(AgentContract):
    id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    plan_step_id: str
    decision_point_id: str
    policy_id: str
    policy_version: int = Field(ge=1)
    arm_id: str
    context: TeachingContext
    propensity: float = Field(gt=0, le=1)
    score: float
    exploratory: bool
    reward_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RewardEvidence(AgentContract):
    source: str
    reference: str
    summary: str = Field(min_length=1, max_length=1_000)


class RewardComponents(AgentContract):
    task_completion: float = Field(ge=0, le=1)
    immediate_verification: float = Field(ge=0, le=1)
    delayed_retention: float | None = Field(default=None, ge=0, le=1)
    transfer: float | None = Field(default=None, ge=0, le=1)
    user_feedback: float | None = Field(default=None, ge=-1, le=1)
    token_efficiency: float = Field(ge=0, le=1)
    tool_efficiency: float = Field(ge=0, le=1)
    latency_efficiency: float = Field(ge=0, le=1)


class RewardRecord(AgentContract):
    """Decomposed reward with a non-compensable safety gate."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    reward_version: str = "learning-reward-1.0.0"
    status: RewardStatus
    components: RewardComponents
    safety_violations: list[str] = Field(default_factory=list, max_length=50)
    hard_gate_passed: bool
    optimization_score: float | None = Field(default=None, ge=-1, le=1)
    evidence: list[RewardEvidence] = Field(default_factory=list, max_length=100)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    matured_at: datetime | None = None

    @model_validator(mode="after")
    def validate_hard_gate(self) -> Self:
        if self.safety_violations and self.hard_gate_passed:
            raise ValueError("Safety violations cannot pass the hard gate")
        if not self.hard_gate_passed and self.optimization_score is not None:
            raise ValueError("Unsafe rewards cannot expose an optimization score")
        if self.status == RewardStatus.MATURE and (
            self.components.delayed_retention is None
            or self.components.transfer is None
        ):
            raise ValueError("Mature reward requires retention and transfer labels")
        return self


class DelayedLearningOutcome(AgentContract):
    retention: float = Field(ge=0, le=1)
    transfer: float = Field(ge=0, le=1)
    user_feedback: float | None = Field(default=None, ge=-1, le=1)
    evidence_reference: str = Field(min_length=1, max_length=500)


class TrajectoryReview(AgentContract):
    run_id: str
    decision: ReviewDecision
    reviewer: str = Field(min_length=1, max_length=200)
    note: str = Field(min_length=1, max_length=2_000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TrajectoryReviewRequest(AgentContract):
    decision: ReviewDecision
    reviewer: str = Field(min_length=1, max_length=200)
    note: str = Field(min_length=1, max_length=2_000)


class AgentTransition(AgentContract):
    """Agent Lightning-style public transition without hidden chain-of-thought."""

    run_id: str
    sequence: int = Field(ge=1)
    state_ref: str
    action: dict[str, object]
    observation: dict[str, object] | None = None
    verifier: dict[str, object] | None = None
    reward: float | None = Field(default=None, ge=-1, le=1)


class SFTTrajectory(AgentContract):
    run_id: str
    policy_version: str
    prompt_versions: list[str]
    tool_names: list[str]
    transitions: list[AgentTransition] = Field(min_length=1)
    reward_id: str
    review: TrajectoryReview


class PreferencePair(AgentContract):
    id: str = Field(default_factory=lambda: str(uuid4()))
    context_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    chosen_run_id: str
    rejected_run_id: str
    chosen_reward_id: str
    rejected_reward_id: str
    margin: float = Field(gt=0, le=2)
    evidence_note: str = Field(min_length=1, max_length=2_000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PreferencePairRequest(AgentContract):
    context_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    chosen_run_id: str
    rejected_run_id: str
    evidence_note: str = Field(min_length=1, max_length=2_000)


class ReplaySample(AgentContract):
    id: str
    split: str = Field(pattern=r"^(train|validation|holdout)$")
    logged_arm: str
    logged_propensity: float = Field(gt=0, le=1)
    candidate_probabilities: dict[str, float]
    reward: float = Field(ge=-1, le=1)
    hard_gate_passed: bool
    tool_calls: int = Field(ge=0)
    tokens: int = Field(ge=0)
    latency_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_probability_distribution(self) -> Self:
        if any(
            value < 0 or value > 1 for value in self.candidate_probabilities.values()
        ):
            raise ValueError("Candidate probabilities must be within [0, 1]")
        total = sum(self.candidate_probabilities.values())
        if abs(total - 1) > 1e-6:
            raise ValueError("Candidate probabilities must sum to one")
        return self


class ExperimentManifest(AgentContract):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = Field(min_length=1, max_length=300)
    change_type: str = Field(
        pattern=r"^(prompt|context|harness|model|bandit|training)$"
    )
    baseline_version: str
    candidate_version: str
    dataset_version: str
    model_version: str
    prompt_versions: list[str]
    tool_registry_version: str
    code_version: str
    reward_version: str
    minimum_effective_sample_size: float = Field(default=10, gt=0)
    maximum_safety_violations: int = Field(default=0, ge=0)
    maximum_token_ratio: float = Field(default=1.1, gt=0)
    minimum_reward_lift: float = Field(default=0.01, ge=0)
    status: ExperimentStatus = ExperimentStatus.DRAFT
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ExperimentReport(AgentContract):
    manifest: ExperimentManifest
    split: str
    sample_count: int = Field(ge=0)
    effective_sample_size: float = Field(ge=0)
    baseline_reward: float
    ips_reward: float
    snips_reward: float
    reward_lift: float
    token_ratio: float = Field(ge=0)
    safety_violations: int = Field(ge=0)
    confidence_low: float
    confidence_high: float
    promotable: bool
    rejection_reasons: list[str]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ExperimentEvaluationRequest(AgentContract):
    manifest: ExperimentManifest
    samples: list[ReplaySample] = Field(min_length=1, max_length=100_000)
    split: str = Field(default="holdout", pattern=r"^(train|validation|holdout)$")
