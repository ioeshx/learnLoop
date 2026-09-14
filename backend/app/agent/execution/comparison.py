"""Deterministic fixed_v1 versus dynamic_v2 trace comparison."""

from dataclasses import dataclass

from app.agent.execution.models import (
    AgentEvent,
    AgentRun,
    ModelCallTrace,
    ToolCallTrace,
)


@dataclass(frozen=True, slots=True)
class RunMetrics:
    run_id: str
    engine_version: str
    completed: bool
    terminal_reason: str | None
    event_count: int
    action_count: int
    rejected_action_count: int
    tool_calls: int
    repeated_tool_calls: int
    model_calls: int
    total_tokens: int
    total_duration_ms: float

    def as_dict(self) -> dict[str, object]:
        return {
            field: getattr(self, field)
            for field in self.__dataclass_fields__
        }


def compare_runs(
    fixed_run: AgentRun,
    fixed_events: list[AgentEvent],
    fixed_tools: list[ToolCallTrace],
    fixed_models: list[ModelCallTrace],
    dynamic_run: AgentRun,
    dynamic_events: list[AgentEvent],
    dynamic_tools: list[ToolCallTrace],
    dynamic_models: list[ModelCallTrace],
) -> dict[str, object]:
    """生成不带主观评分的 paired-run report。

    对照只允许相同 graph/resource，避免拿不同学习任务的成本和成功状态做无效比较。
    质量是否提升仍由 Scenario expected end-state 决定，本函数只提供可复现的 Trace 指标。
    """

    if (
        fixed_run.graph_kind != dynamic_run.graph_kind
        or fixed_run.resource_id != dynamic_run.resource_id
    ):
        raise ValueError("paired Agent runs must use the same graph and resource")
    if fixed_run.engine_version != "fixed_v1":
        raise ValueError("fixed_run must use fixed_v1")
    if dynamic_run.engine_version != "dynamic_v2":
        raise ValueError("dynamic_run must use dynamic_v2")
    fixed = _metrics(fixed_run, fixed_events, fixed_tools, fixed_models)
    dynamic = _metrics(
        dynamic_run, dynamic_events, dynamic_tools, dynamic_models
    )
    return {
        "resource_id": fixed_run.resource_id,
        "graph": fixed_run.graph_kind,
        "fixed_v1": fixed.as_dict(),
        "dynamic_v2": dynamic.as_dict(),
        "delta": {
            "completed": int(dynamic.completed) - int(fixed.completed),
            "tool_calls": dynamic.tool_calls - fixed.tool_calls,
            "model_calls": dynamic.model_calls - fixed.model_calls,
            "total_tokens": dynamic.total_tokens - fixed.total_tokens,
            "total_duration_ms": round(
                dynamic.total_duration_ms - fixed.total_duration_ms, 3
            ),
        },
    }


def _metrics(
    run: AgentRun,
    events: list[AgentEvent],
    tools: list[ToolCallTrace],
    models: list[ModelCallTrace],
) -> RunMetrics:
    signatures = [
        (item.tool_name, repr(sorted(item.arguments.items()))) for item in tools
    ]
    repeated = len(signatures) - len(set(signatures))
    return RunMetrics(
        run_id=run.run_id,
        engine_version=run.engine_version,
        completed=run.status == "completed",
        terminal_reason=run.terminal_reason,
        event_count=len(events),
        action_count=sum(item.event == "action_decided" for item in events),
        rejected_action_count=sum(
            item.event == "action_rejected" for item in events
        ),
        tool_calls=len(tools),
        repeated_tool_calls=repeated,
        model_calls=len(models),
        total_tokens=sum(item.total_tokens for item in models),
        total_duration_ms=round(
            sum(item.duration_ms for item in models)
            + sum(item.duration_ms or 0 for item in tools),
            3,
        ),
    )
