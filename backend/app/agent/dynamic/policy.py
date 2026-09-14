"""Model-backed Planner/Executor/Replanner policy for dynamic_v2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from app.agent.dynamic.context import ContextPackage
from app.agent.dynamic.models import (
    AgentAction,
    AgentPlan,
    DynamicAgentState,
    ReplanProposal,
    ToolResult,
)
from app.agent.prompts.dynamic_agent import (
    DECISION_PROMPT,
    PLANNER_PROMPT,
    REPLAN_PROMPT,
)
from app.infrastructure.llm import StructuredModel, TokenUsage


@dataclass(frozen=True, slots=True)
class PolicyResult[OutputT: BaseModel]:
    value: OutputT
    usage: TokenUsage


class AgentPolicy(Protocol):
    async def create_plan(
        self,
        *,
        context: ContextPackage,
    ) -> PolicyResult[AgentPlan]: ...

    async def decide(
        self, context: ContextPackage
    ) -> PolicyResult[AgentAction]: ...

    async def replan(
        self,
        *,
        state: DynamicAgentState,
        context: ContextPackage,
        failure: ToolResult,
    ) -> PolicyResult[ReplanProposal]: ...


class ModelAgentPolicy:
    """将 StructuredModel 限制为三个 typed policy 接口。

    Planner、Executor、Replanner 使用不同 Prompt 和输出 Schema，便于独立版本化、评测
    与回滚。Kernel 不读取自由文本推理，只消费通过 Pydantic 校验的公开决策对象。
    """

    def __init__(self, model: StructuredModel) -> None:
        self.model = model

    async def create_plan(
        self,
        *,
        context: ContextPackage,
    ) -> PolicyResult[AgentPlan]:
        values = context.values
        result = await self.model.generate(
            PLANNER_PROMPT,
            {
                "objective": values["objective"],
                "initial_state": values["initial_state"],
                "available_tools": values["available_tools"],
                "max_steps": values["max_steps"],
            },
            AgentPlan,
            max_output_tokens=context.snapshot.reserved_output_tokens,
        )
        return PolicyResult(result.value, result.usage)

    async def decide(
        self, context: ContextPackage
    ) -> PolicyResult[AgentAction]:
        result = await self.model.generate(
            DECISION_PROMPT,
            {"context": context.values},
            AgentAction,
            max_output_tokens=context.snapshot.reserved_output_tokens,
        )
        return PolicyResult(result.value, result.usage)

    async def replan(
        self,
        *,
        state: DynamicAgentState,
        context: ContextPackage,
        failure: ToolResult,
    ) -> PolicyResult[ReplanProposal]:
        result = await self.model.generate(
            REPLAN_PROMPT,
            {
                "context": context.values,
                "failure": failure.model_dump(mode="json"),
            },
            ReplanProposal,
            max_output_tokens=context.snapshot.reserved_output_tokens,
        )
        return PolicyResult(result.value, result.usage)
