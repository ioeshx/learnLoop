"""Versioned Scenario, Trial, Grade and Report contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.types import JsonValue


class ReliabilityContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FaultKind(StrEnum):
    TIMEOUT = "timeout"
    TRANSIENT_TOOL = "transient_tool"
    MALFORMED_MODEL = "malformed_model"
    STALE_ARTIFACT = "stale_artifact"
    GOAL_SHIFT = "goal_shift"
    PROMPT_INJECTION = "prompt_injection"


class AgentVariant(StrEnum):
    FIXED = "fixed"
    DYNAMIC = "dynamic"
    POLICY = "policy"
    MODEL_ROUTE = "model_route"
    TEAM = "team"


class FaultSpec(ReliabilityContract):
    id: str
    kind: FaultKind
    target: str = Field(min_length=1, max_length=200)
    probability: float = Field(default=1, ge=0, le=1)
    occurrence: int = Field(default=1, ge=1, le=20)
    recoverable: bool = True
    safety_critical: bool = False
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class ReliabilityScenario(ReliabilityContract):
    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    title: str = Field(min_length=1, max_length=300)
    task_kind: str = Field(min_length=1, max_length=100)
    role: str = Field(default="lead", min_length=1, max_length=100)
    variant: AgentVariant
    required_evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    max_tokens: int = Field(default=10_000, ge=1)
    max_estimated_cost_usd: float = Field(default=1, ge=0)
    faults: list[FaultSpec] = Field(default_factory=list, max_length=30)
    fixture_only: bool = True


class EnvironmentSnapshot(ReliabilityContract):
    runtime_version: str
    policy_version: str
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    provider_profile_versions: dict[str, str] = Field(default_factory=dict)
    dataset_version: str


class ScheduledFault(ReliabilityContract):
    spec: FaultSpec
    scheduled: bool
    random_value: float = Field(ge=0, le=1)


class TrialManifest(ReliabilityContract):
    id: str = Field(default_factory=lambda: str(uuid4()))
    scenario_id: str
    scenario_version: str
    trial_index: int = Field(ge=0)
    seed: int = Field(ge=0)
    environment: EnvironmentSnapshot
    faults: list[ScheduledFault]
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FaultRecord(ReliabilityContract):
    fault_id: str
    kind: FaultKind
    target: str
    injected: bool
    recovered: bool
    occurrence: int = Field(ge=1)


class TrajectoryStep(ReliabilityContract):
    index: int = Field(ge=0)
    action: str
    status: Literal["succeeded", "failed", "recovered", "blocked"]
    evidence_ids: list[str] = Field(default_factory=list)
    tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0, ge=0)
    duplicate: bool = False
    safety_violations: list[str] = Field(default_factory=list)


class TrialOutcome(ReliabilityContract):
    final_status: Literal["completed", "failed", "cancelled"]
    actual_evidence_ids: list[str] = Field(default_factory=list)
    trajectory: list[TrajectoryStep]
    faults: list[FaultRecord]
    total_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    terminal_reason: str | None = None


class TrialGrade(ReliabilityContract):
    passed: bool
    task_success: bool
    safety_passed: bool
    evidence_coverage: float = Field(ge=0, le=1)
    trajectory_invariants_passed: bool
    score: float = Field(ge=0, le=1)
    violations: list[str] = Field(default_factory=list)


class ReliabilityTrial(ReliabilityContract):
    manifest: TrialManifest
    scenario: ReliabilityScenario
    outcome: TrialOutcome
    grade: TrialGrade


class SliceMetric(ReliabilityContract):
    dimension: Literal["fault", "task_kind", "role", "variant"]
    value: str
    trials: int = Field(ge=1)
    pass_rate: float = Field(ge=0, le=1)
    safety_rate: float = Field(ge=0, le=1)
    average_score: float = Field(ge=0, le=1)


class ReliabilityMetrics(ReliabilityContract):
    pass_at_k: float = Field(ge=0, le=1)
    pass_power_k: float = Field(ge=0, le=1)
    trial_pass_rate: float = Field(ge=0, le=1)
    recovery_rate: float = Field(ge=0, le=1)
    redundancy_rate: float = Field(ge=0, le=1)
    safety_rate: float = Field(ge=0, le=1)
    cost_p95_usd: float = Field(ge=0)
    tokens_p95: float = Field(ge=0)
    worst_slice_score: float = Field(ge=0, le=1)


class ReliabilityReport(ReliabilityContract):
    id: str = Field(default_factory=lambda: str(uuid4()))
    report_version: str = "agent-reliability-1.0.0"
    base_seed: int = Field(ge=0)
    trials_per_scenario: int = Field(ge=1, le=100)
    fixture_only: bool
    metrics: ReliabilityMetrics
    slices: list[SliceMetric]
    trials: list[ReliabilityTrial]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ReliabilityRunRequest(ReliabilityContract):
    scenarios: list[ReliabilityScenario] = Field(min_length=1, max_length=50)
    trials_per_scenario: int = Field(default=5, ge=1, le=100)
    base_seed: int = Field(default=17, ge=0)
    environment: EnvironmentSnapshot

    @model_validator(mode="after")
    def unique_scenarios(self) -> Self:
        identities = [(item.id, item.version) for item in self.scenarios]
        if len(identities) != len(set(identities)):
            raise ValueError("Reliability Scenario identities must be unique")
        return self
