"""Minimal Stage 11 Context Compiler with source and token accounting."""

from __future__ import annotations

import json
from dataclasses import dataclass
from math import ceil

from app.agent.dynamic.models import DynamicAgentState, PlanStep, ToolSpec


@dataclass(frozen=True, slots=True)
class ContextPackage:
    values: dict[str, object]
    estimated_tokens: int
    source_ids: tuple[str, ...]
    truncated_observations: int


class MinimalContextCompiler:
    """为当前 Step 编译 Just-in-time Context，而不是塞入完整 Session 历史。

    Stage 11 只实现最小 Context policy：系统保留 objective、当前 Plan Step、最近
    Observations、剩余 Budget 和被 allowlist 过滤后的 Tool Schema。完整 compaction
    与 Artifact source mapping 留在 Stage 12。
    """

    def __init__(self, *, max_context_tokens: int = 12_000) -> None:
        if max_context_tokens < 1_000:
            raise ValueError("max_context_tokens must be at least 1000")
        self.max_context_tokens = max_context_tokens

    def compile(
        self,
        state: DynamicAgentState,
        step: PlanStep,
        tools: list[ToolSpec],
    ) -> ContextPackage:
        observations = [
            observation.model_dump(mode="json")
            for observation in state.observations[-12:]
        ]
        truncated = max(0, len(state.observations) - len(observations))
        values: dict[str, object] = {
            "objective": state.plan.objective,
            "plan_version": state.plan.version,
            "current_step": step.model_dump(mode="json"),
            "recent_observations": observations,
            "budget": state.budget.model_dump(mode="json"),
            "usage": state.usage.model_dump(mode="json"),
            "available_tools": [tool.model_dump(mode="json") for tool in tools],
        }
        estimated = _estimate_tokens(values)
        while estimated > self.max_context_tokens and observations:
            observations.pop(0)
            truncated += 1
            estimated = _estimate_tokens(values)
        if estimated > self.max_context_tokens:
            raise ValueError(
                "minimal Agent context exceeds the configured token budget"
            )
        return ContextPackage(
            values=values,
            estimated_tokens=estimated,
            source_ids=tuple(item["id"] for item in observations),
            truncated_observations=truncated,
        )


def _estimate_tokens(value: object) -> int:
    # 没有 provider tokenizer 时使用保守字符估算，并把结果写入 Trace 供 Stage 12 校准。
    serialized = json.dumps(value, ensure_ascii=False, default=str)
    return ceil(len(serialized) / 3)
