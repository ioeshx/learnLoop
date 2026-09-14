"""Trust-aware ingestion, lifecycle governance, and retrieval for Agent Memory."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from math import exp
from uuid import uuid4

from app.agent.memory.models import (
    MemoryCandidate,
    MemoryQuery,
    MemoryRecall,
    MemoryWriteOutcome,
)
from app.application.ports import UnitOfWorkFactory
from app.domain.memory import (
    MemoryAggregate,
    MemoryEvidence,
    MemoryKind,
    MemoryRecord,
    MemoryRevision,
    MemorySensitivity,
    MemoryStatus,
    MemoryTrust,
)

_MINIMUM_WRITE_CONFIDENCE = 0.65
_AUTO_ACTIVATE_CONFIDENCE = 0.80
_TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_]+|[\u3400-\u9fff]+")
_SENSITIVE_PATTERNS = (
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"),
    re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    re.compile(r"(?i)\b(?:password|api[_ -]?key|secret|token)\s*[:=]"),
)


class MemoryService:
    """Policy Enforcement Point for every durable Memory mutation.

    The service deliberately keeps candidate extraction separate from activation.
    Model or Tool text may propose a candidate, but only this deterministic pipeline
    may cross the durable Memory trust boundary. This is the Memory equivalent of
    the Agent kernel's guarded ``ToolExecutor``.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or _utc_now

    async def ingest(self, candidate: MemoryCandidate) -> MemoryWriteOutcome:
        """Run sensitivity → trust → dedup → conflict → approval in fixed order."""

        now = _as_utc(candidate.valid_from)
        normalized = _normalize(candidate.content)
        fingerprint = _fingerprint(candidate.kind, candidate.memory_key, normalized)
        sensitivity = _classify_sensitivity(candidate.content)
        rejection = _rejection_reason(candidate, sensitivity)
        requires_approval = _requires_approval(candidate, sensitivity)

        async with self._uow_factory() as uow:
            duplicate = await uow.memories.find_by_fingerprint(
                candidate.user_id, fingerprint
            )
            if duplicate is not None:
                evidence = _evidence(candidate, duplicate.record.id)
                await uow.memories.add_evidence(evidence)
                merged = replace(
                    duplicate.record,
                    confidence=max(duplicate.record.confidence, candidate.confidence),
                    importance=max(duplicate.record.importance, candidate.importance),
                    updated_at=now,
                )
                await uow.memories.update(merged)
                await uow.commit()
                refreshed = await self.get(merged.id)
                assert refreshed is not None
                return MemoryWriteOutcome(
                    action="merged",
                    aggregate=refreshed,
                    reason="exact normalized payload already exists",
                )

            current = await uow.memories.find_active_by_key(
                candidate.user_id, candidate.memory_key
            )
            status = (
                MemoryStatus.REJECTED
                if rejection is not None
                else MemoryStatus.CANDIDATE
                if requires_approval
                else MemoryStatus.ACTIVE
            )
            record = MemoryRecord(
                id=str(uuid4()),
                user_id=candidate.user_id,
                kind=candidate.kind,
                content=candidate.content,
                attributes=dict(candidate.attributes),
                memory_key=candidate.memory_key,
                fingerprint=fingerprint,
                confidence=candidate.confidence,
                importance=candidate.importance,
                status=status,
                trust=candidate.trust,
                sensitivity=sensitivity,
                requires_approval=requires_approval,
                goal_id=candidate.goal_id,
                knowledge_node_id=candidate.knowledge_node_id,
                valid_from=now,
                expires_at=candidate.expires_at,
                supersedes_id=current.record.id if current is not None else None,
                created_at=now,
                updated_at=now,
            )
            revision = MemoryRevision(
                id=str(uuid4()),
                memory_id=record.id,
                revision=1,
                previous_content=current.record.content if current else None,
                new_content=record.content,
                reason=(rejection or "candidate extracted at a lifecycle boundary"),
                actor="memory_policy",
                created_at=now,
            )
            await uow.memories.add(record, _evidence(candidate, record.id), revision)
            # A low-impact trusted replacement can supersede immediately. High-impact
            # candidates leave the old active row untouched until explicit approval.
            if status == MemoryStatus.ACTIVE and current is not None:
                await uow.memories.update(
                    current.record.transition(MemoryStatus.EXPIRED, now=now)
                )
            await uow.commit()

        aggregate = await self.get(record.id)
        assert aggregate is not None
        return MemoryWriteOutcome(
            action="rejected" if rejection else "created",
            aggregate=aggregate,
            reason=rejection or (
                "explicit approval required"
                if requires_approval
                else "trusted candidate activated"
            ),
        )

    async def capture_run_outcome(
        self,
        *,
        user_id: str,
        run_id: str,
        session_id: str,
        goal_id: str | None,
        knowledge_node_id: str | None,
        objective: str,
        summary: str | None,
        completed_step_ids: list[str],
        now: datetime | None = None,
    ) -> MemoryWriteOutcome:
        """Extract only a verified Episodic Memory when a Run reaches completion.

        Raw Tool output and arbitrary user text are intentionally not summarized into
        Semantic or Procedural Memory here. They remain evidence in the Run Trace and
        require an explicit, separately governed candidate before persistence.
        """

        observed_at = now or self._clock()
        content = summary or (
            f"完成了“{objective}”，验证通过的步骤：{', '.join(completed_step_ids)}。"
        )
        return await self.ingest(
            MemoryCandidate(
                user_id=user_id,
                kind=MemoryKind.EPISODIC,
                content=content,
                attributes={
                    "objective": objective,
                    "completed_step_ids": completed_step_ids,
                    "authoritative_domain_state": False,
                },
                memory_key=f"session:{session_id}:verified-outcome",
                confidence=0.95,
                importance=0.6,
                trust=MemoryTrust.VERIFIED,
                source_type="agent_run",
                source_id=run_id,
                source_excerpt=content,
                lifecycle_event="run_completed",
                goal_id=goal_id,
                knowledge_node_id=knowledge_node_id,
                run_id=run_id,
                session_id=session_id,
                valid_from=observed_at,
            )
        )

    async def get(self, memory_id: str) -> MemoryAggregate | None:
        async with self._uow_factory() as uow:
            return await uow.memories.get(memory_id)

    async def list_for_user(
        self,
        user_id: str,
        *,
        status: MemoryStatus | None = None,
        kind: MemoryKind | None = None,
        limit: int = 100,
    ) -> list[MemoryAggregate]:
        async with self._uow_factory() as uow:
            await uow.memories.expire_due(user_id, self._clock())
            await uow.commit()
            return await uow.memories.list_for_user(
                user_id, status=status, kind=kind, limit=limit
            )

    async def approve(self, memory_id: str, *, actor: str = "user") -> MemoryAggregate:
        now = self._clock()
        async with self._uow_factory() as uow:
            aggregate = await uow.memories.get(memory_id)
            if aggregate is None:
                raise LookupError(memory_id)
            if aggregate.record.status != MemoryStatus.CANDIDATE:
                raise ValueError("only candidate Memory can be approved")
            if aggregate.record.supersedes_id:
                superseded = await uow.memories.get(aggregate.record.supersedes_id)
                if superseded and superseded.record.status == MemoryStatus.ACTIVE:
                    await uow.memories.update(
                        superseded.record.transition(MemoryStatus.EXPIRED, now=now)
                    )
            activated = aggregate.record.transition(MemoryStatus.ACTIVE, now=now)
            await uow.memories.update(activated)
            await uow.memories.add_revision(
                MemoryRevision(
                    id=str(uuid4()),
                    memory_id=memory_id,
                    revision=len(aggregate.revisions) + 1,
                    previous_content=activated.content,
                    new_content=activated.content,
                    reason="candidate approved",
                    actor=actor,
                    created_at=now,
                )
            )
            await uow.commit()
        result = await self.get(memory_id)
        assert result is not None
        return result

    async def reject(self, memory_id: str, *, actor: str = "user") -> MemoryAggregate:
        return await self._transition(memory_id, MemoryStatus.REJECTED, actor=actor)

    async def deactivate(
        self, memory_id: str, *, actor: str = "user"
    ) -> MemoryAggregate:
        return await self._transition(memory_id, MemoryStatus.EXPIRED, actor=actor)

    async def _transition(
        self, memory_id: str, status: MemoryStatus, *, actor: str
    ) -> MemoryAggregate:
        now = self._clock()
        async with self._uow_factory() as uow:
            aggregate = await uow.memories.get(memory_id)
            if aggregate is None:
                raise LookupError(memory_id)
            changed = aggregate.record.transition(status, now=now)
            await uow.memories.update(changed)
            await uow.memories.add_revision(
                MemoryRevision(
                    id=str(uuid4()),
                    memory_id=memory_id,
                    revision=len(aggregate.revisions) + 1,
                    previous_content=changed.content,
                    new_content=changed.content,
                    reason=f"status changed to {status.value}",
                    actor=actor,
                    created_at=now,
                )
            )
            await uow.commit()
        result = await self.get(memory_id)
        assert result is not None
        return result

    async def correct(
        self, memory_id: str, *, content: str, reason: str
    ) -> MemoryWriteOutcome:
        existing = await self.get(memory_id)
        if existing is None:
            raise LookupError(memory_id)
        record = existing.record
        return await self.ingest(
            MemoryCandidate(
                user_id=record.user_id,
                kind=record.kind,
                content=content,
                attributes={**record.attributes, "correction_reason": reason},
                memory_key=record.memory_key,
                confidence=1.0,
                importance=record.importance,
                trust=MemoryTrust.USER_ASSERTED,
                source_type="user_correction",
                source_id=memory_id,
                source_excerpt=content,
                lifecycle_event="explicit_user",
                goal_id=record.goal_id,
                knowledge_node_id=record.knowledge_node_id,
                valid_from=self._clock(),
            )
        )

    async def delete(self, memory_id: str) -> bool:
        async with self._uow_factory() as uow:
            deleted = await uow.memories.delete(memory_id)
            await uow.commit()
            return deleted

    async def retrieve(self, query: MemoryQuery) -> list[MemoryRecall]:
        """Apply scoped candidate filtering before deterministic hybrid reranking.

        SQLite remains the source of truth. Lexical overlap supplies the first-stage
        recall signal; confidence, importance, trust, recency, and current scope form
        the second-stage ranking score. A threshold enforces explicit abstention.
        """

        now = self._clock()
        aggregates = await self.list_for_user(
            query.user_id, status=MemoryStatus.ACTIVE, limit=500
        )
        scoped = [item for item in aggregates if _scope_matches(item, query)]
        active_by_key: dict[str, list[MemoryAggregate]] = {}
        for item in scoped:
            active_by_key.setdefault(item.record.memory_key, []).append(item)

        recalls: list[MemoryRecall] = []
        for aggregate in scoped:
            record = aggregate.record
            if query.kinds and record.kind not in query.kinds:
                continue
            relevance = _lexical_relevance(
                query.text,
                " ".join(
                    [record.content, record.memory_key, _stable_json(record.attributes)]
                ),
            )
            # Quality priors may rerank a relevant candidate, but must never create
            # relevance from nothing. This gate is the explicit no-answer policy.
            if relevance == 0:
                continue
            scope_bonus = (
                1.0 if record.knowledge_node_id else 0.7 if record.goal_id else 0.4
            )
            age_days = max(0.0, (now - record.valid_from).total_seconds() / 86_400)
            recency = exp(-age_days / 180)
            trust_score = {
                MemoryTrust.SYSTEM: 1.0,
                MemoryTrust.VERIFIED: 0.95,
                MemoryTrust.USER_ASSERTED: 0.85,
                MemoryTrust.UNTRUSTED: 0.0,
            }[record.trust]
            score = min(
                1.0,
                0.55 * relevance
                + 0.14 * record.confidence
                + 0.12 * record.importance
                + 0.10 * trust_score
                + 0.05 * recency
                + 0.04 * scope_bonus,
            )
            if score < query.minimum_score:
                continue
            conflicts = [
                item.record.id
                for item in active_by_key[record.memory_key]
                if item.record.id != record.id
                and item.record.fingerprint != record.fingerprint
            ]
            recalls.append(
                MemoryRecall(
                    memory_id=record.id,
                    memory_key=record.memory_key,
                    kind=record.kind,
                    content=record.content,
                    attributes=record.attributes,
                    score=score,
                    confidence=record.confidence,
                    importance=record.importance,
                    trust=record.trust,
                    goal_id=record.goal_id,
                    knowledge_node_id=record.knowledge_node_id,
                    valid_from=record.valid_from,
                    expires_at=record.expires_at,
                    evidence=[
                        {
                            "evidence_id": evidence.id,
                            "source_type": evidence.source_type,
                            "source_id": evidence.source_id,
                            "trust": evidence.trust,
                            "observed_at": evidence.observed_at,
                            "run_id": evidence.run_id,
                            "session_id": evidence.session_id,
                        }
                        for evidence in aggregate.evidence
                    ],
                    conflicting_memory_ids=conflicts,
                )
            )
        recalls.sort(
            key=lambda item: (item.score, item.valid_from, item.memory_id), reverse=True
        )
        return recalls[: query.limit]


