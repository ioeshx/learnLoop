"""Serial, scoped, and cancellable Subagent-as-Tool orchestration."""

from __future__ import annotations

import asyncio
import hashlib
from collections import defaultdict
from datetime import UTC, datetime
from time import perf_counter

from app.agent.delegation.models import (
    DelegatedCitation,
    DelegatedClaim,
    DelegatedEvidence,
    DelegationBudget,
    DelegationRecord,
    DelegationRequest,
    DelegationResult,
    DelegationStatus,
    DelegationUsage,
    SubagentRole,
)
from app.agent.dynamic.models import DynamicAgentState
from app.agent.execution.models import AgentRun, EventKind, RunStatus, TerminalReason
from app.agent.execution.store import SqliteAgentRunStore
from app.agent.research import ResearchTutor
from app.agent.research.models import (
    CitationStatus,
    EvidenceVerdict,
    ResearchBudget,
    ResearchResult,
    ResearchTrace,
    RetrievalMode,
)
from app.agent.research.policy import ResearchPolicy
from app.observability import bind_agent_run, reset_agent_run


class DelegationExecutionError(Exception):
    """Carry a persisted child failure across the Tool boundary."""

    def __init__(self, result: DelegationResult) -> None:
        super().__init__(result.failure_code or result.status.value)
        self.result = result


