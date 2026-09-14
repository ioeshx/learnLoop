"""Strict boundary schemas for Memory ingestion and retrieval."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.memory import (
    MemoryAggregate,
    MemoryKind,
    MemoryStatus,
    MemoryTrust,
)


class MemoryContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MemoryCandidate(MemoryContract):
    user_id: str = Field(min_length=1)
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=4_000)
    attributes: dict[str, Any] = Field(default_factory=dict)
    memory_key: str = Field(min_length=1, max_length=300)
    confidence: float = Field(ge=0, le=1)
    importance: float = Field(default=0.5, ge=0, le=1)
    trust: MemoryTrust
    source_type: str = Field(min_length=1, max_length=80)
    source_id: str = Field(min_length=1, max_length=200)
    source_excerpt: str = Field(min_length=1, max_length=2_000)
    lifecycle_event: Literal["run_completed", "handoff", "explicit_user"]
    goal_id: str | None = None
    knowledge_node_id: str | None = None
    run_id: str | None = None
    session_id: str | None = None
    attempt_id: str | None = None
    valid_from: datetime
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> MemoryCandidate:
        if self.knowledge_node_id is not None and self.goal_id is None:
            raise ValueError("knowledge-node Memory requires goal scope")
        if self.expires_at is not None and self.expires_at <= self.valid_from:
            raise ValueError("expires_at must be later than valid_from")
        return self


class MemoryQuery(MemoryContract):
    user_id: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=2_000)
    goal_id: str | None = None
    knowledge_node_id: str | None = None
    kinds: list[MemoryKind] = Field(default_factory=list, max_length=4)
    limit: int = Field(default=6, ge=1, le=20)
    minimum_score: float = Field(default=0.24, ge=0, le=1)


class MemoryRecall(MemoryContract):
    memory_id: str
    memory_key: str
    kind: MemoryKind
    content: str
    attributes: dict[str, Any]
    score: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    importance: float = Field(ge=0, le=1)
    trust: MemoryTrust
    goal_id: str | None
    knowledge_node_id: str | None
    valid_from: datetime
    expires_at: datetime | None
    evidence: list[dict[str, Any]]
    conflicting_memory_ids: list[str] = Field(default_factory=list)


class MemoryWriteOutcome(MemoryContract):
    action: Literal["created", "merged", "rejected"]
    aggregate: MemoryAggregate
    reason: str

    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, arbitrary_types_allowed=True
    )


class MemoryListFilter(MemoryContract):
    status: MemoryStatus | None = None
    kind: MemoryKind | None = None
    limit: int = Field(default=100, ge=1, le=500)


class MemoryCorrection(MemoryContract):
    content: str = Field(min_length=1, max_length=4_000)
    reason: str = Field(min_length=1, max_length=500)
