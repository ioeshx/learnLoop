"""Serializable execution metadata kept beside LangGraph checkpoints."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

GraphKind = Literal["daily_learning", "goal_planning"]
RunStatus = Literal[
    "created", "running", "awaiting_input", "completed", "failed"
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
# graph_kind   daily_learning 或 goal_planning
# resource_id  session_id 或 goal_id
# status       当前生命周期状态
@dataclass(frozen=True, slots=True)
class AgentRun:
    run_id: str
    thread_id: str
    graph_kind: GraphKind
    resource_id: str
    status: RunStatus
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
