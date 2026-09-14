"""Deterministic routing, decomposition, grading, and citation verification."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from app.agent.research.models import (
    CitationLink,
    CitationStatus,
    Claim,
    ClaimDraft,
    EvidenceGrade,
    EvidenceItem,
    EvidenceVerdict,
    RetrievalMode,
    SubQuestion,
)
from app.domain.resources import ResourceCitation

_TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_]+|[\u3400-\u9fff]+")
_NO_RETRIEVAL = re.compile(
    r"^(?:你好|您好|谢谢|感谢|再见|hi|hello|thanks)[!！,.，。?？\s]*$",
    re.IGNORECASE,
)
_MULTI_HOP_MARKERS = (
    "比较",
    "对比",
    "区别",
    "分别",
    "以及",
    "并解释",
    "为什么",
    "如何影响",
    "compare",
    "difference",
    "and explain",
    "why",
)
_INJECTION_PATTERNS = (
    re.compile(r"忽略(?:之前|以上|系统).{0,12}(?:指令|规则|提示)", re.IGNORECASE),
    re.compile(r"ignore (?:all |the )?(?:previous|system) instructions", re.I),
    re.compile(r"(?:system prompt|developer message|tool permission)", re.I),
    re.compile(r"(?:永久|always).{0,10}(?:记住|memory|允许|permission)", re.I),
)


class ResearchPolicy:
    """Code-enforced control policy around untrusted retrieval content."""

    def route(self, question: str) -> RetrievalMode:
        normalized = question.strip()
        if _NO_RETRIEVAL.search(normalized):
            return RetrievalMode.NO_RETRIEVAL
        marker_count = sum(
            marker.casefold() in normalized.casefold() for marker in _MULTI_HOP_MARKERS
        )
        if marker_count or len(normalized) > 120:
            return RetrievalMode.MULTI_STEP_RESEARCH
        return RetrievalMode.SINGLE_RETRIEVAL

    def decompose(self, question: str, mode: RetrievalMode) -> list[SubQuestion]:
        if mode == RetrievalMode.NO_RETRIEVAL:
            return []
        if mode == RetrievalMode.SINGLE_RETRIEVAL:
            return [SubQuestion(id="sq-1", text=question)]

        # Rule-based decomposition is intentionally conservative: every generated
        # SubQuestion remains a substring or explicit facet of the user's request,
        # preventing an LLM planner from silently expanding research scope.
        parts = [
            item.strip(" ，,。？?；;")
            for item in re.split(r"(?:并且|以及|并解释|；|;|。|\?)", question)
            if item.strip(" ，,。？?；;")
        ]
        if len(parts) < 2:
            parts = [question, f"{question} 的适用条件与限制"]
        return [
            SubQuestion(
                id=f"sq-{index}",
                text=part[:1_000],
                depends_on=[] if index == 1 else ["sq-1"],
            )
            for index, part in enumerate(parts[:4], 1)
        ]

    def grade(
        self,
        citation: ResourceCitation,
        *,
        query: str,
        accepted: Iterable[EvidenceItem],
    ) -> EvidenceGrade:
        query_terms = _terms(query)
        evidence_terms = _terms(f"{citation.title} {citation.excerpt}")
        coverage = _overlap(query_terms, evidence_terms, denominator="query")
        relevance = coverage
        source_quality = 0.82 if citation.source_uri is None else 0.68
        duplicate = max(
            (_jaccard(evidence_terms, _terms(item.excerpt)) for item in accepted),
            default=0.0,
        )
        # Injection is evaluated before relevance: a Chunk cannot smuggle an
        # instruction into synthesis merely by also containing relevant keywords.
        if any(pattern.search(citation.excerpt) for pattern in _INJECTION_PATTERNS):
            verdict = EvidenceVerdict.PROMPT_INJECTION
            reason = "content contains instruction-like prompt injection"
        elif relevance < 0.12:
            verdict = EvidenceVerdict.LOW_RELEVANCE
            reason = "lexical relevance is below the evidence gate"
        elif source_quality < 0.5:
            verdict = EvidenceVerdict.LOW_QUALITY
            reason = "source quality is below the evidence gate"
        elif duplicate >= 0.88:
            verdict = EvidenceVerdict.DUPLICATE
            reason = "content duplicates already accepted Evidence"
        else:
            verdict = EvidenceVerdict.ACCEPTED
            reason = "evidence passed relevance, quality, duplication, and safety gates"
        return EvidenceGrade(
            relevance=relevance,
            source_quality=source_quality,
            duplicate_score=duplicate,
            coverage=coverage,
            verdict=verdict,
            reason=reason,
        )

    def rewrite_query(self, subquestion: SubQuestion, round_no: int) -> str:
        suffixes = ("关键概念 原理", "适用条件 限制 例子", "定义 机制 证据")
        suffix = suffixes[min(round_no - 1, len(suffixes) - 1)]
        return f"{subquestion.text} {suffix}"


class CitationVerifier:
    """Verify Claim → Evidence links without trusting model-declared support."""

    def verify(
        self, drafts: list[ClaimDraft], evidence: list[EvidenceItem]
    ) -> tuple[list[Claim], list[CitationLink]]:
        by_id = {item.id: item for item in evidence}
        claims: list[Claim] = []
        citations: list[CitationLink] = []
        for draft in drafts:
            claim = Claim(
                text=draft.text,
                importance=draft.importance,
                citation_status=CitationStatus.UNSUPPORTED,
                included_in_answer=False,
            )
            link_statuses: list[CitationStatus] = []
            draft_terms = _terms(draft.text)
            # evidence_ids are model-proposed foreign keys. Resolve them against the
            # accepted Evidence ledger instead of trusting the structured output.
            for evidence_id in dict.fromkeys(draft.evidence_ids):
                item = by_id.get(evidence_id)
                if item is None or item.grade.verdict != EvidenceVerdict.ACCEPTED:
                    continue
                support = _overlap(
                    draft_terms, _terms(item.excerpt), denominator="query"
                )
                status = (
                    CitationStatus.SUPPORTED
                    if support >= 0.55
                    else CitationStatus.PARTIALLY_SUPPORTED
                    if support >= 0.22
                    else CitationStatus.UNSUPPORTED
                )
                link_statuses.append(status)
                citations.append(
                    CitationLink(
                        claim_id=claim.id,
                        evidence_id=item.id,
                        resource_id=item.resource_id,
                        chunk_id=item.chunk_id,
                        status=status,
                        explanation=(
                            f"deterministic lexical entailment proxy={support:.3f}"
                        ),
                    )
                )
            # One supported link is enough to ground this atomic Claim. With only
            # partial links, the composer must downgrade the language explicitly.
            overall = _aggregate_support(link_statuses)
            claims.append(
                claim.model_copy(
                    update={
                        "citation_status": overall,
                        "included_in_answer": overall
                        in {
                            CitationStatus.SUPPORTED,
                            CitationStatus.PARTIALLY_SUPPORTED,
                        },
                    }
                )
            )
        return claims, citations


def evidence_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _terms(value: str) -> set[str]:
    terms: set[str] = set()
    for token in _TOKEN_PATTERN.findall(value.casefold()):
        if re.fullmatch(r"[\u3400-\u9fff]+", token):
            terms.update(
                token[index : index + 2] for index in range(max(0, len(token) - 1))
            )
        else:
            terms.add(token)
    return terms


def _overlap(
    left: set[str], right: set[str], *, denominator: str = "union"
) -> float:
    if not left or not right:
        return 0.0
    divisor = len(left) if denominator == "query" else len(left | right)
    return len(left & right) / divisor


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _aggregate_support(statuses: list[CitationStatus]) -> CitationStatus:
    if CitationStatus.SUPPORTED in statuses:
        return CitationStatus.SUPPORTED
    if CitationStatus.PARTIALLY_SUPPORTED in statuses:
        return CitationStatus.PARTIALLY_SUPPORTED
    return CitationStatus.UNSUPPORTED
