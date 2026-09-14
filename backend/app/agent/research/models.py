"""Typed contracts for Agentic retrieval, evidence, claims, and citations."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ResearchContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RetrievalMode(StrEnum):
    NO_RETRIEVAL = "no_retrieval"
    SINGLE_RETRIEVAL = "single_retrieval"
    MULTI_STEP_RESEARCH = "multi_step_research"


class EvidenceVerdict(StrEnum):
    ACCEPTED = "accepted"
    LOW_RELEVANCE = "low_relevance"
    LOW_QUALITY = "low_quality"
    DUPLICATE = "duplicate"
    PROMPT_INJECTION = "prompt_injection"


class CitationStatus(StrEnum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"


class ResearchBudget(ResearchContract):
    max_rounds: int = Field(default=3, ge=1, le=8)
    max_queries: int = Field(default=8, ge=1, le=30)
    max_sources: int = Field(default=12, ge=1, le=50)
    max_read_chars: int = Field(default=30_000, ge=1_000, le=200_000)
    max_context_tokens: int = Field(default=8_000, ge=500, le=50_000)


class ResearchUsage(ResearchContract):
    rounds: int = Field(default=0, ge=0)
    queries: int = Field(default=0, ge=0)
    sources: int = Field(default=0, ge=0)
    read_chars: int = Field(default=0, ge=0)
    estimated_tokens: int = Field(default=0, ge=0)
    stopped_reason: str | None = None


class ResearchRequest(ResearchContract):
    question: str = Field(min_length=1, max_length=4_000)
    goal_id: str = Field(min_length=1)
    knowledge_node_id: str | None = None
    mode_override: RetrievalMode | None = None
    budget: ResearchBudget = Field(default_factory=ResearchBudget)


class SubQuestion(ResearchContract):
    id: str
    text: str = Field(min_length=1, max_length=1_000)
    depends_on: list[str] = Field(default_factory=list, max_length=6)


class ResearchQuery(ResearchContract):
    id: str = Field(default_factory=lambda: str(uuid4()))
    subquestion_id: str
    text: str = Field(min_length=1, max_length=2_000)
    round_no: int = Field(ge=1)
    parent_query_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class EvidenceGrade(ResearchContract):
    relevance: float = Field(ge=0, le=1)
    source_quality: float = Field(ge=0, le=1)
    duplicate_score: float = Field(ge=0, le=1)
    coverage: float = Field(ge=0, le=1)
    verdict: EvidenceVerdict
    reason: str = Field(min_length=1, max_length=500)


class EvidenceItem(ResearchContract):
    """Immutable Chunk evidence with enough provenance for replay."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    query_id: str
    subquestion_id: str
    resource_id: str
    chunk_id: str
    title: str
    excerpt: str = Field(min_length=1, max_length=4_000)
    page_number: int | None
    section: str | None
    source_uri: str | None
    resource_version: str
    resource_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    trust: Literal["untrusted"] = "untrusted"
    grade: EvidenceGrade


class ClaimDraft(ResearchContract):
    text: str = Field(min_length=1, max_length=2_000)
    importance: Literal["critical", "supporting"] = "critical"
    evidence_ids: list[str] = Field(min_length=1, max_length=8)


class ClaimDraftSet(ResearchContract):
    claims: list[ClaimDraft] = Field(default_factory=list, max_length=20)


class Claim(ResearchContract):
    id: str = Field(default_factory=lambda: str(uuid4()))
    text: str = Field(min_length=1, max_length=2_000)
    importance: Literal["critical", "supporting"]
    citation_status: CitationStatus
    included_in_answer: bool


class CitationLink(ResearchContract):
    id: str = Field(default_factory=lambda: str(uuid4()))
    claim_id: str
    evidence_id: str
    resource_id: str
    chunk_id: str
    status: CitationStatus
    explanation: str = Field(min_length=1, max_length=500)


class ResearchTrace(ResearchContract):
    """Replayable public trajectory of the bounded Research Agent."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    request: ResearchRequest
    mode: RetrievalMode
    subquestions: list[SubQuestion]
    queries: list[ResearchQuery]
    evidence: list[EvidenceItem]
    claims: list[Claim]
    citations: list[CitationLink]
    gaps: list[str]
    usage: ResearchUsage
    status: Literal["completed", "insufficient_evidence", "failed"]
    answer: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_citation_graph(self) -> Self:
        evidence_ids = {item.id for item in self.evidence}
        claim_ids = {item.id for item in self.claims}
        if any(item.claim_id not in claim_ids for item in self.citations):
            raise ValueError("citation references an unknown Claim")
        if any(item.evidence_id not in evidence_ids for item in self.citations):
            raise ValueError("citation references unknown Evidence")
        return self


class ResearchResult(ResearchContract):
    trace_id: str
    mode: RetrievalMode
    status: Literal["completed", "insufficient_evidence", "failed"]
    answer: str
    claims: list[Claim]
    citations: list[CitationLink]
    evidence: list[EvidenceItem]
    gaps: list[str]
    usage: ResearchUsage
