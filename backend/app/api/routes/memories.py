"""Governance and retrieval API for Stage 13 Agent Memory."""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.agent.memory import MemoryCandidate, MemoryQuery, MemoryService
from app.api.dependencies import ApplicationDependenciesDep
from app.application.errors import ConflictError, NotFoundError
from app.application.services import DEFAULT_USER_ID
from app.domain.memory import (
    MemoryAggregate,
    MemoryKind,
    MemoryStatus,
    MemoryTrust,
)

router = APIRouter(prefix="/memories")


class CandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: MemoryKind
    content: str = Field(min_length=1, max_length=4_000)
    memory_key: str = Field(min_length=1, max_length=300)
    attributes: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0, le=1)
    importance: float = Field(default=0.5, ge=0, le=1)
    goal_id: str | None = None
    knowledge_node_id: str | None = None
    expires_at: datetime | None = None


class CorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    content: str = Field(min_length=1, max_length=4_000)
    reason: str = Field(min_length=1, max_length=500)


class MemoryResponse(BaseModel):
    id: str
    user_id: str
    kind: MemoryKind
    content: str
    attributes: dict[str, Any]
    memory_key: str
    confidence: float
    importance: float
    status: MemoryStatus
    trust: MemoryTrust
    sensitivity: str
    requires_approval: bool
    goal_id: str | None
    knowledge_node_id: str | None
    valid_from: datetime
    expires_at: datetime | None
    supersedes_id: str | None
    evidence: list[dict[str, Any]]
    revisions: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, aggregate: MemoryAggregate) -> "MemoryResponse":
        record = aggregate.record
        return cls(
            id=record.id,
            user_id=record.user_id,
            kind=record.kind,
            content=record.content,
            attributes=record.attributes,
            memory_key=record.memory_key,
            confidence=record.confidence,
            importance=record.importance,
            status=record.status,
            trust=record.trust,
            sensitivity=record.sensitivity.value,
            requires_approval=record.requires_approval,
            goal_id=record.goal_id,
            knowledge_node_id=record.knowledge_node_id,
            valid_from=record.valid_from,
            expires_at=record.expires_at,
            supersedes_id=record.supersedes_id,
            evidence=[
                {
                    "id": item.id,
                    "source_type": item.source_type,
                    "source_id": item.source_id,
                    "excerpt": item.excerpt,
                    "trust": item.trust,
                    "observed_at": item.observed_at,
                    "run_id": item.run_id,
                    "session_id": item.session_id,
                    "attempt_id": item.attempt_id,
                }
                for item in aggregate.evidence
            ],
            revisions=[
                {
                    "id": item.id,
                    "revision": item.revision,
                    "previous_content": item.previous_content,
                    "new_content": item.new_content,
                    "reason": item.reason,
                    "actor": item.actor,
                    "created_at": item.created_at,
                }
                for item in aggregate.revisions
            ],
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class RecallResponse(BaseModel):
    memory_id: str
    memory_key: str
    kind: MemoryKind
    content: str
    attributes: dict[str, Any]
    score: float
    confidence: float
    importance: float
    trust: MemoryTrust
    goal_id: str | None
    knowledge_node_id: str | None
    valid_from: datetime
    expires_at: datetime | None
    evidence: list[dict[str, Any]]
    conflicting_memory_ids: list[str]


def _service(dependencies: ApplicationDependenciesDep) -> MemoryService:
    return MemoryService(dependencies.uow_factory, clock=dependencies.clock)


@router.get("", response_model=list[MemoryResponse])
async def list_memories(
    dependencies: ApplicationDependenciesDep,
    memory_status: MemoryStatus | None = None,
    kind: MemoryKind | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[MemoryResponse]:
    items = await _service(dependencies).list_for_user(
        DEFAULT_USER_ID, status=memory_status, kind=kind, limit=limit
    )
    return [MemoryResponse.from_domain(item) for item in items]


@router.get("/search", response_model=list[RecallResponse])
async def search_memories(
    dependencies: ApplicationDependenciesDep,
    query: Annotated[str, Query(min_length=1, max_length=2_000)],
    goal_id: str | None = None,
    knowledge_node_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 6,
) -> list[RecallResponse]:
    recalls = await _service(dependencies).retrieve(
        MemoryQuery(
            user_id=DEFAULT_USER_ID,
            text=query,
            goal_id=goal_id,
            knowledge_node_id=knowledge_node_id,
            limit=limit,
        )
    )
    return [RecallResponse(**item.model_dump()) for item in recalls]


@router.post("/candidates", response_model=MemoryResponse, status_code=201)
async def create_memory_candidate(
    payload: CandidateRequest, dependencies: ApplicationDependenciesDep
) -> MemoryResponse:
    now = datetime.now(UTC)
    outcome = await _service(dependencies).ingest(
        MemoryCandidate(
            user_id=DEFAULT_USER_ID,
            kind=payload.kind,
            content=payload.content,
            attributes=payload.attributes,
            memory_key=payload.memory_key,
            confidence=payload.confidence,
            importance=payload.importance,
            trust=MemoryTrust.USER_ASSERTED,
            source_type="user_input",
            source_id="memory_governance_ui",
            source_excerpt=payload.content,
            lifecycle_event="explicit_user",
            goal_id=payload.goal_id,
            knowledge_node_id=payload.knowledge_node_id,
            valid_from=now,
            expires_at=payload.expires_at,
        )
    )
    return MemoryResponse.from_domain(outcome.aggregate)


@router.get("/{memory_id}", response_model=MemoryResponse)
async def get_memory(
    memory_id: str, dependencies: ApplicationDependenciesDep
) -> MemoryResponse:
    aggregate = await _service(dependencies).get(memory_id)
    if aggregate is None:
        raise NotFoundError("memory", memory_id)
    return MemoryResponse.from_domain(aggregate)


@router.post("/{memory_id}/approve", response_model=MemoryResponse)
async def approve_memory(
    memory_id: str, dependencies: ApplicationDependenciesDep
) -> MemoryResponse:
    try:
        aggregate = await _service(dependencies).approve(memory_id)
    except LookupError as error:
        raise NotFoundError("memory", memory_id) from error
    except ValueError as error:
        raise ConflictError(str(error)) from error
    return MemoryResponse.from_domain(aggregate)


@router.post("/{memory_id}/reject", response_model=MemoryResponse)
async def reject_memory(
    memory_id: str, dependencies: ApplicationDependenciesDep
) -> MemoryResponse:
    try:
        aggregate = await _service(dependencies).reject(memory_id)
    except LookupError as error:
        raise NotFoundError("memory", memory_id) from error
    return MemoryResponse.from_domain(aggregate)


@router.post("/{memory_id}/deactivate", response_model=MemoryResponse)
async def deactivate_memory(
    memory_id: str, dependencies: ApplicationDependenciesDep
) -> MemoryResponse:
    try:
        aggregate = await _service(dependencies).deactivate(memory_id)
    except LookupError as error:
        raise NotFoundError("memory", memory_id) from error
    return MemoryResponse.from_domain(aggregate)


@router.patch("/{memory_id}", response_model=MemoryResponse)
async def correct_memory(
    memory_id: str,
    payload: CorrectionRequest,
    dependencies: ApplicationDependenciesDep,
) -> MemoryResponse:
    try:
        outcome = await _service(dependencies).correct(
            memory_id, content=payload.content, reason=payload.reason
        )
    except LookupError as error:
        raise NotFoundError("memory", memory_id) from error
    return MemoryResponse.from_domain(outcome.aggregate)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: str, dependencies: ApplicationDependenciesDep
) -> Response:
    if not await _service(dependencies).delete(memory_id):
        raise NotFoundError("memory", memory_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