def _rejection_reason(
    candidate: MemoryCandidate, sensitivity: MemorySensitivity
) -> str | None:
    if candidate.kind == MemoryKind.WORKING:
        return "Working Memory belongs to durable Run state, not long-term storage"
    if candidate.confidence < _MINIMUM_WRITE_CONFIDENCE:
        return "candidate confidence is below the persistence threshold"
    if (
        candidate.trust == MemoryTrust.UNTRUSTED
        and candidate.kind in {MemoryKind.SEMANTIC, MemoryKind.PROCEDURAL}
    ):
        return "untrusted content cannot become Semantic or Procedural Memory"
    if sensitivity == MemorySensitivity.SENSITIVE:
        return "PII or secret-like content is blocked from durable Memory"
    return None


def _requires_approval(
    candidate: MemoryCandidate, sensitivity: MemorySensitivity
) -> bool:
    high_impact = candidate.kind in {MemoryKind.SEMANTIC, MemoryKind.PROCEDURAL}
    high_impact = high_impact or bool(candidate.attributes.get("profile_change"))
    return (
        high_impact
        or sensitivity != MemorySensitivity.NORMAL
        or candidate.trust == MemoryTrust.USER_ASSERTED
        or candidate.confidence < _AUTO_ACTIVATE_CONFIDENCE
    )


