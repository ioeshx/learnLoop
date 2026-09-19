"""Application service for Policy simulation and audit queries."""

from __future__ import annotations

from typing import Protocol

from app.agent.policy.engine import AgentPolicyEngine
from app.agent.policy.models import PolicyDecision, PolicyRequest


class PolicyAuditStore(Protocol):
    async def save_agent_policy_decision(
        self, decision: PolicyDecision
    ) -> bool: ...

    async def get_agent_policy_decision_by_fingerprint(
        self, request_fingerprint: str
    ) -> str | None: ...

    async def list_agent_policy_decisions(
        self, *, run_id: str | None = None, limit: int = 200
    ) -> list[str]: ...


class AgentPolicyService:
    def __init__(
        self, engine: AgentPolicyEngine, store: PolicyAuditStore
    ) -> None:
        self.engine = engine
        self.store = store

    async def decide(self, request: PolicyRequest) -> PolicyDecision:
        decision = self.engine.evaluate(request)
        existing = await self.store.get_agent_policy_decision_by_fingerprint(
            decision.request_fingerprint
        )
        if existing is not None:
            return PolicyDecision.model_validate_json(existing)
        created = await self.store.save_agent_policy_decision(decision)
        if not created:
            raced = await self.store.get_agent_policy_decision_by_fingerprint(
                decision.request_fingerprint
            )
            if raced is None:
                raise RuntimeError("Policy decision disappeared after write race")
            return PolicyDecision.model_validate_json(raced)
        return decision

    def simulate(self, request: PolicyRequest) -> PolicyDecision:
        """Evaluate without persistence so dry-run cannot mutate the audit ledger."""

        return self.engine.evaluate(request)

    async def list_decisions(
        self, *, run_id: str | None = None, limit: int = 200
    ) -> list[PolicyDecision]:
        return [
            PolicyDecision.model_validate_json(item)
            for item in await self.store.list_agent_policy_decisions(
                run_id=run_id, limit=limit
            )
        ]
