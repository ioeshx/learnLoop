"""Typed public contracts for the bounded LearnLoop v2 Agent kernel."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentContract(BaseModel):
    """所有 Agent 边界对象的严格基类，禁止模型偷偷增加未审核字段。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class StepStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class ActionType(StrEnum):
    CALL_TOOL = "call_tool"
    PRESENT_CONTENT = "present_content"
    REQUEST_INPUT = "request_input"
    COMPLETE_STEP = "complete_step"
    FINISH_RUN = "finish_run"


class VerificationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class ToolRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ApprovalPolicy(StrEnum):
    NEVER = "never"
    WHEN_REQUESTED = "when_requested"
    ALWAYS = "always"


class ToolErrorKind(StrEnum):
    INVALID_ARGUMENTS = "invalid_arguments"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    PERMISSION_DENIED = "permission_denied"
    TIMEOUT = "timeout"
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    CANCELLED = "cancelled"


class PlanStep(AgentContract):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
    objective: str = Field(min_length=1, max_length=500)
    dependencies: list[str] = Field(default_factory=list, max_length=8)
    success_criteria: list[str] = Field(min_length=1, max_length=6)
    allowed_tools: list[str] = Field(default_factory=list, max_length=12)
    status: StepStatus = StepStatus.PENDING
    evidence_ids: list[str] = Field(default_factory=list, max_length=30)
    attempts: int = Field(default=0, ge=0, le=100)


class AgentPlan(AgentContract):
    """可执行的短期 Plan，与面向用户的长期 ``StudyPlan`` 完全分离。

    ``validate_dag`` 同时检查 reference integrity 和 cycle。这样 Planner 即使输出了
    语法正确的 JSON，也不能把一个不可调度的 Plan 交给 Executor。
    """

    objective: str = Field(min_length=1, max_length=1_000)
    version: int = Field(default=1, ge=1)
    assumptions: list[str] = Field(default_factory=list, max_length=8)
    constraints: list[str] = Field(default_factory=list, max_length=8)
    steps: list[PlanStep] = Field(min_length=2, max_length=6)
    change_reason: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_dag(self) -> Self:
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("plan step ids must be unique")
        known = set(ids)
        active_count = sum(step.status == StepStatus.ACTIVE for step in self.steps)
        if active_count > 1:
            raise ValueError("a plan may contain at most one active step")
        for step in self.steps:
            if step.id in step.dependencies:
                raise ValueError("a plan step cannot depend on itself")
            if not set(step.dependencies).issubset(known):
                raise ValueError("plan step references an unknown dependency")

        # DFS color marking is deterministic and gives a hard DAG invariant. A model
        # cannot evade this check by placing the cycle far away from the active Step.
        graph = {step.id: step.dependencies for step in self.steps}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ValueError("plan dependencies must be acyclic")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in graph[step_id]:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in ids:
            visit(step_id)
        return self

    def ready_step(self) -> PlanStep | None:
        completed = {
            step.id for step in self.steps if step.status == StepStatus.COMPLETED
        }
        return next(
            (
                step
                for step in self.steps
                if step.status in {StepStatus.PENDING, StepStatus.ACTIVE}
                and set(step.dependencies).issubset(completed)
            ),
            None,
        )

    @property
    def complete(self) -> bool:
        return all(
            step.status in {StepStatus.COMPLETED, StepStatus.SKIPPED}
            for step in self.steps
        )


