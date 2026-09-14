"""Typed contracts for bounded Subagent-as-Tool delegation."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.research.models import CitationStatus


class DelegationContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SubagentRole(StrEnum):
    RESEARCHER = "researcher"
    CURRICULUM = "curriculum"
    TUTOR = "tutor"
    EVALUATOR = "evaluator"


class DelegationStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEADLINE_EXCEEDED = "deadline_exceeded"


class DelegationBudget(DelegationContract):
    allocated_tokens: int = Field(ge=500, le=50_000)
    max_queries: int = Field(ge=1, le=30)
    max_sources: int = Field(ge=1, le=50)
    deadline_seconds: float = Field(gt=0, le=600)


class DelegationUsage(DelegationContract):
    allocated_tokens: int = Field(default=0, ge=0)
    used_tokens: int = Field(default=0, ge=0)
    queries: int = Field(default=0, ge=0)
    sources: int = Field(default=0, ge=0)
    duration_ms: float = Field(default=0, ge=0)


class DelegationRequest(DelegationContract):
    """Immutable task envelope derived from the Lead Agent's trusted scope."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    parent_run_id: str
    plan_step_id: str
    role: SubagentRole
    objective: str = Field(min_length=1, max_length=2_000)
    goal_id: str
    knowledge_node_id: str | None = None
    allowed_tools: list[str] = Field(min_length=1, max_length=8)
    budget: DelegationBudget
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DelegatedEvidence(DelegationContract):
    id: str
    resource_id: str
    chunk_id: str
    title: str
    locator: str
    excerpt: str = Field(min_length=1, max_length=800)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DelegatedClaim(DelegationContract):
    id: str
    text: str = Field(min_length=1, max_length=2_000)
    citation_status: CitationStatus


class DelegatedCitation(DelegationContract):
    claim_id: str
    evidence_id: str
    resource_id: str
    chunk_id: str
    status: CitationStatus


class DelegationResult(DelegationContract):
    """Compressed public result; never exposes a Subagent's private Context."""

    delegation_id: str
    parent_run_id: str
    child_run_id: str
    role: SubagentRole
    status: DelegationStatus
    summary: str = Field(max_length=8_000)
    claims: list[DelegatedClaim] = Field(default_factory=list, max_length=20)
    citations: list[DelegatedCitation] = Field(default_factory=list, max_length=40)
    evidence: list[DelegatedEvidence] = Field(default_factory=list, max_length=20)
    unresolved_questions: list[str] = Field(default_factory=list, max_length=12)
    usage: DelegationUsage
    reused: bool = False
    failure_code: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_evidence_graph(self) -> Self:
        claim_ids = {item.id for item in self.claims}
        evidence_ids = {item.id for item in self.evidence}
        if any(item.claim_id not in claim_ids for item in self.citations):
            raise ValueError("delegated Citation references an unknown Claim")
        if any(item.evidence_id not in evidence_ids for item in self.citations):
            raise ValueError("delegated Citation references unknown Evidence")
        cited_claim_ids = {item.claim_id for item in self.citations}
        if self.status == DelegationStatus.COMPLETED and not claim_ids.issubset(
            cited_claim_ids
        ):
            raise ValueError("every completed delegated Claim requires a Citation")
        if self.usage.used_tokens > self.usage.allocated_tokens:
            raise ValueError("delegated usage exceeds allocated tokens")
        return self


class DelegationRecord(DelegationContract):
    request: DelegationRequest
    child_run_id: str
    status: DelegationStatus
    result: DelegationResult | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
