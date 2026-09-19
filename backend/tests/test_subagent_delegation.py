"""Stage 15 Subagent-as-Tool isolation, budget, and lifecycle tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.agent.delegation import DelegationService, DelegationStatus
from app.agent.dynamic.budget import BudgetLedger
from app.agent.dynamic.models import (
    AgentPlan,
    BudgetUsage,
    DynamicAgentState,
    PlanStep,
    RunBudget,
)
from app.agent.dynamic.tools import ToolExecutor, build_learning_tool_registry
from app.agent.execution import AgentRuntime, SqliteAgentRunStore
from app.agent.research.models import (
    CitationLink,
    CitationStatus,
    Claim,
    EvidenceGrade,
    EvidenceItem,
    EvidenceVerdict,
    ResearchBudget,
    ResearchRequest,
    ResearchResult,
    ResearchTrace,
    ResearchUsage,
    RetrievalMode,
    SubQuestion,
)
from app.config import Settings
from app.main import create_app

NOW = datetime(2026, 9, 16, 8, tzinfo=UTC)


class FakeResearcher:
    def __init__(self, *, block: bool = False, invalid_graph: bool = False) -> None:
        self.block = block
        self.invalid_graph = invalid_graph
        self.started = asyncio.Event()
        self.requests: list[ResearchRequest] = []
        self.traces: dict[str, ResearchTrace] = {}

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
            budget=ResearchBudget(),
        )

    async def run(self, request: ResearchRequest) -> ResearchResult:
        self.requests.append(request)
        self.started.set()
        if self.block:
            await asyncio.Event().wait()
        evidence = EvidenceItem(
            id="evidence-1",
            query_id="query-1",
            subquestion_id="sq-1",
            resource_id="resource-1",
            chunk_id="chunk-1",
            title="Graph notes",
            excerpt="BFS 使用队列，DFS 使用栈。",
            page_number=1,
            section="Traversal",
            source_uri=None,
            resource_version=NOW.isoformat(),
            resource_sha256="a" * 64,
            content_sha256="b" * 64,
            grade=EvidenceGrade(
                relevance=1,
                source_quality=1,
                duplicate_score=0,
                coverage=1,
                verdict=EvidenceVerdict.ACCEPTED,
                reason="accepted",
            ),
        )
        claim = Claim(
            id="claim-1",
            text="BFS 使用队列。",
            importance="critical",
            citation_status=CitationStatus.SUPPORTED,
            included_in_answer=True,
        )
        citations = (
            []
            if self.invalid_graph
            else [
                CitationLink(
                    id="citation-1",
                    claim_id=claim.id,
                    evidence_id=evidence.id,
                    resource_id=evidence.resource_id,
                    chunk_id=evidence.chunk_id,
                    status=CitationStatus.SUPPORTED,
                    explanation="supported",
                )
            ]
        )
        usage = ResearchUsage(
            rounds=1,
            queries=2,
            sources=1,
            read_chars=len(evidence.excerpt),
            estimated_tokens=120,
        )
        trace = ResearchTrace(
            id="trace-1",
            user_id="local-user",
            request=request,
            mode=RetrievalMode.MULTI_STEP_RESEARCH,
            subquestions=[SubQuestion(id="sq-1", text=request.question)],
            queries=[],
            evidence=[evidence],
            claims=[claim],
            citations=citations,
            gaps=[],
            usage=usage,
            status="completed",
            answer="BFS 使用队列。[1]",
            created_at=NOW,
            completed_at=NOW,
        )
        self.traces[trace.id] = trace
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

    async def get(self, trace_id: str) -> ResearchTrace | None:
        return self.traces.get(trace_id)


async def _lead(
    store: SqliteAgentRunStore,
    *,
    total_tokens: int = 0,
    max_total_tokens: int = 10_000,
) -> tuple[object, DynamicAgentState]:
    run, _ = await store.create_or_get(
        "daily_learning", "session-1", engine_version="dynamic_v2"
    )
    await store.set_status(run.run_id, "running")
    state = DynamicAgentState(
        run_id=run.run_id,
        user_id="local-user",
        session_id="session-1",
        goal_id="goal-1",
        knowledge_node_id="node-1",
        plan=AgentPlan(
            objective="完成图遍历学习",
            steps=[
                PlanStep(
                    id="research",
                    objective="比较 BFS 与 DFS",
                    success_criteria=["有引用"],
                    allowed_tools=["delegate.research"],
                ),
                PlanStep(
                    id="teach",
                    objective="讲解结论",
                    dependencies=["research"],
                    success_criteria=["完成讲解"],
                ),
            ],
        ),
        budget=RunBudget(
            max_input_tokens=max_total_tokens,
            max_output_tokens=500,
            max_total_tokens=max_total_tokens,
        ),
        # Run deadline is relative to test execution; evidence timestamps below
        # remain frozen for deterministic provenance assertions.
        usage=BudgetUsage(started_at=datetime.now(UTC), total_tokens=total_tokens),
    )
    await store.save_dynamic_state(run.run_id, state.model_dump_json())
    refreshed = await store.get(run.run_id)
    assert refreshed is not None
    return refreshed, state


@pytest.mark.asyncio
async def test_researcher_gets_isolated_scope_and_compressed_result(
    tmp_path: Path,
) -> None:
    store = await SqliteAgentRunStore.open(tmp_path / "checkpoint.db")
    try:
        lead, _ = await _lead(store)
        researcher = FakeResearcher()
        service = DelegationService(
            store=store,
            researcher=researcher,  # type: ignore[arg-type]
            max_tokens=1_000,
        )

        result = await service.delegate_research(
            parent_run_id=lead.run_id,  # type: ignore[attr-defined]
            plan_step_id="research",
            objective="比较 BFS 与 DFS 的结构以及适用场景",
        )

        assert result.status == DelegationStatus.COMPLETED
        assert result.parent_run_id == lead.run_id  # type: ignore[attr-defined]
        assert result.evidence[0].excerpt == "BFS 使用队列，DFS 使用栈。"
        assert researcher.requests[0].goal_id == "goal-1"
        assert researcher.requests[0].knowledge_node_id == "node-1"
        assert researcher.requests[0].budget.max_context_tokens == 1_000
        child = await store.get(result.child_run_id)
        assert child is not None and child.parent_run_id == lead.run_id  # type: ignore[attr-defined]
        assert child.graph_kind == "researcher"
        assert child.status == "completed"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_duplicate_delegation_reuses_child_without_new_work(
    tmp_path: Path,
) -> None:
    store = await SqliteAgentRunStore.open(tmp_path / "checkpoint.db")
    try:
        lead, _ = await _lead(store)
        researcher = FakeResearcher()
        service = DelegationService(
            store=store, researcher=researcher  # type: ignore[arg-type]
        )
        arguments = {
            "parent_run_id": lead.run_id,  # type: ignore[attr-defined]
            "plan_step_id": "research",
            "objective": "比较 BFS 与 DFS 以及解释各自限制",
        }

        first = await service.delegate_research(**arguments)  # type: ignore[arg-type]
        second = await service.delegate_research(**arguments)  # type: ignore[arg-type]

        assert second.reused is True
        assert second.child_run_id == first.child_run_id
        assert len(researcher.requests) == 1
        assert len(await store.list_children(lead.run_id)) == 1  # type: ignore[attr-defined]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_agent_trace_api_exposes_delegation_tree(tmp_path: Path) -> None:
    store = await SqliteAgentRunStore.open(tmp_path / "checkpoint.db")
    try:
        lead, _ = await _lead(store)
        service = DelegationService(
            store=store,
            researcher=FakeResearcher(),  # type: ignore[arg-type]
        )
        result = await service.delegate_research(
            parent_run_id=lead.run_id,  # type: ignore[attr-defined]
            plan_step_id="research",
            objective="比较 BFS 与 DFS 以及解释各自限制",
        )
        runtime = AgentRuntime(
            checkpointer=object(),  # type: ignore[arg-type]
            run_store=store,
            daily_graph=object(),  # type: ignore[arg-type]
            goal_graph=object(),  # type: ignore[arg-type]
            tools=object(),  # type: ignore[arg-type]
            model=None,
            retention_days=30,
            dynamic_kernel=None,
            delegation=service,
        )
        application = create_app(
            Settings(environment="test", data_dir=tmp_path / "data")
        )
        application.state.agent_runtime = runtime
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            children = await client.get(
                f"/api/v1/agent/runs/{lead.run_id}/children"  # type: ignore[attr-defined]
            )
            trace = await client.get(
                f"/api/v1/agent/runs/{lead.run_id}/trace"  # type: ignore[attr-defined]
            )

        assert children.status_code == 200
        assert children.json()["runs"][0]["run_id"] == result.child_run_id
        assert children.json()["delegations"][0]["request"]["role"] == "researcher"
        assert trace.status_code == 200
        assert trace.json()["child_runs"][0]["parent_run_id"] == lead.run_id  # type: ignore[attr-defined]
        assert trace.json()["delegations"][0]["result"]["claims"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_simple_task_and_insufficient_parent_budget_are_rejected(
    tmp_path: Path,
) -> None:
    store = await SqliteAgentRunStore.open(tmp_path / "checkpoint.db")
    try:
        lead, _ = await _lead(store, total_tokens=9_700)
        service = DelegationService(
            store=store,
            researcher=FakeResearcher(),  # type: ignore[arg-type]
            max_tokens=1_000,
        )
        with pytest.raises(ValueError, match="simple research"):
            await service.delegate_research(
                parent_run_id=lead.run_id,  # type: ignore[attr-defined]
                plan_step_id="research",
                objective="BFS 定义",
            )
        with pytest.raises(ValueError, match="insufficient Token"):
            await service.delegate_research(
                parent_run_id=lead.run_id,  # type: ignore[attr-defined]
                plan_step_id="research",
                objective="比较 BFS 与 DFS 以及解释各自限制",
            )
        assert await store.list_children(lead.run_id) == []  # type: ignore[attr-defined]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_parent_cancel_stops_running_researcher_child(tmp_path: Path) -> None:
    store = await SqliteAgentRunStore.open(tmp_path / "checkpoint.db")
    try:
        lead, _ = await _lead(store)
        researcher = FakeResearcher(block=True)
        service = DelegationService(
            store=store,
            researcher=researcher,  # type: ignore[arg-type]
            poll_seconds=0.01,
        )
        delegated = asyncio.create_task(
            service.delegate_research(
                parent_run_id=lead.run_id,  # type: ignore[attr-defined]
                plan_step_id="research",
                objective="比较 BFS 与 DFS 以及解释各自限制",
            )
        )
        await asyncio.wait_for(researcher.started.wait(), timeout=1)
        await store.request_cancel(lead.run_id)  # type: ignore[attr-defined]

        result = await asyncio.wait_for(delegated, timeout=1)

        assert result.status == DelegationStatus.CANCELLED
        child = await store.get(result.child_run_id)
        assert child is not None and child.status == "cancelled"
        assert child.cancel_requested is False
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_lead_verifier_rejects_ungrounded_child_claim(tmp_path: Path) -> None:
    store = await SqliteAgentRunStore.open(tmp_path / "checkpoint.db")
    try:
        lead, _ = await _lead(store)
        service = DelegationService(
            store=store,
            researcher=FakeResearcher(invalid_graph=True),  # type: ignore[arg-type]
        )

        result = await service.delegate_research(
            parent_run_id=lead.run_id,  # type: ignore[attr-defined]
            plan_step_id="research",
            objective="比较 BFS 与 DFS 以及解释各自限制",
        )

        assert result.status == DelegationStatus.FAILED
        assert result.failure_code == "subagent_failed"
        child = await store.get(result.child_run_id)
        assert child is not None and child.status == "failed"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_delegation_tool_injects_parent_identity_and_maps_child_failure(
    tmp_path: Path,
) -> None:
    store = await SqliteAgentRunStore.open(tmp_path / "checkpoint.db")
    try:
        lead, _ = await _lead(store)
        service = DelegationService(
            store=store,
            researcher=FakeResearcher(invalid_graph=True),  # type: ignore[arg-type]
        )
        registry = build_learning_tool_registry(
            object(), delegation=service  # type: ignore[arg-type]
        )

        result = await ToolExecutor(registry).execute(
            name="delegate.research",
            arguments={"objective": "比较 BFS 与 DFS 以及解释各自限制"},
            allowed_tools={"delegate.research"},
            run_id=lead.run_id,  # type: ignore[attr-defined]
            plan_step_id="research",
        )

        assert result.succeeded is False
        assert result.error is not None and result.error.kind == "permanent"
        assert result.output is not None
        assert result.output["parent_run_id"] == lead.run_id  # type: ignore[index,attr-defined]
        invalid_scope = await ToolExecutor(registry).execute(
            name="delegate.research",
            arguments={
                "objective": "比较 BFS 与 DFS 以及解释各自限制",
                "goal_id": "outside-goal",
            },
            allowed_tools={"delegate.research"},
            run_id=lead.run_id,  # type: ignore[attr-defined]
            plan_step_id="research",
        )
        assert invalid_scope.error is not None
        assert invalid_scope.error.kind == "invalid_arguments"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_delegation_deadline_stops_child(tmp_path: Path) -> None:
    store = await SqliteAgentRunStore.open(tmp_path / "checkpoint.db")
    try:
        lead, _ = await _lead(store)
        service = DelegationService(
            store=store,
            researcher=FakeResearcher(block=True),  # type: ignore[arg-type]
            deadline_seconds=0.02,
            poll_seconds=0.005,
        )

        result = await service.delegate_research(
            parent_run_id=lead.run_id,  # type: ignore[attr-defined]
            plan_step_id="research",
            objective="比较 BFS 与 DFS 以及解释各自限制",
        )

        assert result.status == DelegationStatus.DEADLINE_EXCEEDED
        child = await store.get(result.child_run_id)
        assert child is not None
        assert child.status == "failed"
        assert child.terminal_reason == "deadline_exceeded"
    finally:
        await store.close()


def test_delegation_reservation_is_charged_to_parent_budget() -> None:
    budget = RunBudget(
        max_input_tokens=2_000,
        max_output_tokens=500,
        max_total_tokens=2_500,
    )
    ledger = BudgetLedger(budget, BudgetUsage())

    allowed = ledger.reserve_delegation(2_000)
    exhausted = ledger.reserve_delegation(600)

    assert allowed.allowed is True
    assert exhausted.allowed is False
    assert ledger.usage.delegated_tokens == 2_600
    assert ledger.usage.total_tokens == 2_600


@pytest.mark.asyncio
async def test_runtime_cancel_propagates_to_descendants(tmp_path: Path) -> None:
    store = await SqliteAgentRunStore.open(tmp_path / "checkpoint.db")
    try:
        lead, _ = await _lead(store)
        child, _ = await store.create_or_get(
            "researcher",
            "delegation-1",
            engine_version="dynamic_v2",
            parent_run_id=lead.run_id,  # type: ignore[attr-defined]
        )
        await store.set_status(child.run_id, "running")
        runtime = AgentRuntime(
            checkpointer=object(),  # type: ignore[arg-type]
            run_store=store,
            daily_graph=object(),  # type: ignore[arg-type]
            goal_graph=object(),  # type: ignore[arg-type]
            tools=object(),  # type: ignore[arg-type]
            model=None,
            retention_days=30,
            dynamic_kernel=None,
        )

        cancelled = await runtime.cancel_run(lead)  # type: ignore[arg-type]

        assert cancelled.status == "cancelled"
        refreshed_child = await store.get(child.run_id)
        assert refreshed_child is not None
        assert refreshed_child.status == "cancelled"
        assert refreshed_child.cancel_requested is True
    finally:
        await store.close()
