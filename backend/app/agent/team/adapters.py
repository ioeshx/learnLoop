"""First trusted Role adapters: Researcher compatibility and Evaluator."""

from __future__ import annotations

from typing import Protocol

from pydantic import TypeAdapter
from pydantic.types import JsonValue

from app.agent.delegation import DelegationResult
from app.agent.policy import (
    DataLabel,
    DataSource,
    IntegrityLevel,
    Sensitivity,
    TrustLevel,
)
from app.agent.team.models import AgentCard, ArtifactDraft, TeamPart, TeamTask


class ResearchDelegator(Protocol):
    async def delegate_research(
        self, *, parent_run_id: str, plan_step_id: str, objective: str
    ) -> DelegationResult: ...


class ResearcherRoleAdapter:
    def __init__(self, delegation: ResearchDelegator) -> None:
        self.delegation = delegation

    @property
    def card(self) -> AgentCard:
        return AgentCard(
            role_id="researcher",
            version="1.0.0",
            description="Bounded local retrieval and cited research synthesis.",
            capabilities=["multi_step_research", "cited_evidence"],
            input_contract="research-objective-v1",
            output_contract="delegation-result-v1",
            allowed_tools=["research.search"],
            read_only=True,
        )

    async def execute(self, task: TeamTask) -> ArtifactDraft:
        result = await self.delegation.delegate_research(
            parent_run_id=task.parent_run_id,
            plan_step_id=task.plan_step_id,
            objective=task.objective,
        )
        return ArtifactDraft(
            parts=[
                TeamPart(
                    kind="json",
                    value=TypeAdapter(JsonValue).validate_python(
                        result.model_dump(mode="json")
                    ),
                    labels=[
                        DataLabel(
                            source=DataSource.SUBAGENT,
                            source_ref=f"delegation:{result.delegation_id}",
                            trust=TrustLevel.UNTRUSTED,
                            sensitivity=Sensitivity.PERSONAL,
                            integrity=IntegrityLevel.HASHED,
                        )
                    ],
                )
            ],
            used_tokens=result.usage.used_tokens,
            metadata={
                "delegation_id": result.delegation_id,
                "child_run_id": result.child_run_id,
                "status": result.status.value,
            },
        )


class EvaluatorRoleAdapter:
    """Deterministic evidence/rubric adapter; it cannot override Lead Verifier."""

    @property
    def card(self) -> AgentCard:
        return AgentCard(
            role_id="evaluator",
            version="1.0.0",
            description="Deterministic rubric and evidence-reference inspection.",
            capabilities=["rubric_check", "evidence_inventory"],
            input_contract="evaluation-input-v1",
            output_contract="evaluation-report-v1",
            allowed_tools=[],
            read_only=True,
        )

    async def execute(self, task: TeamTask) -> ArtifactDraft:
        payload = next(
            (
                part.value
                for part in task.parts
                if part.kind == "json" and isinstance(part.value, dict)
            ),
            {},
        )
        answer = str(payload.get("answer", ""))
        raw_rubric = payload.get("rubric", [])
        rubric = (
            [str(item) for item in raw_rubric]
            if isinstance(raw_rubric, list)
            else []
        )
        raw_evidence = payload.get("evidence_ids", [])
        evidence_ids = (
            [str(item) for item in raw_evidence]
            if isinstance(raw_evidence, list)
            else []
        )
        report: JsonValue = TypeAdapter(JsonValue).validate_python(
            {
                "answer_present": bool(answer.strip()),
                "rubric_items": rubric,
                "evidence_ids": evidence_ids,
                "violations": (
                    []
                    if answer.strip() and evidence_ids
                    else ["missing_answer_or_evidence"]
                ),
                "advisory_only": True,
            }
        )
        labels = [label for part in task.parts for label in part.labels]
        return ArtifactDraft(
            parts=[TeamPart(kind="json", value=report, labels=labels)],
            used_tokens=0,
            metadata={"verifier_override_allowed": False},
        )