class DelegationService:
    """Policy Enforcement Point between the Lead Agent and child Agents.

    Only the Researcher role is executable in Stage 15. The child receives a task,
    trusted scope, allowlist and sub-budget—not the Lead's full Context or Memory.
    Per-parent locks make the first release serial and deterministic; persistent
    fingerprints prevent duplicate work across crash/retry boundaries.
    """

    def __init__(
        self,
        *,
        store: SqliteAgentRunStore,
        researcher: ResearchTutor,
        max_children: int = 3,
        max_tokens: int = 6_000,
        max_queries: int = 6,
        max_sources: int = 10,
        deadline_seconds: float = 60,
        poll_seconds: float = 0.05,
    ) -> None:
        self.store = store
        self.researcher = researcher
        self.max_children = max_children
        self.max_tokens = max_tokens
        self.max_queries = max_queries
        self.max_sources = max_sources
        self.deadline_seconds = deadline_seconds
        self.poll_seconds = poll_seconds
        self._parent_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._routing_policy = ResearchPolicy()

    async def delegate_research(
        self, *, parent_run_id: str, plan_step_id: str, objective: str
    ) -> DelegationResult:
        """Create or reuse one serial Researcher child Run.

        The Tool input deliberately contains no goal/node identifiers. Scope is read
        from trusted durable Lead state, closing the confused-deputy path where a
        model could ask a child to read resources outside the parent Session.
        """

        normalized_objective = " ".join(objective.split())
        async with self._parent_locks[parent_run_id]:
            parent, state = await self._load_parent(parent_run_id)
            self._validate_objective(normalized_objective)
            if state.goal_id is None:
                raise ValueError("Lead Agent state has no goal scope")
            fingerprint = _fingerprint(
                parent_run_id,
                SubagentRole.RESEARCHER,
                normalized_objective,
                state.goal_id,
                state.knowledge_node_id,
            )
            existing = await self._existing(parent_run_id, fingerprint)
            if existing is not None:
                await self.store.append_event(
                    parent_run_id,
                    "delegation_reused",
                    node=plan_step_id,
                    data={
                        "delegation_id": existing.request.id,
                        "child_run_id": existing.child_run_id,
                        "fingerprint": fingerprint,
                    },
                )
                if existing.result is None:
                    raise ValueError("equivalent delegation is already running")
                return existing.result.model_copy(update={"reused": True})

            records = await self.list_for_parent(parent_run_id)
            if len(records) >= self.max_children:
                raise ValueError("Lead Agent reached the maximum child Run count")
            budget = self._allocate_budget(parent, state, records)
            request = DelegationRequest(
                parent_run_id=parent_run_id,
                plan_step_id=plan_step_id,
                role=SubagentRole.RESEARCHER,
                objective=normalized_objective,
                goal_id=state.goal_id,
                knowledge_node_id=state.knowledge_node_id,
                allowed_tools=["research.search"],
                budget=budget,
                fingerprint=fingerprint,
            )
            child, _ = await self.store.create_or_get(
                "researcher",
                request.id,
                engine_version="dynamic_v2",
                parent_run_id=parent_run_id,
            )
            created = await self.store.create_delegation(
                request.model_dump_json(),
                delegation_id=request.id,
                parent_run_id=parent_run_id,
                child_run_id=child.run_id,
                role=request.role.value,
                fingerprint=fingerprint,
            )
            if not created:
                await self.store.delete(child.run_id)
                raced = await self._existing(parent_run_id, fingerprint)
                if raced is None or raced.result is None:
                    raise ValueError("equivalent delegation is already running")
                return raced.result.model_copy(update={"reused": True})

            await self.store.set_status(child.run_id, "running")
            event_data: dict[str, object] = {
                "delegation_id": request.id,
                "parent_run_id": parent_run_id,
                "child_run_id": child.run_id,
                "role": request.role.value,
                "objective": request.objective,
                "allowed_tools": request.allowed_tools,
                "budget": request.budget.model_dump(mode="json"),
            }
            await self.store.append_event(
                parent_run_id,
                "delegation_started",
                node=plan_step_id,
                data=event_data,
            )
            await self.store.append_event(
                child.run_id,
                "run_started",
                node=request.role.value,
                data=event_data,
            )
            return await self._run_researcher(request, child)

    async def list_for_parent(self, parent_run_id: str) -> list[DelegationRecord]:
        rows = await self.store.list_delegations(parent_run_id)
        return [_record_from_row(row) for row in rows]

    async def get(self, delegation_id: str) -> DelegationRecord | None:
        row = await self.store.get_delegation(delegation_id)
        return _record_from_row(row) if row is not None else None

    async def _load_parent(
        self, parent_run_id: str
    ) -> tuple[AgentRun, DynamicAgentState]:
        parent = await self.store.get(parent_run_id)
        if parent is None:
            raise ValueError("Lead Agent Run was not found")
        if (
            parent.graph_kind != "daily_learning"
            or parent.engine_version != "dynamic_v2"
        ):
            raise ValueError("delegation requires a dynamic Lead Learning Agent")
        if parent.status != "running" or parent.cancel_requested:
            raise ValueError("Lead Agent is not available for delegation")
        raw_state = await self.store.load_dynamic_state(parent_run_id)
        if raw_state is None:
            raise ValueError("Lead Agent durable state was not found")
        return parent, DynamicAgentState.model_validate_json(raw_state)

    def _validate_objective(self, objective: str) -> None:
        if not objective:
            raise ValueError("delegation objective must not be empty")
        mode = self._routing_policy.route(objective)
        if mode != RetrievalMode.MULTI_STEP_RESEARCH:
            raise ValueError(
                "simple research must use research.ask instead of a Subagent"
            )

    def _allocate_budget(
        self,
        parent: AgentRun,
        state: DynamicAgentState,
        records: list[DelegationRecord],
    ) -> DelegationBudget:
        del parent
        remaining_tokens = state.budget.max_total_tokens - state.usage.total_tokens
        previously_allocated = sum(
            item.request.budget.allocated_tokens for item in records
        )
        remaining_pool = self.max_tokens * self.max_children - previously_allocated
        allocated_tokens = min(self.max_tokens, remaining_tokens, remaining_pool)
        elapsed = (datetime.now(UTC) - state.usage.started_at).total_seconds()
        remaining_deadline = state.budget.deadline_seconds - elapsed
        if allocated_tokens < 500:
            raise ValueError("Lead Agent has insufficient Token budget for delegation")
        if remaining_deadline <= 0:
            raise ValueError("Lead Agent deadline leaves no time for delegation")
        return DelegationBudget(
            allocated_tokens=allocated_tokens,
            max_queries=self.max_queries,
            max_sources=self.max_sources,
            deadline_seconds=min(self.deadline_seconds, remaining_deadline),
        )

    async def _existing(
        self, parent_run_id: str, fingerprint: str
    ) -> DelegationRecord | None:
        row = await self.store.find_delegation(parent_run_id, fingerprint)
        return _record_from_row(row) if row is not None else None

    async def _run_researcher(
        self, request: DelegationRequest, child: AgentRun
    ) -> DelegationResult:
        started = perf_counter()
        base_request = self.researcher.request_for(
            request.objective,
            goal_id=request.goal_id,
            knowledge_node_id=request.knowledge_node_id,
            mode_override=RetrievalMode.MULTI_STEP_RESEARCH,
        )
        research_request = base_request.model_copy(
            update={
                "budget": ResearchBudget(
                    max_rounds=base_request.budget.max_rounds,
                    max_queries=request.budget.max_queries,
                    max_sources=request.budget.max_sources,
                    max_read_chars=min(
                        base_request.budget.max_read_chars,
                        request.budget.allocated_tokens * 3,
                    ),
                    max_context_tokens=request.budget.allocated_tokens,
                )
            }
        )

        async def invoke() -> ResearchResult:
            token = bind_agent_run(child.run_id)
            try:
                return await self.researcher.run(research_request)
            finally:
                reset_agent_run(token)

        task = asyncio.create_task(invoke())
        deadline = asyncio.get_running_loop().time() + request.budget.deadline_seconds
        try:
            while not task.done():
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return await self._stop_child(
                        request,
                        child,
                        task,
                        DelegationStatus.DEADLINE_EXCEEDED,
                        "deadline_exceeded",
                        started,
                    )
                await asyncio.wait(
                    {task}, timeout=min(self.poll_seconds, remaining)
                )
                parent = await self.store.get(request.parent_run_id)
                refreshed_child = await self.store.get(child.run_id)
                if (
                    parent is None
                    or parent.cancel_requested
                    or refreshed_child is None
                    or refreshed_child.cancel_requested
                ):
                    return await self._stop_child(
                        request,
                        child,
                        task,
                        DelegationStatus.CANCELLED,
                        "cancelled",
                        started,
                    )
            research = await task
            trace = await self.researcher.get(research.trace_id)
            result = self._verify_and_compress(
                request,
                child.run_id,
                trace,
                duration_ms=(perf_counter() - started) * 1_000,
            )
            await self._finish_child(request, child, result)
            return result
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            result = _failure_result(
                request,
                child.run_id,
                DelegationStatus.CANCELLED,
                "Lead execution stopped the Researcher Subagent.",
                "cancelled",
                (perf_counter() - started) * 1_000,
            )
            await self._finish_child(request, child, result)
            raise
        except Exception as error:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            result = _failure_result(
                request,
                child.run_id,
                DelegationStatus.FAILED,
                f"{type(error).__name__}: {str(error)[:1_000]}",
                "subagent_failed",
                (perf_counter() - started) * 1_000,
            )
            await self._finish_child(request, child, result)
            return result

    def _verify_and_compress(
        self,
        request: DelegationRequest,
        child_run_id: str,
        trace: ResearchTrace | None,
        *,
        duration_ms: float,
    ) -> DelegationResult:
        """Re-validate the child output before it crosses into Lead Context."""

        if trace is None:
            raise ValueError("Researcher returned a missing Trace")
        if (
            trace.request.goal_id != request.goal_id
            or trace.request.knowledge_node_id != request.knowledge_node_id
        ):
            raise ValueError("Researcher result escaped the delegated scope")
        accepted = {
            item.id: item
            for item in trace.evidence
            if item.grade.verdict == EvidenceVerdict.ACCEPTED
        }
        included_claims = {
            item.id: item for item in trace.claims if item.included_in_answer
        }
        citations = [
            item
            for item in trace.citations
            if item.claim_id in included_claims
            and item.evidence_id in accepted
            and item.status != CitationStatus.UNSUPPORTED
        ]
        cited_claims = {item.claim_id for item in citations}
        if set(included_claims) - cited_claims:
            raise ValueError("Researcher returned an ungrounded included Claim")
        cited_evidence = {item.evidence_id for item in citations}
        evidence = [
            DelegatedEvidence(
                id=item.id,
                resource_id=item.resource_id,
                chunk_id=item.chunk_id,
                title=item.title,
                locator=item.section
                or (f"page:{item.page_number}" if item.page_number else item.chunk_id),
                excerpt=item.excerpt[:800],
                content_sha256=item.content_sha256,
            )
            for item in accepted.values()
            if item.id in cited_evidence
        ]
        used_tokens = trace.usage.estimated_tokens
        if used_tokens > request.budget.allocated_tokens:
            raise ValueError("Researcher exceeded its delegated Token allocation")
        status = (
            DelegationStatus.COMPLETED
            if trace.status == "completed"
            else DelegationStatus.INSUFFICIENT_EVIDENCE
        )
        return DelegationResult(
            delegation_id=request.id,
            parent_run_id=request.parent_run_id,
            child_run_id=child_run_id,
            role=request.role,
            status=status,
            summary=trace.answer[:8_000],
            claims=[
                DelegatedClaim(
                    id=item.id,
                    text=item.text,
                    citation_status=item.citation_status,
                )
                for item in included_claims.values()
            ],
            citations=[
                DelegatedCitation(
                    claim_id=item.claim_id,
                    evidence_id=item.evidence_id,
                    resource_id=item.resource_id,
                    chunk_id=item.chunk_id,
                    status=item.status,
                )
                for item in citations
            ],
            evidence=evidence,
            unresolved_questions=trace.gaps,
            usage=DelegationUsage(
                allocated_tokens=request.budget.allocated_tokens,
                used_tokens=used_tokens,
                queries=trace.usage.queries,
                sources=trace.usage.sources,
                duration_ms=duration_ms,
            ),
        )

    async def _stop_child(
        self,
        request: DelegationRequest,
        child: AgentRun,
        task: asyncio.Task[ResearchResult],
        status: DelegationStatus,
        failure_code: str,
        started: float,
    ) -> DelegationResult:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        result = _failure_result(
            request,
            child.run_id,
            status,
            "Researcher stopped before producing a verified result.",
            failure_code,
            (perf_counter() - started) * 1_000,
        )
        await self._finish_child(request, child, result)
        return result

    async def _finish_child(
        self,
        request: DelegationRequest,
        child: AgentRun,
        result: DelegationResult,
    ) -> None:
        await self.store.finish_delegation(
            request.id,
            status=result.status.value,
            result_json=result.model_dump_json(),
            used_tokens=result.usage.used_tokens,
        )
        if result.status in {
            DelegationStatus.COMPLETED,
            DelegationStatus.INSUFFICIENT_EVIDENCE,
        }:
            run_status: RunStatus = "completed"
            terminal_reason: TerminalReason = "completed"
            event: EventKind = "run_completed"
            parent_event: EventKind = "delegation_completed"
        elif result.status == DelegationStatus.CANCELLED:
            run_status = "cancelled"
            terminal_reason = "cancelled"
            event = "run_cancelled"
            parent_event = "delegation_cancelled"
        else:
            run_status = "failed"
            terminal_reason = (
                "deadline_exceeded"
                if result.status == DelegationStatus.DEADLINE_EXCEEDED
                else "failed"
            )
            event = "run_failed"
            parent_event = "delegation_failed"
        refreshed = await self.store.get(child.run_id)
        if refreshed is not None and refreshed.status == "running":
            await self.store.set_status(
                child.run_id,
                run_status,
                terminal_reason=terminal_reason,
            )
        data: dict[str, object] = {
            "delegation_id": request.id,
            "parent_run_id": request.parent_run_id,
            "child_run_id": child.run_id,
            "status": result.status.value,
            "failure_code": result.failure_code,
            "usage": result.usage.model_dump(mode="json"),
            "claim_count": len(result.claims),
            "evidence_count": len(result.evidence),
            "unresolved_count": len(result.unresolved_questions),
        }
        await self.store.append_event(child.run_id, event, data=data)
        await self.store.append_event(
            request.parent_run_id,
            parent_event,
            node=request.plan_step_id,
            data=data,
        )


