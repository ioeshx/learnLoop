"""Deterministic-first verification and constrained replanning helpers."""

from __future__ import annotations

from app.agent.dynamic.models import (
    AgentPlan,
    Observation,
    PlanStep,
    ReplanProposal,
    StepStatus,
    VerificationResult,
    VerificationStatus,
)


class DeterministicVerifier:
    """优先使用可重复的外部 evidence 验证 Step，拒绝 self-declared success。"""

    def verify_step(
        self,
        step: PlanStep,
        requested_evidence_ids: list[str],
        observations: list[Observation],
    ) -> VerificationResult:
        observation_by_id = {item.id: item for item in observations}
        evidence = [
            observation_by_id[item]
            for item in requested_evidence_ids
            if item in observation_by_id
            and observation_by_id[item].plan_step_id == step.id
        ]
        if not evidence:
            return VerificationResult(
                plan_step_id=step.id,
                status=VerificationStatus.INCONCLUSIVE,
                failure_code="missing_evidence",
                explanation="Step completion did not reference a known Observation.",
                suggested_action="collect evidence with an allowed Tool",
            )
        if any(not item.succeeded for item in evidence):
            return VerificationResult(
                plan_step_id=step.id,
                status=VerificationStatus.FAILED,
                evidence_ids=[item.id for item in evidence],
                failure_code="failed_observation",
                explanation=(
                    "At least one completion evidence item represents a failure."
                ),
                suggested_action="retry, choose another Tool, or replan",
            )
        return VerificationResult(
            plan_step_id=step.id,
            status=VerificationStatus.PASSED,
            evidence_ids=[item.id for item in evidence],
            explanation=(
                "All referenced Observations succeeded; deterministic Step evidence "
                "requirements are satisfied."
            ),
        )

    def verify_plan(self, plan: AgentPlan) -> VerificationResult:
        """只有全部必要 Step 已通过验证时才允许 finish_run。"""

        if not plan.complete:
            return VerificationResult(
                plan_step_id="plan",
                status=VerificationStatus.FAILED,
                failure_code="unfinished_plan",
                explanation="The Agent Plan still contains unfinished Steps.",
                suggested_action="continue the next ready Step",
            )
        return VerificationResult(
            plan_step_id="plan",
            status=VerificationStatus.PASSED,
            evidence_ids=[
                evidence_id
                for step in plan.steps
                for evidence_id in step.evidence_ids
            ],
            explanation="All required Plan Steps have verified completion evidence.",
        )

    def verify_finish(self, plan: AgentPlan) -> VerificationResult:
        if not plan.complete:
            ready = plan.ready_step()
            return VerificationResult(
                plan_step_id=ready.id if ready is not None else "plan",
                status=VerificationStatus.FAILED,
                failure_code="incomplete_plan",
                explanation="The Agent cannot finish while required Steps remain open.",
                suggested_action="continue the next ready Step",
            )
        evidence_ids = [item for step in plan.steps for item in step.evidence_ids]
        return VerificationResult(
            plan_step_id="plan",
            status=VerificationStatus.PASSED,
            evidence_ids=evidence_ids,
            explanation="Every required Plan Step reached a verified terminal state.",
        )


def apply_replan(current: AgentPlan, proposal: ReplanProposal) -> AgentPlan:
    """创建新的 immutable Plan version，并保护已完成 Step 及其 evidence。

    Replanner 只能修改未开始的部分。完成 Step 必须保留原内容，避免模型通过重写历史
    来掩盖失败或伪造成功；这一约束是 Agent trajectory 可审计性的关键。
    """

    completed = {
        step.id: step for step in current.steps if step.status == StepStatus.COMPLETED
    }
    proposed = {step.id: step for step in proposal.steps}
    if not set(completed).issubset(proposed):
        raise ValueError("replan cannot remove completed steps")
    merged: list[PlanStep] = []
    for step in proposal.steps:
        old = completed.get(step.id)
        if old is not None:
            if step.objective != old.objective or step.evidence_ids != old.evidence_ids:
                raise ValueError("replan cannot rewrite completed step evidence")
            merged.append(old)
        else:
            merged.append(step.model_copy(update={"status": StepStatus.PENDING}))
    return AgentPlan(
        objective=current.objective,
        version=current.version + 1,
        assumptions=current.assumptions,
        constraints=current.constraints,
        steps=merged,
        change_reason=proposal.reason,
    )
