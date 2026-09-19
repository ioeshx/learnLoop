"""Typed contracts for the v3 general Agent Team runtime."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.types import JsonValue

from app.agent.policy import CapabilityGrant, DataLabel


class TeamContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TeamTaskStatus(StrEnum):
    SUBMITTED = "submitted"
    RUNNING = "running"
    INPUT_REQUIRED = "input_required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TeamFailurePolicy(StrEnum):
    FAIL_FAST = "fail_fast"
    VERIFIED_PARTIAL = "verified_partial"


class TeamPart(TeamContract):
    kind: Literal["text", "json", "evidence_ref"]
    value: JsonValue
    media_type: str = "application/json"
    labels: list[DataLabel] = Field(default_factory=list, max_length=50)


class AgentCard(TeamContract):
    """Trusted role declaration; it never grants authority by itself."""

    role_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    description: str = Field(min_length=1, max_length=1_000)
    capabilities: list[str] = Field(min_length=1, max_length=30)
    input_contract: str = Field(min_length=1, max_length=200)
    output_contract: str = Field(min_length=1, max_length=200)
    allowed_tools: list[str] = Field(default_factory=list, max_length=20)
    risk: Literal["low", "medium", "high"] = "low"
    read_only: bool = True


class TeamBudget(TeamContract):
    max_total_tokens: int = Field(ge=100, le=200_000)
    deadline_seconds: float = Field(gt=0, le=3_600)
    max_parallel_children: int = Field(default=2, ge=1, le=16)
    max_children: int = Field(default=8, ge=1, le=64)


class TeamTaskSpec(TeamContract):
    task_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,99}$")
    role_id: str
    objective: str = Field(min_length=1, max_length=2_000)
    parts: list[TeamPart] = Field(default_factory=list, max_length=30)
    dependency_keys: list[str] = Field(default_factory=list, max_length=20)
    allocated_tokens: int = Field(ge=100, le=50_000)


class TeamRunRequest(TeamContract):
    parent_run_id: str
    plan_step_id: str
    tasks: list[TeamTaskSpec] = Field(min_length=1, max_length=64)
    budget: TeamBudget
    failure_policy: TeamFailurePolicy = TeamFailurePolicy.FAIL_FAST

    @model_validator(mode="after")
    def validate_dag_contract(self) -> Self:
        keys = [item.task_key for item in self.tasks]
        if len(keys) != len(set(keys)):
            raise ValueError("Team task keys must be unique")
        known = set(keys)
        for task in self.tasks:
            if task.task_key in task.dependency_keys:
                raise ValueError("Team task cannot depend on itself")
            if not set(task.dependency_keys).issubset(known):
                raise ValueError("Team task references an unknown dependency")
        _assert_acyclic(self.tasks)
        reserved_tokens = sum(item.allocated_tokens for item in self.tasks)
        if reserved_tokens > self.budget.max_total_tokens:
            raise ValueError("Team task reservations exceed the parent Token budget")
        if len(self.tasks) > self.budget.max_children:
            raise ValueError("Team task count exceeds max_children")
        return self


class TeamTask(TeamContract):
    id: str = Field(default_factory=lambda: str(uuid4()))
    parent_run_id: str
    plan_step_id: str
    task_key: str
    role_id: str
    objective: str
    parts: list[TeamPart]
    dependency_keys: list[str]
    allocated_tokens: int
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: TeamTaskStatus = TeamTaskStatus.SUBMITTED
    policy_decision_id: str | None = None
    child_grants: list[CapabilityGrant] = Field(default_factory=list)
    artifact_id: str | None = None
    used_tokens: int = Field(default=0, ge=0)
    error_code: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_usage(self) -> Self:
        if self.used_tokens > self.allocated_tokens:
            raise ValueError("Team task usage exceeds its Token reservation")
        return self


class ArtifactDraft(TeamContract):
    parts: list[TeamPart] = Field(min_length=1, max_length=50)
    used_tokens: int = Field(default=0, ge=0)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class TeamArtifact(TeamContract):
    id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    role_id: str
    parts: list[TeamPart]
    labels: list[DataLabel]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verified: bool = False
    verifier_version: str = "team-artifact-verifier-1.0.0"
    policy_decision_id: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TeamRunResult(TeamContract):
    parent_run_id: str
    tasks: list[TeamTask]
    artifacts: list[TeamArtifact]
    failure_policy: TeamFailurePolicy
    completed: int = Field(ge=0)
    failed: int = Field(ge=0)
    cancelled: int = Field(ge=0)
    used_tokens: int = Field(ge=0)


def _assert_acyclic(tasks: list[TeamTaskSpec]) -> None:
    dependencies = {item.task_key: set(item.dependency_keys) for item in tasks}
    ready = [key for key, values in dependencies.items() if not values]
    visited: set[str] = set()
    while ready:
        key = ready.pop()
        if key in visited:
            continue
        visited.add(key)
        for candidate, values in dependencies.items():
            if candidate not in visited and values.issubset(visited):
                ready.append(candidate)
    if len(visited) != len(tasks):
        raise ValueError("Team task dependency graph must be acyclic")
