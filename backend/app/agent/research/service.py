"""Budgeted gap-driven Agentic RAG orchestration."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from math import ceil
from typing import Literal, Protocol

from app.agent.research.models import (
    CitationLink,
    CitationStatus,
    Claim,
    ClaimDraft,
    EvidenceItem,
    EvidenceVerdict,
    ResearchBudget,
    ResearchQuery,
    ResearchRequest,
    ResearchResult,
    ResearchTrace,
    ResearchUsage,
    RetrievalMode,
    SubQuestion,
)
from app.agent.research.policy import CitationVerifier, ResearchPolicy, evidence_hash
from app.application.services import DEFAULT_USER_ID
from app.infrastructure.rag import RagService


class ResearchSynthesizer(Protocol):
    async def propose_claims(
        self,
        request: ResearchRequest,
        subquestions: list[SubQuestion],
        evidence: list[EvidenceItem],
    ) -> list[ClaimDraft]: ...


class ResearchTraceStore(Protocol):
    async def save(self, trace: ResearchTrace) -> None: ...

    async def get(self, trace_id: str) -> ResearchTrace | None: ...

    async def list_for_user(
        self, user_id: str, *, goal_id: str | None = None, limit: int = 50
    ) -> list[ResearchTrace]: ...


class ExtractiveResearchSynthesizer:
    """Offline fallback that can only copy bounded sentences from Evidence."""

    async def propose_claims(
        self,
        request: ResearchRequest,
        subquestions: list[SubQuestion],
        evidence: list[EvidenceItem],
    ) -> list[ClaimDraft]:
        del request
        drafts: list[ClaimDraft] = []
        for subquestion in subquestions:
            candidate = next(
                (
                    item
                    for item in evidence
                    if item.subquestion_id == subquestion.id
                    and item.grade.verdict == EvidenceVerdict.ACCEPTED
                ),
                None,
            )
            if candidate is None:
                continue
            sentence = _first_sentence(candidate.excerpt)
            drafts.append(
                ClaimDraft(
                    text=sentence,
                    importance="critical",
                    evidence_ids=[candidate.id],
                )
            )
        return drafts


class ResearchTutor:
    """Bounded single-Agent research loop over the learner's local corpus.

    The loop owns routing, query uniqueness, budgets, Evidence grading, gap checks,
    synthesis and citation verification. Retrieved text never becomes an instruction:
    it is always wrapped as ``trust=untrusted`` Evidence and is not exposed to Tool,
    Memory, or policy mutation paths.
    """

    def __init__(
        self,
        *,
        rag: RagService,
        store: ResearchTraceStore,
        policy: ResearchPolicy | None = None,
        verifier: CitationVerifier | None = None,
        synthesizer: ResearchSynthesizer | None = None,
        clock: Callable[[], datetime] | None = None,
        user_id: str = DEFAULT_USER_ID,
        default_budget: ResearchBudget | None = None,
    ) -> None:
        self.rag = rag
        self.store = store
        self.policy = policy or ResearchPolicy()
        self.verifier = verifier or CitationVerifier()
        self.synthesizer = synthesizer or ExtractiveResearchSynthesizer()
        self.clock = clock or _utc_now
        self.user_id = user_id
        self.default_budget = default_budget or ResearchBudget()

    def request_for(
        self,
        question: str,
        *,
        goal_id: str,
        knowledge_node_id: str | None = None,
        mode_override: RetrievalMode | None = None,
    ) -> ResearchRequest:
        return ResearchRequest(
            question=question,
            goal_id=goal_id,
            knowledge_node_id=knowledge_node_id,
            mode_override=mode_override,
            budget=self.default_budget,
        )

    async def run(self, request: ResearchRequest) -> ResearchResult:
        """Execute ``retrieve → grade → gap → rewrite`` under hard budgets."""

        started_at = self.clock()
        await self.rag.validate_scope(request.goal_id, request.knowledge_node_id)
        mode = request.mode_override or self.policy.route(request.question)
        subquestions = self.policy.decompose(request.question, mode)
        queries: list[ResearchQuery] = []
        evidence: list[EvidenceItem] = []
        seen_queries: set[str] = set()
        usage = ResearchUsage()

        # NO_RETRIEVAL is a first-class route: returning before RagService.search()
        # makes the zero-cost invariant observable rather than a Prompt convention.
        if mode == RetrievalMode.NO_RETRIEVAL:
            trace = ResearchTrace(
                user_id=self.user_id,
                request=request,
                mode=mode,
                subquestions=[],
                queries=[],
                evidence=[],
                claims=[],
                citations=[],
                gaps=[],
                usage=usage,
                status="completed",
                answer="该请求无需访问本地资料，请使用常规 Tutor 对话直接处理。",
                created_at=started_at,
                completed_at=self.clock(),
            )
            await self.store.save(trace)
            return _result(trace)

        parent_query_by_subquestion: dict[str, str] = {}
        stop_reason: str | None = None
        # Each round visits only unresolved SubQuestions. parent_query_id preserves
        # the rewrite lineage, while seen_queries prevents cyclic Agent behavior.
        for round_no in range(1, request.budget.max_rounds + 1):
            uncovered = _uncovered_subquestions(subquestions, evidence)
            if not uncovered:
                break
            round_used = False
            for subquestion in uncovered:
                if len(queries) >= request.budget.max_queries:
                    stop_reason = "max_queries"
                    break
                query_text = (
                    subquestion.text
                    if round_no == 1
                    else self.policy.rewrite_query(subquestion, round_no - 1)
                )
                signature = " ".join(query_text.casefold().split())
                if signature in seen_queries:
                    continue
                seen_queries.add(signature)
                query = ResearchQuery(
                    subquestion_id=subquestion.id,
                    text=query_text,
                    round_no=round_no,
                    parent_query_id=parent_query_by_subquestion.get(subquestion.id),
                    created_at=self.clock(),
                )
                queries.append(query)
                parent_query_by_subquestion[subquestion.id] = query.id
                round_used = True
                if len(evidence) >= request.budget.max_sources:
                    stop_reason = "max_sources"
                    break
                citations = await self.rag.search(
                    query.text,
                    goal_id=request.goal_id,
                    knowledge_node_id=request.knowledge_node_id,
                    limit=min(6, request.budget.max_sources - len(evidence)),
                )
                for citation in citations:
                    # Budget checks happen before materializing a Chunk as Evidence.
                    # The model can observe Usage but cannot grant itself more budget.
                    if len(evidence) >= request.budget.max_sources:
                        stop_reason = "max_sources"
                        break
                    remaining_chars = request.budget.max_read_chars - sum(
                        len(item.excerpt) for item in evidence
                    )
                    if remaining_chars <= 0:
                        stop_reason = "max_read_chars"
                        break
                    excerpt = citation.excerpt[:remaining_chars]
                    projected_tokens = ceil(
                        (sum(len(item.excerpt) for item in evidence) + len(excerpt))
                        / 3
                    )
                    if projected_tokens > request.budget.max_context_tokens:
                        stop_reason = "max_context_tokens"
                        break
                    resource = await self.rag.get(citation.resource_id)
                    grade = self.policy.grade(
                        citation,
                        query=query.text,
                        accepted=[
                            item
                            for item in evidence
                            if item.grade.verdict == EvidenceVerdict.ACCEPTED
                        ],
                    )
                    evidence.append(
                        EvidenceItem(
                            query_id=query.id,
                            subquestion_id=subquestion.id,
                            resource_id=citation.resource_id,
                            chunk_id=citation.chunk_id,
                            title=citation.title,
                            excerpt=excerpt,
                            page_number=citation.page_number,
                            section=citation.section,
                            source_uri=citation.source_uri,
                            resource_version=resource.updated_at.isoformat(),
                            resource_sha256=resource.sha256,
                            content_sha256=evidence_hash(excerpt),
                            grade=grade,
                        )
                    )
                if stop_reason:
                    break
            usage = ResearchUsage(
                rounds=round_no,
                queries=len(queries),
                sources=len(evidence),
                read_chars=sum(len(item.excerpt) for item in evidence),
                estimated_tokens=ceil(
                    sum(len(item.excerpt) for item in evidence) / 3
                ),
                stopped_reason=stop_reason,
            )
            if stop_reason or not round_used:
                break

        gaps = [item.text for item in _uncovered_subquestions(subquestions, evidence)]
        if gaps and usage.stopped_reason is None:
            # Exhausting the for-loop is a normal bounded termination, not an
            # exception. Record it explicitly so every incomplete Trace explains
            # whether research ran out of rounds or could not generate a new Query.
            usage = usage.model_copy(
                update={
                    "stopped_reason": (
                        "max_rounds"
                        if usage.rounds >= request.budget.max_rounds
                        else "no_new_queries"
                    )
                }
            )
        accepted = [
            item for item in evidence if item.grade.verdict == EvidenceVerdict.ACCEPTED
        ]
        # Synthesis creates candidates only. Final answer admission remains owned by
        # an independent CitationVerifier operating on the complete Evidence graph.
        drafts = await self.synthesizer.propose_claims(
            request, subquestions, accepted
        )
        claims, citation_links = self.verifier.verify(drafts, evidence)
        answer = _compose_answer(claims, citation_links, evidence, gaps)
        included_claims = [item for item in claims if item.included_in_answer]
        status: Literal["completed", "insufficient_evidence", "failed"] = (
            "completed" if included_claims and not gaps else "insufficient_evidence"
        )
        trace = ResearchTrace(
            user_id=self.user_id,
            request=request,
            mode=mode,
            subquestions=subquestions,
            queries=queries,
            evidence=evidence,
            claims=claims,
            citations=citation_links,
            gaps=gaps,
            usage=usage,
            status=status,
            answer=answer,
            created_at=started_at,
            completed_at=self.clock(),
        )
        await self.store.save(trace)
        return _result(trace)

    async def get(self, trace_id: str) -> ResearchTrace | None:
        return await self.store.get(trace_id)

    async def list(
        self, *, goal_id: str | None = None, limit: int = 50
    ) -> list[ResearchTrace]:
        return await self.store.list_for_user(
            self.user_id, goal_id=goal_id, limit=limit
        )


def _uncovered_subquestions(
    subquestions: list[SubQuestion], evidence: list[EvidenceItem]
) -> list[SubQuestion]:
    covered = {
        item.subquestion_id
        for item in evidence
        if item.grade.verdict == EvidenceVerdict.ACCEPTED
        and item.grade.coverage >= 0.12
    }
    return [item for item in subquestions if item.id not in covered]


def _compose_answer(
    claims: list[Claim],
    citations: list[CitationLink],
    evidence: list[EvidenceItem],
    gaps: list[str],
) -> str:
    evidence_by_id = {item.id: item for item in evidence}
    lines: list[str] = []
    source_numbers: dict[str, int] = {}
    for claim in claims:
        if not claim.included_in_answer:
            continue
        links = [
            item
            for item in citations
            if item.claim_id == claim.id
            and item.status != CitationStatus.UNSUPPORTED
        ]
        markers: list[str] = []
        for link in links:
            evidence_item = evidence_by_id.get(link.evidence_id)
            if evidence_item is None:
                continue
            number = source_numbers.setdefault(
                evidence_item.id, len(source_numbers) + 1
            )
            markers.append(f"[{number}]")
        prefix = (
            "根据现有资料，可能："
            if claim.citation_status == CitationStatus.PARTIALLY_SUPPORTED
            else ""
        )
        lines.append(f"{prefix}{claim.text} {' '.join(markers)}".strip())
    if gaps:
        lines.append(
            "证据不足，尚无法确认："
            + "；".join(gaps)
            + "。请在资料库补充相关材料后重试。"
        )
    if not lines:
        lines.append("本地资料中没有达到质量门槛的证据，无法给出可靠结论。")
    if source_numbers:
        lines.append("\n来源：")
        for evidence_id, number in source_numbers.items():
            item = evidence_by_id[evidence_id]
            locator = item.section or (
                f"第 {item.page_number} 页" if item.page_number else item.chunk_id
            )
            lines.append(f"[{number}] {item.title} · {locator}")
    return "\n".join(lines)


def _first_sentence(value: str) -> str:
    for delimiter in ("。", "！", "？", ". ", "! ", "? "):
        if delimiter in value:
            return value.split(delimiter, 1)[0].strip() + delimiter.strip()
    return value[:600].strip()


def _result(trace: ResearchTrace) -> ResearchResult:
    return ResearchResult(
        trace_id=trace.id,
        mode=trace.mode,
        status=trace.status,
        answer=trace.answer,
        claims=trace.claims,
        citations=trace.citations,
        evidence=trace.evidence,
        gaps=trace.gaps,
        usage=trace.usage,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)