class AgentAction(AgentContract):
    action: ActionType
    plan_step_id: str
    reason_summary: str = Field(min_length=1, max_length=500)
    expected_observation: str = Field(min_length=1, max_length=500)
    tool_name: str | None = Field(default=None, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)
    content: str | None = Field(default=None, max_length=12_000)
    input_type: Literal["answer", "clarification", "approval"] | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_action_payload(self) -> Self:
        if self.action == ActionType.CALL_TOOL and not self.tool_name:
            raise ValueError("call_tool requires tool_name")
        if self.action != ActionType.CALL_TOOL and self.tool_name is not None:
            raise ValueError("tool_name is only valid for call_tool")
        if self.action == ActionType.PRESENT_CONTENT and not self.content:
            raise ValueError("present_content requires content")
        if (
            self.action == ActionType.REQUEST_INPUT
            and (not self.content or self.input_type is None)
        ):
            raise ValueError("request_input requires content and input_type")
        return self

    def signature(self) -> str:
        """返回稳定动作签名，用于检测语义等价的重复 Tool loop。"""

        import json

        return json.dumps(
            {
                "action": self.action,
                "step": self.plan_step_id,
                "tool": self.tool_name,
                "arguments": self.arguments,
                "content": self.content,
                "input_type": self.input_type,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


class Observation(AgentContract):
    id: str = Field(default_factory=lambda: str(uuid4()))
    action_id: str
    plan_step_id: str
    source: str
    succeeded: bool
    summary: str = Field(min_length=1, max_length=4_000)
    data: dict[str, Any] = Field(default_factory=dict)
    error_kind: ToolErrorKind | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class VerificationResult(AgentContract):
    id: str = Field(default_factory=lambda: str(uuid4()))
    plan_step_id: str
    status: VerificationStatus
    evidence_ids: list[str] = Field(default_factory=list, max_length=30)
    failure_code: str | None = Field(default=None, max_length=100)
    explanation: str = Field(min_length=1, max_length=2_000)
    suggested_action: str | None = Field(default=None, max_length=500)


class ReplanProposal(AgentContract):
    reason: str = Field(min_length=1, max_length=1_000)
    steps: list[PlanStep] = Field(min_length=2, max_length=6)


class ToolError(AgentContract):
    kind: ToolErrorKind
    message: str = Field(min_length=1, max_length=2_000)
    retryable: bool = False


class ToolResult(AgentContract):
    tool_name: str
    succeeded: bool
    output: dict[str, Any] | list[Any] | None = None
    error: ToolError | None = None
    truncated: bool = False
    duration_ms: float = Field(ge=0)


class ToolSpec(AgentContract):
    name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    description: str = Field(min_length=1, max_length=1_000)
    risk: ToolRisk
    approval_policy: ApprovalPolicy = ApprovalPolicy.NEVER
    read_only: bool
    idempotent: bool
    timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    max_result_chars: int = Field(default=8_000, ge=256, le=100_000)
    input_schema: dict[str, Any]


class RunBudget(AgentContract):
    max_steps: int = Field(default=24, ge=2, le=200)
    max_model_calls: int = Field(default=30, ge=1, le=200)
    max_tool_calls: int = Field(default=20, ge=0, le=200)
    max_input_tokens: int = Field(default=60_000, ge=1_000)
    max_output_tokens: int = Field(default=20_000, ge=500)
    max_total_tokens: int = Field(default=80_000, ge=1_500)
    deadline_seconds: float = Field(default=300.0, gt=0, le=3_600)
    max_same_action: int = Field(default=2, ge=1, le=10)
    max_consecutive_failures: int = Field(default=3, ge=1, le=20)
    max_replans: int = Field(default=2, ge=0, le=10)

    @model_validator(mode="after")
    def validate_token_budget(self) -> Self:
        if self.max_total_tokens > self.max_input_tokens + self.max_output_tokens:
            raise ValueError(
                "max_total_tokens cannot exceed input plus output token limits"
            )
        return self


class BudgetUsage(AgentContract):
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    steps: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    replans: int = Field(default=0, ge=0)


class BudgetDecision(AgentContract):
    allowed: bool
    terminal_reason: str | None = None
    detail: str | None = None


class AgentInterrupt(AgentContract):
    id: str = Field(default_factory=lambda: str(uuid4()))
    type: Literal["answer", "clarification", "approval"]
    prompt: str = Field(min_length=1, max_length=4_000)
    plan_step_id: str
    allowed_actions: list[str] = Field(default_factory=list, max_length=10)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DynamicAgentState(AgentContract):
    """Kernel 的 durable state；每个 action 后整体保存，支持跨进程 resume。"""

    run_id: str
    plan: AgentPlan
    budget: RunBudget
    usage: BudgetUsage
    observations: list[Observation] = Field(default_factory=list, max_length=100)
    pending_interrupt: AgentInterrupt | None = None
    last_action_signature: str | None = None
    same_action_count: int = Field(default=0, ge=0)
    consecutive_failures: int = Field(default=0, ge=0)
    final_summary: str | None = Field(default=None, max_length=12_000)