def _evidence(candidate: MemoryCandidate, memory_id: str) -> MemoryEvidence:
    return MemoryEvidence.create(
        memory_id=memory_id,
        source_type=candidate.source_type,
        source_id=candidate.source_id,
        excerpt=candidate.source_excerpt,
        trust=candidate.trust,
        observed_at=candidate.valid_from,
        run_id=candidate.run_id,
        session_id=candidate.session_id,
        attempt_id=candidate.attempt_id,
    )


def _scope_matches(aggregate: MemoryAggregate, query: MemoryQuery) -> bool:
    record = aggregate.record
    goal_matches = record.goal_id is None or record.goal_id == query.goal_id
    node_matches = (
        record.knowledge_node_id is None
        or record.knowledge_node_id == query.knowledge_node_id
    )
    return goal_matches and node_matches


def _lexical_relevance(query: str, document: str) -> float:
    query_terms = _lexical_terms(query)
    document_terms = _lexical_terms(document)
    if not query_terms or not document_terms:
        return 0.0
    overlap = len(query_terms & document_terms)
    return overlap / len(query_terms)


def _lexical_terms(value: str) -> set[str]:
    """Tokenize Latin words and CJK bigrams without a language-specific package.

    Single CJK characters create severe false positives (for example the common
    particle “的”). Character bigrams preserve deterministic local retrieval while
    giving the abstention threshold a meaningful lexical signal.
    """

    terms: set[str] = set()
    for token in _TOKEN_PATTERN.findall(value.casefold()):
        if re.fullmatch(r"[\u3400-\u9fff]+", token):
            terms.update(
                token[index : index + 2] for index in range(max(0, len(token) - 1))
            )
        else:
            terms.add(token)
    return terms


def _classify_sensitivity(content: str) -> MemorySensitivity:
    if any(pattern.search(content) for pattern in _SENSITIVE_PATTERNS):
        return MemorySensitivity.SENSITIVE
    return MemorySensitivity.NORMAL


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _fingerprint(kind: MemoryKind, memory_key: str, normalized: str) -> str:
    value = f"{kind.value}\0{memory_key.casefold()}\0{normalized}"
    return hashlib.sha256(value.encode()).hexdigest()


def _stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)
