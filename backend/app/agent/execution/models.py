"""Serializable execution metadata kept beside LangGraph checkpoints."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

GraphKind = Literal["daily_learning", "goal_planning", "researcher"]
EngineVersion = Literal["fixed_v1", "dynamic_v2"]
RunStatus = Literal[
    "created", "running", "awaiting_input", "completed", "failed", "cancelled"
]
TerminalReason = Literal[
    "completed",
    "failed",
    "cancelled",
    "budget_exhausted",
    "deadline_exceeded",
    "verification_failed",
]
EventKind = Literal[
    "run_started",
    "node_started",
    "node_completed",
    "tool_started",
    "tool_completed",
    "interrupt_created",
    "run_completed",
    "run_failed",
    "model_completed",
    "plan_created",
    "plan_rejected",
    "plan_replanned",
    "action_decided",
    "action_rejected",
    "content_presented",
    "observation_recorded",
    "verification_completed",
    "context_compiled",
    "context_snapshot_created",
    "memory_extracted",
    "budget_updated",
    "run_paused",
    "run_cancelled",
    "delegation_started",
    "delegation_completed",
    "delegation_failed",
    "delegation_cancelled",
    "delegation_reused",
    "reflection_created",
    "reflection_recalled",
    "skill_candidate_created",
    "skill_recalled",
    "skill_usage_recorded",
    "skill_quarantined",
]


@dataclass(frozen=True, slots=True)
class ToolCallTrace:
    call_id: str
    run_id: str
    tool_name: str
    arguments: dict[str, object]
    result_summary: dict[str, object] | None
    status: str
    duration_ms: float | None
    error: str | None
    started_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ModelCallTrace:
    call_id: str
    run_id: str | None
    prompt_name: str
    prompt_version: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    duration_ms: float
    attempts: int
    repaired: bool
    error: str | None
    created_at: datetime


# run_id       LearnLoop 暴露给 API 和前端的运行 ID
# thread_id    LangGraph Checkpoint 线程 ID
# graph_kind   Lead workflow 或受控 Subagent role
# resource_id  session_id 或 goal_id
# status       当前生命周期状态
@dataclass(frozen=True, slots=True)
class AgentRun:
    run_id: str
    thread_id: str
    graph_kind: GraphKind
    resource_id: str
    engine_version: EngineVersion
    parent_run_id: str | None
    attempt_no: int
    status: RunStatus
    terminal_reason: TerminalReason | None
    cancel_requested: bool
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AgentEvent:
    run_id: str
    sequence: int
    event: EventKind
    node: str | None
    timestamp: datetime
    data: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "sequence": self.sequence,
            "event": self.event,
            "node": self.node,
            "timestamp": self.timestamp.isoformat(),
            "data": self.data,
        }
