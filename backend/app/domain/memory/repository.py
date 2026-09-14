"""Persistence port for governed Agent Memory."""

from datetime import datetime
from typing import Protocol

from app.domain.memory.models import (
    MemoryAggregate,
    MemoryEvidence,
    MemoryKind,
    MemoryRecord,
    MemoryRevision,
    MemoryStatus,
)


class MemoryRepository(Protocol):
    async def add(
        self,
        record: MemoryRecord,
        evidence: MemoryEvidence,
        revision: MemoryRevision,
    ) -> None: ...

    async def get(self, memory_id: str) -> MemoryAggregate | None: ...

    async def list_for_user(
        self,
        user_id: str,
        *,
        status: MemoryStatus | None = None,
        kind: MemoryKind | None = None,
        limit: int = 100,
    ) -> list[MemoryAggregate]: ...

    async def find_by_fingerprint(
        self, user_id: str, fingerprint: str
    ) -> MemoryAggregate | None: ...

    async def find_active_by_key(
        self, user_id: str, memory_key: str
    ) -> MemoryAggregate | None: ...

    async def update(self, record: MemoryRecord) -> None: ...

    async def add_evidence(self, evidence: MemoryEvidence) -> None: ...

    async def add_revision(self, revision: MemoryRevision) -> None: ...

    async def delete(self, memory_id: str) -> bool: ...

    async def expire_due(self, user_id: str, now: datetime) -> int: ...