def _failure_result(
    request: DelegationRequest,
    child_run_id: str,
    status: DelegationStatus,
    summary: str,
    failure_code: str,
    duration_ms: float,
) -> DelegationResult:
    return DelegationResult(
        delegation_id=request.id,
        parent_run_id=request.parent_run_id,
        child_run_id=child_run_id,
        role=request.role,
        status=status,
        summary=summary,
        unresolved_questions=[request.objective],
        usage=DelegationUsage(
            allocated_tokens=request.budget.allocated_tokens,
            duration_ms=duration_ms,
        ),
        failure_code=failure_code,
    )


def _fingerprint(
    parent_run_id: str,
    role: SubagentRole,
    objective: str,
    goal_id: str,
    knowledge_node_id: str | None,
) -> str:
    canonical = "\x1f".join(
        (
            parent_run_id,
            role.value,
            objective.casefold(),
            goal_id,
            knowledge_node_id or "",
        )
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _record_from_row(row: dict[str, object]) -> DelegationRecord:
    request = DelegationRequest.model_validate_json(str(row["request_json"]))
    result_json = row.get("result_json")
    return DelegationRecord(
        request=request,
        child_run_id=str(row["child_run_id"]),
        status=DelegationStatus(str(row["status"])),
        result=(
            DelegationResult.model_validate_json(str(result_json))
            if result_json is not None
            else None
        ),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )
