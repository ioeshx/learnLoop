"""Agent Memory aggregates with explicit provenance and lifecycle state."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


class MemoryKind(StrEnum):
    """Cognitive role of a Memory item, not its storage tier."""

    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"


class MemoryStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    REJECTED = "rejected"
    EXPIRED = "expired"


class MemoryTrust(StrEnum):
    UNTRUSTED = "untrusted"
    USER_ASSERTED = "user_asserted"
    VERIFIED = "verified"
    SYSTEM = "system"


class MemorySensitivity(StrEnum):
    NORMAL = "normal"
    PERSONAL = "personal"
    SENSITIVE = "sensitive"


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """Current materialized view of one governed long-term Memory item.

    ``memory_key`` identifies the logical fact or preference across revisions,
    while ``fingerprint`` identifies an exact normalized payload. Separating the
    two makes deduplication and temporal supersession deterministic.
    """

    id: str
    user_id: str
    kind: MemoryKind
    content: str
    attributes: dict[str, Any]
    memory_key: str
    fingerprint: str
    confidence: float
    importance: float
    status: MemoryStatus
    trust: MemoryTrust
    sensitivity: MemorySensitivity
    requires_approval: bool
    goal_id: str | None
    knowledge_node_id: str | None
    valid_from: datetime
    expires_at: datetime | None
    supersedes_id: str | None
    created_at: datetime
    updated_at: datetime

    def transition(
        self,
        status: MemoryStatus,
        *,
        now: datetime,
        supersedes_id: str | None = None,
    ) -> MemoryRecord:
        return replace(
            self,
            status=status,
            supersedes_id=(
                supersedes_id if supersedes_id is not None else self.supersedes_id
            ),
            updated_at=now,
        )


@dataclass(frozen=True, slots=True)
class MemoryEvidence:
    id: str
    memory_id: str
    source_type: str
    source_id: str
    excerpt: str
    trust: MemoryTrust
    observed_at: datetime
    run_id: str | None = None
    session_id: str | None = None
    attempt_id: str | None = None

    @classmethod
    def create(
        cls,
        *,
        memory_id: str,
        source_type: str,
        source_id: str,
        excerpt: str,
        trust: MemoryTrust,
        observed_at: datetime,
        run_id: str | None = None,
        session_id: str | None = None,
        attempt_id: str | None = None,
    ) -> MemoryEvidence:
        return cls(
            id=str(uuid4()),
            memory_id=memory_id,
            source_type=source_type,
            source_id=source_id,
            excerpt=excerpt,
            trust=trust,
            observed_at=observed_at,
            run_id=run_id,
            session_id=session_id,
            attempt_id=attempt_id,
        )


@dataclass(frozen=True, slots=True)
class MemoryRevision:
    id: str
    memory_id: str
    revision: int
    previous_content: str | None
    new_content: str
    reason: str
    actor: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MemoryAggregate:
    record: MemoryRecord
    evidence: tuple[MemoryEvidence, ...] = field(default_factory=tuple)
    revisions: tuple[MemoryRevision, ...] = field(default_factory=tuple)
