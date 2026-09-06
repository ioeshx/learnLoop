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
]


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
